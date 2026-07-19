"""Explicit input contracts for the public Mercury MCP tools."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ConnectorId = Literal["flowaccount", "peak", "express", "custom", "generic_mcp"]
ConnectorEnvironment = Literal["production", "sandbox", "uat", "local", "gateway"]
SearchMode = Literal["hybrid", "keyword", "vector"]
AccountingSkillId = Literal[
    "accounts-payable-reconciliation-th",
    "accounts-receivable-reconciliation-th",
    "bank-settlement-reconciliation-th",
    "company-health-check-th",
    "connector-credential-setup-th",
    "connector-setup-guide-th",
    "flowaccount-connector-setup-th",
    "flowaccount-journal-posting-th",
    "invoice-review-th",
    "management-report-th",
    "marketplace-settlement-review-th",
    "mercury-flow-runner",
    "month-end-evidence-gathering-th",
    "peak-connector-setup-th",
    "vat-summary-th",
]


class StrictMcpInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
        description="Dotted ERP capability such as documents.invoice.list.",
    )
    accounting_use: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
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
        max_length=20_000,
        description="The user's accounting question or requested outcome.",
    )
    workspace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_048,
        description="Mercury public workspace id when workspace context is required.",
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


class InlineFlowSource(StrictMcpInput):
    source_type: Literal["flow_yaml"] = Field(description="Run one inline Mercury Flow.")
    flow_yaml: str = Field(
        min_length=1,
        max_length=500_000,
        description="Complete inline Mercury Flow YAML.",
    )
    workspace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_048,
        description="Workspace id required when the flow uses an ERP connector.",
    )


class FlowFilesSource(StrictMcpInput):
    source_type: Literal["flow_files"] = Field(description="Run an in-memory flow suite.")
    flow_files: list[FlowFileInput] = Field(
        min_length=1,
        max_length=50,
        description="Mercury Flow files, each with a relative path and YAML content.",
    )
    workspace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_048,
        description="Workspace id required when a selected flow uses an ERP connector.",
    )
    config_yaml: str | None = Field(
        default=None,
        max_length=500_000,
        description="Optional Mercury workspace config YAML for discovery and order.",
    )
    include_tags: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Select flows containing at least one exact tag in this list.",
    )
    exclude_tags: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Skip flows containing any exact tag in this list.",
    )
    continue_on_failure: bool = Field(
        default=True,
        description="Continue with later selected files after one flow fails.",
    )


class WorkspaceFlowSource(StrictMcpInput):
    source_type: Literal["workspace_flow"] = Field(description="Run one saved workspace flow.")
    workspace_id: str = Field(
        min_length=1,
        max_length=2_048,
        description="Mercury public workspace id that owns the saved flow.",
    )
    workspace_flow_id: str = Field(
        min_length=1,
        max_length=500,
        description="Saved Mercury flow id returned by list_workspace_flows.",
    )


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
    tags: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Search and organization tags for the saved flow.",
    )
