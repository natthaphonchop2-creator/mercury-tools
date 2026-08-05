"""Closed MCP Apps resource and output helpers for document previews.

This module deliberately has no dependency on the execution runtime.  The
server integration can attach these pure helpers to the V1 render tool without
making provider payloads available to either models or the browser widget.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DOCUMENT_PREVIEW_WIDGET_URI = "ui://widget/mercury-document-preview-v1.html"
DOCUMENT_PREVIEW_WIDGET_MIME_TYPE = "text/html;profile=mcp-app"
DOCUMENT_PREVIEW_WIDGET_DESCRIPTION = (
    "An immutable accounting-document preview awaiting confirmation. "
    "It only confirms the displayed preview version."
)
_WIDGET_PATH = Path(__file__).with_name("widgets") / "mercury-document-preview-v1.html"

_SUMMARY_FIELDS = (
    "workspace_id",
    "preview_id",
    "state_version",
    "status",
    "provider",
    "environment",
    "document_count",
    "currency",
    "subtotal",
    "tax_total",
    "grand_total",
    "warning_count",
    "expires_at",
)
_DOCUMENT_FIELDS = (
    "draft_id",
    "document_type",
    "counterparty_display",
    "issue_date",
    "due_date",
    "currency",
    "subtotal",
    "discount_total",
    "vat_total",
    "withholding_tax_total",
    "grand_total",
    "warnings",
    "accountant_review_points",
    "confirmation_label",
)
_LINE_FIELDS = (
    "description",
    "quantity",
    "unit_price",
    "discount_amount",
    "vat_rate",
    "vat_amount",
    "withholding_rate",
    "withholding_amount",
    "line_total",
)


def document_preview_widget_html() -> str:
    """Return the versioned resource as a complete, offline HTML document."""

    return _WIDGET_PATH.read_text(encoding="utf-8")


def preview_widget_tool_meta() -> dict[str, object]:
    """Return tool metadata for MCP Apps and OpenAI Apps compatibility."""

    return {
        "ui": {
            "resourceUri": DOCUMENT_PREVIEW_WIDGET_URI,
            "domain": "https://mercury-tools-mcp.onrender.com",
            "csp": {"connectDomains": [], "resourceDomains": []},
            "prefersBorder": True,
        },
        "openai/outputTemplate": DOCUMENT_PREVIEW_WIDGET_URI,
        "openai/widgetDescription": DOCUMENT_PREVIEW_WIDGET_DESCRIPTION,
        "openai/widgetPrefersBorder": True,
    }


def document_preview_widget_resource() -> dict[str, object]:
    """Describe the static resource for a server resource registry."""

    return {
        "uri": DOCUMENT_PREVIEW_WIDGET_URI,
        "name": "mercury_document_preview_v1",
        "title": "Mercury document preview",
        "description": DOCUMENT_PREVIEW_WIDGET_DESCRIPTION,
        "mimeType": DOCUMENT_PREVIEW_WIDGET_MIME_TYPE,
        "_meta": preview_widget_tool_meta(),
    }


def register_document_preview_widget(server: Any) -> None:
    """Register the immutable preview resource once on a FastMCP-compatible server."""

    resources = getattr(getattr(server, "_resource_manager", None), "_resources", None)
    if not isinstance(resources, dict):
        raise TypeError("preview_widget_server_invalid")
    if DOCUMENT_PREVIEW_WIDGET_URI in resources:
        return
    server.resource(
        DOCUMENT_PREVIEW_WIDGET_URI,
        name="mercury_document_preview_v1",
        title="Mercury document preview",
        description=DOCUMENT_PREVIEW_WIDGET_DESCRIPTION,
        mime_type=DOCUMENT_PREVIEW_WIDGET_MIME_TYPE,
        meta=preview_widget_tool_meta(),
    )(document_preview_widget_html)


def build_document_preview_result(
    summary: Mapping[str, object], widget_preview: Mapping[str, object]
) -> dict[str, object]:
    """Build separate model, text, and widget result surfaces.

    Unknown keys are never copied.  In particular, encrypted or raw provider
    request payloads cannot pass through this adapter by accident.
    """

    safe_summary = _safe_summary(summary)
    safe_widget_preview = _safe_widget_preview(widget_preview, safe_summary)
    return {
        "structuredContent": {"schema": "mercury.preview.summary.v1", **safe_summary},
        "content": [
            {
                "type": "text",
                "text": render_document_preview_text_fallback(safe_summary, safe_widget_preview),
            }
        ],
        "_meta": {
            **preview_widget_tool_meta(),
            "mercury/preview": {
                "schema": "mercury.preview.widget.v1",
                **safe_widget_preview,
            },
        },
    }


def render_document_preview_text_fallback(
    summary: Mapping[str, object], widget_preview: Mapping[str, object]
) -> str:
    """Render the complete Thai confirmation instruction for text-only hosts."""

    safe_summary = _safe_summary(summary)
    safe_widget_preview = _safe_widget_preview(widget_preview, safe_summary)
    warnings = _collect_warnings(safe_widget_preview)
    lines = [
        "ตัวอย่างเอกสาร Mercury สำหรับตรวจสอบก่อนสร้างจริง",
        f"Workspace: {safe_summary['workspace_id']}",
        f"Preview: {safe_summary['preview_id']}",
        f"State version: {safe_summary['state_version']}",
        f"สถานะ: {safe_summary['status']}",
        f"ผู้ให้บริการ: {safe_summary['provider']} ({safe_summary['environment']})",
        f"จำนวนเอกสาร: {safe_summary['document_count']}",
        f"ยอดก่อนภาษี: {safe_summary['currency']} {safe_summary['subtotal']}",
        f"ภาษี: {safe_summary['currency']} {safe_summary['tax_total']}",
        f"ยอดรวม: {safe_summary['currency']} {safe_summary['grand_total']}",
        f"หมดอายุ: {safe_summary['expires_at']}",
    ]
    if warnings:
        lines.extend(("คำเตือน:", *(f"- {warning}" for warning in warnings)))

    if safe_summary["status"] in {"prepared", "awaiting_confirmation"}:
        lines.append(
            "ขั้นตอนถัดไป: เรียก confirm_document_create ด้วย "
            f"workspace_id={safe_summary['workspace_id']}, "
            f"preview_id={safe_summary['preview_id']}, "
            f"state_version={safe_summary['state_version']}, "
            'confirmation="CONFIRM_CREATE"'
        )
    else:
        lines.append(
            "ตัวอย่างนี้ไม่พร้อมยืนยันแล้ว: เรียก render_document_preview ใหม่ "
            "เพื่อรับสถานะล่าสุดจาก Mercury"
        )
    return "\n".join(lines)


def _safe_summary(summary: Mapping[str, object]) -> dict[str, object]:
    missing = [field for field in _SUMMARY_FIELDS if field not in summary]
    if missing:
        raise ValueError(f"missing_preview_summary_fields:{','.join(missing)}")

    return {field: summary[field] for field in _SUMMARY_FIELDS}


def _safe_widget_preview(
    widget_preview: Mapping[str, object], summary: Mapping[str, object]
) -> dict[str, object]:
    documents_value = widget_preview.get("documents", ())
    if not isinstance(documents_value, Sequence) or isinstance(documents_value, (str, bytes)):
        raise ValueError("invalid_preview_documents")

    return {
        **summary,
        "documents": [_safe_document(document) for document in documents_value],
    }


def _safe_document(document: object) -> dict[str, object]:
    if not isinstance(document, Mapping):
        raise ValueError("invalid_preview_document")

    missing = [field for field in _DOCUMENT_FIELDS if field not in document]
    if missing:
        raise ValueError(f"missing_preview_document_fields:{','.join(missing)}")
    lines_value = document.get("lines", ())
    if not isinstance(lines_value, Sequence) or isinstance(lines_value, (str, bytes)):
        raise ValueError("invalid_preview_document_lines")

    safe_document = {field: document[field] for field in _DOCUMENT_FIELDS}
    safe_document["lines"] = [_safe_line(line) for line in lines_value]
    safe_document["warnings"] = _safe_strings(document["warnings"], "invalid_preview_warnings")
    safe_document["accountant_review_points"] = _safe_strings(
        document["accountant_review_points"], "invalid_preview_review_points"
    )
    return safe_document


def _safe_line(line: object) -> dict[str, object]:
    if not isinstance(line, Mapping):
        raise ValueError("invalid_preview_line")
    missing = [field for field in _LINE_FIELDS if field not in line]
    if missing:
        raise ValueError(f"missing_preview_line_fields:{','.join(missing)}")
    return {field: line[field] for field in _LINE_FIELDS}


def _safe_strings(value: object, error_code: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(error_code)
    if not all(isinstance(item, str) for item in value):
        raise ValueError(error_code)
    return list(value)


def _collect_warnings(widget_preview: Mapping[str, object]) -> list[str]:
    warnings: list[str] = []
    for document in widget_preview["documents"]:
        if not isinstance(document, Mapping):
            continue
        for warning in document.get("warnings", []):
            if isinstance(warning, str) and warning not in warnings:
                warnings.append(warning)
    return warnings


__all__ = [
    "DOCUMENT_PREVIEW_WIDGET_DESCRIPTION",
    "DOCUMENT_PREVIEW_WIDGET_MIME_TYPE",
    "DOCUMENT_PREVIEW_WIDGET_URI",
    "build_document_preview_result",
    "document_preview_widget_html",
    "document_preview_widget_resource",
    "preview_widget_tool_meta",
    "register_document_preview_widget",
    "render_document_preview_text_fallback",
]
