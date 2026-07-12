"""Append-only local audit evidence with strict, indexed exact lookup."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import time
from collections.abc import Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mercury_tools.safety.redaction import redact_json

try:  # pragma: no cover - platform import
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None

_EVENT_ID = re.compile(r"^evt_[0-9a-f]{24}$")
_REPOSITORY_ID = re.compile(r"^repo_[0-9a-f]{16}$")
_ACTION_ID = re.compile(r"^act_[0-9a-f]{24}$")
_VERSION_ID = re.compile(r"^av_[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^req_[0-9a-z_]{8,128}$")
_SESSION_ID = re.compile(r"^(?:ses|session)_[0-9a-z]{8,128}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STATUS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PAYLOAD_HASH = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_REFERENCE = re.compile(
    r"^(?:artifact:)?art_[0-9a-f]{24,64}(?:\.[a-z0-9]{1,16})?$|"
    r"^artifacts/art_[0-9a-f]{24,64}(?:\.[a-z0-9]{1,16})?$"
)
_EMAIL_VALUE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Z]{2,}(?![\w.-])")
_TAX_ID_VALUE = re.compile(r"(?<!\d)\d{13}(?!\d)")
_TOKEN_VALUE = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+[A-Za-z0-9+/=._-]+|"
    r"\b(?:github_pat_|ghp_|sk-|sk_|xox[bp]-|ya29\.)[A-Za-z0-9._-]+)"
)

MAX_AUDIT_LINE_BYTES = 64 * 1024
MAX_AUDIT_TOP_LEVEL_KEYS = 64
MAX_AUDIT_RESPONSE_KEYS = 32
MAX_AUDIT_KEY_BYTES = 64
MAX_AUDIT_SCALAR_BYTES = 2 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_INDEX_SUFFIX = ".index.sqlite"
_INDEX_SCHEMA_VERSION = 2
_LOCK_GUARD_SUFFIX = ".lock.guard"
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.025
_ROW_HASH = re.compile(r"^[0-9a-f]{64}$")

_SAFE_EVENT_FIELDS = frozenset(
    {
        "action_id",
        "artifact_path",
        "confirmation_count",
        "connector_id",
        "environment",
        "event",
        "event_id",
        "failure_reason",
        "latency_ms",
        "local_session_id",
        "method",
        "payload_hash",
        "recorded_at",
        "repository_id",
        "request_id",
        "required_confirmations",
        "response_summary",
        "risk_tier",
        "state",
        "version_id",
    }
)
_SAFE_RESPONSE_FIELDS = frozenset(
    {
        "error_code",
        "http_status",
        "latency_ms",
        "outcome",
        "provider_code",
        "provider_status",
        "status",
        "status_class",
        "success",
    }
)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_EVENT_VALUES = frozenset(
    {
        "completed",
        "confirmation_recorded",
        "confirmed",
        "dispatch_started",
        "execution_completed",
        "execution_started",
        "invalidated",
        "pre_dispatch_failed",
        "preview_created",
    }
)
_STATE_VALUES = frozenset(
    {
        "awaiting_confirmation",
        "awaiting_final_confirmation",
        "executing",
        "failed",
        "outcome_unknown",
        "previewed",
        "ready_to_execute",
        "succeeded",
    }
)
_FAILURE_VALUES = frozenset(
    {
        "credentials_cleared",
        "execution_failed",
        "outcome_unknown",
        "pre_dispatch_failed",
        "preview_expired",
    }
)
_RESPONSE_SEMANTIC_VALUES = {
    "error_code": frozenset(
        {
            "authentication_failed",
            "authorization_failed",
            "network_error",
            "provider_error",
            "rate_limited",
            "request_failed",
            "timeout",
            "unknown",
            "validation_failed",
        }
    ),
    "outcome": frozenset(
        {"failed", "outcome_unknown", "succeeded", "timeout", "unknown"}
    ),
    "provider_code": frozenset({"error", "failed", "success", "unknown"}),
    "provider_status": frozenset(
        {"error", "failed", "ok", "pending", "success", "timeout", "unknown"}
    ),
    "status": frozenset(
        {"error", "failed", "ok", "pending", "success", "timeout", "unknown"}
    ),
    "status_class": frozenset(
        {
            "2xx",
            "3xx",
            "4xx",
            "5xx",
            "network_error",
            "provider_error",
            "success",
            "timeout",
            "unknown",
        }
    ),
}


class _IndexResetRequired(Exception):
    pass


@dataclass(frozen=True)
class _FallbackGuard:
    path: Path
    device: int
    inode: int


class AuditLedger:
    """Durable JSONL evidence with an owner-only, reconstructible event index."""

    def __init__(self, path: Path) -> None:
        candidate = Path(path)
        if candidate.name in {"", ".", ".."}:
            raise ValueError("invalid_audit_path")
        try:
            parent = candidate.parent.resolve(strict=True)
            parent_mode = candidate.parent.lstat().st_mode
        except OSError as exc:
            raise ValueError("invalid_audit_path") from exc
        if candidate.parent != parent or stat.S_ISLNK(parent_mode) or not stat.S_ISDIR(
            parent_mode
        ):
            raise ValueError("invalid_audit_path")
        self._parent = parent
        self._name = candidate.name
        self._path = parent / self._name
        self._index_name = self._name + _INDEX_SUFFIX
        self._index_path = parent / self._index_name
        self._validate_target(self._path)
        self._validate_target(self._index_path)
        self._enforce_target_mode(self._name)
        self._enforce_target_mode(self._index_name)

    @property
    def index_path(self) -> Path:
        return self._index_path

    def record(self, event: Mapping[str, Any]) -> str:
        if not isinstance(event, Mapping):
            raise ValueError("invalid_audit_event")
        row = _sanitize_mapping(event)
        row.pop("event_id", None)
        row.pop("recorded_at", None)

        ledger_fd = self._open_ledger(create=True, write=True)
        connection: sqlite3.Connection | None = None
        fallback_guard: _FallbackGuard | None = None
        try:
            fallback_guard = _lock_file(ledger_fd, self._parent, self._name)
            connection = self._open_ready_index(ledger_fd)
            event_id = self._new_event_id(connection)
            row["event_id"] = event_id
            row["recorded_at"] = datetime.now(UTC).isoformat()
            encoded = _encode_row(row)
            offset = os.fstat(ledger_fd).st_size
            written = os.write(ledger_fd, encoded)
            if written != len(encoded):
                raise ValueError("audit_write_failed")
            os.fsync(ledger_fd)
            ledger_state = os.fstat(ledger_fd)
            self._index_insert(
                connection,
                event_id=event_id,
                offset=offset,
                length=len(encoded),
                row_hash=_row_hash(encoded),
                ledger_state=ledger_state,
            )
            return event_id
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError("audit_write_failed") from exc
        finally:
            if connection is not None:
                connection.close()
            try:
                _unlock_file(ledger_fd, fallback_guard)
            finally:
                os.close(ledger_fd)

    def get(self, event_id: str) -> dict[str, Any] | None:
        if not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None:
            return None
        ledger_fd = self._open_ledger(create=False, write=False)
        if ledger_fd is None:
            return None
        connection: sqlite3.Connection | None = None
        fallback_guard: _FallbackGuard | None = None
        try:
            fallback_guard = _lock_file(ledger_fd, self._parent, self._name)
            connection = self._open_ready_index(ledger_fd)
            indexed = self._indexed_event(connection, event_id)
            if indexed is None:
                self._rebuild_index(connection, ledger_fd, os.fstat(ledger_fd))
                indexed = self._indexed_event(connection, event_id)
                if indexed is None:
                    return None
            try:
                return self._read_indexed_event(ledger_fd, event_id, indexed)
            except ValueError as exc:
                if str(exc) != "audit_index_corrupt":
                    raise
                self._rebuild_index(connection, ledger_fd, os.fstat(ledger_fd))
                recovered = self._indexed_event(connection, event_id)
                if recovered is None:
                    return None
                return self._read_indexed_event(ledger_fd, event_id, recovered)
        except sqlite3.Error as exc:
            raise ValueError("audit_index_corrupt") from exc
        finally:
            if connection is not None:
                connection.close()
            try:
                _unlock_file(ledger_fd, fallback_guard)
            finally:
                os.close(ledger_fd)

    @staticmethod
    def _indexed_event(
        connection: sqlite3.Connection,
        event_id: str,
    ) -> sqlite3.Row | tuple[Any, ...] | None:
        return connection.execute(
            "SELECT byte_offset, byte_length, row_hash FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()

    @staticmethod
    def _read_indexed_event(
        ledger_fd: int,
        event_id: str,
        indexed: sqlite3.Row | tuple[Any, ...],
    ) -> dict[str, Any]:
        row_hash = indexed[2]
        if not isinstance(row_hash, str) or _ROW_HASH.fullmatch(row_hash) is None:
            raise ValueError("audit_index_corrupt")
        encoded = _read_exact_row(
            ledger_fd,
            offset=indexed[0],
            length=indexed[1],
        )
        if not secrets.compare_digest(_row_hash(encoded), row_hash):
            raise ValueError("audit_index_corrupt")
        decoded = _decode_ledger_row(encoded)
        if decoded.get("event_id") != event_id:
            raise ValueError("audit_index_corrupt")
        return _sanitize_mapping_for_read(decoded)

    def _open_ledger(self, *, create: bool, write: bool) -> int | None:
        self._validate_target(self._path)
        directory_fd = self._open_parent()
        file_fd = -1
        try:
            flags = (os.O_RDWR | os.O_APPEND) if write else os.O_RDONLY
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            if create:
                flags |= os.O_CREAT
            try:
                file_fd = os.open(self._name, flags, 0o600, dir_fd=directory_fd)
            except FileNotFoundError:
                if not create:
                    return None
                raise
            self._validate_open_file(directory_fd, self._name, file_fd)
            result = file_fd
            file_fd = -1
            return result
        except ValueError:
            raise
        except OSError as exc:
            code = "audit_write_failed" if write else "audit_read_failed"
            raise ValueError(code) from exc
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(directory_fd)

    def _open_index(self) -> sqlite3.Connection:
        for attempt in range(2):
            try:
                return self._open_index_once()
            except _IndexResetRequired as exc:
                if attempt == 1:
                    raise ValueError("audit_index_corrupt") from exc
                self._discard_index()
        raise ValueError("audit_index_corrupt")

    def _open_ready_index(self, ledger_fd: int) -> sqlite3.Connection:
        failure: BaseException | None = None
        for attempt in range(2):
            connection: sqlite3.Connection | None = None
            attempt_failure: BaseException | None = None
            try:
                connection = self._open_index()
                self._ensure_index(connection, ledger_fd)
                return connection
            except sqlite3.Error as exc:
                attempt_failure = failure = exc
            except ValueError as exc:
                if str(exc) != "audit_index_corrupt":
                    raise
                attempt_failure = failure = exc
            finally:
                if attempt_failure is not None and connection is not None:
                    connection.close()
            if attempt == 0:
                self._discard_index()
        raise ValueError("audit_index_corrupt") from failure

    def _open_index_once(self) -> sqlite3.Connection:
        self._validate_target(self._index_path)
        directory_fd = self._open_parent()
        index_fd = -1
        connection: sqlite3.Connection | None = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            index_fd = os.open(self._index_name, flags, 0o600, dir_fd=directory_fd)
            retained = self._validate_open_file(
                directory_fd,
                self._index_name,
                index_fd,
            )
            connection = sqlite3.connect(
                self._index_path,
                isolation_level=None,
                timeout=5,
            )
            self._verify_identity(directory_fd, self._index_name, retained)
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            self._initialize_index_schema(connection)
            result = connection
            connection = None
            return result
        except sqlite3.Error as exc:
            raise _IndexResetRequired from exc
        except _IndexResetRequired:
            raise
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError("invalid_audit_index_path") from exc
        finally:
            if connection is not None:
                connection.close()
            if index_fd >= 0:
                os.close(index_fd)
            os.close(directory_fd)

    @staticmethod
    def _initialize_index_schema(connection: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if tables:
            if version != _INDEX_SCHEMA_VERSION or tables != {
                "events",
                "ledger_metadata",
            }:
                raise _IndexResetRequired
            columns = {
                "events": ("event_id", "byte_offset", "byte_length", "row_hash"),
                "ledger_metadata": (
                    "singleton",
                    "device",
                    "inode",
                    "byte_size",
                    "modified_ns",
                    "changed_ns",
                    "event_count",
                    "indexed_bytes",
                ),
            }
            for table, expected in columns.items():
                actual = tuple(
                    row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                )
                if actual != expected:
                    raise _IndexResetRequired
            return

        connection.execute(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                byte_offset INTEGER NOT NULL,
                byte_length INTEGER NOT NULL,
                row_hash TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE ledger_metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                device INTEGER NOT NULL,
                inode INTEGER NOT NULL,
                byte_size INTEGER NOT NULL,
                modified_ns INTEGER NOT NULL,
                changed_ns INTEGER NOT NULL,
                event_count INTEGER NOT NULL,
                indexed_bytes INTEGER NOT NULL
            )
            """
        )
        connection.execute(f"PRAGMA user_version={_INDEX_SCHEMA_VERSION}")

    def _discard_index(self) -> None:
        directory_fd = self._open_parent()
        index_fd = -1
        try:
            flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                index_fd = os.open(self._index_name, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                return
            retained = self._validate_open_file(
                directory_fd,
                self._index_name,
                index_fd,
            )
            self._verify_identity(directory_fd, self._index_name, retained)
            os.unlink(self._index_name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except OSError as exc:
            raise ValueError("invalid_audit_index_path") from exc
        finally:
            if index_fd >= 0:
                os.close(index_fd)
            os.close(directory_fd)

    def _ensure_index(self, connection: sqlite3.Connection, ledger_fd: int) -> None:
        ledger_state = os.fstat(ledger_fd)
        metadata = connection.execute(
            """
            SELECT device, inode, byte_size, modified_ns, changed_ns,
                   event_count, indexed_bytes
            FROM ledger_metadata WHERE singleton = 1
            """
        ).fetchone()
        index_shape = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(byte_length), 0),
                   COALESCE(MAX(byte_offset + byte_length), 0),
                   COALESCE(MIN(byte_offset), 0)
            FROM events
            """
        ).fetchone()
        count, total_length, final_offset, first_offset = index_shape
        expected = (*_ledger_fingerprint(ledger_state), count, ledger_state.st_size)
        complete = (
            total_length == ledger_state.st_size
            and final_offset == ledger_state.st_size
            and first_offset == 0
        )
        if metadata is not None and tuple(metadata) == expected and complete:
            return
        self._rebuild_index(connection, ledger_fd, ledger_state)

    def _rebuild_index(
        self,
        connection: sqlite3.Connection,
        ledger_fd: int,
        initial_state: os.stat_result,
    ) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM events")
            event_count = 0
            for offset, encoded in _iter_ledger_rows(ledger_fd):
                decoded = _decode_ledger_row(encoded)
                sanitized = _sanitize_mapping_for_read(decoded)
                event_id = sanitized.get("event_id")
                if not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None:
                    raise ValueError("audit_ledger_corrupt")
                try:
                    connection.execute(
                        """
                        INSERT INTO events (event_id, byte_offset, byte_length, row_hash)
                        VALUES (?, ?, ?, ?)
                        """,
                        (event_id, offset, len(encoded), _row_hash(encoded)),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("audit_ledger_corrupt") from exc
                event_count += 1
            final_state = os.fstat(ledger_fd)
            if _ledger_fingerprint(final_state) != _ledger_fingerprint(initial_state):
                raise ValueError("audit_ledger_unavailable")
            self._replace_metadata(connection, final_state, event_count)
            connection.execute("COMMIT")
        except ValueError:
            _rollback(connection)
            raise
        except sqlite3.Error as exc:
            _rollback(connection)
            raise ValueError("audit_index_corrupt") from exc

    @staticmethod
    def _replace_metadata(
        connection: sqlite3.Connection,
        ledger_state: os.stat_result,
        event_count: int,
    ) -> None:
        connection.execute("DELETE FROM ledger_metadata")
        connection.execute(
            """
            INSERT INTO ledger_metadata (
                singleton, device, inode, byte_size, modified_ns, changed_ns,
                event_count, indexed_bytes
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*_ledger_fingerprint(ledger_state), event_count, ledger_state.st_size),
        )

    def _index_insert(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        offset: int,
        length: int,
        row_hash: str,
        ledger_state: os.stat_result,
    ) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO events (event_id, byte_offset, byte_length, row_hash)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, offset, length, row_hash),
            )
            event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self._replace_metadata(connection, ledger_state, event_count)
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            _rollback(connection)
            raise ValueError("audit_index_unavailable") from exc

    @staticmethod
    def _new_event_id(connection: sqlite3.Connection) -> str:
        for _ in range(16):
            event_id = "evt_" + secrets.token_hex(12)
            exists = connection.execute(
                "SELECT 1 FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if exists is None:
                return event_id
        raise ValueError("audit_index_unavailable")

    def _open_parent(self) -> int:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(self._parent, flags)
        except OSError as exc:
            raise ValueError("invalid_audit_path") from exc

    def _enforce_target_mode(self, name: str) -> None:
        directory_fd = self._open_parent()
        file_fd = -1
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                file_fd = os.open(name, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                return
            self._validate_open_file(directory_fd, name, file_fd)
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(directory_fd)

    @staticmethod
    def _validate_open_file(
        directory_fd: int,
        name: str,
        file_fd: int,
    ) -> os.stat_result:
        retained = os.fstat(file_fd)
        if not stat.S_ISREG(retained.st_mode):
            raise ValueError("invalid_audit_path")
        if os.name == "posix":
            if retained.st_uid != os.getuid() or retained.st_nlink != 1:
                raise ValueError("invalid_audit_path")
            if stat.S_IMODE(retained.st_mode) != 0o600:
                os.fchmod(file_fd, 0o600)
                retained = os.fstat(file_fd)
            if stat.S_IMODE(retained.st_mode) != 0o600:
                raise ValueError("invalid_audit_path")
        AuditLedger._verify_identity(directory_fd, name, retained)
        return retained

    @staticmethod
    def _verify_identity(
        directory_fd: int,
        name: str,
        retained: os.stat_result,
    ) -> None:
        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("invalid_audit_path") from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != (retained.st_dev, retained.st_ino)
        ):
            raise ValueError("invalid_audit_path")

    @staticmethod
    def _validate_target(path: Path) -> None:
        try:
            state = path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("invalid_audit_path") from exc
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
            raise ValueError("invalid_audit_path")


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for count, (key, item) in enumerate(value.items(), start=1):
        if count > MAX_AUDIT_TOP_LEVEL_KEYS:
            raise ValueError("audit_event_too_large")
        name = _bounded_key(key)
        if name not in _SAFE_EVENT_FIELDS:
            continue
        if name == "response_summary":
            sanitized[name] = _sanitize_response_summary(item)
        else:
            sanitized[name] = _sanitize_event_field(name, item)
    return sanitized


def _sanitize_mapping_for_read(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return _sanitize_mapping(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("audit_ledger_corrupt") from None


def _sanitize_response_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_audit_event")
    sanitized: dict[str, Any] = {}
    for count, (key, item) in enumerate(value.items(), start=1):
        if count > MAX_AUDIT_RESPONSE_KEYS:
            raise ValueError("audit_event_too_large")
        name = _bounded_key(key)
        if name not in _SAFE_RESPONSE_FIELDS:
            continue
        sanitized[name] = _sanitize_response_field(name, item)
    return sanitized


def _sanitize_event_field(name: str, value: Any) -> Any:
    scalar = _bounded_scalar(value)
    if name == "artifact_path":
        if not isinstance(scalar, str) or _ARTIFACT_REFERENCE.fullmatch(scalar) is None:
            return "[REDACTED]"
        return scalar
    if name == "method":
        if not isinstance(scalar, str) or scalar not in _METHODS:
            raise ValueError("invalid_audit_event")
    elif name == "event":
        scalar = _semantic_value(scalar, _EVENT_VALUES, _CODE)
    elif name == "state":
        scalar = _semantic_value(scalar, _STATE_VALUES, _CODE)
    elif name == "failure_reason":
        scalar = _semantic_value(scalar, _FAILURE_VALUES, _CODE)
    elif name in {"connector_id", "environment"}:
        _require_pattern(scalar, _IDENTIFIER)
    elif name == "repository_id":
        _require_pattern(scalar, _REPOSITORY_ID)
    elif name == "action_id":
        _require_pattern(scalar, _ACTION_ID)
    elif name == "version_id":
        _require_pattern(scalar, _VERSION_ID)
    elif name == "request_id":
        _require_pattern(scalar, _REQUEST_ID)
    elif name == "local_session_id":
        _require_pattern(scalar, _SESSION_ID)
    elif name == "event_id":
        _require_pattern(scalar, _EVENT_ID)
    elif name == "payload_hash":
        _require_pattern(scalar, _PAYLOAD_HASH)
    elif name == "recorded_at":
        scalar = _validated_timestamp(scalar)
    elif name in {"confirmation_count", "required_confirmations", "risk_tier"}:
        scalar = _bounded_integer(scalar, maximum=2)
    elif name == "latency_ms":
        scalar = _bounded_number(scalar, maximum=86_400_000)
    return _redact_scalar(scalar)


def _sanitize_response_field(name: str, value: Any) -> Any:
    scalar = _bounded_scalar(value)
    if name == "http_status":
        if isinstance(scalar, bool) or not isinstance(scalar, int) or not 100 <= scalar <= 599:
            raise ValueError("invalid_audit_event")
    elif name == "latency_ms":
        scalar = _bounded_number(scalar, maximum=86_400_000)
    elif name == "success":
        if not isinstance(scalar, bool):
            raise ValueError("invalid_audit_event")
    elif name == "provider_code":
        if not (
            isinstance(scalar, int)
            and not isinstance(scalar, bool)
            and 0 <= scalar <= 2_147_483_647
        ):
            scalar = _semantic_value(
                scalar,
                _RESPONSE_SEMANTIC_VALUES["provider_code"],
                _STATUS,
            )
    else:
        scalar = _semantic_value(
            scalar,
            _RESPONSE_SEMANTIC_VALUES[name],
            _STATUS,
        )
    return _redact_scalar(scalar)


def _semantic_value(
    value: Any,
    allowed: frozenset[str],
    pattern: re.Pattern[str],
) -> str:
    if value == "[REDACTED]":
        return value
    _require_pattern(value, pattern)
    return value if value in allowed else "[REDACTED]"


def _bounded_key(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_audit_event")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("invalid_audit_event") from exc
    if size == 0 or size > MAX_AUDIT_KEY_BYTES:
        raise ValueError("audit_event_too_large")
    return value


def _bounded_scalar(value: Any) -> Any:
    if not (value is None or isinstance(value, (bool, int, float, str))):
        raise ValueError("invalid_audit_event")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("invalid_audit_event")
    if isinstance(value, str):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("invalid_audit_event") from exc
        if size > MAX_AUDIT_SCALAR_BYTES:
            raise ValueError("audit_event_too_large")
    return value


def _bounded_integer(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError("invalid_audit_event")
    return value


def _bounded_number(value: Any, *, maximum: int) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= maximum
    ):
        raise ValueError("invalid_audit_event")
    return value


def _require_pattern(value: Any, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError("invalid_audit_event")


def _validated_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_audit_event")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid_audit_event") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid_audit_event")
    return parsed.astimezone(UTC).isoformat()


def _redact_scalar(value: Any) -> Any:
    redacted = redact_json(value)
    if isinstance(redacted, str) and (
        _EMAIL_VALUE.search(redacted)
        or _TAX_ID_VALUE.search(redacted)
        or _TOKEN_VALUE.search(redacted)
    ):
        return "[REDACTED]"
    return redacted


def _encode_row(row: Mapping[str, Any]) -> bytes:
    try:
        encoded = (
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("invalid_audit_event") from exc
    if len(encoded) > MAX_AUDIT_LINE_BYTES:
        raise ValueError("audit_event_too_large")
    return encoded


def _decode_ledger_row(encoded: bytes) -> Mapping[str, Any]:
    if not encoded.endswith(b"\n") or len(encoded) > MAX_AUDIT_LINE_BYTES:
        raise ValueError("audit_ledger_corrupt")
    try:
        decoded = json.loads(encoded[:-1].decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("audit_ledger_corrupt") from None
    if not isinstance(decoded, Mapping):
        raise ValueError("audit_ledger_corrupt")
    return decoded


def _iter_ledger_rows(file_fd: int) -> Iterator[tuple[int, bytes]]:
    try:
        os.lseek(file_fd, 0, os.SEEK_SET)
    except OSError as exc:
        raise ValueError("audit_read_failed") from exc
    buffer = bytearray()
    offset = 0
    while True:
        try:
            chunk = os.read(file_fd, _READ_CHUNK_BYTES)
        except OSError as exc:
            raise ValueError("audit_read_failed") from exc
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            length = newline + 1
            if length > MAX_AUDIT_LINE_BYTES:
                raise ValueError("audit_ledger_corrupt")
            encoded = bytes(buffer[:length])
            del buffer[:length]
            yield offset, encoded
            offset += length
        if len(buffer) > MAX_AUDIT_LINE_BYTES:
            raise ValueError("audit_ledger_corrupt")
    if buffer:
        raise ValueError("audit_ledger_corrupt")


def _read_exact_row(file_fd: int, *, offset: Any, length: Any) -> bytes:
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or isinstance(length, bool)
        or not isinstance(length, int)
        or not 0 < length <= MAX_AUDIT_LINE_BYTES
    ):
        raise ValueError("audit_index_corrupt")
    ledger_size = os.fstat(file_fd).st_size
    if offset + length > ledger_size:
        raise ValueError("audit_index_corrupt")
    try:
        if offset > 0:
            if hasattr(os, "pread"):
                preceding = os.pread(file_fd, 1, offset - 1)
            else:  # pragma: no cover - portable fallback
                os.lseek(file_fd, offset - 1, os.SEEK_SET)
                preceding = os.read(file_fd, 1)
            if preceding != b"\n":
                raise ValueError("audit_index_corrupt")
        if hasattr(os, "pread"):
            encoded = os.pread(file_fd, length, offset)
        else:  # pragma: no cover - portable fallback
            os.lseek(file_fd, offset, os.SEEK_SET)
            encoded = os.read(file_fd, length)
    except OSError as exc:
        raise ValueError("audit_read_failed") from exc
    if len(encoded) != length or not encoded.endswith(b"\n"):
        raise ValueError("audit_index_corrupt")
    return encoded


def _ledger_fingerprint(state: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        state.st_dev,
        state.st_ino,
        state.st_size,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )


def _row_hash(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def _lock_file(
    file_fd: int,
    parent: Path,
    ledger_name: str,
) -> _FallbackGuard | None:
    if _fcntl is not None:
        try:
            _fcntl.flock(file_fd, _fcntl.LOCK_EX)
            return None
        except OSError as exc:
            raise ValueError("audit_ledger_unavailable") from exc

    guard_path = parent / f"{ledger_name}{_LOCK_GUARD_SUFFIX}"
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:  # pragma: no branch - fallback used only without fcntl
        try:
            os.mkdir(guard_path, 0o700)
            state = guard_path.lstat()
            if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
                raise ValueError("audit_ledger_unavailable")
            if os.name == "posix":
                if state.st_uid != os.getuid():
                    raise ValueError("audit_ledger_unavailable")
                if stat.S_IMODE(state.st_mode) != 0o700:
                    os.chmod(guard_path, 0o700)
                    state = guard_path.lstat()
                if stat.S_IMODE(state.st_mode) != 0o700:
                    raise ValueError("audit_ledger_unavailable")
            return _FallbackGuard(guard_path, state.st_dev, state.st_ino)
        except FileExistsError:
            try:
                state = guard_path.lstat()
            except OSError as exc:
                raise ValueError("audit_ledger_unavailable") from exc
            if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
                raise ValueError("audit_ledger_unavailable") from None
            if time.monotonic() >= deadline:
                raise ValueError("audit_ledger_unavailable") from None
            time.sleep(_LOCK_POLL_SECONDS)
        except ValueError:
            with suppress(OSError):
                guard_path.rmdir()
            raise
        except OSError as exc:
            raise ValueError("audit_ledger_unavailable") from exc


def _unlock_file(file_fd: int, fallback_guard: _FallbackGuard | None) -> None:
    if _fcntl is not None:
        with suppress(OSError):
            _fcntl.flock(file_fd, _fcntl.LOCK_UN)
        return
    if fallback_guard is not None:
        try:
            state = fallback_guard.path.lstat()
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or (state.st_dev, state.st_ino)
                != (fallback_guard.device, fallback_guard.inode)
            ):
                raise ValueError("audit_ledger_unavailable")
            fallback_guard.path.rmdir()
        except OSError as exc:
            raise ValueError("audit_ledger_unavailable") from exc


def _rollback(connection: sqlite3.Connection) -> None:
    with suppress(sqlite3.Error):
        connection.execute("ROLLBACK")
