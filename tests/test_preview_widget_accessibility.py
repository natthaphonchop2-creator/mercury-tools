from __future__ import annotations

from mercury_tools.mcp.widget_tools import document_preview_widget_html


def test_widget_is_a_self_contained_thai_accessible_document() -> None:
    html = document_preview_widget_html()

    assert '<html lang="th">' in html
    assert '<main aria-labelledby="preview-title">' in html
    assert '<table aria-describedby="document-summary">' in html
    assert "<caption>รายการเอกสารที่รอการยืนยัน</caption>" in html
    assert '<button id="confirm-button" type="button" disabled>' in html
    assert 'aria-live="polite"' in html
    assert ":focus-visible" in html
    assert "font-variant-numeric: tabular-nums" in html
    assert "Sarabun" in html
    assert "@media (max-width: 640px)" in html
    assert "@media print" in html
    assert "@page { size: A4;" in html


def test_widget_has_no_remote_assets_storage_or_direct_provider_network_access() -> None:
    html = document_preview_widget_html()

    assert "https://" not in html
    assert "http://" not in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "flowaccount" not in html.lower()
    assert "peakaccount" not in html.lower()


def test_widget_bridge_can_only_confirm_the_displayed_preview_state() -> None:
    html = document_preview_widget_html()

    assert html.count("window.openai.callTool(") == 1
    assert '"confirm_document_create"' in html
    assert "workspace_id: preview.workspace_id" in html
    assert "preview_id: preview.preview_id" in html
    assert "state_version: preview.state_version" in html
    assert 'confirmation: "CONFIRM_CREATE"' in html
    assert "render_document_preview" not in html
