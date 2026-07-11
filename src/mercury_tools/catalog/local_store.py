import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from mercury_tools.catalog.identity import (
    canonical_json,
    validate_action_identity,
    validate_source_identity,
)
from mercury_tools.catalog.models import CatalogAction, CatalogSource
from mercury_tools.local.repository import RepositoryContext

_ACTION_FILENAME = re.compile(r"^act_[0-9a-f]{24}\.json$")
_SOURCE_FILENAME = re.compile(r"^src_[0-9a-f]{24}\.json$")


class LocalCatalogStore:
    def __init__(self, context: RepositoryContext) -> None:
        self._context = context

    def write_import(self, source: CatalogSource, actions: list[CatalogAction]) -> None:
        sources_dir, actions_dir = self._directories()
        validate_source_identity(source)
        source_payload = canonical_json(source.model_dump(mode="json"))
        self._write_atomic(sources_dir, f"{source.source_id}.json", source_payload)

        seen_action_ids: set[str] = set()
        for action in actions:
            validate_action_identity(action)
            if action.action_id in seen_action_ids:
                raise ValueError("catalog_action_duplicate")
            seen_action_ids.add(action.action_id)
            payload = canonical_json(action.model_dump(mode="json"))
            self._write_atomic(actions_dir, f"{action.action_id}.json", payload)

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
    def _write_atomic(directory: Path, filename: str, payload: str) -> None:
        if not (_ACTION_FILENAME.fullmatch(filename) or _SOURCE_FILENAME.fullmatch(filename)):
            raise ValueError("catalog_filename_invalid")
        destination = directory / filename
        if destination.parent != directory:
            raise ValueError("catalog_path_escape")
        if destination.exists() or destination.is_symlink():
            _reject_symlink(destination)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".catalog-",
            suffix=".tmp",
            dir=directory,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        finally:
            if temporary_path.exists() or temporary_path.is_symlink():
                temporary_path.unlink()


def merge_actions(
    global_actions: Iterable[CatalogAction],
    local_actions: Iterable[CatalogAction],
) -> list[CatalogAction]:
    merged = {action.action_id: action for action in global_actions}
    merged.update({action.action_id: action for action in local_actions})
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
    except ValidationError as error:
        raise ValueError("catalog_action_invalid") from error


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


def _reject_symlink(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(status.st_mode):
        raise ValueError("catalog_symlink")
