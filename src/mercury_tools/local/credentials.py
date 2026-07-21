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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import wraps
from io import StringIO
from pathlib import Path
from typing import Any, TypeVar, cast

from dotenv import dotenv_values

from mercury_tools.drivers.models import CredentialField, CredentialStatus
from mercury_tools.local.operation_lock import repository_operation_lock
from mercury_tools.local.repository import RepositoryContext

_DOTENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_SIMPLE_COMPONENT = re.compile(r"^[a-z][a-z0-9]*$")
_SIMPLE_FIELD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_PROFILE_METADATA_PREFIX = "_MERCURY_PROFILE_INDEX_"
_HASH_LENGTH = 12
_GENERATION_KEY = secrets.token_bytes(32)

_Return = TypeVar("_Return")


def _credential_locked(method: Callable[..., _Return]) -> Callable[..., _Return]:
    @wraps(method)
    def wrapped(owner: Any, *args: Any, **kwargs: Any) -> _Return:
        with _validated_parent_fd(owner._root, owner._parent):
            pass
        with repository_operation_lock(owner._context):
            return method(owner, *args, **kwargs)

    return cast(Callable[..., _Return], wrapped)


@dataclass
class _CredentialDocument:
    values: dict[str, str]
    profiles: dict[tuple[str, str], dict[str, str]]


@dataclass(frozen=True)
class CredentialSnapshot:
    """One internally consistent credential view for optimistic probe binding."""

    credentials: dict[str, str] = dataclass_field(repr=False)
    status: CredentialStatus
    generation: bytes = dataclass_field(repr=False)


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
        with _validated_parent_fd(self._root, self._parent) as parent_fd:
            document = self._read(parent_fd)
        return _status_from_document(connector_id, environment, field_names, document)

    @_credential_locked
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

        with _validated_parent_fd(self._root, self._parent) as parent_fd:
            document = self._read(parent_fd)
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

            self._write(parent_fd, document)
            persisted = self._read(parent_fd)
            return _status_from_document(
                connector_id,
                environment,
                field_names,
                persisted,
            )

    def load(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[CredentialField],
    ) -> dict[str, str]:
        field_names = _field_environment_names(connector_id, environment, fields)
        with _validated_parent_fd(self._root, self._parent) as parent_fd:
            document = self._read(parent_fd)
        return {
            field_name: document.values[environment_name]
            for field_name, environment_name in field_names.items()
            if document.values.get(environment_name, "") != ""
        }

    @_credential_locked
    def revision(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[CredentialField],
    ) -> bytes:
        """Return a process-opaque revision without returning credential values."""

        field_names = _field_environment_names(connector_id, environment, fields)
        with _validated_parent_fd(self._root, self._parent) as parent_fd:
            document = self._read(parent_fd)
        return _credential_generation(
            connector_id,
            environment,
            field_names,
            document,
        )

    @_credential_locked
    def snapshot(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[CredentialField],
    ) -> CredentialSnapshot:
        field_names = _field_environment_names(connector_id, environment, fields)
        with _validated_parent_fd(self._root, self._parent) as parent_fd:
            document = self._read(parent_fd)
        credentials = {
            field_name: document.values[environment_name]
            for field_name, environment_name in field_names.items()
            if document.values.get(environment_name, "") != ""
        }
        return CredentialSnapshot(
            credentials=credentials,
            status=_status_from_document(
                connector_id,
                environment,
                field_names,
                document,
            ),
            generation=_credential_generation(
                connector_id,
                environment,
                field_names,
                document,
            ),
        )

    @_credential_locked
    def clear(
        self,
        connector_id: str | None = None,
        environment: str | None = None,
        clear_all: bool = False,
    ) -> int:
        if clear_all:
            if connector_id is not None or environment is not None:
                raise ValueError("credential_clear_scope_ambiguous")
            with _validated_parent_fd(self._root, self._parent) as parent_fd:
                document = self._read(parent_fd)
                _unlink_credentials(parent_fd)
                return len(document.values)
        if connector_id is None and environment is None:
            raise ValueError("credential_clear_scope_required")
        if connector_id is not None:
            _validate_identifier(connector_id)
        if environment is not None:
            _validate_identifier(environment)

        with _validated_parent_fd(self._root, self._parent) as parent_fd:
            document = self._read(parent_fd)
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
                self._write(parent_fd, document)
            return len(names_to_clear)

    def _read(self, parent_fd: int) -> _CredentialDocument:
        return _read_document(parent_fd)

    def _write(self, parent_fd: int, document: _CredentialDocument) -> None:
        text = _serialize_document(document)
        if _parse_dotenv_text(text) != document:
            raise ValueError("invalid_credential_file")
        _atomic_write(parent_fd, text)


def _credential_generation(
    connector_id: str,
    environment: str,
    field_names: Mapping[str, str],
    document: _CredentialDocument,
) -> bytes:
    profile = (connector_id, environment)
    generation_payload = json.dumps(
        {
            "connector_id": connector_id,
            "environment": environment,
            "declared_fields": sorted(field_names.items()),
            "profile_fields": sorted(document.profiles.get(profile, {}).items()),
            "credentials": sorted(
                (field_name, document.values.get(environment_name, ""))
                for field_name, environment_name in field_names.items()
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.blake2b(
        generation_payload,
        digest_size=32,
        key=_GENERATION_KEY,
        person=b"mercury-cred-v1",
    ).digest()


def _status_from_document(
    connector_id: str,
    environment: str,
    field_names: Mapping[str, str],
    document: _CredentialDocument,
) -> CredentialStatus:
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


def _read_document(parent_fd: int) -> _CredentialDocument:
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
    for metadata_name, metadata_value in metadata.items():
        profile, fields = _parse_profile_metadata(metadata_name, metadata_value)
        if profile in profiles:
            raise ValueError("ambiguous_credential_identifier")
        profiles[profile] = fields
    if _owned_environment_names(profiles) != set(values):
        raise ValueError("invalid_credential_file")
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
        if environment_name != expected_name:
            raise ValueError("invalid_credential_file")
        if field_name in fields or environment_name in indexed_names:
            raise ValueError("ambiguous_credential_identifier")
        fields[field_name] = environment_name
        indexed_names.add(environment_name)
    profile = (connector_id, environment)
    if name != _profile_metadata_name(profile):
        raise ValueError("invalid_credential_file")
    return profile, fields


def _owned_environment_names(
    profiles: Mapping[tuple[str, str], Mapping[str, str]],
) -> set[str]:
    owned_names: set[str] = set()
    for profile, fields in profiles.items():
        if not fields:
            raise ValueError("invalid_credential_file")
        for field_name, indexed_name in fields.items():
            recomputed_name = credential_env_name(*profile, field_name)
            if indexed_name != recomputed_name:
                raise ValueError("invalid_credential_file")
            if recomputed_name in owned_names:
                raise ValueError("ambiguous_credential_identifier")
            owned_names.add(recomputed_name)
    return owned_names


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
    if _owned_environment_names(document.profiles) != set(document.values):
        raise ValueError("invalid_credential_file")
    serialized_values = dict(document.values)
    for profile, fields in document.profiles.items():
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


def _atomic_write(parent_fd: int, text: str, mode: int = 0o600) -> None:
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


def _unlink_credentials(parent_fd: int) -> None:
    if _credential_file_state(parent_fd) is not None:
        os.unlink("credentials.env", dir_fd=parent_fd)
        os.fsync(parent_fd)


@contextmanager
def _validated_parent_fd(root: Path, parent: Path) -> Iterator[int]:
    root_fd = -1
    parent_fd = -1
    try:
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | os.O_DIRECTORY
                | os.O_NOFOLLOW
            )
            root_fd = os.open(root, flags)
            if parent != root / ".mercury":
                raise ValueError("credential_parent_invalid")
            parent_fd = os.open(".mercury", flags, dir_fd=root_fd)
            _validate_repository_descriptors(root, root_fd, parent_fd)
        except ValueError:
            raise
        except OSError:
            raise ValueError("credential_parent_invalid") from None
        try:
            yield parent_fd
        finally:
            try:
                _validate_repository_descriptors(root, root_fd, parent_fd)
            except (OSError, ValueError):
                raise ValueError("credential_parent_invalid") from None
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _validate_repository_descriptors(root: Path, root_fd: int, parent_fd: int) -> None:
    current_root = os.lstat(root)
    opened_root = os.fstat(root_fd)
    current_parent = os.stat(".mercury", dir_fd=root_fd, follow_symlinks=False)
    opened_parent = os.fstat(parent_fd)
    if (
        stat.S_ISLNK(current_root.st_mode)
        or not stat.S_ISDIR(current_root.st_mode)
        or not stat.S_ISDIR(opened_root.st_mode)
        or (current_root.st_dev, current_root.st_ino)
        != (opened_root.st_dev, opened_root.st_ino)
        or stat.S_ISLNK(current_parent.st_mode)
        or not stat.S_ISDIR(current_parent.st_mode)
        or not stat.S_ISDIR(opened_parent.st_mode)
        or (current_parent.st_dev, current_parent.st_ino)
        != (opened_parent.st_dev, opened_parent.st_ino)
    ):
        raise ValueError("credential_parent_invalid")
