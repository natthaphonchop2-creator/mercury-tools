"""The sole execution authority for qualified downstream provider-MCP actions."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import canonical_json, validate_credential_safe
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.providers.base import (
    ProviderOperationClass,
    ProviderQualificationState,
    QualifiedCapabilityBinding,
    VerifiedRuntimeBinding,
)
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.streamable_mcp import (
    ProviderOperationDeadline,
    current_provider_operation_deadline,
)
from mercury_tools.qualification.artifacts import (
    QualificationArtifact,
    load_catalog_qualification_artifact,
)

_PROVIDER = r"^(?:flowaccount|peak)$"
_ENVIRONMENT = r"^[a-z][a-z0-9_-]{0,63}$"
_CAPABILITY = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
_TOOL = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_SHA256 = r"^[0-9a-f]{64}$"
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_PROFILE_CAPABILITY = "provider_profile.get"
_FLOWACCOUNT_PROFILE_TOOL = "get_provider_profile"


class CapabilitySelection(BaseModel):
    """The exact immutable capability definition selected for execution."""

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


ArtifactLoader = Callable[[str], QualificationArtifact]


class CapabilityQualificationGate:
    """Resolve only current catalog rows and their immutable artifact revisions."""

    def __init__(
        self,
        qualifications: Iterable[ProviderMCPQualification],
        *,
        artifacts: Iterable[QualificationArtifact] = (),
        artifact_loader: ArtifactLoader | None = None,
    ) -> None:
        self._qualifications = tuple(
            ProviderMCPQualification.model_validate(item) for item in qualifications
        )
        artifact_index: dict[str, QualificationArtifact] = {}
        for artifact in artifacts:
            checked = QualificationArtifact.model_validate(artifact)
            if checked.catalog_uri in artifact_index:
                raise ValueError("qualification_artifact_duplicate")
            artifact_index[checked.catalog_uri] = checked
        self._artifacts = artifact_index
        self._artifact_loader = artifact_loader

    def resolve(
        self,
        selection: CapabilitySelection,
        *,
        company_sha256: str,
        now: datetime | None = None,
    ) -> CapabilityResolution:
        checked_selection = CapabilitySelection.model_validate(selection)
        checked_company = _require_company_sha256(company_sha256)
        matching = [
            item
            for item in self._qualifications
            if _selection_identity_from_qualification(item)
            == _selection_identity(checked_selection)
        ]
        return self._resolve_matches(
            matching,
            company_sha256=checked_company,
            now=_now(now),
        )

    def resolve_current(
        self,
        *,
        provider: str,
        environment: str,
        normalized_capability: str,
        provider_tool_name: str,
        company_sha256: str,
        now: datetime | None = None,
    ) -> CapabilityResolution:
        checked_company = _require_company_sha256(company_sha256)
        matching = [
            item
            for item in self._qualifications
            if (
                item.provider,
                item.environment,
                item.normalized_capability,
                item.provider_tool_name,
            )
            == (provider, environment, normalized_capability, provider_tool_name)
        ]
        return self._resolve_matches(
            matching,
            company_sha256=checked_company,
            now=_now(now),
        )

    def bind(
        self,
        selection: CapabilitySelection,
        *,
        company_sha256: str,
        now: datetime | None = None,
    ) -> QualifiedCapabilityBinding:
        resolved = self.resolve(
            selection,
            company_sha256=company_sha256,
            now=now,
        )
        return self._binding_from_resolution(resolved)

    def bind_current(
        self,
        *,
        provider: str,
        environment: str,
        normalized_capability: str,
        provider_tool_name: str,
        company_sha256: str,
        now: datetime | None = None,
    ) -> QualifiedCapabilityBinding:
        resolved = self.resolve_current(
            provider=provider,
            environment=environment,
            normalized_capability=normalized_capability,
            provider_tool_name=provider_tool_name,
            company_sha256=company_sha256,
            now=now,
        )
        return self._binding_from_resolution(resolved)

    def _bind_flowaccount_oauth_profile_bootstrap(
        self,
        *,
        environment: str,
        now: datetime,
    ) -> QualifiedCapabilityBinding:
        """Resolve the reviewed FlowAccount OAuth profile bootstrap subject only.

        This is intentionally private: caller-controlled generic gate APIs always
        require a server-derived company hash. The resolver invokes it only after
        it has validated the provisional OAuth connection state.
        """

        return self._binding_from_resolution(
            self._resolve_flowaccount_oauth_profile_bootstrap(
                environment=environment,
                now=now,
            )
        )

    def _resolve_flowaccount_oauth_profile_bootstrap(
        self,
        *,
        environment: str,
        now: datetime,
    ) -> CapabilityResolution:
        matching = [
            item
            for item in self._qualifications
            if (
                item.provider,
                item.environment,
                item.normalized_capability,
                item.provider_tool_name,
            )
            == (
                ProviderId.FLOWACCOUNT.value,
                environment,
                _PROFILE_CAPABILITY,
                _FLOWACCOUNT_PROFILE_TOOL,
            )
        ]
        candidates = tuple(matching)
        valid = [
            item
            for item in candidates
            if item.company_sha256 is not None
            and self._is_current_enabled(
                item,
                company_sha256=item.company_sha256,
                now=now,
            )
        ]
        if len(valid) != 1:
            raise QualificationGateError(
                "capability_unavailable" if not candidates else "insufficient_evidence"
            )
        return CapabilityResolution(status="enabled", qualification=valid[0])

    def verify(
        self,
        selection: CapabilitySelection,
        *,
        resource_uri_sha256: str,
        company_sha256: str,
        now: datetime | None = None,
    ) -> VerifiedRuntimeBinding:
        binding = self.bind(
            selection,
            company_sha256=company_sha256,
            now=now,
        )
        qualification = self.resolve(
            selection,
            company_sha256=company_sha256,
            now=now,
        ).qualification
        if qualification is None:
            raise QualificationGateError("capability_unavailable")
        return _verified_binding(qualification, binding, resource_uri_sha256)

    def _binding_from_resolution(
        self,
        resolved: CapabilityResolution,
    ) -> QualifiedCapabilityBinding:
        if resolved.status != "enabled" or resolved.qualification is None:
            raise QualificationGateError(resolved.status)
        qualification = resolved.qualification
        if qualification.evidence_revision_sha256 is None:
            raise QualificationGateError("insufficient_evidence")
        return QualifiedCapabilityBinding(
            provider=ProviderId(qualification.provider),
            environment=qualification.environment,
            normalized_capability=qualification.normalized_capability,
            provider_tool=qualification.provider_tool_name,
            operation_class=_operation_class(qualification),
            qualification_hash=qualification.evidence_revision_sha256,
        )

    def _resolve_matches(
        self,
        matching: Iterable[ProviderMCPQualification],
        *,
        company_sha256: str,
        now: datetime,
    ) -> CapabilityResolution:
        candidates = tuple(matching)
        if not candidates:
            return CapabilityResolution(status="capability_unavailable")
        valid = [
            item
            for item in candidates
            if self._is_current_enabled(
                item,
                company_sha256=company_sha256,
                now=now,
            )
        ]
        if len(valid) == 1:
            return CapabilityResolution(status="enabled", qualification=valid[0])
        if len(valid) > 1:
            return CapabilityResolution(status="insufficient_evidence")
        if any(
            item.qualification_state in {QualificationState.DISABLED, QualificationState.SUPERSEDED}
            for item in candidates
        ) and not any(
            item.qualification_state is QualificationState.ENABLED for item in candidates
        ):
            return CapabilityResolution(status="capability_unavailable")
        return CapabilityResolution(status="insufficient_evidence")

    def _is_current_enabled(
        self,
        qualification: ProviderMCPQualification,
        *,
        company_sha256: str,
        now: datetime,
    ) -> bool:
        if (
            qualification.qualification_state is not QualificationState.ENABLED
            or qualification.evidence_expires_at is None
            or qualification.evidence_evaluated_at is None
            or qualification.evidence_expires_at <= now
            or qualification.evidence_evaluated_at > now
            or qualification.company_sha256 is None
            or qualification.evidence_revision_sha256 is None
        ):
            return False
        if not secrets.compare_digest(company_sha256, qualification.company_sha256):
            return False
        artifact = self._artifact_for(qualification)
        if artifact is None:
            return False
        try:
            artifact.require_valid_for(
                qualification,
                now=now,
                expected_company_sha256=qualification.company_sha256,
            )
        except ValueError:
            return False
        if (
            artifact.catalog_uri != qualification.qualification_evidence_uri
            or artifact.evidence_revision_sha256 != qualification.evidence_revision_sha256
            or artifact.evaluated_at != qualification.evidence_evaluated_at
            or artifact.evidence_expires_at != qualification.evidence_expires_at
        ):
            return False
        if qualification.environment == "production":
            if (
                qualification.production_canary_at is None
                or qualification.production_canary_at > now
                or qualification.nonproduction_evidence_revision_sha256 is None
                or qualification.nonproduction_company_sha256 is None
            ):
                return False
            return self._has_current_nonproduction_reference(qualification, now=now)
        return True

    def _has_current_nonproduction_reference(
        self,
        production: ProviderMCPQualification,
        *,
        now: datetime,
    ) -> bool:
        matches = [
            candidate
            for candidate in self._qualifications
            if (
                candidate.environment != "production"
                and candidate.evidence_revision_sha256
                == production.nonproduction_evidence_revision_sha256
                and candidate.company_sha256 == production.nonproduction_company_sha256
                and candidate.qualification_state
                in {QualificationState.NONPRODUCTION_QUALIFIED, QualificationState.ENABLED}
                and _same_cross_environment_capability(production, candidate)
                and self._has_current_evidence(candidate, now=now)
            )
        ]
        return len(matches) == 1

    def _has_current_evidence(
        self,
        qualification: ProviderMCPQualification,
        *,
        now: datetime,
    ) -> bool:
        if (
            qualification.evidence_expires_at is None
            or qualification.evidence_evaluated_at is None
            or qualification.evidence_expires_at <= now
            or qualification.evidence_evaluated_at > now
            or qualification.company_sha256 is None
        ):
            return False
        artifact = self._artifact_for(qualification)
        if artifact is None:
            return False
        try:
            artifact.require_valid_for(
                qualification,
                now=now,
                expected_company_sha256=qualification.company_sha256,
            )
        except ValueError:
            return False
        return (
            artifact.catalog_uri == qualification.qualification_evidence_uri
            and artifact.evidence_revision_sha256 == qualification.evidence_revision_sha256
            and artifact.evaluated_at == qualification.evidence_evaluated_at
            and artifact.evidence_expires_at == qualification.evidence_expires_at
        )

    def _artifact_for(
        self,
        qualification: ProviderMCPQualification,
    ) -> QualificationArtifact | None:
        uri = qualification.qualification_evidence_uri
        if uri is None:
            return None
        try:
            artifact = self._artifacts.get(uri)
            if artifact is None and self._artifact_loader is not None:
                artifact = QualificationArtifact.model_validate(self._artifact_loader(uri))
            return artifact
        except (OSError, TypeError, ValueError):
            return None


class QualificationCatalog(Protocol):
    def list_provider_mcp_qualifications(self) -> list[ProviderMCPQualification]: ...


@dataclass(frozen=True, slots=True)
class CatalogQualificationSnapshot:
    """One immutable catalog view for a single provider operation."""

    gate: CapabilityQualificationGate
    now: datetime


class CatalogQualificationResolver:
    """Resolve catalog authority under the enclosing provider operation deadline."""

    def __init__(
        self,
        *,
        catalog: QualificationCatalog,
        catalog_root: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(getattr(catalog, "list_provider_mcp_qualifications", None)):
            raise ValueError("qualification_catalog_invalid")
        self._catalog = catalog
        self._catalog_root = str(catalog_root)
        self._now = now or (lambda: datetime.now(UTC))
        self._snapshot: ContextVar[CatalogQualificationSnapshot | None] = ContextVar(
            "mercury_catalog_qualification_snapshot",
            default=None,
        )

    async def open_snapshot(
        self,
        deadline: ProviderOperationDeadline | None = None,
    ) -> CatalogQualificationSnapshot:
        """Load catalog rows without blocking the event loop or escaping deadline."""

        checked_deadline = self._deadline(deadline)
        checked_deadline.check()
        try:
            async with asyncio.timeout_at(checked_deadline.expires_at):
                qualifications = await asyncio.to_thread(
                    self._catalog.list_provider_mcp_qualifications
                )
            checked_deadline.check()
            gate = CapabilityQualificationGate(
                qualifications,
                artifact_loader=lambda uri: load_catalog_qualification_artifact(
                    self._catalog_root,
                    uri,
                ),
            )
        except (OSError, TypeError, ValueError, RuntimeError):
            gate = CapabilityQualificationGate(())
        return CatalogQualificationSnapshot(gate=gate, now=self._checked_now())

    @contextmanager
    def use_snapshot(self, snapshot: CatalogQualificationSnapshot) -> Iterator[None]:
        checked = CatalogQualificationSnapshot(
            gate=snapshot.gate,
            now=snapshot.now,
        )
        token = self._snapshot.set(checked)
        try:
            yield
        finally:
            self._snapshot.reset(token)

    async def bind_bootstrap(
        self,
        connection: ProviderConnection,
        *,
        normalized_capability: str = _PROFILE_CAPABILITY,
        provider_tool_name: str,
        deadline: ProviderOperationDeadline | None = None,
    ) -> QualifiedCapabilityBinding:
        checked = ProviderConnection.model_validate(connection)
        if not _is_exact_flowaccount_oauth_profile_bootstrap(
            checked,
            normalized_capability=normalized_capability,
            provider_tool_name=provider_tool_name,
        ):
            raise QualificationGateError("capability_unavailable")
        snapshot = await self._current_snapshot(deadline)
        return snapshot.gate._bind_flowaccount_oauth_profile_bootstrap(
            environment=checked.environment,
            now=snapshot.now,
        )

    async def bind_for_connection(
        self,
        connection: ProviderConnection,
        *,
        normalized_capability: str,
        provider_tool_name: str,
        deadline: ProviderOperationDeadline | None = None,
    ) -> QualifiedCapabilityBinding:
        checked = ProviderConnection.model_validate(connection)
        snapshot = await self._current_snapshot(deadline)
        return snapshot.gate.bind_current(
            provider=checked.provider.value,
            environment=checked.environment,
            normalized_capability=normalized_capability,
            provider_tool_name=provider_tool_name,
            company_sha256=_server_company_sha256(checked),
            now=snapshot.now,
        )

    async def resolve_for_connection(
        self,
        connection: ProviderConnection,
        *,
        selection: CapabilitySelection,
        deadline: ProviderOperationDeadline | None = None,
    ) -> CapabilityResolution:
        """Resolve public status through the same connection-bound gate as dispatch."""

        checked_connection = ProviderConnection.model_validate(connection)
        checked_selection = CapabilitySelection.model_validate(selection)
        if (
            checked_selection.provider != checked_connection.provider.value
            or checked_selection.environment != checked_connection.environment
        ):
            return CapabilityResolution(status="capability_unavailable")
        snapshot = await self._current_snapshot(deadline)
        return snapshot.gate.resolve(
            checked_selection,
            company_sha256=_server_company_sha256(checked_connection),
            now=snapshot.now,
        )

    async def assert_binding(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        *,
        deadline: ProviderOperationDeadline | None = None,
    ) -> QualifiedCapabilityBinding:
        snapshot = await self._current_snapshot(deadline)
        return self._assert_binding(snapshot, connection, binding)

    def _assert_binding(
        self,
        snapshot: CatalogQualificationSnapshot,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
    ) -> QualifiedCapabilityBinding:
        checked_binding = QualifiedCapabilityBinding.model_validate(binding)
        checked_connection = ProviderConnection.model_validate(connection)
        if checked_binding.provider is not checked_connection.provider:
            raise QualificationGateError("capability_unavailable")
        if _is_profile_bootstrap(checked_connection, checked_binding):
            expected = snapshot.gate._bind_flowaccount_oauth_profile_bootstrap(
                environment=checked_connection.environment,
                now=snapshot.now,
            )
        else:
            expected = snapshot.gate.bind_current(
                provider=checked_connection.provider.value,
                environment=checked_connection.environment,
                normalized_capability=checked_binding.normalized_capability,
                provider_tool_name=checked_binding.provider_tool,
                company_sha256=_server_company_sha256(checked_connection),
                now=snapshot.now,
            )
        if checked_binding != expected:
            raise QualificationGateError("capability_unavailable")
        return expected

    async def verify_binding(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
        *,
        deadline: ProviderOperationDeadline | None = None,
    ) -> VerifiedRuntimeBinding:
        snapshot = await self._current_snapshot(deadline)
        expected = self._assert_binding(snapshot, connection, binding)
        checked_connection = ProviderConnection.model_validate(connection)
        gate = snapshot.gate
        if _is_profile_bootstrap(checked_connection, expected):
            resolution = gate._resolve_flowaccount_oauth_profile_bootstrap(
                environment=checked_connection.environment,
                now=snapshot.now,
            )
        else:
            resolution = gate.resolve_current(
                provider=checked_connection.provider.value,
                environment=checked_connection.environment,
                normalized_capability=expected.normalized_capability,
                provider_tool_name=expected.provider_tool,
                company_sha256=_server_company_sha256(checked_connection),
                now=snapshot.now,
            )
        if resolution.status != "enabled" or resolution.qualification is None:
            raise QualificationGateError(resolution.status)
        if gate._binding_from_resolution(resolution) != expected:
            raise QualificationGateError("capability_unavailable")
        return _verified_binding(resolution.qualification, expected, resource_uri_sha256)

    async def _current_snapshot(
        self,
        deadline: ProviderOperationDeadline | None = None,
    ) -> CatalogQualificationSnapshot:
        snapshot = self._snapshot.get()
        if snapshot is not None:
            return snapshot
        return await self.open_snapshot(deadline)

    @staticmethod
    def _deadline(
        deadline: ProviderOperationDeadline | None,
    ) -> ProviderOperationDeadline:
        selected = deadline or current_provider_operation_deadline()
        if selected is None:
            raise QualificationGateError("insufficient_evidence")
        return selected

    def _checked_now(self) -> datetime:
        return _now(self._now())


def transition_qualification(
    qualification: ProviderMCPQualification,
    target_state: QualificationState,
    *,
    evidence: QualificationArtifact | None = None,
    nonproduction_evidence: Iterable[ProviderMCPQualification] = (),
    nonproduction_artifacts: Iterable[QualificationArtifact] = (),
    canary: OwnerAuthorizedCanary | None = None,
    disable_reason: str | None = None,
    now: datetime,
) -> ProviderMCPQualification:
    """Advance one immutable evidence revision through the only allowed lifecycle."""

    current = ProviderMCPQualification.model_validate(qualification)
    target = QualificationState(target_state)
    checked_now = _now(now)
    if target not in _ALLOWED_TRANSITIONS[current.qualification_state]:
        raise ValueError("qualification_transition_invalid")

    updates: dict[str, object] = {"qualification_state": target}
    if target is QualificationState.SCHEMA_VALIDATED:
        _require_absent(evidence, canary, disable_reason)
    elif target is QualificationState.NONPRODUCTION_QUALIFIED:
        checked_evidence = _require_current_artifact(current, evidence, now=checked_now)
        updates.update(_evidence_updates(checked_evidence))
        if current.environment == "production":
            candidate = _require_nonproduction_evidence(
                current,
                nonproduction_evidence,
                nonproduction_artifacts,
                now=checked_now,
            )
            updates.update(
                {
                    "nonproduction_evidence_revision_sha256": candidate.evidence_revision_sha256,
                    "nonproduction_company_sha256": candidate.company_sha256,
                }
            )
    elif target is QualificationState.ENABLED:
        if not _is_v1_enabled_operation(current):
            raise ValueError("qualification_operation_not_allowed")
        checked_evidence = _require_current_artifact(current, evidence, now=checked_now)
        _require_record_matches_artifact(current, checked_evidence)
        if current.environment == "production":
            candidate = _require_nonproduction_evidence(
                current,
                nonproduction_evidence,
                nonproduction_artifacts,
                now=checked_now,
            )
            if (
                current.nonproduction_evidence_revision_sha256 != candidate.evidence_revision_sha256
                or current.nonproduction_company_sha256 != candidate.company_sha256
            ):
                raise ValueError("nonproduction_evidence_required")
            if canary is None:
                raise ValueError("production_canary_required")
            checked_canary = OwnerAuthorizedCanary.model_validate(canary)
            if checked_canary.authorized_at > checked_now:
                raise ValueError("production_canary_invalid")
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
        updates["disable_reason"] = disable_reason

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


def _require_current_artifact(
    definition: ProviderMCPQualification,
    artifact: QualificationArtifact | None,
    *,
    now: datetime,
) -> QualificationArtifact:
    if artifact is None:
        raise ValueError("qualification_evidence_required")
    checked = QualificationArtifact.model_validate(artifact)
    checked.require_valid_for(definition, now=now)
    return checked


def _evidence_updates(artifact: QualificationArtifact) -> dict[str, object]:
    return {
        "company_sha256": artifact.company_sha256,
        "evidence_revision_sha256": artifact.evidence_revision_sha256,
        "qualification_evidence_uri": artifact.catalog_uri,
        "evidence_evaluated_at": artifact.evaluated_at,
        "evidence_expires_at": artifact.evidence_expires_at,
    }


def _require_record_matches_artifact(
    qualification: ProviderMCPQualification,
    artifact: QualificationArtifact,
) -> None:
    if (
        qualification.company_sha256 != artifact.company_sha256
        or qualification.evidence_revision_sha256 != artifact.evidence_revision_sha256
        or qualification.qualification_evidence_uri != artifact.catalog_uri
        or qualification.evidence_evaluated_at != artifact.evaluated_at
        or qualification.evidence_expires_at != artifact.evidence_expires_at
    ):
        raise ValueError("qualification_evidence_mismatch")


def _require_nonproduction_evidence(
    production: ProviderMCPQualification,
    candidates: Iterable[ProviderMCPQualification],
    artifacts: Iterable[QualificationArtifact],
    *,
    now: datetime,
) -> ProviderMCPQualification:
    artifact_by_uri = {
        checked.catalog_uri: checked
        for artifact in artifacts
        if (checked := QualificationArtifact.model_validate(artifact)).passed
    }
    matches: list[ProviderMCPQualification] = []
    for candidate in candidates:
        checked = ProviderMCPQualification.model_validate(candidate)
        artifact = (
            artifact_by_uri.get(checked.qualification_evidence_uri or "")
            if checked.qualification_evidence_uri is not None
            else None
        )
        if (
            checked.environment != "production"
            and checked.qualification_state
            in {QualificationState.NONPRODUCTION_QUALIFIED, QualificationState.ENABLED}
            and _same_cross_environment_capability(production, checked)
            and artifact is not None
        ):
            try:
                artifact.require_valid_for(
                    checked,
                    now=now,
                    expected_company_sha256=checked.company_sha256,
                )
                _require_record_matches_artifact(checked, artifact)
            except ValueError:
                continue
            matches.append(checked)
    if len(matches) != 1:
        raise ValueError("nonproduction_evidence_required")
    return matches[0]


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


def _verified_binding(
    qualification: ProviderMCPQualification,
    binding: QualifiedCapabilityBinding,
    resource_uri_sha256: str,
) -> VerifiedRuntimeBinding:
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
        qualification_hash=binding.qualification_hash,
    )


def _server_company_sha256(connection: ProviderConnection) -> str:
    value = connection.provider_account_id
    if value.startswith("oauth-pending-"):
        raise QualificationGateError("insufficient_evidence")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_profile_bootstrap(
    connection: ProviderConnection,
    binding: QualifiedCapabilityBinding,
) -> bool:
    return _is_exact_flowaccount_oauth_profile_bootstrap(
        connection,
        normalized_capability=binding.normalized_capability,
        provider_tool_name=binding.provider_tool,
    )


def _is_exact_flowaccount_oauth_profile_bootstrap(
    connection: ProviderConnection,
    *,
    normalized_capability: str,
    provider_tool_name: str,
) -> bool:
    return (
        connection.provider is ProviderId.FLOWACCOUNT
        and connection.authorization_method is AuthorizationMethod.OAUTH2_PKCE
        and connection.readiness is ConnectionReadiness.REQUIRES_VALIDATION
        and normalized_capability == _PROFILE_CAPABILITY
        and provider_tool_name == _FLOWACCOUNT_PROFILE_TOOL
        and connection.provider_account_id.startswith("oauth-pending-")
    )


def _sha256_match(value: str) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(_SHA256, value) else None


def _require_company_sha256(value: str) -> str:
    checked = _sha256_match(value)
    if checked is None:
        raise ValueError("qualification_company_invalid")
    return checked


def _json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _now(value: datetime | None) -> datetime:
    checked = value or datetime.now(UTC)
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise ValueError("qualification_time_invalid")
    return checked.astimezone(UTC)


def _require_absent(
    evidence: QualificationArtifact | None,
    canary: OwnerAuthorizedCanary | None,
    disable_reason: str | None,
) -> None:
    if evidence is not None or canary is not None or disable_reason is not None:
        raise ValueError("qualification_transition_arguments_invalid")


__all__ = [
    "CapabilityQualificationGate",
    "CapabilityResolution",
    "CapabilitySelection",
    "CatalogQualificationResolver",
    "OwnerAuthorizedCanary",
    "QualificationGateError",
    "transition_qualification",
]
