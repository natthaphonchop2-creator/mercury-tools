"""Atomic repository-local credential storage."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

from mercury_tools.drivers.models import CredentialField, CredentialStatus
from mercury_tools.local.repository import RepositoryContext

_DOTENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_SIMPLE_COMPONENT = re.compile(r"^[a-z][a-z0-9]*$")
_SIMPLE_FIELD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_PROFILE_METADATA_PREFIX = "_MERCURY_PROFILE_INDEX_"
_HASH_LENGTH = 12


@dataclass
class _CredentialDocument:
    values: dict[str, str]
    profiles: dict[tuple[str, str], dict[str, str]]


class _DuplicateIndexKeyError(ValueError):
    pass


class CredentialStore:
    """Store credentials only in one repository's ``.mercury/credentials.env`` file."""

    def __init__(self, context: RepositoryContext) -> None:
        try:
            resolved_root = context.root.resolve(strict=True)
        except OSError as exc:
            raise ValueError("credential_parent_invalid") from exc
        expected_parent = resolved_root / ".mercury"
        expected_path = expected_parent / "credentials.env"
        if (
            context.root != resolved_root
            or context.mercury_dir != expected_parent
            or context.credentials_path != expected_path
        ):
            raise ValueError("invalid_credentials_path")
        self._context = context
        self._root = resolved_root
        self._parent = expected_parent
        with _validated_parent_fd(self._root, self._parent):
            pass

    def status(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[CredentialField],
    ) -> CredentialStatus:
        field_names = _field_environment_names(connector_id, environment, fields)
        document = self._read()
        present_fields = tuple(
            field_name
            for field_name, environment_name in field_names.items()
            if document.values.get(environment_name, "") != ""
        )
        present = set(present_fields)
        required_fields = tuple(field_names)
        missing_fields = tuple(
            field_name for field_name in required_fields if field_name not in present
        )
        return CredentialStatus(
            connector_id=connector_id,
            environment=environment,
            required_fields=required_fields,
            present_fields=present_fields,
            missing_fields=missing_fields,
            configured=not missing_fields,
        )

    def save(
        self,
        connector_id: str,
        environment: str,
        values: Mapping[str, str],
        fields: Sequence[CredentialField],
    ) -> CredentialStatus:
        field_names = _field_environment_names(connector_id, environment, fields)
        if not isinstance(values, Mapping):
            raise ValueError("invalid_credential_values")
        if any(not isinstance(name, str) or name not in field_names for name in values):
            raise ValueError("undeclared_credential_field")
        for value in values.values():
            if not isinstance(value, str):
                raise ValueError("invalid_credential_value")
            _reject_control_or_format_characters(value)

        document = self._read()
        profile = (connector_id, environment)
        profile_fields = dict(document.profiles.get(profile, {}))
        owners = {
            name: stored_profile
            for stored_profile, stored_fields in document.profiles.items()
            for name in stored_fields.values()
        }
        for field_name, value in values.items():
            environment_name = field_names[field_name]
            if owners.get(environment_name, profile) != profile:
                raise ValueError("ambiguous_credential_identifier")
            if value:
                document.values[environment_name] = value
                profile_fields[field_name] = environment_name
            else:
                document.values.pop(environment_name, None)
                profile_fields.pop(field_name, None)
        if profile_fields:
            document.profiles[profile] = profile_fields
        else:
            document.profiles.pop(profile, None)

        self._write(document)
        return self.status(connector_id, environment, fields)

    def load(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[CredentialField],
    ) -> dict[str, str]:
        field_names = _field_environment_names(connector_id, environment, fields)
        document = self._read()
        return {
            field_name: document.values[environment_name]
            for field_name, environment_name in field_names.items()
            if document.values.get(environment_name, "") != ""
        }

    def clear(
        self,
        connector_id: str | None = None,
        environment: str | None = None,
        clear_all: bool = False,
    ) -> int:
        if clear_all:
            if connector_id is not None or environment is not None:
                raise ValueError("credential_clear_scope_ambiguous")
            document = self._read()
            _unlink_credentials(self._root, self._parent)
            return len(document.values)
        if connector_id is None and environment is None:
            raise ValueError("credential_clear_scope_required")
        if connector_id is not None:
            _validate_identifier(connector_id)
        if environment is not None:
            _validate_identifier(environment)

        document = self._read()
        names_to_clear: set[str] = set()
        for profile, profile_fields in document.profiles.items():
            if _profile_matches(profile, connector_id, environment):
                for field_name, indexed_name in profile_fields.items():
                    recomputed_name = credential_env_name(*profile, field_name)
                    if indexed_name != recomputed_name:
                        raise ValueError("invalid_credential_file")
                    names_to_clear.add(recomputed_name)

        for name in names_to_clear:
            document.values.pop(name, None)
        document.profiles = {
            profile: remaining
            for profile, fields in document.profiles.items()
            if (
                remaining := {
                    field_name: name
                    for field_name, name in fields.items()
                    if name not in names_to_clear
                }
            )
        }
        if names_to_clear:
            self._write(document)
        return len(names_to_clear)

    def _read(self) -> _CredentialDocument:
        return _read_document(self._root, self._parent)

    def _write(self, document: _CredentialDocument) -> None:
        text = _serialize_document(document)
        if _parse_dotenv_text(text) != document:
            raise ValueError("invalid_credential_file")
        _atomic_write(self._root, self._parent, text)


def credential_env_name(connector_id: str, environment: str, field: str) -> str:
    parts = (connector_id, environment, field)
    for part in parts:
        _validate_identifier(part)
    normalized = tuple(_normalize_identifier(part) for part in parts)
    readable_name = "MERCURY_" + "_".join(normalized)
    if _is_unambiguous_legacy_tuple(connector_id, environment, field):
        return readable_name
    digest = hashlib.sha256(_tuple_bytes(parts)).hexdigest().upper()[:_HASH_LENGTH]
    return f"{readable_name}__H_{digest}"


def _field_environment_names(
    connector_id: str,
    environment: str,
    fields: Sequence[CredentialField],
) -> dict[str, str]:
    credential_env_name(connector_id, environment, "field")
    field_names: dict[str, str] = {}
    environment_names: set[str] = set()
    for field in fields:
        if not isinstance(field, CredentialField):
            raise ValueError("invalid_credential_field")
        if not isinstance(field.name, str) or not isinstance(field.label, str):
            raise ValueError("invalid_credential_field")
        if not isinstance(field.secret, bool):
            raise ValueError("invalid_credential_field")
        environment_name = credential_env_name(connector_id, environment, field.name)
        if field.name in field_names or environment_name in environment_names:
            raise ValueError("ambiguous_credential_identifier")
        field_names[field.name] = environment_name
        environment_names.add(environment_name)
    return field_names


def _validate_identifier(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_credential_identifier")
    _reject_control_or_format_characters(value)
    if not value.isascii() or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("invalid_credential_identifier")
    if not any(character.isalnum() for character in value):
        raise ValueError("invalid_credential_identifier")


def _normalize_identifier(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).upper()


def _is_unambiguous_legacy_tuple(connector_id: str, environment: str, field: str) -> bool:
    return (
        _SIMPLE_COMPONENT.fullmatch(connector_id) is not None
        and _SIMPLE_COMPONENT.fullmatch(environment) is not None
        and _SIMPLE_FIELD.fullmatch(field) is not None
    )


def _tuple_bytes(parts: tuple[str, ...]) -> bytes:
    return json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _profile_metadata_name(profile: tuple[str, str]) -> str:
    digest = hashlib.sha256(_tuple_bytes(profile)).hexdigest().upper()
    return _PROFILE_METADATA_PREFIX + digest


def _profile_metadata_value(profile: tuple[str, str], fields: Mapping[str, str]) -> str:
    return json.dumps(
        {
            "connector_id": profile[0],
            "environment": profile[1],
            "fields": [
                {"field_name": field_name, "env_name": fields[field_name]}
                for field_name in sorted(fields)
            ],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _profile_matches(
    profile: tuple[str, str],
    connector_id: str | None,
    environment: str | None,
) -> bool:
    return (connector_id is None or profile[0] == connector_id) and (
        environment is None or profile[1] == environment
    )


def _reject_control_or_format_characters(value: str) -> None:
    if any(
        ord(character) < 32
        or ord(character) == 127
        or unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
        for character in value
    ):
        raise ValueError("credential_control_character")


def _read_document(root: Path, parent: Path) -> _CredentialDocument:
    with _validated_parent_fd(root, parent) as parent_fd:
        file_descriptor = _open_credentials_for_read(parent_fd)
        if file_descriptor is None:
            return _CredentialDocument(values={}, profiles={})
        try:
            with os.fdopen(file_descriptor, "r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("invalid_credential_file") from exc
    return _parse_dotenv_text(text)


def _open_credentials_for_read(parent_fd: int) -> int | None:
    state = _credential_file_state(parent_fd)
    if state is None:
        return None
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open("credentials.env", flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("credential_path_symlink") from exc
        raise ValueError("invalid_credential_file") from exc
    if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
        os.close(file_descriptor)
        raise ValueError("invalid_credential_file")
    return file_descriptor


def _credential_file_state(parent_fd: int) -> os.stat_result | None:
    try:
        state = os.stat("credentials.env", dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("invalid_credential_file") from exc
    if stat.S_ISLNK(state.st_mode):
        raise ValueError("credential_path_symlink")
    if not stat.S_ISREG(state.st_mode):
        raise ValueError("invalid_credential_file")
    return state


def _parse_dotenv_text(text: str) -> _CredentialDocument:
    if any(unicodedata.category(character) in {"Zl", "Zp"} for character in text):
        raise ValueError("invalid_credential_file")
    _validate_dotenv_assignments(text)
    parsed = dotenv_values(stream=StringIO(text), interpolate=False)
    values: dict[str, str] = {}
    metadata: dict[str, str] = {}
    for name, value in parsed.items():
        if name is None or value is None:
            raise ValueError("invalid_credential_file")
        _reject_control_or_format_characters(name)
        _reject_control_or_format_characters(value)
        if name.startswith(_PROFILE_METADATA_PREFIX):
            target = metadata
        elif name.startswith("MERCURY_"):
            target = values
        else:
            raise ValueError("invalid_credential_file")
        target[name] = value

    profiles: dict[tuple[str, str], dict[str, str]] = {}
    indexed_names: set[str] = set()
    for metadata_name, metadata_value in metadata.items():
        profile, fields = _parse_profile_metadata(metadata_name, metadata_value)
        names = set(fields.values())
        if profile in profiles or indexed_names.intersection(names):
            raise ValueError("ambiguous_credential_identifier")
        if not names.issubset(values):
            raise ValueError("invalid_credential_file")
        profiles[profile] = fields
        indexed_names.update(names)
    return _CredentialDocument(values=values, profiles=profiles)


def _parse_profile_metadata(
    name: str, value: str
) -> tuple[tuple[str, str], dict[str, str]]:
    try:
        payload = json.loads(value, object_pairs_hook=_reject_duplicate_index_keys)
    except (TypeError, json.JSONDecodeError, _DuplicateIndexKeyError) as exc:
        raise ValueError("invalid_credential_file") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "connector_id",
        "environment",
        "fields",
    }:
        raise ValueError("invalid_credential_file")
    connector_id = payload["connector_id"]
    environment = payload["environment"]
    entries = payload["fields"]
    _validate_identifier(connector_id)
    _validate_identifier(environment)
    if not isinstance(entries, list) or not entries:
        raise ValueError("invalid_credential_file")
    fields: dict[str, str] = {}
    indexed_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"field_name", "env_name"}:
            raise ValueError("invalid_credential_file")
        field_name = entry["field_name"]
        environment_name = entry["env_name"]
        if not isinstance(field_name, str) or not isinstance(environment_name, str):
            raise ValueError("invalid_credential_file")
        expected_name = credential_env_name(connector_id, environment, field_name)
        if (
            environment_name != expected_name
            or field_name in fields
            or environment_name in indexed_names
        ):
            raise ValueError("ambiguous_credential_identifier")
        fields[field_name] = environment_name
        indexed_names.add(environment_name)
    profile = (connector_id, environment)
    if name != _profile_metadata_name(profile):
        raise ValueError("invalid_credential_file")
    return profile, fields


def _reject_duplicate_index_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateIndexKeyError()
        value[key] = item
    return value


def _validate_dotenv_assignments(text: str) -> None:
    seen_names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _DOTENV_ASSIGNMENT.match(stripped)
        if match is None:
            raise ValueError("invalid_credential_file")
        name = match.group(1)
        if name in seen_names:
            raise ValueError("ambiguous_credential_identifier")
        seen_names.add(name)


def _serialize_document(document: _CredentialDocument) -> str:
    serialized_values = dict(document.values)
    for profile, fields in document.profiles.items():
        names = set(fields.values())
        if not fields or not names.issubset(document.values):
            raise ValueError("invalid_credential_file")
        metadata_name = _profile_metadata_name(profile)
        if metadata_name in serialized_values:
            raise ValueError("ambiguous_credential_identifier")
        serialized_values[metadata_name] = _profile_metadata_value(profile, fields)
    return "".join(
        f"{name}={_quote_dotenv(serialized_values[name])}\n" for name in sorted(serialized_values)
    )


def _quote_dotenv(value: str) -> str:
    _reject_control_or_format_characters(value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _atomic_write(root: Path, parent: Path, text: str, mode: int = 0o600) -> None:
    with _validated_parent_fd(root, parent) as parent_fd:
        _credential_file_state(parent_fd)
        temporary_name = f".credentials.env.{secrets.token_hex(12)}"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_descriptor = os.open(temporary_name, flags, mode, dir_fd=parent_fd)
        try:
            if os.name == "posix":
                os.fchmod(file_descriptor, mode)
            with os.fdopen(
                file_descriptor, "w", encoding="utf-8", newline="\n", closefd=False
            ) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.close(file_descriptor)
            file_descriptor = -1
            _credential_file_state(parent_fd)
            os.replace(
                temporary_name,
                "credentials.env",
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent_fd)


def _unlink_credentials(root: Path, parent: Path) -> None:
    with _validated_parent_fd(root, parent) as parent_fd:
        if _credential_file_state(parent_fd) is not None:
            os.unlink("credentials.env", dir_fd=parent_fd)
            os.fsync(parent_fd)


@contextmanager
def _validated_parent_fd(root: Path, parent: Path) -> Iterator[int]:
    file_descriptor = -1
    try:
        root_state = os.lstat(root)
        if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
            raise ValueError("credential_parent_invalid")
        resolved_root = root.resolve(strict=True)
        expected_parent = resolved_root / ".mercury"
        if resolved_root != root or parent != expected_parent:
            raise ValueError("credential_parent_invalid")
        parent_state = os.lstat(parent)
        if stat.S_ISLNK(parent_state.st_mode) or not stat.S_ISDIR(parent_state.st_mode):
            raise ValueError("credential_parent_invalid")
        if parent.resolve(strict=True) != expected_parent:
            raise ValueError("credential_parent_invalid")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_descriptor = os.open(parent, flags)
        opened_state = os.fstat(file_descriptor)
        current_state = os.lstat(parent)
        if (opened_state.st_dev, opened_state.st_ino) != (
            current_state.st_dev,
            current_state.st_ino,
        ):
            raise ValueError("credential_parent_invalid")
        yield file_descriptor
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("credential_parent_invalid") from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
