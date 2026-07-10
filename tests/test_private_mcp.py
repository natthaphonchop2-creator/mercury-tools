from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from mercury_tools.config import load_settings
from mercury_tools.journals.models import JournalValidationError
from mercury_tools.mcp import private_server
from mercury_tools.mcp.private_server import private_mcp
from mercury_tools.mcp.server import create_http_app, mcp


@pytest.fixture(autouse=True)
def _clear_private_env(monkeypatch) -> None:
    monkeypatch.delenv("MERCURY_PRIVATE_MCP_TOKEN", raising=False)
    monkeypatch.delenv("MERCURY_PRIVATE_MCP_PATH", raising=False)
    monkeypatch.delenv("MERCURY_TOOLS_HTTP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", raising=False)


@pytest.mark.asyncio
async def test_public_and_private_tool_registries_are_separate() -> None:
    public_names = {tool.name for tool in await mcp.list_tools()}
    private_names = {tool.name for tool in await private_mcp.list_tools()}
    expected = {
        "preview_flowaccount_journal",
        "create_flowaccount_journal_draft",
        "approve_flowaccount_journal",
    }

    assert expected.isdisjoint(public_names)
    assert private_names == expected


def test_private_settings_are_disabled_without_token(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_PRIVATE_MCP_PATH", "company-mcp")

    settings = load_settings()

    assert settings.private_mcp_path == "/company-mcp"
    assert settings.private_mcp_configured is False


def test_private_mcp_requires_dedicated_bearer(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_PRIVATE_MCP_TOKEN", "private-token")
    monkeypatch.setenv("MERCURY_PRIVATE_MCP_PATH", "/private-mcp")
    client = TestClient(create_http_app(require_auth=False), raise_server_exceptions=False)

    assert client.get("/mcp").status_code != 401
    assert client.get("/private-mcp").status_code == 401
    assert (
        client.get(
            "/private-mcp",
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/private-mcp",
            headers={"Authorization": "Bearer private-token"},
        ).status_code
        != 401
    )


def test_private_mcp_is_not_mounted_without_token() -> None:
    client = TestClient(create_http_app(require_auth=False), raise_server_exceptions=False)

    assert client.get("/private-mcp").status_code == 404
    assert client.get("/healthz").json()["private_mcp"] == "disabled"


def test_private_preview_returns_structured_validation_error(monkeypatch) -> None:
    class FakeService:
        def preview(self, **kwargs):
            raise JournalValidationError(
                "unbalanced_journal",
                "Total debit must equal total credit.",
                details={"total_debit": "100.00", "total_credit": "90.00"},
            )

    monkeypatch.setattr(private_server, "_journal_service", lambda: FakeService())

    payload = private_server.preview_flowaccount_journal(
        workspace_id="mw_publiccontestworkspace001",
        document_date="2026-07-10",
        reference="REF-1",
        description="Test",
        lines=[],
    )

    assert payload == {
        "status": "validation_error",
        "code": "unbalanced_journal",
        "message": "Total debit must equal total credit.",
        "details": {"total_debit": "100.00", "total_credit": "90.00"},
    }
