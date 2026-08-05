from __future__ import annotations

from mercury_tools.mcp.widget_tools import (
    DOCUMENT_PREVIEW_WIDGET_MIME_TYPE,
    DOCUMENT_PREVIEW_WIDGET_URI,
    build_document_preview_result,
    document_preview_widget_resource,
    preview_widget_tool_meta,
    render_document_preview_text_fallback,
)


def _summary() -> dict[str, object]:
    return {
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "preview_id": "66666666-6666-4666-8666-666666666666",
        "state_version": 3,
        "status": "awaiting_confirmation",
        "provider": "flowaccount",
        "environment": "sandbox",
        "document_count": 1,
        "currency": "THB",
        "subtotal": "100.00",
        "tax_total": "7.00",
        "grand_total": "107.00",
        "warning_count": 1,
        "expires_at": "2026-08-06T12:00:00Z",
        "provider_payload": {"client_secret": "must-not-leak"},
    }


def _widget_preview() -> dict[str, object]:
    return {
        **_summary(),
        "documents": [
            {
                "draft_id": "draft-1",
                "document_type": "invoice",
                "counterparty_display": "บริษัท ตัวอย่าง จำกัด",
                "issue_date": "2026-08-06",
                "due_date": "2026-08-20",
                "currency": "THB",
                "lines": [
                    {
                        "description": "บริการบัญชี",
                        "quantity": "1",
                        "unit_price": "100.00",
                        "discount_amount": "0.00",
                        "vat_rate": "7",
                        "vat_amount": "7.00",
                        "withholding_rate": "0",
                        "withholding_amount": "0.00",
                        "line_total": "107.00",
                        "provider_payload": {"token": "must-not-leak"},
                    }
                ],
                "subtotal": "100.00",
                "discount_total": "0.00",
                "vat_total": "7.00",
                "withholding_tax_total": "0.00",
                "grand_total": "107.00",
                "warnings": ["โปรดตรวจสอบอัตราภาษีมูลค่าเพิ่ม"],
                "accountant_review_points": ["ตรวจสอบเอกสารประกอบ"],
                "confirmation_label": "ยืนยันสร้างเอกสาร",
                "provider_arguments": {"client_secret": "must-not-leak"},
            }
        ],
    }


def test_document_preview_widget_resource_has_the_approved_uri_and_mime_type() -> None:
    resource = document_preview_widget_resource()

    assert DOCUMENT_PREVIEW_WIDGET_URI == "ui://widget/mercury-document-preview-v1.html"
    assert DOCUMENT_PREVIEW_WIDGET_MIME_TYPE == "text/html;profile=mcp-app"
    assert resource["uri"] == DOCUMENT_PREVIEW_WIDGET_URI
    assert resource["mimeType"] == DOCUMENT_PREVIEW_WIDGET_MIME_TYPE
    assert resource["_meta"]["ui"]["resourceUri"] == DOCUMENT_PREVIEW_WIDGET_URI
    assert resource["_meta"]["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
    }
    assert resource["_meta"]["ui"]["domain"] == "https://mercury-tools-mcp.onrender.com"


def test_widget_tool_metadata_has_standard_and_openai_compatibility_pointers() -> None:
    metadata = preview_widget_tool_meta()

    assert metadata["ui"]["resourceUri"] == DOCUMENT_PREVIEW_WIDGET_URI
    assert metadata["openai/outputTemplate"] == DOCUMENT_PREVIEW_WIDGET_URI
    assert "immutable accounting-document preview" in metadata["openai/widgetDescription"]


def test_preview_result_separates_model_text_and_widget_only_surfaces() -> None:
    result = build_document_preview_result(_summary(), _widget_preview())

    assert result["structuredContent"]["schema"] == "mercury.preview.summary.v1"
    assert result["_meta"]["mercury/preview"]["schema"] == "mercury.preview.widget.v1"
    assert result["_meta"]["ui"]["resourceUri"] == DOCUMENT_PREVIEW_WIDGET_URI
    assert result["_meta"]["openai/outputTemplate"] == DOCUMENT_PREVIEW_WIDGET_URI
    assert result["content"][0]["type"] == "text"
    assert "provider_payload" not in str(result)
    assert "client_secret" not in str(result)
    assert "must-not-leak" not in str(result)


def test_thai_text_fallback_has_identity_totals_warning_and_exact_next_action() -> None:
    fallback = render_document_preview_text_fallback(_summary(), _widget_preview())

    assert "Workspace: 33333333-3333-4333-8333-333333333333" in fallback
    assert "Preview: 66666666-6666-4666-8666-666666666666" in fallback
    assert "State version: 3" in fallback
    assert "ยอดรวม: THB 107.00" in fallback
    assert "โปรดตรวจสอบอัตราภาษีมูลค่าเพิ่ม" in fallback
    assert "confirm_document_create" in fallback
    assert 'confirmation="CONFIRM_CREATE"' in fallback


def test_expired_preview_fallback_requires_a_fresh_render_instead_of_confirmation() -> None:
    summary = _summary() | {"status": "expired"}

    fallback = render_document_preview_text_fallback(summary, _widget_preview())

    assert "render_document_preview" in fallback
    assert 'confirmation="CONFIRM_CREATE"' not in fallback
