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
_EMAIL_VALUE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Z]{2,}(?![\w.-])")
_TAX_ID_VALUE = re.compile(r"(?<!\d)\d{13}(?!\d)")
_TOKEN_VALUE = re.compile(
    r"(?i)(?:\b(?:bearer|basic)\s+[A-Za-z0-9+/=._-]+|"
    r"\b(?:github_pat_|ghp_|sk-|sk_|xox[bp]-|ya29\.)[A-Za-z0-9._-]+)"
)
# Bounds apply to the complete scan, even when a matching row appears early.
MAX_AUDIT_LINE_BYTES = 64 * 1024
MAX_AUDIT_SCAN_BYTES = 8 * 1024 * 1024
MAX_AUDIT_SCAN_LINES = 100_000
_READ_CHUNK_BYTES = 64 * 1024
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
        if len(encoded) > MAX_AUDIT_LINE_BYTES:
            raise ValueError("audit_event_too_large")
        self._append(encoded)
        return event_id

    def get(self, event_id: str) -> dict[str, Any] | None:
        if not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None:
            return None
        file_fd = self._open_for_read()
        if file_fd is None:
            return None
        match: dict[str, Any] | None = None
        try:
            for line in _bounded_lines(file_fd):
                try:
                    decoded = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(decoded, Mapping) or decoded.get("event_id") != event_id:
                    continue
                sanitized = _sanitize_mapping(decoded)
                if sanitized.get("event_id") == event_id:
                    match = sanitized
            return match
        finally:
            os.close(file_fd)

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

    def _open_for_read(self) -> int | None:
        self._validate_target()
        try:
            directory_fd = self._open_parent()
            try:
                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                file_fd = os.open(self._name, flags, dir_fd=directory_fd)
            finally:
                os.close(directory_fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError("audit_read_failed") from exc
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ValueError("audit_read_failed")
            return file_fd
        except Exception:
            os.close(file_fd)
            raise

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
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if name == "response_summary":
            sanitized[name] = _sanitize_response_summary(item)
        elif name in _SAFE_EVENT_SCALARS:
            sanitized[name] = _sanitize_audit_scalar(item)
    return sanitized


def _sanitize_response_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "[REDACTED]"}
    return {
        str(key): _sanitize_audit_scalar(item)
        for key, item in redact_json(value).items()
        if str(key) in _SAFE_RESPONSE_SUMMARY
    }


def _sanitize_audit_scalar(value: Any) -> Any:
    redacted = redact_json(value)
    if not (redacted is None or isinstance(redacted, (bool, int, float, str))):
        return "[REDACTED]"
    if isinstance(redacted, str) and (
        _EMAIL_VALUE.search(redacted)
        or _TAX_ID_VALUE.search(redacted)
        or _TOKEN_VALUE.search(redacted)
    ):
        return "[REDACTED]"
    return redacted


def _bounded_lines(file_fd: int):
    buffer = bytearray()
    scanned_bytes = 0
    scanned_lines = 0
    while True:
        try:
            chunk = os.read(file_fd, _READ_CHUNK_BYTES)
        except OSError as exc:
            raise ValueError("audit_read_failed") from exc
        if not chunk:
            break
        scanned_bytes += len(chunk)
        if scanned_bytes > MAX_AUDIT_SCAN_BYTES:
            raise ValueError("audit_scan_limit_exceeded")
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            if newline > MAX_AUDIT_LINE_BYTES:
                raise ValueError("audit_scan_limit_exceeded")
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            scanned_lines += 1
            if scanned_lines > MAX_AUDIT_SCAN_LINES:
                raise ValueError("audit_scan_limit_exceeded")
            yield line
        if len(buffer) > MAX_AUDIT_LINE_BYTES:
            raise ValueError("audit_scan_limit_exceeded")
    if buffer:
        scanned_lines += 1
        if (
            len(buffer) > MAX_AUDIT_LINE_BYTES
            or scanned_lines > MAX_AUDIT_SCAN_LINES
        ):
            raise ValueError("audit_scan_limit_exceeded")
        yield bytes(buffer)
