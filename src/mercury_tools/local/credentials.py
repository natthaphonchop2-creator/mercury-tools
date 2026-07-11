"""Atomic repository-local credential storage."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from contextlib import suppress
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

from mercury_tools.drivers.models import CredentialField, CredentialStatus
from mercury_tools.local.repository import RepositoryContext

_DOTENV_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class CredentialStore:
    """Store credentials only in one repository's ``.mercury/credentials.env`` file."""

    def __init__(self, context: RepositoryContext) -> None:
        if context.credentials_path != context.root / ".mercury" / "credentials.env":
            raise ValueError("invalid_credentials_path")
        self._context = context

    def status(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[CredentialField],
    ) -> CredentialStatus:
        field_names = _field_environment_names(connector_id, environment, fields)
        stored_values = _read_dotenv(self._context.credentials_path)
        present_fields = tuple(
            field_name
            for field_name, environment_name in field_names.items()
            if stored_values.get(environment_name, "") != ""
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

        stored_values = _read_dotenv(self._context.credentials_path)
        for field_name, value in values.items():
            if not isinstance(value, str):
                raise ValueError("invalid_credential_value")
            _reject_control_or_format_characters(value)
            environment_name = field_names[field_name]
            if value:
                stored_values[environment_name] = value
            else:
                stored_values.pop(environment_name, None)

        _atomic_write(self._context.credentials_path, _serialize_dotenv(stored_values))
        return self.status(connector_id, environment, fields)

    def load(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[CredentialField],
    ) -> dict[str, str]:
        field_names = _field_environment_names(connector_id, environment, fields)
        stored_values = _read_dotenv(self._context.credentials_path)
        return {
            field_name: stored_values[environment_name]
            for field_name, environment_name in field_names.items()
            if stored_values.get(environment_name, "") != ""
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
            _reject_symlink(self._context.credentials_path)
            if not self._context.credentials_path.exists():
                return 0
            values = _read_dotenv(self._context.credentials_path)
            self._context.credentials_path.unlink()
            return len(values)

        if connector_id is None or environment is None:
            raise ValueError("credential_clear_scope_required")
        profile_prefix = _credential_profile_prefix(connector_id, environment)
        stored_values = _read_dotenv(self._context.credentials_path)
        names_to_clear = [name for name in stored_values if name.startswith(profile_prefix)]
        for name in names_to_clear:
            del stored_values[name]
        if names_to_clear:
            _atomic_write(self._context.credentials_path, _serialize_dotenv(stored_values))
        return len(names_to_clear)


def credential_env_name(connector_id: str, environment: str, field: str) -> str:
    return _credential_profile_prefix(connector_id, environment) + _normalize_identifier(field)


def _credential_profile_prefix(connector_id: str, environment: str) -> str:
    return f"MERCURY_{_normalize_identifier(connector_id)}_{_normalize_identifier(environment)}_"


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


def _normalize_identifier(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_credential_identifier")
    _reject_control_or_format_characters(value)
    normalized = "".join(
        character if character.isascii() and character.isalnum() else "_" for character in value
    ).upper()
    if not any(character.isalnum() for character in normalized):
        raise ValueError("invalid_credential_identifier")
    return normalized


def _reject_control_or_format_characters(value: str) -> None:
    if any(
        ord(character) < 32
        or ord(character) == 127
        or unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
    ):
        raise ValueError("credential_control_character")


def _read_dotenv(path: Path) -> dict[str, str]:
    _reject_symlink(path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_credential_file") from exc
    _validate_dotenv_assignments(text)
    parsed = dotenv_values(stream=StringIO(text), interpolate=False)
    values: dict[str, str] = {}
    for name, value in parsed.items():
        if name is None or value is None or not name.startswith("MERCURY_"):
            raise ValueError("invalid_credential_file")
        _reject_control_or_format_characters(name)
        _reject_control_or_format_characters(value)
        values[name] = value
    return values


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


def _serialize_dotenv(values: Mapping[str, str]) -> str:
    return "".join(f"{name}={_quote_dotenv(values[name])}\n" for name in sorted(values))


def _quote_dotenv(value: str) -> str:
    _reject_control_or_format_characters(value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    _reject_symlink(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("credential_path_symlink")
