import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from mercury_tools.catalog.identity import (
    canonical_json,
    validate_action_identity,
)
from mercury_tools.catalog.models import (
    CatalogAction,
    CatalogSource,
    revalidate_catalog_action,
    revalidate_catalog_source,
)
from mercury_tools.local.repository import RepositoryContext

_ACTION_FILENAME = re.compile(r"^act_[0-9a-f]{24}\.json$")
_SOURCE_FILENAME = re.compile(r"^src_[0-9a-f]{24}\.json$")


@dataclass
class _WritePlan:
    destination: Path
    payload: bytes
    staged_path: Path | None = None
    backup_path: Path | None = None
    existed: bool = False


class LocalCatalogStore:
    def __init__(self, context: RepositoryContext) -> None:
        self._context = context

    def write_import(self, source: CatalogSource, actions: list[CatalogAction]) -> None:
        sources_dir, actions_dir = self._directories()
        plans = self._prepare_import(sources_dir, actions_dir, source, actions)
        try:
            self._stage_import(plans)
            self._commit_import(plans)
        finally:
            for plan in plans:
                _remove_temporary(plan.staged_path)
                _remove_temporary(plan.backup_path)

    def list_actions(self) -> list[CatalogAction]:
        _, actions_dir = self._directories()
        actions: list[CatalogAction] = []
        seen_action_ids: set[str] = set()
        seen_version_ids: set[str] = set()
        for path in sorted(actions_dir.iterdir(), key=lambda item: item.name):
            _require_regular_file(path)
            if not _ACTION_FILENAME.fullmatch(path.name):
                raise ValueError("catalog_action_filename_invalid")
            action = _decode_action(path.read_text(encoding="utf-8"), path.name[:-5])
            if action.action_id in seen_action_ids or action.version_id in seen_version_ids:
                raise ValueError("catalog_action_duplicate")
            seen_action_ids.add(action.action_id)
            seen_version_ids.add(action.version_id)
            actions.append(action)
        return merge_actions((), actions)

    def _directories(self) -> tuple[Path, Path]:
        context = self._context
        if context.catalog_dir != context.mercury_dir / "catalog":
            raise ValueError("catalog_path_escape")
        if not context.catalog_dir.is_relative_to(context.mercury_dir):
            raise ValueError("catalog_path_escape")
        _require_real_directory(context.root)
        _require_real_directory(context.mercury_dir)
        _require_real_directory(context.catalog_dir)
        sources_dir = context.catalog_dir / "sources"
        actions_dir = context.catalog_dir / "actions"
        _require_real_directory(sources_dir)
        _require_real_directory(actions_dir)
        return sources_dir, actions_dir

    @staticmethod
    def _prepare_import(
        sources_dir: Path,
        actions_dir: Path,
        source: CatalogSource,
        actions: list[CatalogAction],
    ) -> list[_WritePlan]:
        try:
            validated_source = revalidate_catalog_source(source)
        except (AttributeError, TypeError, ValidationError, ValueError):
            raise ValueError("catalog_source_invalid") from None

        validated_actions: list[CatalogAction] = []
        seen_action_ids: set[str] = set()
        for action in actions:
            try:
                validated_action = revalidate_catalog_action(action)
            except (AttributeError, TypeError, ValidationError, ValueError):
                raise ValueError("catalog_action_invalid") from None
            if validated_action.action_id in seen_action_ids:
                raise ValueError("catalog_action_duplicate")
            seen_action_ids.add(validated_action.action_id)
            validated_actions.append(validated_action)

        plans = [
            _plan_write(
                sources_dir,
                f"{validated_source.source_id}.json",
                canonical_json(validated_source.model_dump(mode="json")),
            )
        ]
        plans.extend(
            _plan_write(
                actions_dir,
                f"{action.action_id}.json",
                canonical_json(action.model_dump(mode="json")),
            )
            for action in validated_actions
        )
        return plans

    @staticmethod
    def _stage_import(plans: list[_WritePlan]) -> None:
        for plan in plans:
            plan.staged_path = _stage_bytes(plan.destination.parent, plan.payload)
        for plan in plans:
            plan.existed = plan.destination.exists()
            if plan.existed:
                _require_regular_file(plan.destination)
                plan.backup_path = _stage_bytes(
                    plan.destination.parent,
                    _read_regular_file(plan.destination),
                )

    @staticmethod
    def _commit_import(plans: list[_WritePlan]) -> None:
        committed: list[_WritePlan] = []
        try:
            for plan in plans:
                _validate_destination(plan.destination)
                if plan.staged_path is None:
                    raise RuntimeError("catalog_stage_missing")
                os.replace(plan.staged_path, plan.destination)
                plan.staged_path = None
                committed.append(plan)
                _fsync_directory(plan.destination.parent)
        except BaseException:
            try:
                _rollback_import(committed)
            except BaseException as rollback_error:
                raise RuntimeError("catalog_import_rollback_failed") from rollback_error
            raise


def merge_actions(
    global_actions: Iterable[CatalogAction],
    local_actions: Iterable[CatalogAction],
) -> list[CatalogAction]:
    global_by_id = _unique_actions(global_actions)
    local_by_id = _unique_actions(local_actions)
    merged = {**global_by_id, **local_by_id}
    return sorted(
        merged.values(),
        key=lambda action: (
            action.connector_id,
            action.capability,
            action.method.value,
            action.path_template,
            action.variant_id,
            action.action_id,
            action.version_id,
        ),
    )


def _decode_action(payload: str, expected_action_id: str) -> CatalogAction:
    try:
        data = json.loads(payload)
        action = CatalogAction.model_validate(data)
        validate_action_identity(action)
        if action.action_id != expected_action_id:
            raise ValueError("catalog_action_filename_mismatch")
        if payload != canonical_json(action.model_dump(mode="json")):
            raise ValueError("catalog_action_content_invalid")
        return action
    except ValidationError:
        raise ValueError("catalog_action_invalid") from None


def _unique_actions(actions: Iterable[CatalogAction]) -> dict[str, CatalogAction]:
    indexed: dict[str, CatalogAction] = {}
    for action in actions:
        if action.action_id in indexed:
            raise ValueError("catalog_action_duplicate")
        indexed[action.action_id] = action
    return indexed


def _plan_write(directory: Path, filename: str, payload: str) -> _WritePlan:
    if not (_ACTION_FILENAME.fullmatch(filename) or _SOURCE_FILENAME.fullmatch(filename)):
        raise ValueError("catalog_filename_invalid")
    destination = directory / filename
    if destination.parent != directory:
        raise ValueError("catalog_path_escape")
    _validate_destination(destination)
    return _WritePlan(destination=destination, payload=payload.encode("utf-8"))


def _stage_bytes(directory: Path, payload: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".catalog-",
        suffix=".tmp",
        dir=directory,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary_path
    except BaseException:
        _remove_temporary(temporary_path)
        raise


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("catalog_path_invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _validate_destination(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _require_regular_file(path)


def _rollback_import(committed: list[_WritePlan]) -> None:
    for plan in reversed(committed):
        if plan.existed:
            if plan.backup_path is None:
                raise RuntimeError("catalog_backup_missing")
            os.replace(plan.backup_path, plan.destination)
            plan.backup_path = None
        else:
            plan.destination.unlink(missing_ok=True)
        _fsync_directory(plan.destination.parent)


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_temporary(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)


def _require_real_directory(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise ValueError("catalog_path_invalid") from error
    if stat.S_ISLNK(status.st_mode):
        raise ValueError("catalog_symlink")
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError("catalog_path_invalid")


def _require_regular_file(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise ValueError("catalog_path_invalid") from error
    if stat.S_ISLNK(status.st_mode):
        raise ValueError("catalog_symlink")
    if not stat.S_ISREG(status.st_mode):
        raise ValueError("catalog_path_invalid")
