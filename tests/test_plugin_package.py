import json
import re
from pathlib import Path

from mercury_tools.db.product import SKILL_CATALOG_SEED

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins/mercury-finance"

EXPECTED_SKILLS = {
    "flowaccount-connector-setup-th": [
        "create_public_workspace",
        "connector_status",
        "list_connectors",
        "start_connector_setup",
        "submit_connector_credentials",
        "validate_connector_connection",
        "retrieve_workspace_context_pack",
        "search_knowledge",
    ],
    "connector-credential-setup-th": [
        "create_public_workspace",
        "connector_status",
        "list_connectors",
        "start_connector_setup",
        "submit_connector_credentials",
        "validate_connector_connection",
    ],
    "company-health-check-th": [
        "connector_status",
        "retrieve_workspace_context_pack",
        "run_mercury_flow",
    ],
    "vat-summary-th": [
        "connector_status",
        "retrieve_workspace_context_pack",
        "run_mercury_flow",
    ],
    "invoice-review-th": [
        "connector_status",
        "retrieve_workspace_context_pack",
        "run_mercury_flow",
    ],
    "management-report-th": [
        "connector_status",
        "retrieve_workspace_context_pack",
        "run_mercury_flow",
    ],
    "connector-setup-guide-th": [
        "create_public_workspace",
        "connector_status",
        "list_connectors",
        "start_connector_setup",
        "validate_connector_connection",
    ],
    "peak-connector-setup-th": [
        "create_public_workspace",
        "connector_status",
        "list_connectors",
        "start_connector_setup",
        "submit_connector_credentials",
        "validate_connector_connection",
    ],
    "mercury-flow-runner": [
        "connector_status",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
        "run_mercury_flow",
    ],
}


def test_product_catalog_contains_every_bundled_plugin_skill() -> None:
    bundled = {
        path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")
    }
    catalog = {row["skill_id"] for row in SKILL_CATALOG_SEED}

    assert catalog == bundled


def test_contest_plugin_uses_public_workspace_contract() -> None:
    combined = "\n".join(
        path.read_text() for path in sorted((PLUGIN_ROOT / "skills").rglob("SKILL.md"))
    )

    assert "workspace_id" in combined
    assert "client_token" not in combined
    assert "Mercury Connect" not in combined


def test_plugin_capabilities_match_public_read_only_runtime() -> None:
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text())

    assert manifest["interface"]["capabilities"] == ["Interactive", "Read"]


def test_marketplace_points_to_plugin_folder() -> None:
    data = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    plugins = data["plugins"]
    mercury = next(item for item in plugins if item["name"] == "mercury-finance")

    assert data["name"] == "mercury-tools"
    assert data["interface"]["displayName"] == "Mercury Tools"
    assert mercury["source"]["path"] == "./plugins/mercury-finance"
    assert mercury["source"]["source"] == "local"
    assert mercury["policy"]["installation"] == "AVAILABLE"
    assert mercury["policy"]["authentication"] == "ON_INSTALL"
    assert mercury["category"] == "Finance"


def test_plugin_declares_remote_mcp_without_secret_values() -> None:
    plugin = json.loads(
        (ROOT / "plugins/mercury-finance/.codex-plugin/plugin.json").read_text()
    )
    mcp = json.loads((ROOT / "plugins/mercury-finance/.mcp.json").read_text())
    serialized = json.dumps({"plugin": plugin, "mcp": mcp})

    assert plugin["name"] == "mercury-finance"
    assert plugin["skills"] == "./skills/"
    assert plugin["mcpServers"] == "./.mcp.json"
    assert plugin["interface"]["displayName"] == "Mercury Finance"
    assert "https://mercury-tools-mcp.onrender.com/mcp" in serialized
    assert "bearer_token_env_var" not in serialized
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized
    assert "client_secret" not in serialized


def test_judge_quickstart_mentions_plugin_and_no_secrets() -> None:
    text = (ROOT / "docs/JUDGE_QUICKSTART.md").read_text()

    assert "Mercury Finance" in text
    assert "codex plugin marketplace add" in text
    assert "https://mercury-tools-mcp.onrender.com/mcp" in text
    assert "hosted MCP server config" in text
    assert "create_public_workspace" in text
    assert "workspace_id" in text
    assert "private tenant isolation" in text
    assert "client_token" not in text
    assert "Mercury Connect" not in text
    assert "token provided by the Mercury demo owner" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert "client_secret =" not in text


def test_connector_credential_skill_is_gated() -> None:
    skill = (
        ROOT
        / "plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md"
    ).read_text()

    assert "Use when" in skill
    assert "create_public_workspace" in skill
    assert "workspace_id" in skill
    assert "Do not proceed" in skill
    assert "validate_connector_connection" in skill


def test_connector_setup_skills_keep_the_gated_public_sequence() -> None:
    required_order = [
        "connector_status",
        "create_public_workspace",
        "start_connector_setup",
        "missing_fields",
        "submit_connector_credentials",
        "validate_connector_connection",
        "retrieve_workspace_context_pack",
    ]

    for skill_name in (
        "connector-credential-setup-th",
        "flowaccount-connector-setup-th",
        "peak-connector-setup-th",
    ):
        text = (PLUGIN_ROOT / f"skills/{skill_name}/SKILL.md").read_text()
        positions = [text.index(item) for item in required_order]
        assert positions == sorted(positions), skill_name


def test_skill_files_are_compact_and_route_to_mcp_tools() -> None:
    for skill_name, tool_names in EXPECTED_SKILLS.items():
        skill_path = PLUGIN_ROOT / f"skills/{skill_name}/SKILL.md"
        skill = skill_path.read_text()

        assert "Use when" in skill
        assert len(skill.splitlines()) < 80
        for tool_name in tool_names:
            assert tool_name in skill


def test_hosted_workflow_skills_use_public_workspace_connector_status() -> None:
    hosted_skill_names = [
        "company-health-check-th",
        "vat-summary-th",
        "invoice-review-th",
        "management-report-th",
        "mercury-flow-runner",
    ]

    for skill_name in hosted_skill_names:
        skill = (PLUGIN_ROOT / f"skills/{skill_name}/SKILL.md").read_text()
        assert "connector_status" in skill
        assert "workspace_id" in skill
        assert "client_token" not in skill


def test_plugin_package_has_no_embedded_secret_env_names_or_values() -> None:
    files = [
        ROOT / ".agents/plugins/marketplace.json",
        PLUGIN_ROOT / ".codex-plugin/plugin.json",
        PLUGIN_ROOT / ".mcp.json",
        *sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md")),
    ]
    serialized = "\n".join(file.read_text() for file in files)
    env_names = set(
        re.findall(
            r"\b[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS)"
            r"[A-Z0-9_]*\b",
            serialized,
        )
    )

    assert "MERCURY_TOOLS_MCP_TOKEN" not in serialized
    assert env_names == set()
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized
    assert "FLOWACCOUNT_CLIENT_SECRET" not in serialized
    assert "PEAK_CLIENT_SECRET" not in serialized
    assert "sk-" not in serialized
    assert "service_role" not in serialized
