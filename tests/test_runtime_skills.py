from pathlib import Path

import pytest

from mercury_tools.mercury_runtime import skill_markdown

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_SKILLS = (
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
)

CROSS_MCP_SKILLS = (
    "accounts-receivable-reconciliation-th",
    "accounts-payable-reconciliation-th",
    "bank-settlement-reconciliation-th",
    "marketplace-settlement-review-th",
    "month-end-evidence-gathering-th",
)
LOCAL_API_DRIVER_COMMANDS = {
    "credential_status",
    "search_erp_actions",
    "get_erp_action_schema",
    "run_erp_read",
    "preview_erp_write",
    "confirm_erp_write",
    "execute_erp_write",
    "prepare_erp_mutation",
    "execute_erp_create",
    "execute_erp_update",
    "execute_sensitive_erp_action",
    "get_erp_request_status",
}

@pytest.fixture(autouse=True)
def explicit_runtime_source_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_ROOT", str(ROOT))


@pytest.mark.parametrize("skill_name", BUNDLED_SKILLS)
def test_bundled_skill_is_available_to_mcp_runtime(skill_name: str) -> None:
    markdown = skill_markdown(skill_name)

    assert markdown is not None
    assert f"name: {skill_name}" in markdown


def test_bundled_provider_setup_skills_use_v1_connector_lifecycle() -> None:
    for skill_name in (
        "connector-credential-setup-th",
        "flowaccount-connector-setup-th",
        "peak-connector-setup-th",
    ):
        markdown = skill_markdown(skill_name)

        assert markdown is not None
        assert "get_mercury_context" in markdown
        assert "list_accounting_providers" in markdown
        assert "start_provider_connection" in markdown
        assert "list_provider_connections" in markdown
        assert "connector_status" in markdown
        assert "list_provider_capabilities" in markdown
        assert "workspace_id" in markdown
        assert "credentials never enter chat or model context" in markdown
        assert LOCAL_API_DRIVER_COMMANDS.isdisjoint(markdown)


def test_generic_setup_guide_uses_one_hosted_lifecycle() -> None:
    markdown = skill_markdown("connector-setup-guide-th")

    assert markdown is not None
    lifecycle = (
        "get_mercury_context",
        "list_accounting_providers",
        "start_provider_connection",
        "list_provider_connections",
        "connector_status",
        "list_provider_capabilities",
    )
    positions = [markdown.index(tool_name) for tool_name in lifecycle]

    assert positions == sorted(positions)
    assert "authorization_url" in markdown
    assert "setup_url" in markdown
    assert "advanced_local_handoff" not in markdown
    assert LOCAL_API_DRIVER_COMMANDS.isdisjoint(markdown)
    assert "mercury credentials" not in markdown
    assert "workspace_id" in markdown
    assert "credentials never enter chat or model context" in markdown


def test_bundled_journal_skill_requires_connected_provider_and_approval() -> None:
    markdown = skill_markdown("flowaccount-journal-posting-th")

    assert markdown is not None
    assert "get_mercury_context" in markdown
    assert "connector_status" in markdown
    assert "list_provider_capabilities" in markdown
    assert "get_capability_schema" in markdown
    assert "prepare_document_create" in markdown
    assert "render_document_preview" in markdown
    assert "confirm_document_create" in markdown
    assert "explicit user confirmation" in markdown
    assert "Provider credentials never enter chat or model context" in markdown
    assert LOCAL_API_DRIVER_COMMANDS.isdisjoint(markdown)
    assert "create_flowaccount_journal_draft" not in markdown


@pytest.mark.parametrize("skill_name", CROSS_MCP_SKILLS)
def test_cross_mcp_skills_keep_exclusive_route_and_evidence_contract(
    skill_name: str,
) -> None:
    markdown = skill_markdown(skill_name)

    assert markdown is not None
    compact = " ".join(markdown.split())
    assert "get_mercury_context" in markdown
    assert "connector_status" in markdown
    assert "list_provider_capabilities" in markdown
    assert "run_accounting_skill" in markdown
    assert "skill_version=0.1.0" in markdown
    assert "qualification" in compact
    assert "advanced_local_handoff" not in markdown
    assert "untrusted data" in compact
    assert "evidence" in compact
    assert "accountant review" in compact
    assert "This Skill is read-only" in compact
    assert LOCAL_API_DRIVER_COMMANDS.isdisjoint(markdown)
    assert "Provider credentials never enter chat or model context" in markdown


def test_skill_markdown_honors_an_explicit_runtime_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "plugins" / "mercury-finance" / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")
    monkeypatch.setenv("MERCURY_TOOLS_ROOT", str(tmp_path))

    assert skill_markdown("demo-skill") == "# Demo Skill\n"
