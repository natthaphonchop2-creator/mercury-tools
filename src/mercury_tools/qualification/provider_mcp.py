"""The sole execution authority for qualified downstream provider-MCP actions."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import canonical_json, validate_credential_safe
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.providers.base import (
    ProviderOperationClass,
    ProviderQualificationState,
    QualifiedCapabilityBinding,
    VerifiedRuntimeBinding,
)
from mercury_tools.providers.models import ProviderId
from mercury_tools.qualification.artifacts import QualificationArtifact

_PROVIDER = r"^(?:flowaccount|peak)$"
_ENVIRONMENT = r"^[a-z][a-z0-9_-]{0,63}$"
_CAPABILITY = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
_TOOL = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_SHA256 = r"^[0-9a-f]{64}$"
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


class CapabilitySelection(BaseModel):
    """The full identity an execution caller must present to the catalog gate."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    provider: str = Field(pattern=_PROVIDER)
    environment: str = Field(pattern=_ENVIRONMENT)
    normalized_capability: str = Field(pattern=_CAPABILITY)
    provider_tool_name: str = Field(pattern=_TOOL)
    capability_version_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: object) -> object:
        validate_credential_safe(value)
        return value


class OwnerAuthorizedCanary(BaseModel):
    """An owner approval bound to exactly one production capability version."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    provider: str = Field(pattern=_PROVIDER)
    environment: Literal["production"]
    normalized_capability: str = Field(pattern=_CAPABILITY)
    provider_tool_name: str = Field(pattern=_TOOL)
    capability_version_sha256: str = Field(pattern=_SHA256)
    owner_authorized_by: str = Field(pattern=_IDENTIFIER)
    authorized_at: datetime

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: object) -> object:
        validate_credential_safe(value)
        return value

    @model_validator(mode="after")
    def validate_timestamp(self) -> OwnerAuthorizedCanary:
        if self.authorized_at.tzinfo is None or self.authorized_at.utcoffset() is None:
            raise ValueError("production_canary_invalid")
        object.__setattr__(self, "authorized_at", self.authorized_at.astimezone(UTC))
        return self


@dataclass(frozen=True)
class CapabilityResolution:
    status: Literal["enabled", "insufficient_evidence", "capability_unavailable"]
    qualification: ProviderMCPQualification | None = None


class QualificationGateError(RuntimeError):
    """Closed execution denial that never contains provider data."""

    def __init__(self, code: Literal["insufficient_evidence", "capability_unavailable"]):
        self.code = code
        super().__init__(code)


class CapabilityQualificationGate:
    """Resolve only exact catalog qualification records into runtime bindings."""

    def __init__(self, qualifications: Iterable[ProviderMCPQualification]) -> None:
        indexed: dict[tuple[str, str, str, str, str], ProviderMCPQualification] = {}
        for item in qualifications:
            checked = ProviderMCPQualification.model_validate(item)
            identity = _selection_identity_from_qualification(checked)
            if identity in indexed:
                raise ValueError("qualification_catalog_duplicate")
            indexed[identity] = checked
        self._qualifications = indexed

    def resolve(
        self,
        selection: CapabilitySelection,
        *,
        now: datetime | None = None,
    ) -> CapabilityResolution:
        checked_selection = CapabilitySelection.model_validate(selection)
        qualification = self._qualifications.get(_selection_identity(checked_selection))
        if qualification is None:
            return CapabilityResolution(status="capability_unavailable")
        checked_now = _now(now)
        if qualification.qualification_state is QualificationState.ENABLED:
            if (
                qualification.evidence_expires_at is not None
                and qualification.evidence_expires_at > checked_now
            ):
                return CapabilityResolution(status="enabled", qualification=qualification)
            return CapabilityResolution(status="insufficient_evidence")
        if qualification.qualification_state in {
            QualificationState.DISABLED,
            QualificationState.SUPERSEDED,
        }:
            return CapabilityResolution(status="capability_unavailable")
        return CapabilityResolution(status="insufficient_evidence")

    def bind(
        self,
        selection: CapabilitySelection,
        *,
        now: datetime | None = None,
    ) -> QualifiedCapabilityBinding:
        resolved = self.resolve(selection, now=now)
        if resolved.status != "enabled" or resolved.qualification is None:
            raise QualificationGateError(resolved.status)
        qualification = resolved.qualification
        return QualifiedCapabilityBinding(
            provider=ProviderId(qualification.provider),
            environment=qualification.environment,
            normalized_capability=qualification.normalized_capability,
            provider_tool=qualification.provider_tool_name,
            operation_class=_operation_class(qualification),
            qualification_hash=qualification.capability_version_sha256,
        )

    def verify(
        self,
        selection: CapabilitySelection,
        *,
        resource_uri_sha256: str,
        now: datetime | None = None,
    ) -> VerifiedRuntimeBinding:
        binding = self.bind(selection, now=now)
        qualification = self.resolve(selection, now=now).qualification
        if qualification is None:
            raise QualificationGateError("capability_unavailable")
        return VerifiedRuntimeBinding(
            qualification_state=ProviderQualificationState.ENABLED,
            provider=ProviderId(qualification.provider),
            environment=qualification.environment,
            resource_uri_sha256=resource_uri_sha256,
            normalized_capability=qualification.normalized_capability,
            capability_version=qualification.capability_version_sha256,
            provider_tool=qualification.provider_tool_name,
            operation_class=binding.operation_class,
            request_schema_sha256=_json_sha256(qualification.input_schema),
            response_schema_sha256=_json_sha256(qualification.output_schema),
            qualification_hash=qualification.capability_version_sha256,
        )


def transition_qualification(
    qualification: ProviderMCPQualification,
    target_state: QualificationState,
    *,
    evidence: QualificationArtifact | None = None,
    nonproduction_evidence: Iterable[ProviderMCPQualification] = (),
    canary: OwnerAuthorizedCanary | None = None,
    disable_reason: str | None = None,
    now: datetime,
) -> ProviderMCPQualification:
    """Advance one state only after exact, sanitized qualification checks."""

    current = ProviderMCPQualification.model_validate(qualification)
    target = QualificationState(target_state)
    checked_now = _now(now)
    if target not in _ALLOWED_TRANSITIONS[current.qualification_state]:
        raise ValueError("qualification_transition_invalid")

    updates: dict[str, object] = {"qualification_state": target}
    if target is QualificationState.SCHEMA_VALIDATED:
        _require_absent(evidence, canary, disable_reason)
    elif target is QualificationState.NONPRODUCTION_QUALIFIED:
        if evidence is None:
            raise ValueError("qualification_evidence_required")
        if current.environment == "production":
            _require_nonproduction_evidence(
                current,
                QualificationArtifact.model_validate(evidence),
                nonproduction_evidence,
                now=checked_now,
            )
        else:
            QualificationArtifact.model_validate(evidence).require_valid_for(
                current,
                now=checked_now,
            )
        checked_evidence = QualificationArtifact.model_validate(evidence)
        updates.update(
            {
                "qualification_evidence_uri": checked_evidence.catalog_uri,
                "evidence_expires_at": checked_evidence.evidence_expires_at,
            }
        )
    elif target is QualificationState.ENABLED:
        if not _is_v1_enabled_operation(current):
            raise ValueError("qualification_operation_not_allowed")
        if (
            current.qualification_evidence_uri is None
            or current.evidence_expires_at is None
            or current.evidence_expires_at <= checked_now
        ):
            raise ValueError("qualification_evidence_required")
        if current.environment == "production":
            if canary is None:
                raise ValueError("production_canary_required")
            checked_canary = OwnerAuthorizedCanary.model_validate(canary)
            if _canary_identity(checked_canary) != _selection_identity_from_qualification(current):
                raise ValueError("production_canary_mismatch")
            updates.update(
                {
                    "production_canary_at": checked_canary.authorized_at,
                    "owner_authorized_by": checked_canary.owner_authorized_by,
                }
            )
        elif canary is not None:
            raise ValueError("production_canary_unexpected")
    else:
        if not isinstance(disable_reason, str) or not disable_reason:
            raise ValueError("qualification_disable_reason_required")
        updates.update(
            {
                "qualification_evidence_uri": None,
                "evidence_expires_at": None,
                "production_canary_at": None,
                "owner_authorized_by": None,
                "disable_reason": disable_reason,
            }
        )

    values = current.model_dump(mode="python")
    values.update(updates)
    return ProviderMCPQualification.model_validate(values)


_ALLOWED_TRANSITIONS = {
    QualificationState.DISCOVERED_UNREVIEWED: frozenset({QualificationState.SCHEMA_VALIDATED}),
    QualificationState.SCHEMA_VALIDATED: frozenset({QualificationState.NONPRODUCTION_QUALIFIED}),
    QualificationState.NONPRODUCTION_QUALIFIED: frozenset({QualificationState.ENABLED}),
    QualificationState.ENABLED: frozenset(
        {QualificationState.DISABLED, QualificationState.SUPERSEDED}
    ),
    QualificationState.DISABLED: frozenset(),
    QualificationState.SUPERSEDED: frozenset(),
}


def _require_nonproduction_evidence(
    production: ProviderMCPQualification,
    artifact: QualificationArtifact,
    candidates: Iterable[ProviderMCPQualification],
    *,
    now: datetime,
) -> None:
    for candidate in candidates:
        checked = ProviderMCPQualification.model_validate(candidate)
        if (
            checked.environment != "production"
            and checked.qualification_state
            in {QualificationState.NONPRODUCTION_QUALIFIED, QualificationState.ENABLED}
            and checked.evidence_expires_at is not None
            and checked.evidence_expires_at > now
            and _same_cross_environment_capability(production, checked)
        ):
            artifact.require_valid_for(checked, now=now)
            return
    raise ValueError("nonproduction_evidence_required")


def _same_cross_environment_capability(
    left: ProviderMCPQualification,
    right: ProviderMCPQualification,
) -> bool:
    return (
        left.provider,
        left.provider_tool_name,
        left.normalized_capability,
        left.input_schema,
        left.output_schema,
        left.schema_hash,
        left.response_shape_hash,
        left.required_permissions,
    ) == (
        right.provider,
        right.provider_tool_name,
        right.normalized_capability,
        right.input_schema,
        right.output_schema,
        right.schema_hash,
        right.response_shape_hash,
        right.required_permissions,
    )


def _is_v1_enabled_operation(qualification: ProviderMCPQualification) -> bool:
    capability = qualification.normalized_capability
    return (
        capability.endswith(".get")
        or capability.endswith(".list")
        or (capability.startswith("documents.") and capability.endswith(".create"))
    )


def _operation_class(qualification: ProviderMCPQualification) -> ProviderOperationClass:
    return (
        ProviderOperationClass.CREATE
        if qualification.normalized_capability.endswith(".create")
        else ProviderOperationClass.READ
    )


def _selection_identity(selection: CapabilitySelection) -> tuple[str, str, str, str, str]:
    return (
        selection.provider,
        selection.environment,
        selection.normalized_capability,
        selection.provider_tool_name,
        selection.capability_version_sha256,
    )


def _selection_identity_from_qualification(
    qualification: ProviderMCPQualification,
) -> tuple[str, str, str, str, str]:
    return (
        qualification.provider,
        qualification.environment,
        qualification.normalized_capability,
        qualification.provider_tool_name,
        qualification.capability_version_sha256,
    )


def _canary_identity(canary: OwnerAuthorizedCanary) -> tuple[str, str, str, str, str]:
    return (
        canary.provider,
        canary.environment,
        canary.normalized_capability,
        canary.provider_tool_name,
        canary.capability_version_sha256,
    )


def _json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _now(value: datetime | None) -> datetime:
    checked = value or datetime.now(UTC)
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise ValueError("qualification_time_invalid")
    return checked.astimezone(UTC)


def _require_absent(*values: object) -> None:
    if any(value is not None for value in values):
        raise ValueError("qualification_transition_invalid")
