"""Closed public schemas for Mercury V1 MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from mercury_tools.catalog.models import QualificationState
from mercury_tools.providers.models import (
    ConnectionReadiness,
    ProviderId,
)
from mercury_tools.workspaces.models import NextAllowedAction, WorkspaceRole

CAPABILITY_ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
HTTPS_URL_PATTERN = r"^https://[^\s]+$"


class V1PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


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


class DisconnectProviderInput(ConnectionInput):
    confirmation: Literal["DISCONNECT"]


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
    "SHA256_PATTERN",
    "StartProviderConnectionArguments",
    "StartProviderConnectionOutput",
    "V1Notice",
    "V1PublicModel",
    "V1SuccessEnvelope",
    "WorkspaceInput",
    "WorkspaceMembershipOutput",
    "start_provider_connection_input_schema",
]
