from __future__ import annotations

import re
from pathlib import Path

from mercury_tools.mcp.contracts import (
    LEGACY_HOSTED_TOOL_NAMES,
    V1_HOSTED_TOOL_NAMES,
)


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "plugins" / "mercury-finance" / "skills"
SETUP_SKILLS = (
    "connector-credential-setup-th",
    "connector-setup-guide-th",
    "flowaccount-connector-setup-th",
    "peak-connector-setup-th",
)
SETUP_LIFECYCLE = (
    "get_mercury_context",
    "list_accounting_providers",
    "start_provider_connection",
    "list_provider_connections",
    "connector_status",
    "list_provider_capabilities",
)
LEGACY_ONLY_TOOLS = LEGACY_HOSTED_TOOL_NAMES - V1_HOSTED_TOOL_NAMES


def _skill_text(skill_id: str) -> str:
    return (SKILLS_ROOT / skill_id / "SKILL.md").read_text(encoding="utf-8")


def test_all_packaged_skills_use_only_the_mercury_v1_tool_contract() -> None:
    paths = sorted(SKILLS_ROOT.glob("*/SKILL.md"))

    assert len(paths) == 15
    for path in paths:
        text = path.read_text(encoding="utf-8")
        referenced = set(re.findall(r"`([a-z][a-z0-9_]+)`", text))

        assert not (referenced & LEGACY_ONLY_TOOLS), path
        assert all(tool not in text for tool in LEGACY_ONLY_TOOLS), path
        assert "get_mercury_context" in text, path
        assert "credentials never enter chat or model context" in text, path
        assert "provider calls remain with" not in text.lower(), path
        assert "mercury never receives the provider credential" not in text.lower(), path


def test_setup_skills_follow_one_ordered_v1_connection_lifecycle() -> None:
    for skill_id in SETUP_SKILLS:
        text = _skill_text(skill_id)
        positions = [text.index(tool) for tool in SETUP_LIFECYCLE]

        assert positions == sorted(positions), skill_id
        assert "authorization_url" in text or "setup_url" in text, skill_id
        assert "Do not continue" in text, skill_id
        assert "list_provider_capabilities" in text, skill_id


def test_accounting_skills_use_published_skill_execution_and_exact_capability_gates() -> None:
    setup = set(SETUP_SKILLS)
    for path in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        if path.parent.name in setup:
            continue
        text = path.read_text(encoding="utf-8")

        assert "connector_status" in text, path
        assert "list_provider_capabilities" in text, path
        assert "run_accounting_skill" in text, path
        assert "skill_version" in text, path
        assert "qualification" in text.lower(), path


def test_mutation_skill_requires_preview_confirmation_single_dispatch_and_verification() -> None:
    text = _skill_text("flowaccount-journal-posting-th")
    normalized = " ".join(text.lower().split())

    for phrase in (
        "immutable preview",
        "explicit confirmation",
        "dispatch once",
        "verify or reconcile",
        "sanitized audit",
    ):
        assert phrase in normalized
    assert "never retry" in normalized
