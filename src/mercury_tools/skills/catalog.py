"""Immutable accounting Skill definitions and generated public metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class _StrictSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=20_000)
    connector_id: SkillConnectorId | None = None
    connection_mode: SkillConnectionMode | None = None
    environment: SkillEnvironment | None = None
    company_name: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=10_000)


class _PeriodRangeSkillInput(_StrictSkillInput):
    period_start: date | None = None
    period_end: date | None = None

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
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class InvoiceReviewSkillInput(_PeriodRangeSkillInput):
    document_ids: list[str] = Field(default_factory=list, max_length=200)


class ManagementReportSkillInput(_PeriodRangeSkillInput):
    objective: str | None = Field(default=None, max_length=5_000)


class ConnectorSetupSkillInput(_StrictSkillInput):
    pass


class FlowRunnerSkillInput(_StrictSkillInput):
    objective: str | None = Field(default=None, max_length=5_000)


class JournalPostingSkillInput(_PeriodRangeSkillInput):
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    objective: str | None = Field(default=None, max_length=5_000)


class ReconciliationSkillInput(_PeriodRangeSkillInput):
    source_reference: str | None = Field(default=None, max_length=500)


class MarketplaceSettlementSkillInput(ReconciliationSkillInput):
    marketplace_source: str | None = Field(default=None, max_length=200)


class MonthEndEvidenceSkillInput(_StrictSkillInput):
    month: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


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


def accounting_skill_input_schema(skill_id: str) -> dict[str, Any] | None:
    skill = accounting_skill_by_id(skill_id)
    return skill.input_schema.model_json_schema() if skill else None


def accounting_skill_summaries() -> list[dict[str, Any]]:
    return [
        {
            "skill_id": skill.skill_id,
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
            "version": "0.1.0",
            "required_capabilities": list(skill.required_capabilities),
            "required_connectors": list(skill.required_connectors),
            "tags": list(_BACKWARD_COMPATIBLE_TAGS[skill.skill_id]),
        }
        for skill in ACCOUNTING_SKILL_CATALOG
    ]


SKILL_CATALOG_SEED = _skill_catalog_seed()
