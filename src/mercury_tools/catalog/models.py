import hashlib
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import (
    build_source_id,
    canonical_json,
    sanitize_document,
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
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    connector_id: str
    source_type: Literal["openapi3", "swagger2", "postman2.1", "documentation"]
    source_uri: str
    source_hash: str
    imported_version: str
    imported_at: datetime
    driver_suggestion: dict[str, Any] = Field(default_factory=dict)
    sanitization: dict[str, Any]

    @classmethod
    def from_document(
        cls,
        uri: str,
        connector_id: str,
        document: dict[str, Any],
        report: dict[str, Any],
    ) -> "CatalogSource":
        sanitized_document = sanitize_document(document)
        sanitized_report = sanitize_document(report)
        source_hash = hashlib.sha256(
            canonical_json(
                {"document": sanitized_document, "report": sanitized_report}
            ).encode()
        ).hexdigest()
        return cls(
            source_id=build_source_id(connector_id, uri, source_hash),
            connector_id=connector_id,
            source_type=_source_type(document),
            source_uri=uri,
            source_hash=source_hash,
            imported_version=_imported_version(document),
            imported_at=datetime.now(UTC),
            sanitization={
                "document": sanitized_document,
                "report": sanitized_report,
            },
        )


class CatalogAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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

    @model_validator(mode="after")
    def validate_required_confirmations(self) -> "CatalogAction":
        if self.required_confirmations != int(self.risk_tier):
            raise ValueError("required_confirmations must match risk_tier")
        return self


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
