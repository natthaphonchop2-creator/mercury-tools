from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.smoke_tagged_marketplace import (  # noqa: E402
    TaggedMarketplaceError,
    build_tagged_smoke_plan,
)
from scripts.verify_public_release import (  # noqa: E402
    PublicReleaseError,
    build_public_release_plan,
)


def test_tagged_marketplace_plan_is_immutable_and_isolated(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"

    plan = build_tagged_smoke_plan(
        repo="natthaphonchop2-creator/mercury-tools",
        tag="v0.2.1",
        expected_tools=19,
        codex_home=codex_home,
    )

    assert plan.launcher_source == (
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.1"
    )
    assert plan.expected_tools == 19
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
            "v0.2.1",
            "--sparse",
            ".agents/plugins",
            "--sparse",
            "plugins/mercury-finance",
        ),
        ("codex", "plugin", "add", "mercury-finance@mercury-tools"),
        ("codex", "mcp", "list", "--json"),
    )


@pytest.mark.parametrize("tag", ("main", "0.2.1", "v0.2", "v0.2.1^{commit}"))
def test_tagged_marketplace_plan_rejects_moving_or_ambiguous_refs(
    tmp_path: Path,
    tag: str,
) -> None:
    with pytest.raises(TaggedMarketplaceError, match="tag_invalid"):
        build_tagged_smoke_plan(
            repo="natthaphonchop2-creator/mercury-tools",
            tag=tag,
            expected_tools=19,
            codex_home=tmp_path / "codex-home",
        )


def test_public_release_plan_is_anonymous_and_exact(tmp_path: Path) -> None:
    plan = build_public_release_plan(
        repo="natthaphonchop2-creator/mercury-tools",
        tag="v0.2.1",
        release="v0.2.1",
        expected_tools=19,
        workspace=tmp_path,
    )

    assert plan.tag == plan.release == "v0.2.1"
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
            tag="v0.2.1",
            release="v0.2.2",
            expected_tools=19,
            workspace=tmp_path,
        )
