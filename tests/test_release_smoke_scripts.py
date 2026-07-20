from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.smoke_local_plugin import EXPECTED_TOOLS as LOCAL_PLUGIN_TOOLS  # noqa: E402
from scripts.smoke_tagged_marketplace import (  # noqa: E402
    _EXPECTED_LOCAL_TOOLS,
    TaggedMarketplaceError,
    build_tagged_smoke_plan,
)
from scripts.smoke_tagged_marketplace import (  # noqa: E402
    main as tagged_marketplace_main,
)
from scripts.verify_public_release import (  # noqa: E402
    PublicReleaseError,
    build_public_release_plan,
)

V030_LOCAL_TOOLS = {
    "connector_status",
    "credential_status",
    "execute_erp_create",
    "execute_erp_update",
    "execute_sensitive_erp_action",
    "get_document",
    "get_erp_action_schema",
    "get_erp_request_status",
    "import_erp_spec",
    "list_connector_drivers",
    "list_workspace_flows",
    "prepare_erp_mutation",
    "retrieve_context_pack",
    "run_accounting_skill",
    "run_erp_read",
    "run_mercury_flow",
    "run_workspace_flow",
    "save_workspace_flow",
    "search_erp_actions",
    "search_knowledge",
}


def test_release_smokes_use_the_reviewed_v030_local_tool_contract() -> None:
    assert _EXPECTED_LOCAL_TOOLS == V030_LOCAL_TOOLS
    assert LOCAL_PLUGIN_TOOLS == V030_LOCAL_TOOLS


def test_tagged_marketplace_plan_defaults_launcher_to_marketplace_source(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"

    plan = build_tagged_smoke_plan(
        repo="natthaphonchop2-creator/mercury-tools",
        tag="v0.3.0",
        expected_tools=20,
        codex_home=codex_home,
    )

    assert plan.launcher_source == (
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.3.0"
    )
    assert plan.expected_tools == 20
    assert plan.environment == {
        "CODEX_HOME": str(codex_home),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert plan.commands == (
        (
            "codex",
            "plugin",
            "marketplace",
            "add",
            "natthaphonchop2-creator/mercury-tools",
            "--ref",
            "v0.3.0",
            "--sparse",
            ".agents/plugins",
            "--sparse",
            "plugins/mercury-finance",
        ),
        ("codex", "plugin", "add", "mercury-finance@mercury-tools"),
        ("codex", "mcp", "list", "--json"),
    )


def test_tagged_marketplace_cli_supports_staging_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = []
    monkeypatch.setattr(
        "scripts.smoke_tagged_marketplace.run_tagged_smoke",
        plans.append,
    )

    assert (
        tagged_marketplace_main(
            [
                "--repo",
                "natthaphonchop2-creator/mercury-tools",
                "--tag",
                "v0.3.0",
                "--launcher-repo",
                "natthaphonchop2-creator/mercury-tools-staging",
                "--launcher-ref",
                "v0.3.0-rc.1",
                "--expected-tools",
                "20",
                "--codex-home",
                str(tmp_path / "codex-home"),
            ]
        )
        == 0
    )

    assert len(plans) == 1
    assert plans[0].launcher_source == (
        "git+https://github.com/natthaphonchop2-creator/mercury-tools-staging.git@v0.3.0-rc.1"
    )
    assert plans[0].commands[0][4:6] == (
        "natthaphonchop2-creator/mercury-tools",
        "--ref",
    )


@pytest.mark.parametrize(
    ("launcher_repo", "launcher_ref", "error"),
    (
        ("invalid repo", "v0.3.0", "launcher_repository_invalid"),
        ("natthaphonchop2-creator/mercury-tools-staging", "main", "launcher_ref_invalid"),
    ),
)
def test_tagged_marketplace_plan_rejects_invalid_launcher_values(
    tmp_path: Path,
    launcher_repo: str,
    launcher_ref: str,
    error: str,
) -> None:
    with pytest.raises(TaggedMarketplaceError, match=error):
        build_tagged_smoke_plan(
            repo="natthaphonchop2-creator/mercury-tools",
            tag="v0.3.0",
            launcher_repo=launcher_repo,
            launcher_ref=launcher_ref,
            expected_tools=20,
            codex_home=tmp_path / "codex-home",
        )


@pytest.mark.parametrize("tag", ("main", "0.3.0", "v0.3", "v0.3.0^{commit}"))
def test_tagged_marketplace_plan_rejects_moving_or_ambiguous_refs(
    tmp_path: Path,
    tag: str,
) -> None:
    with pytest.raises(TaggedMarketplaceError, match="tag_invalid"):
        build_tagged_smoke_plan(
            repo="natthaphonchop2-creator/mercury-tools",
            tag=tag,
            expected_tools=20,
            codex_home=tmp_path / "codex-home",
        )


def test_public_release_plan_is_anonymous_and_exact(tmp_path: Path) -> None:
    plan = build_public_release_plan(
        repo="natthaphonchop2-creator/mercury-tools",
        tag="v0.3.0",
        release="v0.3.0",
        expected_tools=20,
        workspace=tmp_path,
    )

    assert plan.tag == plan.release == "v0.3.0"
    assert plan.environment == {
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert plan.clone_url == (
        "https://github.com/natthaphonchop2-creator/mercury-tools.git"
    )
    assert "GH_TOKEN" not in plan.environment
    assert "GITHUB_TOKEN" not in plan.environment


def test_public_release_plan_requires_matching_immutable_tag_and_release(
    tmp_path: Path,
) -> None:
    with pytest.raises(PublicReleaseError, match="release_ref_mismatch"):
        build_public_release_plan(
            repo="natthaphonchop2-creator/mercury-tools",
            tag="v0.3.0",
            release="v0.3.1",
            expected_tools=20,
            workspace=tmp_path,
        )
