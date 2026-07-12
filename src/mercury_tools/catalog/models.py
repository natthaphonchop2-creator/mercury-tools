import hashlib
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal

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
        source_type: Literal["openapi3", "swagger2", "postman2.1", "documentation"]
        | None = None,
    ) -> "CatalogSource":
        sanitized_uri = sanitize_document(uri)
        sanitized_document = sanitize_document(document)
        sanitized_report = sanitize_document(report)
        source_hash = hashlib.sha256(
            canonical_json(
                {"document": sanitized_document, "report": sanitized_report}
            ).encode()
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
