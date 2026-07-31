import hashlib
import re
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import (
    build_source_id,
    canonical_json,
    deep_freeze,
    sanitize_document,
    validate_action_identity,
    validate_credential_safe,
    validate_credential_safe_paths,
)


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class RiskTier(IntEnum):
    SAFE_READ = 0
    STANDARD_WRITE = 1
    HIGH_RISK = 2


class ActionConfidence(StrEnum):
    EXACT = "exact"
    EXAMPLE_DERIVED = "example_derived"
    INFERRED = "inferred"


class ObservedState(StrEnum):
    UNTESTED = "untested"
    SUCCESS = "success"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class QualificationState(StrEnum):
    """The only lifecycle states for a provider-MCP capability version."""

    DISCOVERED_UNREVIEWED = "discovered_unreviewed"
    SCHEMA_VALIDATED = "schema_validated"
    NONPRODUCTION_QUALIFIED = "nonproduction_qualified"
    ENABLED = "enabled"
    DISABLED = "disabled"
    SUPERSEDED = "superseded"


_PROVIDER_ID = r"^(?:flowaccount|peak)$"
_CAPABILITY_IDENTIFIER = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
_PROVIDER_TOOL_NAME = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_ENVIRONMENT = r"^[a-z][a-z0-9_-]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"
_SAFE_QUALIFICATION_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_CATALOG_EVIDENCE_URI = re.compile(
    r"^catalog://global/(?:flowaccount|peak)/qualifications/[0-9a-f]{64}-[0-9a-f]{64}\.json$"
)


class CatalogSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    source_id: str
    connector_id: str
    source_type: Literal["openapi3", "swagger2", "postman2.1", "documentation"]
    source_uri: str
    source_hash: str
    imported_version: str
    imported_at: datetime
    driver_suggestion: dict[str, Any] = Field(default_factory=dict)
    sanitization: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: Any) -> Any:
        validate_credential_safe(value)
        validate_credential_safe_paths(value)
        return value

    @model_validator(mode="after")
    def validate_source_integrity(self) -> "CatalogSource":
        if self.imported_at.tzinfo is None or self.imported_at.utcoffset() is None:
            raise ValueError("catalog_source_imported_at_naive")
        if set(self.sanitization) != {"document", "report"}:
            raise ValueError("catalog_source_sanitization_invalid")
        expected_hash = hashlib.sha256(
            canonical_json(self.sanitization).encode("utf-8")
        ).hexdigest()
        if self.source_hash != expected_hash:
            raise ValueError("catalog_source_hash_invalid")
        expected_id = build_source_id(self.connector_id, self.source_uri, self.source_hash)
        if self.source_id != expected_id:
            raise ValueError("catalog_source_id_invalid")

        object.__setattr__(self, "imported_at", self.imported_at.astimezone(UTC))
        object.__setattr__(self, "driver_suggestion", deep_freeze(self.driver_suggestion))
        object.__setattr__(self, "sanitization", deep_freeze(self.sanitization))
        return self

    @classmethod
    def from_document(
        cls,
        uri: str,
        connector_id: str,
        document: dict[str, Any],
        report: dict[str, Any],
        *,
        source_type: Literal["openapi3", "swagger2", "postman2.1", "documentation"] | None = None,
    ) -> "CatalogSource":
        sanitized_uri = sanitize_document(uri)
        sanitized_document = sanitize_document(document)
        sanitized_report = sanitize_document(report)
        source_hash = hashlib.sha256(
            canonical_json({"document": sanitized_document, "report": sanitized_report}).encode()
        ).hexdigest()
        return cls(
            source_id=build_source_id(connector_id, sanitized_uri, source_hash),
            connector_id=connector_id,
            source_type=source_type if source_type is not None else _source_type(document),
            source_uri=sanitized_uri,
            source_hash=source_hash,
            imported_version=_imported_version(document),
            imported_at=datetime.now(UTC),
            sanitization={
                "document": sanitized_document,
                "report": sanitized_report,
            },
        )


class CatalogAction(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    action_id: str
    version_id: str
    connector_id: str
    environments: tuple[str, ...]
    method: HttpMethod
    path_template: str
    operation_id: str
    variant_id: str = "default"
    content_type: str = "application/json"
    aliases_th: tuple[str, ...] = ()
    aliases_en: tuple[str, ...] = ()
    capability: str
    input_schema: dict[str, Any]
    examples: tuple[dict[str, Any], ...] = ()
    risk_tier: RiskTier
    required_confirmations: int
    side_effects: tuple[str, ...] = ()
    preflight_action_ids: tuple[str, ...] = ()
    idempotency: dict[str, Any] = Field(default_factory=dict)
    success_rules: dict[str, Any] = Field(default_factory=dict)
    error_rules: dict[str, Any] = Field(default_factory=dict)
    response_redaction: tuple[str, ...] = ()
    source_uri: str
    source_hash: str
    confidence: ActionConfidence
    observed_state: ObservedState
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: Any) -> Any:
        validate_credential_safe(value)
        validate_credential_safe_paths(value)
        return value

    @model_validator(mode="after")
    def validate_action_integrity(self) -> "CatalogAction":
        if self.required_confirmations != int(self.risk_tier):
            raise ValueError("required_confirmations must match risk_tier")
        if self.method is HttpMethod.GET:
            valid_risk = self.risk_tier is RiskTier.SAFE_READ
        elif self.method is HttpMethod.DELETE:
            valid_risk = self.risk_tier is RiskTier.HIGH_RISK
        else:
            valid_risk = self.risk_tier in {
                RiskTier.STANDARD_WRITE,
                RiskTier.HIGH_RISK,
            }
        if not valid_risk:
            raise ValueError("method_risk_tier_invalid")

        for field_name in type(self).model_fields:
            object.__setattr__(self, field_name, deep_freeze(getattr(self, field_name)))
        return self


class ProviderMCPQualification(BaseModel):
    """An immutable provider-MCP capability version plus its gated state.

    This deliberately does not extend ``CatalogAction``. Existing REST catalog
    versions keep their historical hash while provider-MCP qualification gets a
    separately versioned, exact-selection record.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    id: UUID | None = None
    provider: str = Field(pattern=_PROVIDER_ID)
    environment: str = Field(pattern=_ENVIRONMENT)
    provider_tool_name: str = Field(pattern=_PROVIDER_TOOL_NAME)
    normalized_capability: str = Field(pattern=_CAPABILITY_IDENTIFIER)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    public_output_field_paths: tuple[str, ...] | None = None
    schema_hash: str = Field(pattern=_SHA256)
    response_shape_hash: str = Field(pattern=_SHA256)
    required_permissions: tuple[str, ...]
    capability_version_sha256: str = Field(pattern=_SHA256)
    # The definition version is stable. Each qualification attempt receives a
    # distinct immutable evidence revision after its artifact is reviewed.
    qualification_state: QualificationState = QualificationState.DISCOVERED_UNREVIEWED
    company_sha256: str | None = Field(default=None, pattern=_SHA256)
    evidence_revision_sha256: str | None = Field(default=None, pattern=_SHA256)
    qualification_evidence_uri: str | None = None
    evidence_evaluated_at: datetime | None = None
    evidence_expires_at: datetime | None = None
    nonproduction_evidence_revision_sha256: str | None = Field(default=None, pattern=_SHA256)
    nonproduction_company_sha256: str | None = Field(default=None, pattern=_SHA256)
    production_canary_at: datetime | None = None
    owner_authorized_by: str | None = None
    disable_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: Any) -> Any:
        validate_credential_safe(value)
        validate_credential_safe_paths(value)
        return value

    @model_validator(mode="after")
    def validate_qualification_integrity(self) -> "ProviderMCPQualification":
        if self.public_output_field_paths is not None and (
            tuple(sorted(set(self.public_output_field_paths))) != self.public_output_field_paths
            or any(
                not _valid_public_output_field_path(path) for path in self.public_output_field_paths
            )
        ):
            raise ValueError("provider_mcp_public_output_field_paths_invalid")
        if (
            not self.required_permissions
            or len(self.required_permissions) != len(set(self.required_permissions))
            or tuple(sorted(self.required_permissions)) != self.required_permissions
            or any(
                re.fullmatch(_SAFE_QUALIFICATION_IDENTIFIER, permission) is None
                for permission in self.required_permissions
            )
        ):
            raise ValueError("provider_mcp_qualification_invalid")

        expected_schema_hash = _qualification_schema_hash(
            self.input_schema,
            self.output_schema,
        )
        if self.schema_hash != expected_schema_hash:
            raise ValueError("provider_mcp_qualification_schema_hash_invalid")
        expected_version = _provider_mcp_capability_version_sha256(
            provider=self.provider,
            environment=self.environment,
            provider_tool_name=self.provider_tool_name,
            normalized_capability=self.normalized_capability,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            public_output_field_paths=self.public_output_field_paths,
            schema_hash=self.schema_hash,
            response_shape_hash=self.response_shape_hash,
            required_permissions=self.required_permissions,
        )
        if self.capability_version_sha256 != expected_version:
            raise ValueError("provider_mcp_qualification_version_invalid")

        for field_name in (
            "evidence_evaluated_at",
            "evidence_expires_at",
            "production_canary_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError("provider_mcp_qualification_timestamp_invalid")
                object.__setattr__(self, field_name, value.astimezone(UTC))

        requires_evidence = self.qualification_state not in {
            QualificationState.DISCOVERED_UNREVIEWED,
            QualificationState.SCHEMA_VALIDATED,
        }
        if requires_evidence:
            if (
                self.company_sha256 is None
                or self.evidence_revision_sha256 is None
                or self.qualification_evidence_uri is None
                or _CATALOG_EVIDENCE_URI.fullmatch(self.qualification_evidence_uri) is None
                or self.qualification_evidence_uri
                != _qualification_evidence_uri(
                    provider=self.provider,
                    capability_version_sha256=self.capability_version_sha256,
                    evidence_revision_sha256=self.evidence_revision_sha256,
                )
                or self.evidence_evaluated_at is None
                or self.evidence_expires_at is None
            ):
                raise ValueError("provider_mcp_qualification_evidence_required")
        elif any(
            value is not None
            for value in (
                self.company_sha256,
                self.evidence_revision_sha256,
                self.qualification_evidence_uri,
                self.evidence_evaluated_at,
                self.evidence_expires_at,
                self.nonproduction_evidence_revision_sha256,
                self.nonproduction_company_sha256,
            )
        ):
            raise ValueError("provider_mcp_qualification_evidence_unexpected")

        if self.environment == "production" and requires_evidence:
            if (
                self.nonproduction_evidence_revision_sha256 is None
                or self.nonproduction_company_sha256 is None
            ):
                raise ValueError("provider_mcp_qualification_nonproduction_reference_required")
        elif (
            self.nonproduction_evidence_revision_sha256 is not None
            or self.nonproduction_company_sha256 is not None
        ):
            raise ValueError("provider_mcp_qualification_nonproduction_reference_unexpected")

        if self.qualification_state is QualificationState.ENABLED:
            if self.environment == "production":
                if (
                    self.production_canary_at is None
                    or self.owner_authorized_by is None
                    or re.fullmatch(
                        _SAFE_QUALIFICATION_IDENTIFIER,
                        self.owner_authorized_by,
                    )
                    is None
                ):
                    raise ValueError("provider_mcp_qualification_canary_required")
            elif self.production_canary_at is not None or self.owner_authorized_by is not None:
                raise ValueError("provider_mcp_qualification_canary_unexpected")
        elif self.qualification_state not in {
            QualificationState.DISABLED,
            QualificationState.SUPERSEDED,
        } and (self.production_canary_at is not None or self.owner_authorized_by is not None):
            raise ValueError("provider_mcp_qualification_canary_unexpected")

        if self.qualification_state in {
            QualificationState.DISABLED,
            QualificationState.SUPERSEDED,
        }:
            if self.environment == "production":
                if (
                    self.production_canary_at is None
                    or self.owner_authorized_by is None
                    or re.fullmatch(_SAFE_QUALIFICATION_IDENTIFIER, self.owner_authorized_by)
                    is None
                ):
                    raise ValueError("provider_mcp_qualification_canary_required")
            elif self.production_canary_at is not None or self.owner_authorized_by is not None:
                raise ValueError("provider_mcp_qualification_canary_unexpected")

        if self.qualification_state in {
            QualificationState.DISABLED,
            QualificationState.SUPERSEDED,
        }:
            if (
                self.disable_reason is None
                or re.fullmatch(_SAFE_QUALIFICATION_IDENTIFIER, self.disable_reason) is None
            ):
                raise ValueError("provider_mcp_qualification_disable_reason_required")
        elif self.disable_reason is not None:
            raise ValueError("provider_mcp_qualification_disable_reason_unexpected")

        for field_name in type(self).model_fields:
            object.__setattr__(self, field_name, deep_freeze(getattr(self, field_name)))
        return self

    @classmethod
    def discovered(
        cls,
        *,
        provider: str,
        environment: str,
        provider_tool_name: str,
        normalized_capability: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        public_output_field_paths: tuple[str, ...] = (),
        response_shape_hash: str,
        required_permissions: tuple[str, ...],
    ) -> "ProviderMCPQualification":
        checked_public_paths = tuple(sorted(set(public_output_field_paths)))
        if len(checked_public_paths) != len(public_output_field_paths) or any(
            not _valid_public_output_field_path(path) for path in checked_public_paths
        ):
            raise ValueError("provider_mcp_public_output_field_paths_invalid")
        schema_hash = _qualification_schema_hash(input_schema, output_schema)
        version = _provider_mcp_capability_version_sha256(
            provider=provider,
            environment=environment,
            provider_tool_name=provider_tool_name,
            normalized_capability=normalized_capability,
            input_schema=input_schema,
            output_schema=output_schema,
            public_output_field_paths=checked_public_paths,
            schema_hash=schema_hash,
            response_shape_hash=response_shape_hash,
            required_permissions=required_permissions,
        )
        return cls(
            provider=provider,
            environment=environment,
            provider_tool_name=provider_tool_name,
            normalized_capability=normalized_capability,
            input_schema=input_schema,
            output_schema=output_schema,
            public_output_field_paths=checked_public_paths,
            schema_hash=schema_hash,
            response_shape_hash=response_shape_hash,
            required_permissions=required_permissions,
            capability_version_sha256=version,
        )


def _qualification_schema_hash(
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
) -> str:
    return hashlib.sha256(
        canonical_json({"input_schema": input_schema, "output_schema": output_schema}).encode(
            "utf-8"
        )
    ).hexdigest()


def _qualification_evidence_uri(
    *,
    provider: str,
    capability_version_sha256: str,
    evidence_revision_sha256: str,
) -> str:
    return (
        f"catalog://global/{provider}/qualifications/"
        f"{capability_version_sha256}-{evidence_revision_sha256}.json"
    )


def _provider_mcp_capability_version_sha256(
    *,
    provider: str,
    environment: str,
    provider_tool_name: str,
    normalized_capability: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    public_output_field_paths: tuple[str, ...] | None,
    schema_hash: str,
    response_shape_hash: str,
    required_permissions: tuple[str, ...],
) -> str:
    payload = {
        "provider": provider,
        "environment": environment,
        "provider_tool_name": provider_tool_name,
        "normalized_capability": normalized_capability,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "schema_hash": schema_hash,
        "response_shape_hash": response_shape_hash,
        "required_permissions": required_permissions,
    }
    if public_output_field_paths is not None:
        payload["public_output_field_paths"] = public_output_field_paths
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _valid_public_output_field_path(path: object) -> bool:
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        return False
    for segment in path[1:].split("/"):
        if not segment or ("*" in segment and segment != "*"):
            return False
        index = 0
        while index < len(segment):
            if segment[index] != "~":
                index += 1
                continue
            if index + 1 >= len(segment) or segment[index + 1] not in {"0", "1"}:
                return False
            index += 2
    return True


def revalidate_catalog_source(source: CatalogSource) -> CatalogSource:
    values = {name: getattr(source, name) for name in CatalogSource.model_fields}
    return CatalogSource.model_validate(values)


def revalidate_catalog_action(action: CatalogAction) -> CatalogAction:
    values = {name: getattr(action, name) for name in CatalogAction.model_fields}
    validated = CatalogAction.model_validate(values)
    validate_action_identity(validated)
    return validated


def _source_type(document: dict[str, Any]) -> str:
    if "openapi" in document:
        return "openapi3"
    if "swagger" in document:
        return "swagger2"
    info = document.get("info")
    if isinstance(info, dict) and "schema" in info:
        return "postman2.1"
    return "documentation"


def _imported_version(document: dict[str, Any]) -> str:
    info = document.get("info")
    if isinstance(info, dict):
        version = info.get("version")
        if isinstance(version, str) and version:
            return version
    version = document.get("version")
    return version if isinstance(version, str) and version else "unknown"
