import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins/mercury-finance"

EXPECTED_SKILLS = {
    "connector-credential-setup-th": [
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
        "list_connectors",
        "start_connector_setup",
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


def test_marketplace_points_to_plugin_folder() -> None:
    data = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    plugins = data["plugins"]
    mercury = next(item for item in plugins if item["id"] == "mercury-finance")

    assert mercury["source"]["path"] == "./plugins/mercury-finance"
    assert mercury["name"] == "Mercury Finance"


def test_plugin_declares_remote_mcp_without_secret_values() -> None:
    plugin = json.loads(
        (ROOT / "plugins/mercury-finance/.codex-plugin/plugin.json").read_text()
    )
    mcp = json.loads((ROOT / "plugins/mercury-finance/.mcp.json").read_text())
    serialized = json.dumps({"plugin": plugin, "mcp": mcp})

    assert plugin["name"] == "mercury-finance"
    assert "Mercury Finance" in plugin["display_name"]
    assert "https://mercury-tools-mcp.onrender.com/mcp" in serialized
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized
    assert "client_secret" not in serialized


def test_judge_quickstart_mentions_plugin_and_no_secrets() -> None:
    text = (ROOT / "docs/JUDGE_QUICKSTART.md").read_text()

    assert "Mercury Finance" in text
    assert "codex plugin marketplace add" in text
    assert "https://mercury-tools-mcp.onrender.com/mcp" in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert "client_secret =" not in text


def test_connector_credential_skill_is_gated() -> None:
    skill = (
        ROOT
        / "plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md"
    ).read_text()

    assert "Use when" in skill
    assert "Do not ask the user to paste API keys" in skill
    assert "Do not proceed" in skill
    assert "validate_connector_connection" in skill


def test_skill_files_are_compact_and_route_to_mcp_tools() -> None:
    for skill_name, tool_names in EXPECTED_SKILLS.items():
        skill_path = PLUGIN_ROOT / f"skills/{skill_name}/SKILL.md"
        skill = skill_path.read_text()

        assert "Use when" in skill
        assert len(skill.splitlines()) < 80
        for tool_name in tool_names:
            assert tool_name in skill


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

    assert "MERCURY_TOOLS_MCP_TOKEN" in serialized
    assert env_names == {"MERCURY_TOOLS_MCP_TOKEN"}
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized
    assert "FLOWACCOUNT_CLIENT_SECRET" not in serialized
    assert "PEAK_CLIENT_SECRET" not in serialized
    assert "sk-" not in serialized
    assert "service_role" not in serialized
