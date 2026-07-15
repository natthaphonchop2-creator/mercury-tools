"""Typed, append-only Supabase storage for endpoint validation evidence."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

import httpx
from pydantic import Field

from mercury_tools.catalog.models import HttpMethod
from mercury_tools.config import Settings, require_supabase
from mercury_tools.qualification.models import StrictSafeModel, ValidationKnowledge
from mercury_tools.qualification.selection import (
    EvidenceRequest,
    EvidenceSelection,
    normalize_evidence_time,
    select_evidence,
)

_FILTER_VALUE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_SAFE_TOKEN_PATTERN = r"^[A-Za-z0-9._:-]+$"
_ENVIRONMENTS = frozenset({"sandbox", "test", "uat", "production"})
_VALIDATION_CONFLICT_FIELDS = (
    "connector_id",
    "action_id",
    "version_id",
    "environment",
    "run_id",
)
_VALIDATION_SELECT = ",".join(ValidationKnowledge.model_fields)
_OBSERVATION_SELECT = (
    "opaque_event_id,action_id,version_id,connector_id,method,observed_state,"
    "status_class,latency_ms,metadata"
)
_OBSERVATION_BINDING_SELECT = "connector_id,action_id,version_id"
_BATCH_RESOLUTION_KEYS = {
    "request_index",
    "connector_id",
    "action_id",
    "version_id",
    "environment",
    "records",
}


class ObservationState(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ObservationSource(StrEnum):
    SANDBOX_RUNNER = "sandbox_runner"
    CONTRACT_VALIDATOR = "contract_validator"
    MANUAL_REVIEW = "manual_review"


class ObservationReviewerRole(StrEnum):
    RELEASE_REVIEWER = "release_reviewer"
    ACCOUNTANT_REVIEWER = "accountant_reviewer"
    POLICY_REVIEWER = "policy_reviewer"


class ObservationNote(StrEnum):
    REVIEWED_EXPECTED_OUTCOME = "reviewed_expected_outcome"
    CLASSIFIED_FAILURE = "classified_failure"
    OUTCOME_NOT_PROVEN = "outcome_not_proven"
    CONTRACT_ONLY = "contract_only"
    CLEANUP_VERIFIED = "cleanup_verified"


class ObservationMetadata(StrictSafeModel):
    source: ObservationSource | None = None
    reviewed_by: ObservationReviewerRole | None = None
    note: ObservationNote | None = None


class ValidationObservation(StrictSafeModel):
    opaque_event_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_SAFE_TOKEN_PATTERN,
    )
    action_id: str = Field(min_length=1, max_length=200, pattern=_SAFE_TOKEN_PATTERN)
    version_id: str = Field(min_length=1, max_length=200, pattern=_SAFE_TOKEN_PATTERN)
    connector_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_SAFE_TOKEN_PATTERN,
    )
    method: HttpMethod
    observed_state: ObservationState
    status_class: str = Field(
        min_length=1,
        max_length=200,
        pattern=_SAFE_TOKEN_PATTERN,
    )
    latency_ms: int | None = Field(default=None, ge=0)
    metadata: ObservationMetadata = Field(default_factory=ObservationMetadata)


class ObservationActionVersionBinding(StrictSafeModel):
    connector_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_SAFE_TOKEN_PATTERN,
    )
    action_id: str = Field(min_length=1, max_length=200, pattern=_SAFE_TOKEN_PATTERN)
    version_id: str = Field(min_length=1, max_length=200, pattern=_SAFE_TOKEN_PATTERN)

    @property
    def scope_key(self) -> tuple[str, str, str]:
        return self.connector_id, self.action_id, self.version_id


class ResolutionEntry(StrictSafeModel):
    request: EvidenceRequest
    selection: EvidenceSelection


class ResolveResult(StrictSafeModel):
    entries: tuple[ResolutionEntry, ...]


class CoverageResult(StrictSafeModel):
    connector_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_SAFE_TOKEN_PATTERN,
    )
    environment: Literal["sandbox", "test", "uat", "production"]
    records: tuple[ValidationKnowledge, ...]


class SupabaseValidationStore:
    """Publish and retrieve secret-safe validation evidence with service-role access."""

    def __init__(self, settings: Settings):
        require_supabase(settings)
        self.settings = settings
        self.base_url = f"{settings.supabase_url}/rest/v1"
        self.headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }

    def publish(self, records: Sequence[ValidationKnowledge]) -> int:
        validated = _validated_publish_batch(records)
        pending: list[ValidationKnowledge] = []
        for record in validated:
            existing = self._existing_validation(record)
            if existing is None:
                pending.append(record)
            elif _validation_payload(existing) != _validation_payload(record):
                raise RuntimeError("supabase_validation_conflict")

        if not pending:
            return 0

        response = self._request(
            "POST",
            "erp_action_validation_knowledge",
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            params={
                "on_conflict": "connector_id,action_id,version_id,environment,run_id",
                "select": _VALIDATION_SELECT,
            },
            json=[_validation_payload(record) for record in pending],
        )
        returned = _validation_response_rows(response)
        pending_by_identity = {_validation_identity(record): record for record in pending}
        created: set[tuple[str, str, str, str, str]] = set()
        for record in returned:
            identity = _validation_identity(record)
            expected = pending_by_identity.get(identity)
            if expected is None or identity in created:
                raise RuntimeError("supabase_validation_response_invalid")
            if _validation_payload(record) != _validation_payload(expected):
                raise RuntimeError("supabase_validation_conflict")
            created.add(identity)

        for identity, expected in pending_by_identity.items():
            if identity in created:
                continue
            existing = self._existing_validation(expected)
            if existing is None:
                raise RuntimeError("supabase_validation_response_invalid")
            if _validation_payload(existing) != _validation_payload(expected):
                raise RuntimeError("supabase_validation_conflict")

        return len(created)

    def record_observation(self, observation: ValidationObservation) -> bool:
        validated = _validated_observation(observation)
        self._require_observation_binding(validated)
        existing = self._existing_observation(validated.opaque_event_id)
        if existing is not None:
            if _observation_payload(existing) != _observation_payload(validated):
                raise RuntimeError("supabase_observation_conflict")
            return False

        response = self._request(
            "POST",
            "erp_action_observations",
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            params={
                "on_conflict": "opaque_event_id",
                "select": _OBSERVATION_SELECT,
            },
            json=[_observation_payload(validated)],
        )
        returned = _observation_response_rows(response)
        if len(returned) > 1:
            raise RuntimeError("supabase_validation_response_invalid")
        if returned:
            if _observation_payload(returned[0]) != _observation_payload(validated):
                raise RuntimeError("supabase_observation_conflict")
            return True

        existing = self._existing_observation(validated.opaque_event_id)
        if existing is None:
            raise RuntimeError("supabase_validation_response_invalid")
        if _observation_payload(existing) != _observation_payload(validated):
            raise RuntimeError("supabase_observation_conflict")
        return False

    def resolve(
        self,
        requests: Sequence[EvidenceRequest],
        now: datetime,
    ) -> ResolveResult:
        normalized_now = normalize_evidence_time(now)
        validated_requests = _validated_requests(requests)
        response = self._request(
            "POST",
            "rpc/resolve_erp_action_validation_batch",
            json={
                "p_requests": [
                    request.model_dump(mode="json") for request in validated_requests
                ],
                "p_now": normalized_now.isoformat(),
            },
        )
        records_by_request = _batch_resolution_response_rows(
            response,
            validated_requests,
        )
        entries: list[ResolutionEntry] = []
        for request, records in zip(
            validated_requests,
            records_by_request,
            strict=True,
        ):
            try:
                selection = select_evidence(records, request=request, now=normalized_now)
            except ValueError:
                raise RuntimeError("supabase_validation_response_invalid") from None
            entries.append(ResolutionEntry(request=request, selection=selection))
        return ResolveResult(entries=tuple(entries))

    def coverage(self, connector_id: str, environment: str) -> CoverageResult:
        connector, validated_environment = _validated_coverage_filter(
            connector_id,
            environment,
        )
        response = self._request(
            "GET",
            "erp_action_validation_knowledge",
            params={
                "connector_id": f"eq.{connector}",
                "environment": f"eq.{validated_environment}",
                "select": _VALIDATION_SELECT,
                "order": "action_id.asc,version_id.asc,evaluated_at.desc,run_id.desc",
            },
        )
        records = _validation_response_rows(response)
        if any(
            record.connector_id != connector or record.environment != validated_environment
            for record in records
        ):
            raise RuntimeError("supabase_validation_response_invalid")
        ordered = _ordered_coverage_records(records)
        return CoverageResult(
            connector_id=connector,
            environment=validated_environment,
            records=ordered,
        )

    def _existing_validation(
        self,
        record: ValidationKnowledge,
    ) -> ValidationKnowledge | None:
        response = self._request(
            "GET",
            "erp_action_validation_knowledge",
            params={field: f"eq.{getattr(record, field)}" for field in _VALIDATION_CONFLICT_FIELDS}
            | {"select": _VALIDATION_SELECT},
        )
        rows = _validation_response_rows(response)
        if len(rows) > 1:
            raise RuntimeError("supabase_validation_response_invalid")
        return rows[0] if rows else None

    def _existing_observation(self, opaque_event_id: str) -> ValidationObservation | None:
        response = self._request(
            "GET",
            "erp_action_observations",
            params={
                "opaque_event_id": f"eq.{opaque_event_id}",
                "select": _OBSERVATION_SELECT,
            },
        )
        rows = _observation_response_rows(response)
        if len(rows) > 1:
            raise RuntimeError("supabase_validation_response_invalid")
        return rows[0] if rows else None

    def _require_observation_binding(self, observation: ValidationObservation) -> None:
        response = self._request(
            "GET",
            "erp_action_versions",
            params={
                "connector_id": f"eq.{observation.connector_id}",
                "action_id": f"eq.{observation.action_id}",
                "version_id": f"eq.{observation.version_id}",
                "select": _OBSERVATION_BINDING_SELECT,
                "limit": "2",
            },
        )
        bindings = _observation_binding_response_rows(response)
        if not bindings:
            raise RuntimeError("supabase_observation_scope_mismatch")
        if len(bindings) != 1:
            raise RuntimeError("supabase_validation_response_invalid")
        if bindings[0].scope_key != (
            observation.connector_id,
            observation.action_id,
            observation.version_id,
        ):
            raise RuntimeError("supabase_observation_scope_mismatch")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {**self.headers, **kwargs.pop("headers", {})}
        try:
            response = httpx.request(method, url, headers=headers, timeout=60, **kwargs)
        except httpx.HTTPError:
            raise RuntimeError("supabase_validation_request_failed") from None
        if response.status_code >= 300:
            raise RuntimeError("supabase_validation_request_failed")
        if not response.text:
            return None
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("supabase_validation_response_invalid") from None


def _validated_publish_batch(
    records: Sequence[ValidationKnowledge],
) -> tuple[ValidationKnowledge, ...]:
    if isinstance(records, (str, bytes, bytearray)) or not isinstance(records, Sequence):
        raise ValueError("validation_evidence_invalid")

    validated = tuple(_validated_validation(record) for record in records)
    by_identity: dict[tuple[str, str, str, str, str], ValidationKnowledge] = {}
    by_evidence_id: dict[str, ValidationKnowledge] = {}
    for record in validated:
        identity = _validation_identity(record)
        existing = by_identity.get(identity)
        if existing is not None:
            if _validation_payload(existing) != _validation_payload(record):
                raise RuntimeError("supabase_validation_conflict")
            continue
        evidence_existing = by_evidence_id.get(record.opaque_evidence_id)
        if evidence_existing is not None and (
            _validation_payload(evidence_existing) != _validation_payload(record)
        ):
            raise RuntimeError("supabase_validation_conflict")
        by_identity[identity] = record
        by_evidence_id[record.opaque_evidence_id] = record
    return tuple(by_identity[key] for key in sorted(by_identity))


def _validated_validation(value: Any) -> ValidationKnowledge:
    try:
        record = ValidationKnowledge.model_validate(value)
        if record.environment not in _ENVIRONMENTS:
            raise ValueError
        for field in (*_VALIDATION_CONFLICT_FIELDS, "opaque_evidence_id"):
            candidate = getattr(record, field)
            if not isinstance(candidate, str) or _FILTER_VALUE.fullmatch(candidate) is None:
                raise ValueError
        evaluated_at = normalize_evidence_time(record.evaluated_at)
        expires_at = (
            normalize_evidence_time(record.expires_at) if record.expires_at is not None else None
        )
        if expires_at is not None and expires_at <= evaluated_at:
            raise ValueError
        return record.model_copy(update={"evaluated_at": evaluated_at, "expires_at": expires_at})
    except (TypeError, ValueError):
        raise ValueError("validation_evidence_invalid") from None


def _validated_observation(value: Any) -> ValidationObservation:
    try:
        observation = ValidationObservation.model_validate(value)
        for field in (
            "opaque_event_id",
            "connector_id",
            "action_id",
            "version_id",
        ):
            if _FILTER_VALUE.fullmatch(getattr(observation, field)) is None:
                raise ValueError
        return observation
    except (TypeError, ValueError):
        raise ValueError("validation_observation_invalid") from None


def _validated_requests(requests: Sequence[EvidenceRequest]) -> tuple[EvidenceRequest, ...]:
    if isinstance(requests, (str, bytes, bytearray)) or not isinstance(requests, Sequence):
        raise ValueError("evidence_request_invalid")
    try:
        validated = tuple(EvidenceRequest.model_validate(request) for request in requests)
        if not 1 <= len(validated) <= 100:
            raise ValueError
        scopes = tuple(request.scope_key for request in validated)
        if len(set(scopes)) != len(scopes):
            raise ValueError
        return validated
    except (TypeError, ValueError):
        raise ValueError("evidence_request_invalid") from None


def _validated_coverage_filter(connector_id: Any, environment: Any) -> tuple[str, str]:
    try:
        if (
            not isinstance(connector_id, str)
            or _FILTER_VALUE.fullmatch(connector_id) is None
            or environment not in _ENVIRONMENTS
        ):
            raise ValueError
        EvidenceRequest.model_validate(
            {
                "connector_id": connector_id,
                "action_id": "filter_validation",
                "version_id": "filter_validation",
                "environment": environment,
            }
        )
        return connector_id, environment
    except (TypeError, ValueError):
        raise ValueError("validation_coverage_filter_invalid") from None


def _validation_response_rows(value: Any) -> tuple[ValidationKnowledge, ...]:
    if not isinstance(value, list):
        raise RuntimeError("supabase_validation_response_invalid")
    try:
        return tuple(_validated_validation(row) for row in value)
    except ValueError:
        raise RuntimeError("supabase_validation_response_invalid") from None


def _batch_resolution_response_rows(
    value: Any,
    requests: Sequence[EvidenceRequest],
) -> tuple[tuple[ValidationKnowledge, ...], ...]:
    if not isinstance(value, list) or len(value) != len(requests):
        raise RuntimeError("supabase_validation_response_invalid")
    indexed: dict[int, tuple[ValidationKnowledge, ...]] = {}
    for row in value:
        if not isinstance(row, dict) or set(row) != _BATCH_RESOLUTION_KEYS:
            raise RuntimeError("supabase_validation_response_invalid")
        request_index = row["request_index"]
        if (
            isinstance(request_index, bool)
            or not isinstance(request_index, int)
            or request_index < 0
            or request_index >= len(requests)
            or request_index in indexed
        ):
            raise RuntimeError("supabase_validation_response_invalid")
        expected = requests[request_index]
        if (
            row["connector_id"],
            row["action_id"],
            row["version_id"],
            row["environment"],
        ) != expected.scope_key:
            raise RuntimeError("supabase_validation_response_invalid")
        records = _validation_response_rows(row["records"])
        if any(not expected.matches(record) for record in records):
            raise RuntimeError("supabase_validation_response_invalid")
        indexed[request_index] = records
    if set(indexed) != set(range(len(requests))):
        raise RuntimeError("supabase_validation_response_invalid")
    return tuple(indexed[index] for index in range(len(requests)))


def _observation_response_rows(value: Any) -> tuple[ValidationObservation, ...]:
    if not isinstance(value, list):
        raise RuntimeError("supabase_validation_response_invalid")
    try:
        return tuple(_validated_observation(row) for row in value)
    except ValueError:
        raise RuntimeError("supabase_validation_response_invalid") from None


def _observation_binding_response_rows(
    value: Any,
) -> tuple[ObservationActionVersionBinding, ...]:
    if not isinstance(value, list):
        raise RuntimeError("supabase_validation_response_invalid")
    try:
        return tuple(ObservationActionVersionBinding.model_validate(row) for row in value)
    except (TypeError, ValueError):
        raise RuntimeError("supabase_validation_response_invalid") from None


def _validation_identity(record: ValidationKnowledge) -> tuple[str, str, str, str, str]:
    return (
        record.connector_id,
        record.action_id,
        record.version_id,
        record.environment,
        record.run_id,
    )


def _validation_payload(record: ValidationKnowledge) -> dict[str, Any]:
    return record.model_dump(mode="json")


def _observation_payload(observation: ValidationObservation) -> dict[str, Any]:
    return observation.model_dump(mode="json", exclude_none=True)


def _ordered_coverage_records(
    records: Sequence[ValidationKnowledge],
) -> tuple[ValidationKnowledge, ...]:
    ordered = list(records)
    ordered.sort(key=lambda item: item.run_id, reverse=True)
    ordered.sort(key=lambda item: item.evaluated_at, reverse=True)
    ordered.sort(key=lambda item: (item.action_id, item.version_id))
    return tuple(ordered)
