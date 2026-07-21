from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATEMENT = (
    "Mercury Tools is an independent open-source project and is not affiliated "
    "with Mercury Technologies, Inc."
)
HOSTED_MCP_URL = "https://mercury-tools-mcp.onrender.com/mcp"


def test_readme_is_exact_v030_public_surface() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert " ".join(STATEMENT.split()) in " ".join(text.split())
    assert "one\nhosted Mercury MCP" in text
    assert HOSTED_MCP_URL in text
    assert "v0.2.2" not in text
    assert "repository-local `stdio` MCP" not in text
    assert "Task 18" not in text


def test_plugin_long_description_contains_exact_non_affiliation_statement() -> None:
    plugin = json.loads(
        (ROOT / "plugins/mercury-finance/.codex-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )

    assert STATEMENT in plugin["interface"]["longDescription"]


def test_remote_deployment_and_environment_keep_erp_credentials_local() -> None:
    deployment = (ROOT / "docs/REMOTE_DEPLOYMENT.md").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    combined = deployment + "\n" + example

    assert "Cloud stores catalog, RAG, and audit metadata only." in deployment
    assert "ERP credentials remain repository-local" in deployment
    assert "MERCURY_DEPLOYMENT_COMMIT=" in example
    assert "RENDER_GIT_COMMIT=" in example
    assert "optional explicit override" in deployment
    assert "invalid override fails closed" in deployment
    assert "Render supplies `RENDER_GIT_COMMIT`" in deployment
    assert "The hosted endpoint exposes 24 tools" in deployment
    assert "advanced-local Mercury MCP with 20 tools" in deployment
    assert "--version 0.3.0" in deployment
    assert HOSTED_MCP_URL in deployment
    for obsolete in (
        "MERCURY_PRIVATE_MCP_PATH",
        "MERCURY_PRIVATE_MCP_TOKEN",
        "MERCURY_CREDENTIAL_VAULT_SECRET",
        "encrypted connector records",
        "credential vault",
    ):
        assert obsolete not in combined


def test_judge_quickstart_has_the_v030_hosted_and_advanced_local_workflows() -> None:
    text = (ROOT / "docs/JUDGE_QUICKSTART.md").read_text(encoding="utf-8")

    assert "# Mercury Finance v0.3.0 Judge Quickstart" in text
    assert "codex plugin marketplace add natthaphonchop2-creator/mercury-tools" in text
    assert "--ref v0.3.0" in text
    assert "codex plugin add mercury-finance@mercury-tools" in text
    assert HOSTED_MCP_URL in text
    assert "24 hosted tools" in text
    assert "20 advanced-local tools" in text
    assert "get_connector_setup" in text
    assert "prepare_erp_mutation" in text
    assert "execute_erp_create" in text
    assert "execute_erp_update" in text
    assert "execute_sensitive_erp_action" in text
    assert "Cross-MCP reconciliation" in text
    assert "connect-or-upload" in text
    assert "codex plugin remove mercury-finance@mercury-tools" in text
    assert "preview_erp_write" not in text
    assert "confirm_erp_write" not in text
    assert "execute_erp_write" not in text
    assert "MERCURY_LAUNCHER" not in text
    assert "Task 17" not in text
    assert "remote-tag smoke" not in text


def test_action_catalog_uses_v030_prepare_and_class_specific_execution() -> None:
    text = (ROOT / "docs/ACTION_CATALOG.md").read_text(encoding="utf-8")

    assert "prepare_erp_mutation" in text
    assert "execute_erp_create" in text
    assert "execute_erp_update" in text
    assert "execute_sensitive_erp_action" in text
    assert "preview_erp_write" not in text
    assert "confirm_erp_write" not in text
    assert "execute_erp_write" not in text


def test_release_notes_are_candidate_safe_and_name_every_required_gate() -> None:
    text = (ROOT / "docs/RELEASE_V0.2.2.md").read_text(encoding="utf-8")

    assert STATEMENT in text
    assert "Release candidate" in text
    assert "does not assert that the tag, assets, deployment, or visibility change exists" in text
    for marker in (
        "Gitleaks 8.24.3",
        "TruffleHog 3.88.32",
        "Supabase stack",
        "FlowAccount exact",
        "190-action coverage",
        "PEAK 64-action",
        "one `mercury-finance` stdio MCP",
        "19 local tools",
        "20 hosted tools",
        "`/healthz`",
        "Render exact commit",
        "post-public",
        "release-control",
        "secretless, networkless, read-only candidate container",
        "exact artifact IDs",
    ):
        assert marker in text
