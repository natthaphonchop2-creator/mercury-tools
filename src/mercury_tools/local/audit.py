"""Append-only, repository-local audit ledger with defensive redaction."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mercury_tools.safety.redaction import redact_json

_EVENT_ID = re.compile(r"^evt_[0-9a-f]{24}$")
_MAX_EVENT_BYTES = 64 * 1024
_SECRET_KEY_PARTS = frozenset(
    {"apikey", "authorization", "cookie", "credential", "password", "secret", "token"}
)
_PERSONAL_KEY_PARTS = frozenset(
    {
        "address",
        "citizen",
        "contact",
        "email",
        "firstname",
        "fullname",
        "lastname",
        "mobile",
        "name",
        "national",
        "passport",
        "personal",
        "phone",
        "taxid",
    }
)
_SAFE_EVENT_SCALARS = frozenset(
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
        "request_id",
        "required_confirmations",
        "risk_tier",
        "state",
        "version_id",
    }
)
_SAFE_RESPONSE_SUMMARY = frozenset(
    {
        "error_code",
        "http_status",
        "latency_ms",
        "outcome",
        "provider_code",
        "provider_status",
        "status",
        "status_class",
    }
)


class AuditLedger:
    """Durable JSONL audit rows that never retain request inputs."""

    def __init__(self, path: Path) -> None:
        candidate = Path(path)
        if candidate.name in {"", ".", ".."}:
            raise ValueError("invalid_audit_path")
        try:
            parent = candidate.parent.resolve(strict=True)
            parent_mode = candidate.parent.lstat().st_mode
        except OSError as exc:
            raise ValueError("invalid_audit_path") from exc
        if candidate.parent != parent or stat.S_ISLNK(parent_mode) or not stat.S_ISDIR(parent_mode):
            raise ValueError("invalid_audit_path")
        self._parent = parent
        self._name = candidate.name
        self._path = parent / self._name
        self._validate_target()
        self._enforce_target_mode()

    def record(self, event: Mapping[str, Any]) -> str:
        if not isinstance(event, Mapping):
            raise ValueError("invalid_audit_event")
        event_id = "evt_" + secrets.token_hex(12)
        row = _sanitize_mapping(event)
        row.pop("event_id", None)
        row["event_id"] = event_id
        row["recorded_at"] = datetime.now(UTC).isoformat()
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
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_audit_event") from exc
        if len(encoded) > _MAX_EVENT_BYTES:
            raise ValueError("audit_event_too_large")
        self._append(encoded)
        return event_id

    def get(self, event_id: str) -> dict[str, Any] | None:
        if not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None:
            return None
        for line in self._read_lines():
            try:
                decoded = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(decoded, Mapping) or decoded.get("event_id") != event_id:
                continue
            sanitized = _sanitize_mapping(decoded)
            if sanitized.get("event_id") == event_id:
                return sanitized
        return None

    def _append(self, encoded: bytes) -> None:
        self._validate_target()
        directory_fd = self._open_parent()
        file_fd = -1
        try:
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(self._name, flags, 0o600, dir_fd=directory_fd)
            if os.name == "posix":
                os.fchmod(file_fd, 0o600)
            written = os.write(file_fd, encoded)
            if written != len(encoded):
                raise OSError("audit_write_incomplete")
            os.fsync(file_fd)
        except OSError as exc:
            raise ValueError("audit_write_failed") from exc
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(directory_fd)

    def _read_lines(self) -> list[str]:
        self._validate_target()
        try:
            directory_fd = self._open_parent()
            try:
                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                file_fd = os.open(self._name, flags, dir_fd=directory_fd)
            finally:
                os.close(directory_fd)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ValueError("audit_read_failed") from exc
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(file_fd, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace").splitlines()
        finally:
            os.close(file_fd)

    def _open_parent(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return os.open(self._parent, flags)

    def _enforce_target_mode(self) -> None:
        directory_fd = self._open_parent()
        file_fd = -1
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(self._name, flags, dir_fd=directory_fd)
            if os.name == "posix":
                os.fchmod(file_fd, 0o600)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("invalid_audit_path") from exc
        finally:
            if file_fd >= 0:
                os.close(file_fd)
            os.close(directory_fd)

    def _validate_target(self) -> None:
        try:
            mode = self._path.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("invalid_audit_path") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError("invalid_audit_path")


def _sanitize_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): sanitized
        for key, item in value.items()
        if (sanitized := _sanitize_value(str(key), item)) is not _DROP
    }


_DROP = object()


def _sanitize_value(key: str, value: Any) -> Any:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    if normalized == "requestinputs":
        return _DROP
    if any(part in normalized for part in _SECRET_KEY_PARTS | _PERSONAL_KEY_PARTS):
        return "[REDACTED]"
    if normalized == "responsesummary":
        return _sanitize_response_summary(value)
    if key and key not in _SAFE_EVENT_SCALARS:
        return "[REDACTED]"
    value = redact_json(value)
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, tuple):
        return [_sanitize_value("", item) for item in value]
    if isinstance(value, list):
        return [_sanitize_value("", item) for item in value]
    return value


def _sanitize_response_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "[REDACTED]"}
    return {
        str(key): _sanitize_value("", item)
        if str(key) in _SAFE_RESPONSE_SUMMARY
        else "[REDACTED]"
        for key, item in redact_json(value).items()
    }
