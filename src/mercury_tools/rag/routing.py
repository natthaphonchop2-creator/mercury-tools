"""Deterministic ERP routing for Mercury knowledge retrieval."""

from __future__ import annotations

import re
from typing import Any, Literal

KnowledgeDomain = Literal[
    "connector_endpoint",
    "accounting_standard",
    "tax",
    "workflow",
    "general",
]

CONNECTOR_PATTERNS: dict[str, tuple[str, ...]] = {
    "flowaccount": (r"\bflow\s*account\b",),
    "peak": (r"\bpeak\s*accounting\b", r"\bpeak\b"),
    "express": (r"\bexpress\s*account\b",),
}

DOMAIN_PATTERNS: tuple[tuple[KnowledgeDomain, tuple[str, ...]], ...] = (
    (
        "accounting_standard",
        (
            r"\b(?:tfrs|tas|ifrs|ias)\s*\d+\b",
            r"มาตรฐาน(?:การบัญชี|การรายงานทางการเงิน)",
            r"การรับรู้รายได้",
            r"สินค้าคงเหลือ",
            r"สัญญาเช่า",
            r"เครื่องมือทางการเงิน",
            r"งบกระแสเงินสด",
            r"ภาษีเงินได้รอการตัดบัญชี",
            r"expected credit loss|\becl\b",
            r"property,? plant and equipment",
        ),
    ),
    (
        "tax",
        (
            r"\bvat\b|ภาษีมูลค่าเพิ่ม",
            r"ภาษีซื้อ|ภาษีขาย",
            r"ภาษีหัก\s*ณ\s*ที่จ่าย|withholding tax|\bwht\b",
            r"ใบกำกับภาษี",
            r"ภ\.?พ\.?\s*30|ภ\.?ง\.?ด\.?",
        ),
    ),
    (
        "connector_endpoint",
        (
            r"\bendpoint\b|ปลายทาง\s*api",
            r"\bapi\b.*(?:path|route|request|response)",
            r"/(?:token|clienttoken|company|contacts|products|invoices|tax-invoices)\b",
        ),
    ),
    (
        "workflow",
        (
            r"\bworkflow\b|\bcontext pack\b|\bmanagement report\b",
            r"ขั้นตอนการทำงาน|รายงานผู้บริหาร",
        ),
    ),
)

DOMAIN_DOC_TYPES: dict[KnowledgeDomain, str] = {
    "connector_endpoint": "endpoint_dictionary",
    "accounting_standard": "accounting_standard",
    "tax": "tax",
    "workflow": "workflow",
}


def infer_connector_id(query: str) -> str | None:
    """Infer one connector only when an explicit, unambiguous alias is present."""
    text = str(query or "").casefold()
    matches = {
        connector_id
        for connector_id, patterns in CONNECTOR_PATTERNS.items()
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def infer_knowledge_domain(query: str) -> KnowledgeDomain:
    """Infer one deterministic knowledge domain, using ordered domain priority."""
    text = str(query or "").casefold()
    for domain, patterns in DOMAIN_PATTERNS:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            return domain
    return "general"


def apply_connector_routing(
    query: str,
    filters: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Apply explicit filters first, then deterministic connector inference."""
    applied = dict(filters or {})
    if applied.get("connector"):
        return applied, None
    inferred = infer_connector_id(query)
    if inferred:
        applied["connector"] = inferred
    return applied, inferred


def apply_knowledge_routing(
    query: str,
    filters: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None, KnowledgeDomain | None]:
    """Apply explicit filters before inferred connector and domain filters."""
    applied = dict(filters or {})
    connector = None if applied.get("connector") else infer_connector_id(query)
    domain = None if applied.get("doc_type") else infer_knowledge_domain(query)

    if domain in DOMAIN_DOC_TYPES:
        applied["doc_type"] = DOMAIN_DOC_TYPES[domain]
    if connector and domain not in {"accounting_standard", "tax"}:
        applied["connector"] = connector
    return applied, connector, domain
