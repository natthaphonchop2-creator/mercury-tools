"""Explicit input contracts for the public Mercury MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, model_validator

from mercury_tools.skills.catalog import (
    ACCOUNTING_SKILL_IDS,
    SkillConnectorId,
    SkillEnvironment,
)

ConnectorId = SkillConnectorId
ConnectorEnvironment = SkillEnvironment
ConnectorConnectionMode = Literal["native_mcp", "api_driver", "local_bridge"]
ConnectorUnlinkConfirmation = Literal["unlink"]
SearchMode = Literal["hybrid", "keyword", "vector"]
CAPABILITY_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
AccountingSkillId = Literal[*ACCOUNTING_SKILL_IDS]


class StrictMcpInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityObservation(StrictMcpInput):
    capability: str = Field(pattern=CAPABILITY_PATTERN, max_length=200)
    state: Literal[
        "observed",
        "provider_unavailable",
        "not_authorized",
        "validation_failed",
        "environment_mismatch",
    ]


class ConnectorValidationEvidence(StrictMcpInput):
    source: Literal[
        "native_mcp_safe_read",
        "api_driver_safe_probe",
        "local_bridge_safe_probe",
    ]
    status: Literal["succeeded", "failed"]
    observed_at: datetime
    evidence_ref: str = Field(pattern=r"^evidence_[0-9a-z_-]{8,128}$")
    provider_tool_name: str | None = Field(default=None, max_length=200)
    capabilities: list[CapabilityObservation] = Field(min_length=1, max_length=500)


ConnectorValidationEvidenceInput = SkipValidation[ConnectorValidationEvidence]


class LegacyConnectorSetupRequest(StrictMcpInput):
    """Strict compatibility body for the opt-in legacy setup route."""

    connector_id: ConnectorId
    connection_mode: ConnectorConnectionMode
    environment: ConnectorEnvironment
    company_ref: str | None = Field(default=None, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    external_server_name: str | None = Field(default=None, max_length=200)


class KnowledgeSearchFilters(StrictMcpInput):
    """Supported filters for Mercury knowledge retrieval."""

    jurisdiction: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Jurisdiction selector such as TH or international.",
    )
    connector: ConnectorId | None = Field(
        default=None,
        description="ERP connector whose documentation should be searched.",
    )
    doc_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Document category such as tax, accounting_standard, or endpoint_dictionary.",
    )
    review_status: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Knowledge review state, for example reviewed or draft.",
    )
    effective_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Maximum effective date in YYYY-MM-DD format.",
    )
    action_id: str | None = Field(
        default=None,
        pattern=r"^act_[0-9a-f]{24}$",
        description="Validated endpoint catalog action id.",
    )
    version_id: str | None = Field(
        default=None,
        pattern=r"^av_[0-9a-f]{64}$",
        description="Validated endpoint catalog version id.",
    )
    environment: Literal["sandbox", "test", "uat", "production"] | None = Field(
        default=None,
        description="Environment represented by endpoint validation evidence.",
    )
    capability: str | None = Field(
        default=None,
        pattern=CAPABILITY_PATTERN,
        description="Dotted ERP capability such as documents.invoice.list.",
    )
    accounting_use: str | None = Field(
        default=None,
        pattern=CAPABILITY_PATTERN,
        description="Dotted accounting use case represented by the evidence.",
    )


class SkillInputParameter(StrictMcpInput):
    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$",
        description="Skill-specific parameter name.",
    )
    value: str = Field(
        max_length=10_000,
        description="Non-secret parameter value. Never include ERP credentials.",
    )


class AccountingSkillInputs(StrictMcpInput):
    """Common, non-secret inputs accepted by accounting guidance skills."""

    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
        description="The user's accounting question or requested outcome.",
    )
    connector_id: ConnectorId | None = Field(
        default=None,
        description="ERP connector relevant to the skill.",
    )
    environment: ConnectorEnvironment | None = Field(
        default=None,
        description="ERP environment relevant to the skill.",
    )
    company_name: str | None = Field(
        default=None,
        max_length=500,
        description="Optional display name used in the generated guidance.",
    )
    period_start: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Start date in YYYY-MM-DD format.",
    )
    period_end: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="End date in YYYY-MM-DD format.",
    )
    month: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}$",
        description="Accounting month in YYYY-MM format.",
    )
    document_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="Document ids selected for review; do not include secret values.",
    )
    objective: str | None = Field(
        default=None,
        max_length=5_000,
        description="Business objective or expected report outcome.",
    )
    notes: str | None = Field(
        default=None,
        max_length=10_000,
        description="Additional non-secret context for the skill.",
    )
    parameters: list[SkillInputParameter] = Field(
        default_factory=list,
        max_length=100,
        description="Additional named, non-secret parameters required by a specific skill.",
    )

    @model_validator(mode="after")
    def validate_unique_parameters(self) -> AccountingSkillInputs:
        names = [parameter.name for parameter in self.parameters]
        explicit_fields = set(self.model_fields_set) - {"parameters"}
        if len(set(names)) != len(names) or set(names) & explicit_fields:
            raise ValueError("accounting_skill_parameter_duplicate")
        return self


# Keep the host-visible envelope schema explicit while all raw nested values are
# validated in the sanitized tool handler.
AccountingSkillInputsInput = SkipValidation[AccountingSkillInputs]


class FlowFileInput(StrictMcpInput):
    path: str = Field(
        min_length=1,
        max_length=500,
        description="Safe relative YAML path, for example flows/company-health.yaml.",
    )
    flow_yaml: str = Field(
        min_length=1,
        max_length=500_000,
        description="Complete Mercury Flow YAML content for this file.",
    )


FlowFiles = Annotated[list[FlowFileInput], Field(min_length=1, max_length=50)]
InlineFlowYaml = SkipValidation[
    Annotated[
        str,
        Field(
            min_length=1,
            max_length=500_000,
            description="Complete inline Mercury Flow YAML.",
        ),
    ]
]


class FlowEnvironmentValue(StrictMcpInput):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    value: str = Field(max_length=10_000)


# Preserve the bounded array and item schema while validating the entire raw
# value inside the tool handler, where invalid inputs receive a fixed response.
FlowEnvironmentValues = SkipValidation[
    Annotated[
        list[FlowEnvironmentValue],
        Field(max_length=100),
    ]
]
FlowTag = Annotated[str, Field(min_length=1, max_length=100)]
FlowTags = Annotated[list[FlowTag], Field(max_length=100)]


class InlineFlowSource(StrictMcpInput):
    source_type: Literal["flow_yaml"]
    flow_yaml: str = Field(min_length=1, max_length=500_000)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=2_048)


class FlowFilesSource(StrictMcpInput):
    source_type: Literal["flow_files"]
    flow_files: list[FlowFileInput] = Field(min_length=1, max_length=50)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=2_048)
    config_yaml: str | None = Field(default=None, max_length=500_000)
    include_tags: list[str] = Field(default_factory=list, max_length=100)
    exclude_tags: list[str] = Field(default_factory=list, max_length=100)
    continue_on_failure: bool = True


class WorkspaceFlowSource(StrictMcpInput):
    source_type: Literal["workspace_flow"]
    workspace_id: str = Field(min_length=1, max_length=2_048)
    workspace_flow_id: str = Field(min_length=1, max_length=500)


MercuryFlowSource = Annotated[
    InlineFlowSource | FlowFilesSource | WorkspaceFlowSource,
    Field(discriminator="source_type"),
]


class WorkspaceFlowEnvironment(StrictMcpInput):
    connector_id: ConnectorId | None = Field(
        default=None,
        description="Connector selected for this saved flow.",
    )
    environment: ConnectorEnvironment | None = Field(
        default=None,
        description="Connector environment selected for this saved flow.",
    )
    connection_mode: ConnectorConnectionMode | None = Field(
        default=None,
        description="Connector connection mode selected for this saved flow.",
    )


class WorkspaceFlowMetadata(StrictMcpInput):
    source: Literal["mcp", "plugin", "import", "user"] = Field(
        default="mcp",
        description="Origin of the saved flow definition.",
    )
    connector_id: ConnectorId | None = Field(
        default=None,
        description="Connector required by the saved flow.",
    )
    environment: ConnectorEnvironment | None = Field(
        default=None,
        description="Connector environment required by the saved flow.",
    )
    connection_mode: ConnectorConnectionMode | None = Field(
        default=None,
        description="Connector connection mode required by the saved flow.",
    )
    required_capabilities: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="Dotted ERP capabilities required before the flow can run.",
    )
    env: WorkspaceFlowEnvironment | None = Field(
        default=None,
        description="Non-secret connector selectors persisted with the flow.",
    )
    category: str | None = Field(
        default=None,
        max_length=200,
        description="Optional flow category.",
    )
    summary: str | None = Field(
        default=None,
        max_length=2_000,
        description="Short, non-secret description of the flow.",
    )
    tags: list[FlowTag] = Field(
        default_factory=list,
        max_length=100,
        description="Search and organization tags for the saved flow.",
    )
