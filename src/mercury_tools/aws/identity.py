"""Closed, hash-only identity compatibility evidence for AWS Wave 0."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from contextlib import suppress
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import validate_credential_safe, validate_credential_safe_paths

_SHA256 = r"^[a-f0-9]{64}$"
_REQUIRED_HOSTS = frozenset(("codex", "chatgpt", "claude"))
_MAX_IDENTITY_DECISION_BYTES = 65_536
_UNSAFE_PROBE_KEYS = frozenset(
    (
        "access_token",
        "authorization",
        "authorization_code",
        "code",
        "cookie",
        "cookies",
        "id_token",
        "refresh_token",
        "token",
    )
)


class HostName(StrEnum):
    CODEX = "codex"
    CHATGPT = "chatgpt"
    CLAUDE = "claude"


class RegistrationMode(StrEnum):
    PRE_REGISTERED = "pre_registered"
    DCR = "dcr"


class ProbeResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class IdentityMode(StrEnum):
    COGNITO_PRE_REGISTERED = "cognito_pre_registered"
    EXTERNAL_OIDC_DCR = "external_oidc_dcr"


class _IdentityModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class IdentityHostContract(_IdentityModel):
    schema_version: Literal["mercury.aws.wave0.identity_host_contract.v1"]
    required_hosts: tuple[HostName, ...]
    authorization_flow: Literal["authorization_code"]
    pkce_method: Literal["S256"]
    refresh_token_rotation: Literal["required"]
    audience_resource_binding: Literal["required"]

    @model_validator(mode="after")
    def validate_required_hosts(self) -> IdentityHostContract:
        if (
            len(self.required_hosts) != len(_REQUIRED_HOSTS)
            or {host.value for host in self.required_hosts} != _REQUIRED_HOSTS
        ):
            raise ValueError("identity_required_hosts_invalid")
        return self


class HostIdentityProbe(_IdentityModel):
    schema_version: Literal["mercury.aws.wave0.identity_probe.v1"] = (
        "mercury.aws.wave0.identity_probe.v1"
    )
    host: HostName
    registration_mode: RegistrationMode
    result: ProbeResult
    issuer_origin: str
    pkce_method: str
    checked_at: datetime
    evidence_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_probe(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if any(str(key).casefold() in _UNSAFE_PROBE_KEYS for key in value):
            raise ValueError("identity_probe_unsafe")
        try:
            validate_credential_safe(value)
            validate_credential_safe_paths(value)
        except ValueError:
            raise ValueError("identity_probe_unsafe") from None
        return value

    @model_validator(mode="after")
    def validate_probe(self) -> HostIdentityProbe:
        if self.pkce_method != "S256":
            raise ValueError("identity_pkce_method_invalid")
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("identity_checked_at_invalid")
        if self.registration_mode is RegistrationMode.PRE_REGISTERED:
            if self.issuer_origin != "cognito":
                raise ValueError("identity_issuer_origin_invalid")
        elif not _is_external_https_origin(self.issuer_origin):
            raise ValueError("identity_issuer_origin_invalid")
        return self


class IdentityDecision(_IdentityModel):
    schema_version: Literal["mercury.aws.wave0.identity_decision.v1"] = (
        "mercury.aws.wave0.identity_decision.v1"
    )
    mode: IdentityMode
    issuer_kind: Literal["cognito", "external_oidc"]
    issuer_origin: str
    required_hosts: tuple[HostName, ...]

    @model_validator(mode="after")
    def validate_decision(self) -> IdentityDecision:
        if (
            len(self.required_hosts) != len(_REQUIRED_HOSTS)
            or {host.value for host in self.required_hosts} != _REQUIRED_HOSTS
        ):
            raise ValueError("identity_required_host_missing")
        if self.mode is IdentityMode.COGNITO_PRE_REGISTERED:
            if self.issuer_kind != "cognito" or self.issuer_origin != "cognito":
                raise ValueError("identity_decision_invalid")
        elif self.issuer_kind != "external_oidc" or not _is_external_https_origin(
            self.issuer_origin
        ):
            raise ValueError("identity_decision_invalid")
        return self


def load_identity_host_contract(path: Path) -> IdentityHostContract:
    """Load the non-secret host compatibility contract from trusted YAML."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ValueError("identity_host_contract_invalid") from None
    if not isinstance(raw, dict):
        raise ValueError("identity_host_contract_invalid")
    try:
        return IdentityHostContract.model_validate(raw)
    except ValueError:
        raise ValueError("identity_host_contract_invalid") from None


def record_host_probe(
    contract: IdentityHostContract,
    probe: HostIdentityProbe,
    evidence_path: Path,
    output_dir: Path,
) -> Path:
    """Persist a closed probe record containing only an evidence SHA-256 digest."""

    checked_contract = IdentityHostContract.model_validate(contract)
    checked_probe = HostIdentityProbe.model_validate(probe)
    if checked_probe.host not in checked_contract.required_hosts:
        raise ValueError("identity_required_host_missing")
    if checked_probe.pkce_method != checked_contract.pkce_method:
        raise ValueError("identity_pkce_method_invalid")

    persisted_probe = checked_probe.model_copy(
        update={"evidence_sha256": _sha256_file(evidence_path)}, deep=True
    )
    return _write_probe(output_dir, persisted_probe)


def decide_identity(probes: tuple[HostIdentityProbe, ...]) -> IdentityDecision:
    """Choose the sole allowed issuer strategy from complete host evidence."""

    checked_probes = tuple(HostIdentityProbe.model_validate(probe) for probe in probes)
    host_values = {probe.host.value for probe in checked_probes}
    if host_values != _REQUIRED_HOSTS:
        raise ValueError("identity_required_host_missing")
    _reject_duplicate_host_mode(checked_probes)

    pre_registered = _probes_by_mode(checked_probes, RegistrationMode.PRE_REGISTERED)
    if _all_hosts_pass(pre_registered):
        return IdentityDecision(
            mode=IdentityMode.COGNITO_PRE_REGISTERED,
            issuer_kind="cognito",
            issuer_origin="cognito",
            required_hosts=(HostName.CODEX, HostName.CHATGPT, HostName.CLAUDE),
        )

    dcr = _probes_by_mode(checked_probes, RegistrationMode.DCR)
    if {probe.host.value for probe in dcr} != _REQUIRED_HOSTS:
        raise ValueError("identity_dcr_evidence_missing")
    if any(probe.result is not ProbeResult.PASS for probe in dcr):
        raise ValueError("identity_required_probe_failed")
    issuer_origins = {probe.issuer_origin for probe in dcr}
    if len(issuer_origins) != 1:
        raise ValueError("identity_issuer_not_shared")
    return IdentityDecision(
        mode=IdentityMode.EXTERNAL_OIDC_DCR,
        issuer_kind="external_oidc",
        issuer_origin=issuer_origins.pop(),
        required_hosts=(HostName.CODEX, HostName.CHATGPT, HostName.CLAUDE),
    )


def write_identity_decision(path: Path, decision: IdentityDecision) -> None:
    """Atomically write a complete, closed identity decision with private mode."""

    checked = IdentityDecision.model_validate(decision)
    payload = yaml.safe_dump(
        checked.model_dump(mode="json"), sort_keys=False, allow_unicode=False
    ).encode("utf-8")
    _write_identity_output(
        path,
        payload,
        temporary_prefix=".identity-decision-",
        path_error="identity_decision_path_invalid",
    )


def read_identity_decision(path: Path) -> tuple[IdentityDecision, str] | None:
    """Read one decision without following parent or final-component symlinks."""

    input_path = Path(os.path.abspath(os.fspath(path)))
    if input_path.name in {"", ".", ".."}:
        raise ValueError("identity_decision_path_invalid")
    directory_fd = _open_identity_directory(
        input_path.parent,
        "identity_decision_path_invalid",
        create_missing=False,
    )
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                input_path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise ValueError("identity_decision_path_invalid") from None
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_IDENTITY_DECISION_BYTES:
            raise ValueError("identity_decision_path_invalid")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 64 * 1024):
            total += len(chunk)
            if total > _MAX_IDENTITY_DECISION_BYTES:
                raise ValueError("identity_decision_path_invalid")
            chunks.append(chunk)
        payload = b"".join(chunks)
    except OSError:
        raise ValueError("identity_decision_path_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_fd)

    try:
        raw = yaml.safe_load(payload.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError
        decision = IdentityDecision.model_validate(raw)
    except (UnicodeError, yaml.YAMLError, ValueError):
        raise ValueError("identity_decision_invalid") from None
    return decision, hashlib.sha256(payload).hexdigest()


def _probes_by_mode(
    probes: tuple[HostIdentityProbe, ...], mode: RegistrationMode
) -> tuple[HostIdentityProbe, ...]:
    return tuple(probe for probe in probes if probe.registration_mode is mode)


def _all_hosts_pass(probes: tuple[HostIdentityProbe, ...]) -> bool:
    return (
        {probe.host.value for probe in probes} == _REQUIRED_HOSTS
        and all(probe.result is ProbeResult.PASS for probe in probes)
    )


def _reject_duplicate_host_mode(probes: tuple[HostIdentityProbe, ...]) -> None:
    pairs = {(probe.host, probe.registration_mode) for probe in probes}
    if len(pairs) != len(probes):
        raise ValueError("identity_probe_duplicate")


def _is_external_https_origin(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    hostname = parsed.hostname.casefold() if parsed.hostname else ""
    return bool(
        parsed.scheme == "https"
        and parsed.netloc
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and hostname not in {"localhost", "127.0.0.1", "::1"}
        and not hostname.endswith(".localhost")
    )


def _sha256_file(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise ValueError("identity_evidence_unavailable") from None
    try:
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        raise ValueError("identity_evidence_unavailable") from None
    finally:
        os.close(descriptor)


def _write_probe(output_dir: Path, probe: HostIdentityProbe) -> Path:
    destination = output_dir / f"{probe.host.value}-{probe.registration_mode.value}.json"
    payload = json.dumps(
        probe.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    _write_identity_output(
        destination,
        payload,
        temporary_prefix=".identity-probe-",
        path_error="identity_probe_path_invalid",
    )
    return destination


def _write_identity_output(
    output: Path,
    payload: bytes,
    *,
    temporary_prefix: str,
    path_error: str,
) -> None:
    output_path = Path(os.path.abspath(os.fspath(output)))
    if output_path.name in {"", ".", ".."}:
        raise ValueError(path_error)

    directory_fd = _open_identity_directory(
        output_path.parent,
        path_error,
        create_missing=True,
    )
    temporary_name: str | None = None
    try:
        _reject_symlinked_output(directory_fd, output_path.name, path_error)
        descriptor, temporary_name = _create_temporary_file(directory_fd, temporary_prefix)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            output_path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        os.fsync(directory_fd)
    except BaseException:
        if temporary_name is not None:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=directory_fd)
        raise
    finally:
        os.close(directory_fd)


def _open_identity_directory(
    path: Path,
    path_error: str,
    *,
    create_missing: bool,
) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parts = path.parts
    if not path.is_absolute() or not parts:
        raise ValueError(path_error)
    try:
        directory_fd = os.open(path.anchor, flags)
    except OSError:
        raise ValueError(path_error) from None
    try:
        for component in parts[1:]:
            if create_missing:
                with suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=directory_fd)
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except OSError:
        os.close(directory_fd)
        raise ValueError(path_error) from None


def _reject_symlinked_output(directory_fd: int, output_name: str, path_error: str) -> None:
    try:
        mode = os.stat(output_name, dir_fd=directory_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    except OSError:
        raise ValueError(path_error) from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError(path_error)


def _create_temporary_file(directory_fd: int, prefix: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(10):
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_fd), name
        except FileExistsError:
            continue
    raise OSError("identity_temporary_file_unavailable")
