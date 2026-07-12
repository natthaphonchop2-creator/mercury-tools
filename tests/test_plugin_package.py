import json
import re
from pathlib import Path

from mercury_tools.db.product import SKILL_CATALOG_SEED

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins/mercury-finance"
SKILLS_ROOT = PLUGIN_ROOT / "skills"

SETUP_SKILLS = (
    "connector-setup-guide-th",
    "connector-credential-setup-th",
    "flowaccount-connector-setup-th",
    "peak-connector-setup-th",
)
READ_SKILLS = (
    "company-health-check-th",
    "vat-summary-th",
    "invoice-review-th",
    "management-report-th",
)
EXPECTED_DESCRIPTIONS = {
    "connector-setup-guide-th": (
        "Use when the user needs to choose or configure an accounting or ERP connector"
    ),
    "connector-credential-setup-th": (
        "Use when an accounting or ERP task is blocked because local connector "
        "credentials are not ready"
    ),
    "flowaccount-connector-setup-th": (
        "Use when a FlowAccount task needs local connector setup or connection "
        "troubleshooting"
    ),
    "peak-connector-setup-th": (
        "Use when a PEAK task needs local connector setup or connection troubleshooting"
    ),
    "company-health-check-th": (
        "Use when the user asks for company health, revenue, VAT, cash flow, or "
        "accounting status summaries"
    ),
    "vat-summary-th": (
        "Use when the user asks for Thai VAT output tax, input tax, filing context, "
        "or tax-period summaries"
    ),
    "invoice-review-th": (
        "Use when the user asks to review invoices, tax invoices, receipts, missing "
        "fields, or accounting evidence"
    ),
    "management-report-th": (
        "Use when the user asks for Thai management reports, owner summaries, CFO "
        "packs, or monthly accounting narratives"
    ),
    "mercury-flow-runner": (
        "Use when the user asks to list, save, preview, or run Mercury Flows for "
        "accounting workflows"
    ),
    "flowaccount-journal-posting-th": (
        "Use when the user asks to record, draft, post, or approve a FlowAccount "
        "journal entry"
    ),
}
READ_TOOL_ORDER = (
    "credential_status",
    "retrieve_context_pack",
    "search_erp_actions",
    "get_erp_action_schema",
    "run_erp_read",
)
PACKAGE_FORBIDDEN_TERMS = {
    "approve_flowaccount_journal",
    "connector_status",
    "create_flowaccount_journal_draft",
    "create_public_workspace",
    "list_connectors",
    "preview_flowaccount_journal",
    "required_secret_fields",
    "retrieve_workspace_context_pack",
    "run_mercury_flow",
    "start_connector_setup",
    "submit_connector_credentials",
    "validate_connector_connection",
    "workspace_id",
}
CREDENTIAL_FIELD_NAMES = {
    "application_code",
    "client_id",
    "client_secret",
    "connect_id",
    "connect_key",
    "user_token",
}


def skill_text(skill_name: str) -> str:
    return (SKILLS_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")


def assert_terms_in_order(text: str, terms: tuple[str, ...]) -> None:
    cursor = 0
    for term in terms:
        position = text.find(term, cursor)
        assert position >= 0, f"missing {term!r} after offset {cursor}"
        cursor = position + len(term)


def frontmatter_description(text: str) -> str:
    match = re.search(r"(?m)^description: (.+)$", text)
    assert match is not None
    return match.group(1)


def test_product_catalog_contains_every_bundled_plugin_skill() -> None:
    bundled = {path.parent.name for path in SKILLS_ROOT.glob("*/SKILL.md")}
    catalog = {row["skill_id"] for row in SKILL_CATALOG_SEED}

    assert catalog == bundled == set(EXPECTED_DESCRIPTIONS)


def test_marketplace_contains_exactly_one_mercury_plugin() -> None:
    marketplace = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )

    assert [item["name"] for item in marketplace["plugins"]] == ["mercury-finance"]
    assert not (ROOT / "plugins/mercury-finance-private").exists()
    assert not (ROOT / "tests/test_private_mcp.py").exists()


def test_marketplace_points_to_plugin_folder() -> None:
    data = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    mercury = data["plugins"][0]

    assert data["name"] == "mercury-tools"
    assert data["interface"]["displayName"] == "Mercury Tools"
    assert mercury["source"]["path"] == "./plugins/mercury-finance"
    assert mercury["source"]["source"] == "local"
    assert mercury["policy"]["installation"] == "AVAILABLE"
    assert mercury["policy"]["authentication"] == "ON_INSTALL"
    assert mercury["category"] == "Finance"


def test_skill_frontmatter_descriptions_are_trigger_only() -> None:
    for skill_name, expected in EXPECTED_DESCRIPTIONS.items():
        assert frontmatter_description(skill_text(skill_name)) == expected


def test_setup_skills_use_the_exact_local_credential_gate() -> None:
    required_order = (
        "credential_status",
        "If required credentials are missing, stop",
        "mercury credentials setup",
        "After the user confirms setup is complete",
        "credential_status",
        "mercury credentials test",
        "Continue only when the test reports `connected`",
    )

    for skill_name in SETUP_SKILLS:
        text = skill_text(skill_name)
        assert_terms_in_order(text, required_order)
        assert "Never ask for, accept, or paste credentials in chat." in text


def test_read_skills_use_only_the_generic_read_sequence() -> None:
    disallowed_tools = {
        "preview_erp_write",
        "confirm_erp_write",
        "execute_erp_write",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
    }

    for skill_name in READ_SKILLS:
        text = skill_text(skill_name)
        assert_terms_in_order(text, READ_TOOL_ORDER)
        assert "citations" in text
        assert "ตอบภาษาไทยแบบกระชับ" in text
        assert "unless the user explicitly requests audit detail" in text
        assert not any(tool in text for tool in disallowed_tools)


def test_journal_skill_uses_bound_generic_write_sequence_once() -> None:
    text = skill_text("flowaccount-journal-posting-th")
    required_order = (
        "required accounting context",
        "total debit equals total credit",
        "search_erp_actions",
        "get_erp_action_schema",
        "preview_erp_write",
        "Stop and wait for explicit confirmation",
        "request_id",
        "payload_hash",
        "confirm_erp_write",
        "execute_erp_write",
    )

    assert_terms_in_order(text, required_order)
    assert "Call `execute_erp_write` exactly once" in text
    assert "A Tier 2 approval is a separate action" in text
    assert "fresh `preview_erp_write`" in text
    assert "two separate explicit confirmations" in text
    assert text.count("confirm_erp_write") == 2
    assert "get_erp_request_status" in text
    assert "never replay or retry" in text


def test_flow_runner_cannot_confirm_execute_or_retry_writes() -> None:
    text = skill_text("mercury-flow-runner")

    for tool in (
        "credential_status",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
    ):
        assert tool in text
    assert "read actions or `preview_erp_write`" in text
    assert "Never self-confirm or execute a write" in text
    assert "Never retry a write" in text
    assert "confirm_erp_write" not in text
    assert "execute_erp_write" not in text


def test_public_journal_catalog_tags_exclude_private() -> None:
    journal = next(
        row
        for row in SKILL_CATALOG_SEED
        if row["skill_id"] == "flowaccount-journal-posting-th"
    )

    assert journal["tags"] == ["flowaccount", "journal", "write", "thai"]


def test_skill_package_has_no_private_or_workspace_tool_terms() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    )

    assert not PACKAGE_FORBIDDEN_TERMS.intersection(combined.split())
    for term in PACKAGE_FORBIDDEN_TERMS:
        assert term not in combined


def test_skill_package_has_no_secret_fields_or_credential_chat_flow() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    ).lower()

    for field_name in CREDENTIAL_FIELD_NAMES:
        assert field_name not in combined
    for unsafe_phrase in (
        "ask the user for credentials",
        "ask the user to paste",
        "provide your credentials",
        "send your credentials",
        "send credentials in chat",
        "submit credentials",
    ):
        assert unsafe_phrase not in combined


def test_plugin_capabilities_match_current_public_manifest() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )

    assert manifest["interface"]["capabilities"] == ["Interactive", "Read"]


def test_plugin_declares_current_remote_mcp_without_secret_values() -> None:
    plugin = json.loads(
        (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    serialized = json.dumps({"plugin": plugin, "mcp": mcp})

    assert plugin["name"] == "mercury-finance"
    assert plugin["skills"] == "./skills/"
    assert plugin["mcpServers"] == "./.mcp.json"
    assert plugin["interface"]["displayName"] == "Mercury Finance"
    assert "https://mercury-tools-mcp.onrender.com/mcp" in serialized
    assert "bearer_token_env_var" not in serialized
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized
    assert "client_secret" not in serialized


def test_judge_quickstart_matches_current_public_plugin() -> None:
    text = (ROOT / "docs/JUDGE_QUICKSTART.md").read_text(encoding="utf-8")

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


def test_plugin_package_has_no_embedded_secret_env_names_or_values() -> None:
    files = [
        ROOT / ".agents/plugins/marketplace.json",
        PLUGIN_ROOT / ".codex-plugin/plugin.json",
        PLUGIN_ROOT / ".mcp.json",
        *sorted(SKILLS_ROOT.glob("*/SKILL.md")),
    ]
    serialized = "\n".join(file.read_text(encoding="utf-8") for file in files)
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
