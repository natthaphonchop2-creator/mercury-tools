"""Confined, fail-closed state for one sandbox qualification run."""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from mercury_tools.qualification.models import QualificationRunState, StrictSafeModel

_RUN_ID = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
_FIXTURE_HANDLE = re.compile(r"^fx_[0-9A-HJKMNP-TV-Z]{26}$")
_ACTION_ID = re.compile(r"^act_[0-9a-f]{24}$")
_VERSION_ID = re.compile(r"^av_[0-9a-f]{64}$")
_MAX_STATE_BYTES = 1024 * 1024
_STATE_NAME = "state.json"


class CleanupStatus(StrEnum):
    PENDING = "pending"
    CLEANED = "cleaned"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FixtureReference(StrictSafeModel):
    """The only fixture representation accepted by persistent state."""

    handle: str = Field(pattern=r"^fx_[0-9A-HJKMNP-TV-Z]{26}$")
    action_id: str = Field(pattern=r"^act_[0-9a-f]{24}$")
    version_id: str = Field(pattern=r"^av_[0-9a-f]{64}$")
    cleanup_action_id: str = Field(pattern=r"^act_[0-9a-f]{24}$")
    cleanup_version_id: str = Field(pattern=r"^av_[0-9a-f]{64}$")
    depends_on: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_dependencies(self) -> FixtureReference:
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("fixture_dependency_duplicate")
        for handle in self.depends_on:
            validate_fixture_handle(handle)
        return self


class _StoredFixture(FixtureReference):
    registered_at: datetime
    cleanup_status: CleanupStatus = CleanupStatus.PENDING
    cleanup_updated_at: datetime | None = None

    @model_validator(mode="after")
    def validate_cleanup_timestamp(self) -> _StoredFixture:
        _require_utc_timestamp(self.registered_at)
        if self.cleanup_status is CleanupStatus.PENDING:
            if self.cleanup_updated_at is not None:
                raise ValueError("qualification_state_invalid")
        elif self.cleanup_updated_at is None:
            raise ValueError("qualification_state_invalid")
        else:
            _require_utc_timestamp(self.cleanup_updated_at)
        return self


class _PersistedRun(StrictSafeModel):
    run_id: str = Field(pattern=r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
    state: QualificationRunState
    publication_allowed: bool
    quarantine_reason: Literal["cleanup_failed", "outcome_unknown", "process_lost"] | None
    created_at: datetime
    updated_at: datetime
    fixtures: tuple[_StoredFixture, ...] = ()

    @model_validator(mode="after")
    def validate_run_state(self) -> _PersistedRun:
        _require_utc_timestamp(self.created_at)
        _require_utc_timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("qualification_state_invalid")
        handles = tuple(fixture.handle for fixture in self.fixtures)
        if handles != tuple(sorted(handles)) or len(handles) != len(set(handles)):
            raise ValueError("qualification_state_invalid")
        known_handles = set(handles)
        if any(
            dependency not in known_handles
            for fixture in self.fixtures
            for dependency in fixture.depends_on
        ):
            raise ValueError("qualification_state_invalid")
        if self.state is QualificationRunState.QUARANTINED:
            if self.quarantine_reason is None or self.publication_allowed:
                raise ValueError("qualification_state_invalid")
        elif self.quarantine_reason is not None:
            raise ValueError("qualification_state_invalid")
        if self.state is QualificationRunState.COMPLETED:
            if not self.publication_allowed or any(
                fixture.cleanup_status is not CleanupStatus.CLEANED for fixture in self.fixtures
            ):
                raise ValueError("qualification_state_invalid")
        elif self.publication_allowed:
            raise ValueError("qualification_state_invalid")
        return self


class _DuplicateJsonKey(ValueError):
    pass


class QualificationRunStore:
    """Persist controlled qualification state below one repository root."""

    def __init__(
        self,
        repository_root: Path,
        run_id: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._run_id = validate_run_id(run_id)
        self._root = _validated_root(repository_root)
        _require_atomic_state_capabilities()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._state_path = self._root / ".mercury" / "validation" / self._run_id / _STATE_NAME

        run_fd = _open_run_directory(self._root, self._run_id)
        try:
            existing = _read_state(run_fd)
            recovered = existing is not None
            if existing is None:
                now = self._timestamp()
                initial = _PersistedRun(
                    run_id=self._run_id,
                    state=QualificationRunState.FAILED,
                    publication_allowed=False,
                    quarantine_reason=None,
                    created_at=now,
                    updated_at=now,
                    fixtures=(),
                )
                _write_state(self._root, self._run_id, run_fd, initial)
                _validate_run_directory_binding(self._root, self._run_id, run_fd)
                self._record = initial
            else:
                if existing.run_id != self._run_id:
                    raise ValueError("qualification_state_invalid")
                _validate_run_directory_binding(self._root, self._run_id, run_fd)
                self._record = existing
        finally:
            os.close(run_fd)

        if recovered and self._record.state is QualificationRunState.FAILED:
            self.quarantine("process_lost")

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def state_path(self) -> Path:
        return self._state_path

    @property
    def state(self) -> QualificationRunState:
        return self._record.state

    @property
    def publication_allowed(self) -> bool:
        return self._record.publication_allowed

    @property
    def quarantine_reason(self) -> str | None:
        return self._record.quarantine_reason

    def record_fixtures(self, fixtures: Sequence[FixtureReference]) -> None:
        if self.state is QualificationRunState.COMPLETED:
            raise ValueError("qualification_run_finalized")
        if self.state is QualificationRunState.QUARANTINED:
            raise ValueError("qualification_run_quarantined")
        if isinstance(fixtures, (str, bytes, bytearray)) or not isinstance(fixtures, Sequence):
            raise ValueError("qualification_fixture_invalid")

        existing = {fixture.handle: fixture for fixture in self._record.fixtures}
        changed = False
        now = self._timestamp()
        try:
            for raw_fixture in fixtures:
                reference = FixtureReference.model_validate(
                    {
                        field_name: getattr(raw_fixture, field_name)
                        for field_name in FixtureReference.model_fields
                    }
                )
                current = existing.get(reference.handle)
                if current is not None:
                    if _fixture_reference(current) != reference:
                        raise ValueError("qualification_fixture_mismatch")
                    continue
                existing[reference.handle] = _StoredFixture(
                    **reference.model_dump(mode="python"),
                    registered_at=now,
                    cleanup_status=CleanupStatus.PENDING,
                    cleanup_updated_at=None,
                )
                changed = True
        except ValueError as exc:
            if str(exc) == "qualification_fixture_mismatch":
                raise
            raise ValueError("qualification_fixture_invalid") from None
        except (AttributeError, TypeError):
            raise ValueError("qualification_fixture_invalid") from None

        if changed:
            self._replace(
                transition_at=now,
                fixtures=tuple(existing[key] for key in sorted(existing)),
            )

    def cleanup_status(self, handle: str) -> CleanupStatus:
        checked_handle = validate_fixture_handle(handle)
        for fixture in self._record.fixtures:
            if fixture.handle == checked_handle:
                return fixture.cleanup_status
        raise ValueError("qualification_fixture_missing")

    def mark_cleanup(self, handle: str, status: CleanupStatus) -> None:
        checked_handle = validate_fixture_handle(handle)
        try:
            checked_status = CleanupStatus(status)
        except (TypeError, ValueError):
            raise ValueError("qualification_cleanup_status_invalid") from None
        if checked_status is CleanupStatus.PENDING:
            raise ValueError("qualification_cleanup_status_invalid")

        now = self._timestamp()
        updated: list[_StoredFixture] = []
        found = False
        changed = False
        for fixture in self._record.fixtures:
            if fixture.handle != checked_handle:
                updated.append(fixture)
                continue
            found = True
            if fixture.cleanup_status is checked_status:
                updated.append(fixture)
                continue
            if fixture.cleanup_status is not CleanupStatus.PENDING:
                raise ValueError("qualification_cleanup_status_conflict")
            updated.append(
                fixture.model_copy(
                    update={
                        "cleanup_status": checked_status,
                        "cleanup_updated_at": now,
                    }
                )
            )
            changed = True
        if not found:
            raise ValueError("qualification_fixture_missing")
        if changed:
            self._replace(transition_at=now, fixtures=tuple(updated))

    def quarantine_cleanup(self, handle: str, status: CleanupStatus) -> None:
        checked_handle = validate_fixture_handle(handle)
        try:
            checked_status = CleanupStatus(status)
        except (TypeError, ValueError):
            raise ValueError("qualification_cleanup_status_invalid") from None
        reasons = {
            CleanupStatus.FAILED: "cleanup_failed",
            CleanupStatus.OUTCOME_UNKNOWN: "outcome_unknown",
        }
        reason = reasons.get(checked_status)
        if reason is None:
            raise ValueError("qualification_cleanup_status_invalid")

        target = next(
            (fixture for fixture in self._record.fixtures if fixture.handle == checked_handle),
            None,
        )
        if target is None:
            raise ValueError("qualification_fixture_missing")
        if self.state is QualificationRunState.COMPLETED:
            raise ValueError("qualification_run_finalized")
        if self.state is QualificationRunState.QUARANTINED:
            if self.quarantine_reason == reason and target.cleanup_status is checked_status:
                return
            raise ValueError("qualification_run_quarantined")
        if target.cleanup_status is not CleanupStatus.PENDING:
            raise ValueError("qualification_cleanup_status_conflict")

        now = self._timestamp()
        updated = tuple(
            fixture.model_copy(
                update={
                    "cleanup_status": checked_status,
                    "cleanup_updated_at": now,
                }
            )
            if fixture.handle == checked_handle
            else fixture
            for fixture in self._record.fixtures
        )
        self._replace(
            transition_at=now,
            fixtures=updated,
            state=QualificationRunState.QUARANTINED,
            publication_allowed=False,
            quarantine_reason=reason,
        )

    def quarantine(self, reason: str) -> None:
        if reason not in {"cleanup_failed", "outcome_unknown", "process_lost"}:
            raise ValueError("qualification_quarantine_reason_invalid")
        if self.state is QualificationRunState.QUARANTINED:
            return
        self._replace(
            state=QualificationRunState.QUARANTINED,
            publication_allowed=False,
            quarantine_reason=reason,
        )

    def complete(self) -> None:
        if self.state is QualificationRunState.COMPLETED:
            return
        if self.state is QualificationRunState.QUARANTINED:
            raise ValueError("qualification_run_quarantined")
        if any(
            fixture.cleanup_status is not CleanupStatus.CLEANED for fixture in self._record.fixtures
        ):
            raise ValueError("qualification_cleanup_incomplete")
        self._replace(
            state=QualificationRunState.COMPLETED,
            publication_allowed=True,
            quarantine_reason=None,
        )

    def _replace(
        self,
        *,
        transition_at: datetime | None = None,
        **updates: Any,
    ) -> None:
        timestamp = self._timestamp() if transition_at is None else transition_at
        if timestamp < self._record.updated_at:
            raise ValueError("qualification_clock_regressed")
        updates["updated_at"] = timestamp
        candidate = self._record.model_copy(update=updates)
        run_fd = _open_run_directory(self._root, self._run_id)
        try:
            _write_state(self._root, self._run_id, run_fd, candidate)
            _validate_run_directory_binding(self._root, self._run_id, run_fd)
            self._record = candidate
        finally:
            os.close(run_fd)

    def _timestamp(self) -> datetime:
        try:
            value = self._clock()
            _require_utc_timestamp(value)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("qualification_clock_invalid") from None
        return value.astimezone(UTC)


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("qualification_run_id_invalid")
    return value


def validate_fixture_handle(value: str) -> str:
    if not isinstance(value, str) or _FIXTURE_HANDLE.fullmatch(value) is None:
        raise ValueError("fixture_handle_invalid")
    return value


def validate_action_ref(value: tuple[str, str]) -> tuple[str, str]:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not isinstance(value[1], str)
        or _ACTION_ID.fullmatch(value[0]) is None
        or _VERSION_ID.fullmatch(value[1]) is None
    ):
        raise ValueError("fixture_action_reference_invalid")
    return value


def _fixture_reference(fixture: _StoredFixture) -> FixtureReference:
    return FixtureReference(
        handle=fixture.handle,
        action_id=fixture.action_id,
        version_id=fixture.version_id,
        cleanup_action_id=fixture.cleanup_action_id,
        cleanup_version_id=fixture.cleanup_version_id,
        depends_on=fixture.depends_on,
    )


def _validated_root(value: Path) -> Path:
    try:
        candidate = Path(os.path.abspath(os.fspath(value)))
        state = os.lstat(candidate)
    except (OSError, TypeError, ValueError):
        raise ValueError("qualification_root_invalid") from None
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise ValueError("qualification_root_invalid")
    return candidate


def _require_atomic_state_capabilities() -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    required_dir_fd = (os.open, os.mkdir, os.stat, os.rename, os.unlink)
    if (
        nofollow is None
        or directory is None
        or any(function not in os.supports_dir_fd for function in required_dir_fd)
        or os.stat not in os.supports_follow_symlinks
    ):
        raise ValueError("qualification_state_path_unsafe")


def _open_run_directory(root: Path, run_id: str, *, create: bool = True) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise ValueError("qualification_state_path_unsafe")
    current_fd = -1
    try:
        expected = os.lstat(root)
        current_fd = os.open(
            root,
            os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(current_fd)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise OSError(errno.ESTALE, "root binding changed")
        for name in (".mercury", "validation", run_id):
            next_fd = _open_child_directory(current_fd, name, create=create)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except ValueError:
        if current_fd >= 0:
            with suppress(OSError):
                os.close(current_fd)
        raise
    except OSError:
        if current_fd >= 0:
            with suppress(OSError):
                os.close(current_fd)
        raise ValueError("qualification_state_path_unsafe") from None


def _open_child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        with suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
    state = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise ValueError("qualification_state_path_unsafe")
    child_fd = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_fd,
    )
    opened = os.fstat(child_fd)
    if (opened.st_dev, opened.st_ino) != (state.st_dev, state.st_ino):
        os.close(child_fd)
        raise ValueError("qualification_state_path_unsafe")
    if create:
        os.fchmod(child_fd, 0o700)
    return child_fd


def _validate_run_directory_binding(root: Path, run_id: str, run_fd: int) -> None:
    lexical_fd = -1
    try:
        lexical_fd = _open_run_directory(root, run_id, create=False)
        expected = os.fstat(run_fd)
        current = os.fstat(lexical_fd)
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("qualification_state_path_unsafe")
    except ValueError:
        raise
    except OSError:
        raise ValueError("qualification_state_path_unsafe") from None
    finally:
        if lexical_fd >= 0:
            with suppress(OSError):
                os.close(lexical_fd)


def _read_state(run_fd: int) -> _PersistedRun | None:
    try:
        state = os.stat(_STATE_NAME, dir_fd=run_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError("qualification_state_path_unsafe") from None
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        raise ValueError("qualification_state_path_unsafe")
    try:
        descriptor = os.open(
            _STATE_NAME,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=run_fd,
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (state.st_dev, state.st_ino)
                or opened.st_size > _MAX_STATE_BYTES
            ):
                raise ValueError("qualification_state_path_unsafe")
            data = _read_all(descriptor)
        finally:
            os.close(descriptor)
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(payload, dict):
            raise ValueError
        return _PersistedRun.model_validate(payload)
    except ValueError as exc:
        if str(exc) == "qualification_state_path_unsafe":
            raise
        raise ValueError("qualification_state_invalid") from None
    except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateJsonKey):
        raise ValueError("qualification_state_invalid") from None


def _write_state(
    root: Path,
    run_id: str,
    run_fd: int,
    record: _PersistedRun,
) -> None:
    payload = (
        json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > _MAX_STATE_BYTES:
        raise ValueError("qualification_state_write_failed")
    _validate_state_target(run_fd)

    temporary_name = ""
    descriptor = -1
    try:
        for _ in range(16):
            temporary_name = f".state-{secrets.token_hex(8)}.tmp"
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=run_fd,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise OSError(errno.EEXIST, "temporary state unavailable")
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _validate_state_target(run_fd)
        _validate_run_directory_binding(root, run_id, run_fd)
        os.rename(
            temporary_name,
            _STATE_NAME,
            src_dir_fd=run_fd,
            dst_dir_fd=run_fd,
        )
        _validate_run_directory_binding(root, run_id, run_fd)
        temporary_name = ""
        os.fsync(run_fd)
        _validate_run_directory_binding(root, run_id, run_fd)
    except ValueError:
        raise
    except OSError:
        raise ValueError("qualification_state_write_failed") from None
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        if temporary_name:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=run_fd)


def _validate_state_target(run_fd: int) -> None:
    try:
        state = os.stat(_STATE_NAME, dir_fd=run_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise ValueError("qualification_state_path_unsafe") from None
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        raise ValueError("qualification_state_path_unsafe")


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_STATE_BYTES:
            raise ValueError("qualification_state_invalid")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "state write failed")
        view = view[written:]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _require_utc_timestamp(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("qualification_state_invalid")


__all__ = [
    "CleanupStatus",
    "FixtureReference",
    "QualificationRunStore",
    "validate_action_ref",
    "validate_fixture_handle",
    "validate_run_id",
]
