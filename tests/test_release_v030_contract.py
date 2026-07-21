from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

from mercury_tools.connectors.catalog import CONNECTOR_CATALOG

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PLUGIN_VERSION = "0.3.0+codex.20260719"
EXPECTED_RELEASE_TAG = "v0.3.0"
EXPECTED_RELEASE_CONTROL_PIN = "c21888e09c5759b22744317848fdff63a146a779"
EXPECTED_VERSION = "0.3.0"
EXPECTED_MIGRATION = "20260719120000"
EXPECTED_HOSTED_TOOLS = {
    "check_flow_syntax",
    "connector_capabilities",
    "connector_status",
    "create_public_workspace",
    "flow_cheat_sheet",
    "get_accounting_skill_schema",
    "get_connector_setup",
    "get_document",
    "get_public_workspace",
    "inspect_flow_files",
    "link_connector_profile",
    "list_accounting_skills",
    "list_connectors",
    "list_workspace_flows",
    "retrieve_context_pack",
    "retrieve_workspace_context_pack",
    "run_accounting_skill",
    "run_flow_files",
    "run_inline_flow",
    "run_workspace_flow",
    "save_workspace_flow",
    "search_knowledge",
    "unlink_connector_profile",
    "validate_connector_connection",
}
EXPECTED_CONNECTORS = {
    "custom",
    "express",
    "flowaccount",
    "generic_mcp",
    "peak",
}
EXPECTED_RELEASE_PATHS = {
    ".github/workflows/release-v0.3.0.yml",
    "chatgpt-app-submission.json",
    "docs/RELEASE_V0.3.0.md",
    "plugins/mercury-finance/.codex-plugin/plugin.json",
    "plugins/mercury-finance/.mcp.json",
    "src/mercury_tools/connectors/catalog.py",
    "submission/openai-plugin/listing.json",
    "submission/openai-plugin/test-cases.json",
    "supabase/migrations/20260719120000_connector_neutral_profiles.sql",
}
USABLE_SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"\b(?:ghp|github_pat|sk)-(?:[A-Za-z0-9_-]{20,})"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\b"),
)


def _json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _package_version() -> str:
    source = (ROOT / "src/mercury_tools/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', source, flags=re.MULTILINE)
    assert match is not None
    return match.group(1)


def _latest_required_migration() -> str:
    identities = {
        match.group(1)
        for path in (ROOT / "supabase/migrations").glob("*.sql")
        if (match := re.match(r"^(\d{14})_", path.name)) is not None
    }
    assert identities
    return max(identities)


def test_v030_release_identity_is_exact() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = _json(ROOT / "plugins/mercury-finance/.codex-plugin/plugin.json")
    workflow_path = ROOT / ".github/workflows/release-v0.3.0.yml"
    workflow = (
        yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        if workflow_path.is_file()
        else {}
    )

    project_version = project["project"]["version"]
    package_version = _package_version()
    plugin_version = plugin.get("version")
    release_workflow_tag = workflow.get("env", {}).get("RELEASE_TAG")
    release_control_pin = workflow.get("env", {}).get("RELEASE_CONTROL_PIN")
    latest_required_migration = _latest_required_migration()

    assert project_version == EXPECTED_VERSION
    assert package_version == EXPECTED_VERSION
    assert plugin_version == EXPECTED_PLUGIN_VERSION
    assert release_workflow_tag == EXPECTED_RELEASE_TAG
    assert release_control_pin == EXPECTED_RELEASE_CONTROL_PIN
    assert latest_required_migration == EXPECTED_MIGRATION


def test_v030_release_artifacts_are_hosted_connector_neutral_and_secretless() -> None:
    expected_tree = _json(ROOT / "release-control/expected-public-tree.json")
    plugin = _json(ROOT / "plugins/mercury-finance/.codex-plugin/plugin.json")
    mcp = _json(ROOT / "plugins/mercury-finance/.mcp.json")
    submission = _json(ROOT / "chatgpt-app-submission.json")

    assert expected_tree.get("schema_version") == 1
    assert expected_tree.get("release") == {
        "tag": EXPECTED_RELEASE_TAG,
        "version": EXPECTED_VERSION,
    }
    assert set(expected_tree.get("required_paths", [])) == EXPECTED_RELEASE_PATHS
    for relative_path in EXPECTED_RELEASE_PATHS:
        path = ROOT / relative_path
        assert path.is_file() and not path.is_symlink(), relative_path

    assert plugin.get("version") == EXPECTED_PLUGIN_VERSION
    assert plugin.get("mcpServers") == "./.mcp.json"
    assert mcp == {
        "mcpServers": {
            "mercury-finance": {
                "type": "http",
                "url": "https://mercury-tools-mcp.onrender.com/mcp",
                "note": "Mercury Accounting and ERP connector platform.",
            }
        }
    }
    assert set(submission.get("tools", {})) == EXPECTED_HOSTED_TOOLS
    assert {manifest.connector_id for manifest in CONNECTOR_CATALOG} == EXPECTED_CONNECTORS
    assert all(manifest.connection_modes for manifest in CONNECTOR_CATALOG)

    release_text = "\n".join(
        (ROOT / relative_path).read_text(encoding="utf-8", errors="strict")
        for relative_path in EXPECTED_RELEASE_PATHS
        if (ROOT / relative_path).suffix != ".png"
    )
    assert not any(pattern.search(release_text) for pattern in USABLE_SECRET_PATTERNS)


def test_github_workflows_do_not_create_uninspectable_dependency_caches() -> None:
    for workflow_path in (ROOT / ".github/workflows").glob("*.yml"):
        source = workflow_path.read_text(encoding="utf-8")
        assert "enable-cache: true" not in source, workflow_path.name
        assert "supabase/setup-cli@" not in source, workflow_path.name
