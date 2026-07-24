from __future__ import annotations

import json
import tomllib
from pathlib import Path

from mercury_tools.mcp.contracts import HOSTED_MCP_URL, HOSTED_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "mercury-finance"


def test_plugin_installs_exactly_one_hosted_mcp() -> None:
    payload = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))

    assert payload == {
        "mcpServers": {
            "mercury-finance": {
                "type": "http",
                "url": HOSTED_MCP_URL,
                "note": "Mercury Accounting and ERP connector platform.",
            }
        }
    }
    assert len(HOSTED_TOOL_NAMES) == 24


def test_package_plugin_and_marketplace_versions_are_consistent() -> None:
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )

    assert package["project"]["version"] == manifest["version"]
    assert marketplace["plugins"] == [
        {
            "name": "mercury-finance",
            "source": {"source": "local", "path": "./plugins/mercury-finance"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Finance",
        }
    ]


def test_public_plugin_contains_15_skills_and_no_second_mercury_server() -> None:
    skills = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    public_text = "\n".join(path.read_text(encoding="utf-8").lower() for path in skills)

    assert len(skills) == 15
    assert "advanced_local_handoff" not in public_text
    assert "mercury mcp serve-local" not in public_text
