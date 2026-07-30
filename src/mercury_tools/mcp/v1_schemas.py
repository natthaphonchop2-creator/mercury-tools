"""Closed public schemas for Mercury V1 MCP tools."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    create_model,
    model_validator,
)

from mercury_tools.catalog.models import QualificationState
from mercury_tools.providers.models import (
    ConnectionReadiness,
    ProviderId,
)
from mercury_tools.skills.catalog import (
    ACCOUNTING_SKILL_CATALOG,
    HostConnectedEvidenceInput,
    SkillConnectionMode,
    SkillConnectorId,
    SkillEnvironment,
    published_accounting_skill,
)
from mercury_tools.workspaces.models import NextAllowedAction, WorkspaceRole

CAPABILITY_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
HTTPS_URL_PATTERN = r"^https://[^\s]+$"


class V1PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


def non_nullable_public_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Publish optional properties by omission without accepting JSON null."""

    def normalize(value: Any) -> Any:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value

        normalized = {key: normalize(item) for key, item in value.items()}
        variants = normalized.get("anyOf")
        if isinstance(variants, list):
            non_null = [
                variant
                for variant in variants
                if not (isinstance(variant, dict) and variant.get("type") == "null")
            ]
            if len(non_null) != len(variants):
                normalized.pop("default", None)
                if len(non_null) == 1 and isinstance(non_null[0], dict):
                    annotations = {key: item for key, item in normalized.items() if key != "anyOf"}
                    return {**non_null[0], **annotations}
                normalized["anyOf"] = non_null
        schema_type = normalized.get("type")
        if isinstance(schema_type, list) and "null" in schema_type:
            non_null_types = [item for item in schema_type if item != "null"]
            normalized.pop("default", None)
            normalized["type"] = non_null_types[0] if len(non_null_types) == 1 else non_null_types
        return normalized

    return normalize(deepcopy(dict(schema)))


class GetMercuryContextInput(V1PublicModel):
    pass


class WorkspaceMembershipOutput(V1PublicModel):
    tenant_id: UUID
    tenant_display_name: str = Field(min_length=1, max_length=200)
    workspace_id: UUID
    workspace_display_name: str = Field(min_length=1, max_length=200)
    role: WorkspaceRole


class GetMercuryContextOutput(V1PublicModel):
    status: Literal["ok"]
    active_workspace_id: UUID
    memberships: list[WorkspaceMembershipOutput] = Field(
        min_length=1,
        max_length=100,
    )
    next_allowed_actions: list[NextAllowedAction] = Field(max_length=20)


class FlowAccountConnectionStart(V1PublicModel):
    workspace_id: UUID
    provider: Literal["flowaccount"]
    environment: Literal["sandbox", "production"]


class PeakConnectionStart(V1PublicModel):
    workspace_id: UUID
    provider: Literal["peak"]
    environment: Literal["uat", "production"]


ProviderConnectionStart: TypeAlias = Annotated[
    FlowAccountConnectionStart | PeakConnectionStart,
    Field(discriminator="provider"),
]


class StartProviderConnectionArguments(ArgModelBase):
    """Runtime argument model for the discriminated public start schema."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    workspace_id: UUID
    provider: Literal["flowaccount", "peak"]
    environment: Literal["sandbox", "uat", "production"]

    @model_validator(mode="after")
    def require_provider_environment_pair(self) -> StartProviderConnectionArguments:
        TypeAdapter(ProviderConnectionStart).validate_python(self.model_dump())
        return self


def start_provider_connection_input_schema() -> dict[str, object]:
    """Return the root-level provider/environment discriminated input schema."""

    schema = TypeAdapter(ProviderConnectionStart).json_schema()
    schema["title"] = "start_provider_connectionArguments"
    return schema


class WorkspaceInput(V1PublicModel):
    workspace_id: UUID


class ConnectionInput(V1PublicModel):
    workspace_id: UUID
    connection_id: UUID


class CapabilitySchemaInput(V1PublicModel):
    workspace_id: UUID
    capability_id: str = Field(
        min_length=3,
        max_length=200,
        pattern=CAPABILITY_ID_PATTERN,
    )
    capability_version: str = Field(pattern=SHA256_PATTERN)


class V1Notice(V1PublicModel):
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)


class V1SuccessEnvelope(V1PublicModel):
    status: Literal["ok"] = "ok"
    warnings: list[V1Notice] = Field(default_factory=list, max_length=50)
    accountant_review_points: list[V1Notice] = Field(default_factory=list, max_length=50)
    next_allowed_actions: list[str] = Field(
        default_factory=list,
        max_length=20,
    )


class KnowledgeFiltersInput(V1PublicModel):
    jurisdiction: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    doc_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    review_status: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    effective_on: date | None = None
    source_id: UUID | None = None
    capability_version: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )


class KnowledgeCitationOutput(V1PublicModel):
    source_id: UUID
    source_title: str = Field(min_length=1, max_length=500)
    source_uri: str = Field(min_length=1, max_length=2_000)
    source_url: str | None = Field(default=None, max_length=4_000)
    heading: str | None = Field(default=None, max_length=1_000)


class KnowledgeResultOutput(V1PublicModel):
    chunk_id: UUID
    document_id: UUID
    document_uri: str = Field(min_length=1, max_length=2_000)
    chunk_uri: str = Field(min_length=1, max_length=2_000)
    text: str = Field(min_length=1, max_length=100_000)
    score: float = Field(ge=0)
    jurisdiction: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=200)
    doc_type: str | None = Field(default=None, max_length=200)
    review_status: Literal["reviewed"]
    effective_on: date | None = None
    citation: KnowledgeCitationOutput


class SearchKnowledgeOutput(V1SuccessEnvelope):
    workspace_id: UUID
    query: str = Field(min_length=1, max_length=2_000)
    data: list[KnowledgeResultOutput] = Field(min_length=1, max_length=20)


class RetrieveContextPackOutput(V1SuccessEnvelope):
    workspace_id: UUID
    query: str = Field(min_length=1, max_length=2_000)
    skill_id: str | None = Field(default=None, max_length=200)
    skill_version: str | None = Field(default=None, max_length=64)
    data: list[KnowledgeResultOutput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_complete_skill_identity(self) -> RetrieveContextPackOutput:
        if (self.skill_id is None) != (self.skill_version is None):
            raise ValueError("published_skill_identity_invalid")
        return self


class SkillCapabilityBindingOutput(V1PublicModel):
    skill_capability: str = Field(pattern=CAPABILITY_ID_PATTERN, max_length=200)
    capability_id: str = Field(pattern=CAPABILITY_ID_PATTERN, max_length=200)
    capability_version: str = Field(pattern=SHA256_PATTERN)


class RunAccountingSkillData(V1PublicModel):
    skill_id: str = Field(min_length=1, max_length=200)
    skill_version: str = Field(min_length=1, max_length=64)
    output_schema_name: str = Field(min_length=1, max_length=200)
    capability_bindings: list[SkillCapabilityBindingOutput] = Field(max_length=100)
    knowledge: list[KnowledgeResultOutput] = Field(max_length=20)
    host_evidence_count: int = Field(ge=0, le=100)
    allowed_action_classes: list[Literal["provider_read"]] = Field(max_length=10)
    blocked_action_classes: list[
        Literal["provider_create", "provider_update", "provider_delete"]
    ] = Field(max_length=10)


class RunAccountingSkillOutput(V1SuccessEnvelope):
    workspace_id: UUID
    connection_id: UUID | None = None
    data: RunAccountingSkillData


class RunAccountingSkillArguments(ArgModelBase):
    """Flat runtime model validated against one exact published Skill branch."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    workspace_id: UUID
    connection_id: UUID | None = None
    skill_id: str
    skill_version: str
    query: str = Field(min_length=1, max_length=20_000)
    connector_id: SkillConnectorId | None = None
    connection_mode: SkillConnectionMode | None = None
    environment: SkillEnvironment | None = None
    company_name: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=10_000)
    host_evidence: list[HostConnectedEvidenceInput] = Field(
        default_factory=list,
        max_length=100,
    )
    period_start: date | None = None
    period_end: date | None = None
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    objective: str | None = Field(default=None, max_length=5_000)
    source_reference: str | None = Field(default=None, max_length=500)
    marketplace_source: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_exact_published_branch(self) -> RunAccountingSkillArguments:
        skill = published_accounting_skill(self.skill_id, self.skill_version)
        if skill is None:
            raise ValueError("published_skill_not_found")
        if skill.required_capabilities and self.connection_id is None:
            raise ValueError("provider_connection_required")

        controls = {"workspace_id", "connection_id", "skill_id", "skill_version"}
        unexpected = self.model_fields_set - controls - set(skill.input_schema.model_fields)
        if unexpected:
            raise ValueError("published_skill_input_invalid")
        values = {
            field: getattr(self, field)
            for field in skill.input_schema.model_fields
            if field in self.model_fields_set or field in {"query", "host_evidence"}
        }
        skill.input_schema.model_validate(values)
        return self


def _published_skill_branch_models() -> tuple[type[BaseModel], ...]:
    models: list[type[BaseModel]] = []
    for skill in ACCOUNTING_SKILL_CATALOG:
        model_name = "".join(part.title() for part in skill.skill_id.split("-"))
        connection_field: tuple[Any, Any] = (
            (UUID, ...) if skill.required_capabilities else (UUID | None, None)
        )
        models.append(
            create_model(
                f"{model_name}V{skill.skill_version.replace('.', '_')}Input",
                __base__=skill.input_schema,
                workspace_id=(UUID, ...),
                connection_id=connection_field,
                skill_id=(Literal[skill.skill_id], ...),
                skill_version=(Literal[skill.skill_version], ...),
            )
        )
    return tuple(models)


def run_accounting_skill_input_schema() -> dict[str, object]:
    """Generate the root oneOf from the same immutable Skill input models."""

    definitions: dict[str, object] = {}
    branches: list[dict[str, str]] = []
    discriminator_mapping: dict[str, str] = {}
    for model in _published_skill_branch_models():
        schema = model.model_json_schema()
        nested = schema.pop("$defs", {})
        for name, definition in nested.items():
            existing = definitions.get(name)
            if existing is not None and existing != definition:
                raise RuntimeError("published_skill_schema_collision")
            definitions[name] = definition
        definitions[model.__name__] = schema
        reference = f"#/$defs/{model.__name__}"
        branches.append({"$ref": reference})
        skill_id = schema["properties"]["skill_id"]["const"]
        discriminator_mapping[skill_id] = reference
    return non_nullable_public_schema(
        {
            "$defs": definitions,
            "discriminator": {
                "propertyName": "skill_id",
                "mapping": discriminator_mapping,
            },
            "oneOf": branches,
            "title": "run_accounting_skillArguments",
        }
    )


class DisconnectProviderInput(ConnectionInput):
    confirmation: Literal["DISCONNECT"]


class ProviderReadEnvelope(V1SuccessEnvelope):
    """Internal result model for a catalog-specific generated read contract."""

    workspace_id: UUID
    connection_id: UUID
    provider: ProviderId
    company_display_name: str = Field(min_length=1, max_length=200)
    environment: Literal["sandbox", "uat", "production"]
    capability_id: str = Field(pattern=CAPABILITY_ID_PATTERN, max_length=200)
    capability_version: str = Field(pattern=SHA256_PATTERN)
    data: dict[str, JsonValue]


class FlowAccountProviderOutput(V1PublicModel):
    provider: Literal["flowaccount"]
    connection_method: Literal["oauth2_pkce"]
    environments: list[Literal["sandbox", "production"]] = Field(
        min_length=2,
        max_length=2,
    )


class PeakProviderOutput(V1PublicModel):
    provider: Literal["peak"]
    connection_method: Literal["provider_credentials"]
    environments: list[Literal["uat", "production"]] = Field(
        min_length=2,
        max_length=2,
    )


AccountingProviderOutput: TypeAlias = Annotated[
    FlowAccountProviderOutput | PeakProviderOutput,
    Field(discriminator="provider"),
]


class ListAccountingProvidersOutput(V1SuccessEnvelope):
    data: list[AccountingProviderOutput] = Field(min_length=2, max_length=2)


class FlowAccountConnectionStartData(V1PublicModel):
    provider: Literal["flowaccount"]
    environment: Literal["sandbox", "production"]
    authorization_url: str = Field(
        min_length=9,
        max_length=4_000,
        pattern=HTTPS_URL_PATTERN,
    )
    expires_at: datetime


class PeakConnectionStartData(V1PublicModel):
    provider: Literal["peak"]
    environment: Literal["uat", "production"]
    setup_url: str = Field(
        min_length=9,
        max_length=4_000,
        pattern=HTTPS_URL_PATTERN,
    )
    expires_at: datetime


class StartProviderConnectionOutput(V1SuccessEnvelope):
    workspace_id: UUID
    provider: ProviderId
    environment: Literal["sandbox", "uat", "production"]
    data: Annotated[
        FlowAccountConnectionStartData | PeakConnectionStartData,
        Field(discriminator="provider"),
    ]


class FlowAccountConnectionOutput(V1PublicModel):
    connection_id: UUID
    provider: Literal["flowaccount"]
    environment: Literal["sandbox", "production"]
    account_display_name: str = Field(min_length=1, max_length=200)
    authorization_method: Literal["oauth2_pkce"]
    granted_permissions: list[str] = Field(max_length=200)
    readiness: ConnectionReadiness
    revision: int = Field(ge=1)
    provider_revocation_required: bool


class PeakConnectionOutput(V1PublicModel):
    connection_id: UUID
    provider: Literal["peak"]
    environment: Literal["uat", "production"]
    account_display_name: str = Field(min_length=1, max_length=200)
    authorization_method: Literal["provider_credentials"]
    granted_permissions: list[str] = Field(max_length=200)
    readiness: ConnectionReadiness
    revision: int = Field(ge=1)
    provider_revocation_required: bool


ProviderConnectionOutput: TypeAlias = Annotated[
    FlowAccountConnectionOutput | PeakConnectionOutput,
    Field(discriminator="provider"),
]


class ListProviderConnectionsOutput(V1SuccessEnvelope):
    workspace_id: UUID
    data: list[ProviderConnectionOutput] = Field(max_length=100)


class ConnectorStatusData(V1PublicModel):
    connection: ProviderConnectionOutput
    missing_qualification_capabilities: list[str] = Field(
        max_length=4,
    )


class ConnectorStatusOutput(V1SuccessEnvelope):
    workspace_id: UUID
    connection_id: UUID
    provider: ProviderId
    environment: Literal["sandbox", "uat", "production"]
    data: ConnectorStatusData


class ProviderCapabilityOutput(V1PublicModel):
    capability_id: str = Field(pattern=CAPABILITY_ID_PATTERN, max_length=200)
    capability_version: str = Field(pattern=SHA256_PATTERN)
    qualification_state: QualificationState
    availability: Literal["enabled", "unavailable"]
    status_detail: Literal[
        "enabled",
        "connection_not_ready",
        "capability_unavailable",
        "capability_unreviewed",
        "insufficient_evidence",
    ]


class ListProviderCapabilitiesOutput(V1SuccessEnvelope):
    workspace_id: UUID
    connection_id: UUID
    provider: ProviderId
    environment: Literal["sandbox", "uat", "production"]
    data: list[ProviderCapabilityOutput] = Field(max_length=500)


class ReviewedCapabilitySchemaOutput(V1PublicModel):
    capability_id: str = Field(pattern=CAPABILITY_ID_PATTERN, max_length=200)
    capability_version: str = Field(pattern=SHA256_PATTERN)
    qualification_state: QualificationState
    schema_sha256: str = Field(pattern=SHA256_PATTERN)
    response_shape_sha256: str = Field(pattern=SHA256_PATTERN)
    input_schema_json: str = Field(min_length=2, max_length=1_000_000)
    output_schema_json: str = Field(min_length=2, max_length=1_000_000)


class GetCapabilitySchemaOutput(V1SuccessEnvelope):
    workspace_id: UUID
    capability_id: str = Field(pattern=CAPABILITY_ID_PATTERN, max_length=200)
    capability_version: str = Field(pattern=SHA256_PATTERN)
    data: ReviewedCapabilitySchemaOutput


class FlowAccountDisconnectedData(V1PublicModel):
    provider: Literal["flowaccount"]
    status: Literal["disconnected"]
    local_credentials_deleted: Literal[True]
    remote_revocation_status: Literal[
        "revoked",
        "not_supported",
        "already_disconnected",
    ]
    deleted_envelope_count: int = Field(ge=0, le=16)
    provider_revocation_required: Literal[False]
    revision: int = Field(ge=1)


class FlowAccountRevocationRequiredData(V1PublicModel):
    provider: Literal["flowaccount"]
    status: Literal["provider_revocation_required"]
    local_credentials_deleted: Literal[True]
    remote_revocation_status: Literal["failed", "already_disconnected"]
    deleted_envelope_count: int = Field(ge=0, le=16)
    provider_revocation_required: Literal[True]
    revision: int = Field(ge=1)


FlowAccountDisconnectData: TypeAlias = Annotated[
    FlowAccountDisconnectedData | FlowAccountRevocationRequiredData,
    Field(discriminator="status"),
]


class PeakDisconnectData(V1PublicModel):
    provider: Literal["peak"]
    status: Literal["provider_revocation_required"]
    local_credentials_deleted: Literal[True]
    instruction: Literal["Revoke this credential set in PEAK Account."]


DisconnectProviderData: TypeAlias = Annotated[
    FlowAccountDisconnectData | PeakDisconnectData,
    Field(discriminator="provider"),
]


class DisconnectProviderOutput(V1SuccessEnvelope):
    workspace_id: UUID
    connection_id: UUID
    provider: ProviderId
    environment: Literal["sandbox", "uat", "production"]
    data: DisconnectProviderData


__all__ = [
    "AccountingProviderOutput",
    "CAPABILITY_ID_PATTERN",
    "CapabilitySchemaInput",
    "ConnectionInput",
    "ConnectorStatusData",
    "ConnectorStatusOutput",
    "DisconnectProviderData",
    "DisconnectProviderInput",
    "DisconnectProviderOutput",
    "FlowAccountDisconnectedData",
    "FlowAccountDisconnectData",
    "FlowAccountRevocationRequiredData",
    "FlowAccountConnectionOutput",
    "FlowAccountConnectionStart",
    "FlowAccountConnectionStartData",
    "FlowAccountProviderOutput",
    "GetCapabilitySchemaOutput",
    "GetMercuryContextInput",
    "GetMercuryContextOutput",
    "HTTPS_URL_PATTERN",
    "HostConnectedEvidenceInput",
    "KnowledgeCitationOutput",
    "KnowledgeFiltersInput",
    "KnowledgeResultOutput",
    "ListAccountingProvidersOutput",
    "ListProviderCapabilitiesOutput",
    "ListProviderConnectionsOutput",
    "PeakConnectionOutput",
    "PeakConnectionStart",
    "PeakConnectionStartData",
    "PeakDisconnectData",
    "PeakProviderOutput",
    "ProviderCapabilityOutput",
    "ProviderConnectionOutput",
    "ProviderReadEnvelope",
    "ProviderConnectionStart",
    "ReviewedCapabilitySchemaOutput",
    "RetrieveContextPackOutput",
    "RunAccountingSkillArguments",
    "RunAccountingSkillData",
    "RunAccountingSkillOutput",
    "SHA256_PATTERN",
    "SearchKnowledgeOutput",
    "SkillCapabilityBindingOutput",
    "StartProviderConnectionArguments",
    "StartProviderConnectionOutput",
    "V1Notice",
    "V1PublicModel",
    "V1SuccessEnvelope",
    "WorkspaceInput",
    "WorkspaceMembershipOutput",
    "non_nullable_public_schema",
    "run_accounting_skill_input_schema",
    "start_provider_connection_input_schema",
]
