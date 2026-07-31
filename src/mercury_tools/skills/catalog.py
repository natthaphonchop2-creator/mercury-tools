"""Immutable accounting Skill definitions and generated public metadata."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from mercury_tools.catalog.identity import canonical_json

SkillConnectorId = Literal["flowaccount", "peak", "express", "custom", "generic_mcp"]
SkillConnectionMode = Literal["native_mcp", "api_driver", "local_bridge"]
SkillEnvironment = Literal[
    "production",
    "sandbox",
    "uat",
    "local",
    "gateway",
    "user_supplied",
]
HostEvidenceSource = Literal[
    "google_sheets",
    "google_drive",
    "gmail",
    "host_mcp",
]
HostEvidenceType = Literal[
    "business_record",
    "document_excerpt",
    "message_fact",
]
_V1_SKILL_READ_CAPABILITY_ROUTES = MappingProxyType(
    {
        "company.read": ("provider_profile.get",),
        "documents.invoice.read": ("documents.invoice.get",),
        "provider_profile.get": ("provider_profile.get",),
        "documents.invoice.list": ("documents.invoice.list",),
        "documents.invoice.get": ("documents.invoice.get",),
    }
)
_V1_SKILL_REQUEST_KIND = MappingProxyType(
    {
        "provider_profile.get": "empty",
        "documents.invoice.list": "invoice_list",
        "documents.invoice.get": "invoice_get",
    }
)


def v1_skill_read_capabilities(capability: str) -> tuple[str, ...]:
    """Map one provider-neutral Skill requirement to exact V1 read authority."""

    if not isinstance(capability, str):
        return ()
    return _V1_SKILL_READ_CAPABILITY_ROUTES.get(capability.strip(), ())


@dataclass(frozen=True, slots=True)
class SkillReadMapping:
    """One Git-canonical required Skill read and its deterministic result fact."""

    skill_capability: str
    capability_id: Literal[
        "provider_profile.get",
        "documents.invoice.list",
        "documents.invoice.get",
    ]
    request_kind: Literal["empty", "invoice_list", "invoice_get"]
    result_fact_name: str

    def __post_init__(self) -> None:
        if (
            self.capability_id not in v1_skill_read_capabilities(self.skill_capability)
            or self.request_kind != _V1_SKILL_REQUEST_KIND[self.capability_id]
            or re.fullmatch(r"[a-z][a-z0-9_]{0,99}", self.result_fact_name) is None
        ):
            raise ValueError("skill_read_mapping_invalid")

    def published_projection(self) -> dict[str, str]:
        return {
            "skill_capability": self.skill_capability,
            "capability_id": self.capability_id,
            "request_kind": self.request_kind,
            "result_fact_name": self.result_fact_name,
        }


class _HostBusinessFactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class HostAmountFactInput(_HostBusinessFactInput):
    field: Literal[
        "invoice_total",
        "subtotal_amount",
        "tax_amount",
        "withholding_tax_amount",
        "paid_amount",
        "outstanding_amount",
    ]
    value: Decimal = Field(
        ge=Decimal("-999999999999999.9999"),
        le=Decimal("999999999999999.9999"),
        max_digits=19,
        decimal_places=4,
        allow_inf_nan=False,
    )


class HostRateFactInput(_HostBusinessFactInput):
    field: Literal["vat_rate", "withholding_tax_rate"]
    value: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("100"),
        max_digits=7,
        decimal_places=4,
        allow_inf_nan=False,
    )


class HostDateFactInput(_HostBusinessFactInput):
    field: Literal[
        "document_date",
        "due_date",
        "payment_date",
        "period_start",
        "period_end",
    ]
    value: date


class HostCountFactInput(_HostBusinessFactInput):
    field: Literal["document_count", "line_item_count", "days_overdue"]
    value: int = Field(ge=0, le=1_000_000_000)


class HostBooleanFactInput(_HostBusinessFactInput):
    field: Literal["is_paid", "is_overdue", "is_tax_invoice"]
    value: bool


class HostCurrencyFactInput(_HostBusinessFactInput):
    field: Literal["currency_code"]
    value: str = Field(pattern=r"^[A-Z]{3}$")


class HostDocumentTypeFactInput(_HostBusinessFactInput):
    field: Literal["document_type"]
    value: Literal[
        "invoice",
        "tax_invoice",
        "receipt",
        "credit_note",
        "debit_note",
        "withholding_tax",
        "journal",
        "settlement",
    ]


class HostStatusFactInput(_HostBusinessFactInput):
    field: Literal["document_status", "payment_status"]
    value: Literal[
        "draft",
        "issued",
        "sent",
        "paid",
        "partially_paid",
        "overdue",
        "void",
        "cancelled",
        "unknown",
    ]


HostBusinessFactInput: TypeAlias = Annotated[
    HostAmountFactInput
    | HostRateFactInput
    | HostDateFactInput
    | HostCountFactInput
    | HostBooleanFactInput
    | HostCurrencyFactInput
    | HostDocumentTypeFactInput
    | HostStatusFactInput,
    Field(discriminator="field"),
]


class HostConnectedEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source: HostEvidenceSource
    evidence_type: HostEvidenceType
    source_reference: UUID = Field(description="Host-owned evidence record UUID.")
    facts: list[HostBusinessFactInput] = Field(min_length=1, max_length=100)


class _StrictSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=20_000)
    connector_id: SkillConnectorId | SkipJsonSchema[None] = None
    connection_mode: SkillConnectionMode | SkipJsonSchema[None] = None
    environment: SkillEnvironment | SkipJsonSchema[None] = None
    company_name: str | SkipJsonSchema[None] = Field(default=None, max_length=500)
    notes: str | SkipJsonSchema[None] = Field(default=None, max_length=10_000)
    host_evidence: list[HostConnectedEvidenceInput] = Field(
        default_factory=list,
        max_length=100,
    )


class _PeriodRangeSkillInput(_StrictSkillInput):
    period_start: date | SkipJsonSchema[None] = None
    period_end: date | SkipJsonSchema[None] = None

    @model_validator(mode="after")
    def validate_period(self) -> _PeriodRangeSkillInput:
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_end < self.period_start
        ):
            raise ValueError("period_end_before_period_start")
        return self


class CompanyHealthSkillInput(_PeriodRangeSkillInput):
    pass


class VatSummarySkillInput(_PeriodRangeSkillInput):
    month: str | SkipJsonSchema[None] = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class InvoiceReviewSkillInput(_PeriodRangeSkillInput):
    document_ids: list[str] = Field(default_factory=list, max_length=200)


class ManagementReportSkillInput(_PeriodRangeSkillInput):
    objective: str | SkipJsonSchema[None] = Field(default=None, max_length=5_000)


class ConnectorSetupSkillInput(_StrictSkillInput):
    pass


class FlowRunnerSkillInput(_StrictSkillInput):
    objective: str | SkipJsonSchema[None] = Field(default=None, max_length=5_000)


class JournalPostingSkillInput(_PeriodRangeSkillInput):
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    objective: str | SkipJsonSchema[None] = Field(default=None, max_length=5_000)


class ReconciliationSkillInput(_PeriodRangeSkillInput):
    source_reference: str | SkipJsonSchema[None] = Field(default=None, max_length=500)


class MarketplaceSettlementSkillInput(ReconciliationSkillInput):
    marketplace_source: str | SkipJsonSchema[None] = Field(default=None, max_length=200)


class MonthEndEvidenceSkillInput(_StrictSkillInput):
    month: str | SkipJsonSchema[None] = Field(default=None, pattern=r"^\d{4}-\d{2}$")


@dataclass(frozen=True, slots=True)
class AccountingSkillDefinition:
    skill_id: str
    title: str
    category: str
    summary: str
    required_capabilities: tuple[str, ...]
    optional_capabilities: tuple[str, ...]
    required_connectors: tuple[str, ...]
    input_schema: type[BaseModel]
    output_schema_name: str
    read_mappings: tuple[SkillReadMapping, ...] = ()
    skill_version: str = "0.1.0"
    allowed_action_classes: tuple[str, ...] = ("provider_read",)
    blocked_action_classes: tuple[str, ...] = (
        "provider_create",
        "provider_update",
        "provider_delete",
    )
    evidence_requirements: tuple[str, ...] = (
        "business_fact",
        "knowledge_source",
        "citation",
    )
    knowledge_filters: tuple[tuple[str, str], ...] = (
        ("jurisdiction", "TH"),
        ("review_status", "reviewed"),
    )

    def __post_init__(self) -> None:
        mapped_capabilities = tuple(mapping.skill_capability for mapping in self.read_mappings)
        expected = tuple(
            capability
            for capability in self.required_capabilities
            if v1_skill_read_capabilities(capability)
        )
        if (
            len(mapped_capabilities) != len(set(mapped_capabilities))
            or mapped_capabilities != expected
        ):
            raise ValueError("skill_read_mapping_invalid")

    @property
    def git_source_path(self) -> str:
        return f"plugins/mercury-finance/skills/{self.skill_id}/SKILL.md"

    @property
    def v1_capability_routes(self) -> dict[str, list[str]]:
        declared = dict.fromkeys((*self.required_capabilities, *self.optional_capabilities))
        return {capability: list(v1_skill_read_capabilities(capability)) for capability in declared}

    def published_projection(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "title": self.title,
            "category": self.category,
            "summary": self.summary,
            "input_schema": self.input_schema.model_json_schema(),
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "output_schema_name": {"const": self.output_schema_name},
                    "facts": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 2_000},
                        "maxItems": 500,
                    },
                    "citations": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 2_000},
                        "maxItems": 100,
                    },
                },
                "required": ["output_schema_name", "facts", "citations"],
            },
            "required_capabilities": list(self.required_capabilities),
            "optional_capabilities": list(self.optional_capabilities),
            "v1_capability_routes": self.v1_capability_routes,
            "read_mappings": [mapping.published_projection() for mapping in self.read_mappings],
            "required_connectors": list(self.required_connectors),
            "allowed_action_classes": list(self.allowed_action_classes),
            "blocked_action_classes": list(self.blocked_action_classes),
            "evidence_requirements": list(self.evidence_requirements),
            "knowledge_filters": dict(self.knowledge_filters),
            "citation_required": "citation" in self.evidence_requirements,
            "git_source_path": self.git_source_path,
        }

    @property
    def projection_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json(self.published_projection()).encode("utf-8")
        ).hexdigest()


ACCOUNTING_SKILL_CATALOG: tuple[AccountingSkillDefinition, ...] = (
    AccountingSkillDefinition(
        skill_id="company-health-check-th",
        title="Company Health Check TH",
        category="audit",
        summary="ตรวจสุขภาพบริษัทจากข้อมูลบัญชีและหลักฐานที่มี พร้อมจุดที่ควรให้บัญชีตรวจทาน",
        required_capabilities=("company.read",),
        optional_capabilities=("documents.invoice.list", "tax.vat.summary.read"),
        required_connectors=(),
        input_schema=CompanyHealthSkillInput,
        output_schema_name="company_health_result",
        read_mappings=(
            SkillReadMapping(
                skill_capability="company.read",
                capability_id="provider_profile.get",
                request_kind="empty",
                result_fact_name="company_profile",
            ),
        ),
    ),
    AccountingSkillDefinition(
        skill_id="vat-summary-th",
        title="VAT Summary TH",
        category="tax",
        summary="ช่วยสรุป VAT และบริบทภาษีซื้อ/ภาษีขายพร้อม citation จาก Mercury Wiki",
        required_capabilities=("documents.invoice.list",),
        optional_capabilities=("tax.vat.summary.read",),
        required_connectors=(),
        input_schema=VatSummarySkillInput,
        output_schema_name="vat_summary_result",
        read_mappings=(
            SkillReadMapping(
                skill_capability="documents.invoice.list",
                capability_id="documents.invoice.list",
                request_kind="invoice_list",
                result_fact_name="invoice_list",
            ),
        ),
    ),
    AccountingSkillDefinition(
        skill_id="invoice-review-th",
        title="Invoice Review TH",
        category="audit",
        summary="ตรวจใบแจ้งหนี้/ใบกำกับภาษีและจัดเตรียมงานตาม endpoint capability ที่เชื่อมอยู่",
        required_capabilities=("documents.invoice.list", "documents.invoice.read"),
        optional_capabilities=("contacts.list",),
        required_connectors=(),
        input_schema=InvoiceReviewSkillInput,
        output_schema_name="invoice_review_result",
        read_mappings=(
            SkillReadMapping(
                skill_capability="documents.invoice.list",
                capability_id="documents.invoice.list",
                request_kind="invoice_list",
                result_fact_name="invoice_list",
            ),
            SkillReadMapping(
                skill_capability="documents.invoice.read",
                capability_id="documents.invoice.get",
                request_kind="invoice_get",
                result_fact_name="invoice_detail",
            ),
        ),
    ),
    AccountingSkillDefinition(
        skill_id="management-report-th",
        title="Management Report TH",
        category="reporting",
        summary="เตรียม context pack สำหรับรายงานผู้บริหาร: รายได้, VAT, cash flow, margin",
        required_capabilities=("company.read", "documents.invoice.list"),
        optional_capabilities=("payments.read", "journal.read"),
        required_connectors=(),
        input_schema=ManagementReportSkillInput,
        output_schema_name="management_report_result",
        read_mappings=(
            SkillReadMapping(
                skill_capability="company.read",
                capability_id="provider_profile.get",
                request_kind="empty",
                result_fact_name="company_profile",
            ),
            SkillReadMapping(
                skill_capability="documents.invoice.list",
                capability_id="documents.invoice.list",
                request_kind="invoice_list",
                result_fact_name="invoice_list",
            ),
        ),
    ),
    AccountingSkillDefinition(
        skill_id="connector-setup-guide-th",
        title="Connector Setup Guide TH",
        category="setup",
        summary="แนะนำขั้นตอนเชื่อมโปรแกรมบัญชี โดยแยกข้อมูลที่ต้องถามผู้ใช้กับค่าที่ตั้งล่วงหน้าได้",
        required_capabilities=(),
        optional_capabilities=(),
        required_connectors=(),
        input_schema=ConnectorSetupSkillInput,
        output_schema_name="connector_setup_plan",
    ),
    AccountingSkillDefinition(
        skill_id="connector-credential-setup-th",
        title="Connector Credential Setup TH",
        category="setup",
        summary="นำผู้ใช้เชื่อม ERP ทีละขั้นและหยุดรอจนแต่ละขั้นตรวจสอบสำเร็จ",
        required_capabilities=(),
        optional_capabilities=(),
        required_connectors=(),
        input_schema=ConnectorSetupSkillInput,
        output_schema_name="connector_credential_setup_plan",
    ),
    AccountingSkillDefinition(
        skill_id="flowaccount-connector-setup-th",
        title="FlowAccount Connector Setup TH",
        category="setup",
        summary="เชื่อมและตรวจสอบ FlowAccount แบบ guided setup โดยไม่เปิดเผย credential",
        required_capabilities=(),
        optional_capabilities=(),
        required_connectors=("flowaccount",),
        input_schema=ConnectorSetupSkillInput,
        output_schema_name="connector_setup_plan",
    ),
    AccountingSkillDefinition(
        skill_id="peak-connector-setup-th",
        title="PEAK Connector Setup TH",
        category="setup",
        summary=(
            "แนะนำการเชื่อม PEAK Open API, credential ที่ต้องใช้, "
            "เอกสารอ้างอิง, และ setup validation ก่อนใช้งาน GET/POST endpoint"
        ),
        required_capabilities=(),
        optional_capabilities=(),
        required_connectors=("peak",),
        input_schema=ConnectorSetupSkillInput,
        output_schema_name="connector_setup_plan",
    ),
    AccountingSkillDefinition(
        skill_id="mercury-flow-runner",
        title="Mercury Flow Runner",
        category="automation",
        summary="วางแผน บันทึก และรัน workflow บัญชีแบบ read-only พร้อม capability gate",
        required_capabilities=(),
        optional_capabilities=(),
        required_connectors=(),
        input_schema=FlowRunnerSkillInput,
        output_schema_name="mercury_flow_result",
    ),
    AccountingSkillDefinition(
        skill_id="flowaccount-journal-posting-th",
        title="FlowAccount Journal Posting TH",
        category="accounting",
        summary=("เตรียม ตรวจสมดุล สร้างร่าง และอนุมัติรายการสมุดรายวัน FlowAccount โดยแยกการยืนยันแต่ละขั้น"),
        required_capabilities=("journal.draft.create",),
        optional_capabilities=(),
        required_connectors=("flowaccount",),
        input_schema=JournalPostingSkillInput,
        output_schema_name="journal_posting_plan",
    ),
    AccountingSkillDefinition(
        skill_id="accounts-receivable-reconciliation-th",
        title="Accounts Receivable Reconciliation TH",
        category="reconciliation",
        summary="กระทบยอดลูกหนี้ ใบแจ้งหนี้ ใบเสร็จ และหลักฐานรับชำระ พร้อมแสดงผลต่างอย่างชัดเจน",
        required_capabilities=("documents.invoice.list",),
        optional_capabilities=("payments.read",),
        required_connectors=(),
        input_schema=ReconciliationSkillInput,
        output_schema_name="reconciliation_result",
        read_mappings=(
            SkillReadMapping(
                skill_capability="documents.invoice.list",
                capability_id="documents.invoice.list",
                request_kind="invoice_list",
                result_fact_name="invoice_list",
            ),
        ),
    ),
    AccountingSkillDefinition(
        skill_id="accounts-payable-reconciliation-th",
        title="Accounts Payable Reconciliation TH",
        category="reconciliation",
        summary="กระทบยอดเจ้าหนี้ บิล ค่าใช้จ่าย และหลักฐานจ่ายเงิน พร้อมรายการที่ต้องตรวจทาน",
        required_capabilities=("documents.expense.list",),
        optional_capabilities=("payments.read",),
        required_connectors=(),
        input_schema=ReconciliationSkillInput,
        output_schema_name="reconciliation_result",
    ),
    AccountingSkillDefinition(
        skill_id="bank-settlement-reconciliation-th",
        title="Bank Settlement Reconciliation TH",
        category="reconciliation",
        summary="กระทบยอดรายการ ERP กับ statement หรือ settlement โดยไม่อนุมานข้อมูลธนาคารที่ขาด",
        required_capabilities=(),
        optional_capabilities=("payments.read",),
        required_connectors=(),
        input_schema=ReconciliationSkillInput,
        output_schema_name="reconciliation_result",
    ),
    AccountingSkillDefinition(
        skill_id="marketplace-settlement-review-th",
        title="Marketplace Settlement Review TH",
        category="reconciliation",
        summary="ตรวจ orders, fees, refunds และ payouts จาก marketplace เทียบหลักฐานบัญชีที่เชื่อมได้",
        required_capabilities=(),
        optional_capabilities=("documents.invoice.list", "payments.read"),
        required_connectors=(),
        input_schema=MarketplaceSettlementSkillInput,
        output_schema_name="marketplace_settlement_result",
    ),
    AccountingSkillDefinition(
        skill_id="month-end-evidence-gathering-th",
        title="Month-End Evidence Gathering TH",
        category="accounting",
        summary="รวบรวมและจัดกลุ่มหลักฐานปิดเดือนจากแหล่งที่เชื่อม โดยระบุรายการขาดและข้อขัดแย้ง",
        required_capabilities=(),
        optional_capabilities=(
            "company.read",
            "documents.invoice.list",
            "documents.expense.list",
        ),
        required_connectors=(),
        input_schema=MonthEndEvidenceSkillInput,
        output_schema_name="month_end_evidence_result",
    ),
)

ACCOUNTING_SKILL_IDS = tuple(skill.skill_id for skill in ACCOUNTING_SKILL_CATALOG)
_ACCOUNTING_SKILL_BY_ID = MappingProxyType(
    {skill.skill_id: skill for skill in ACCOUNTING_SKILL_CATALOG}
)
_PUBLISHED_ACCOUNTING_SKILL_BY_ID_VERSION = MappingProxyType(
    {(skill.skill_id, skill.skill_version): skill for skill in ACCOUNTING_SKILL_CATALOG}
)
if len(_ACCOUNTING_SKILL_BY_ID) != len(ACCOUNTING_SKILL_CATALOG):
    raise RuntimeError("accounting_skill_catalog_duplicate")

_BACKWARD_COMPATIBLE_TAGS = MappingProxyType(
    {
        "company-health-check-th": ("audit", "thai", "management"),
        "vat-summary-th": ("vat", "thai", "tax"),
        "invoice-review-th": ("invoice", "audit", "thai"),
        "management-report-th": ("report", "thai", "finance"),
        "connector-setup-guide-th": ("setup", "connector", "thai"),
        "connector-credential-setup-th": ("setup", "credentials", "connector", "thai"),
        "flowaccount-connector-setup-th": ("setup", "connector", "flowaccount", "thai"),
        "peak-connector-setup-th": ("setup", "connector", "peak", "thai"),
        "mercury-flow-runner": ("flow", "workflow", "automation", "read-only"),
        "flowaccount-journal-posting-th": ("flowaccount", "journal", "write", "thai"),
        "accounts-receivable-reconciliation-th": (
            "reconciliation",
            "receivables",
            "cross-mcp",
            "thai",
        ),
        "accounts-payable-reconciliation-th": (
            "reconciliation",
            "payables",
            "cross-mcp",
            "thai",
        ),
        "bank-settlement-reconciliation-th": (
            "reconciliation",
            "bank",
            "settlement",
            "cross-mcp",
            "thai",
        ),
        "marketplace-settlement-review-th": (
            "marketplace",
            "settlement",
            "reconciliation",
            "cross-mcp",
            "thai",
        ),
        "month-end-evidence-gathering-th": (
            "month-end",
            "evidence",
            "accounting",
            "cross-mcp",
            "thai",
        ),
    }
)


def accounting_skill_by_id(skill_id: str) -> AccountingSkillDefinition | None:
    if not isinstance(skill_id, str):
        return None
    return _ACCOUNTING_SKILL_BY_ID.get(skill_id.strip())


def published_accounting_skill(
    skill_id: str,
    skill_version: str,
) -> AccountingSkillDefinition | None:
    """Resolve exactly one Git-canonical first-party Skill version."""

    if not isinstance(skill_id, str) or not isinstance(skill_version, str):
        return None
    return _PUBLISHED_ACCOUNTING_SKILL_BY_ID_VERSION.get((skill_id.strip(), skill_version.strip()))


def accounting_skill_input_schema(skill_id: str) -> dict[str, Any] | None:
    skill = accounting_skill_by_id(skill_id)
    return skill.input_schema.model_json_schema() if skill else None


def accounting_skill_summaries() -> list[dict[str, Any]]:
    return [
        {
            "skill_id": skill.skill_id,
            "skill_version": skill.skill_version,
            "title": skill.title,
            "category": skill.category,
            "summary": skill.summary,
            "required_capabilities": list(skill.required_capabilities),
            "optional_capabilities": list(skill.optional_capabilities),
            "required_connectors": list(skill.required_connectors),
            "output_schema_name": skill.output_schema_name,
        }
        for skill in ACCOUNTING_SKILL_CATALOG
    ]


def _skill_catalog_seed() -> list[dict[str, Any]]:
    return [
        {
            "skill_id": skill.skill_id,
            "title": skill.title,
            "category": skill.category,
            "summary": skill.summary,
            "status": "available",
            "version": skill.skill_version,
            "required_capabilities": list(skill.required_capabilities),
            "required_connectors": list(skill.required_connectors),
            "tags": list(_BACKWARD_COMPATIBLE_TAGS[skill.skill_id]),
        }
        for skill in ACCOUNTING_SKILL_CATALOG
    ]


SKILL_CATALOG_SEED = _skill_catalog_seed()
