from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATEMENT = (
    "Mercury Tools is an independent open-source project and is not affiliated "
    "with Mercury Technologies, Inc."
)
LAUNCHER = (
    "git+https://github.com/natthaphonchop2-creator/"
    "mercury-tools.git@v0.2.1"
)


def test_readme_is_exact_v021_public_surface() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert STATEMENT in text
    assert "--ref v0.2.1" in text
    assert LAUNCHER in text
    assert "exactly 19 local tools" in text
    assert "20-tool hosted HTTP surface" in text
    assert "v0.2.0" not in text
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
    assert "40-character lowercase Git commit" in example
    for obsolete in (
        "MERCURY_PRIVATE_MCP_PATH",
        "MERCURY_PRIVATE_MCP_TOKEN",
        "MERCURY_CREDENTIAL_VAULT_SECRET",
        "encrypted connector records",
        "credential vault",
    ):
        assert obsolete not in combined


def test_judge_quickstart_has_all_exact_copy_paste_workflows() -> None:
    text = (ROOT / "docs/JUDGE_QUICKSTART.md").read_text(encoding="utf-8")

    assert "codex plugin marketplace add natthaphonchop2-creator/mercury-tools" in text
    assert "--ref v0.2.1" in text
    assert "codex plugin add mercury-finance@mercury-tools" in text
    assert LAUNCHER in text
    assert "credentials setup flowaccount --env sandbox" in text
    assert "credentials test flowaccount --env sandbox" in text
    assert "run_erp_read" in text
    assert "preview_erp_write" in text
    assert "Cross-MCP reconciliation" in text
    assert "connect-or-upload" in text
    assert "credentials clear flowaccount --env sandbox" in text
    assert "codex plugin remove mercury-finance@mercury-tools" in text
    assert "Task 17" not in text
    assert "remote-tag smoke" not in text


def test_release_notes_are_candidate_safe_and_name_every_required_gate() -> None:
    text = (ROOT / "docs/RELEASE_V0.2.1.md").read_text(encoding="utf-8")

    assert STATEMENT in text
    assert "Release candidate" in text
    assert "does not assert that the tag, assets, deployment, or visibility change exists" in text
    for marker in (
        "Gitleaks 8.24.3",
        "TruffleHog 3.88.32",
        "Supabase migration",
        "FlowAccount 190-action",
        "PEAK 64-action",
        "one `mercury-finance` stdio MCP",
        "19 local tools",
        "20 hosted tools",
        "`/healthz`",
        "deployment commit",
        "post-public",
    ):
        assert marker in text
