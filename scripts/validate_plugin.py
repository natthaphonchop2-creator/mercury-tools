#!/usr/bin/env python3
"""Validate the public Mercury Finance plugin distribution."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "mercury-finance"
HOSTED_MCP_URL = "https://mercury-tools-mcp.onrender.com/mcp"
BLOCKED_PUBLIC_TERMS = (
    "advanced_local_handoff",
    "advanced local mercury mcp",
    "docs/advanced_local_erp.md",
    "mercury mcp serve-local",
)


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _validate_skill(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} is missing YAML frontmatter")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    if not isinstance(metadata, dict):
        raise ValueError(f"{path} frontmatter must be an object")
    if metadata.get("name") != path.parent.name:
        raise ValueError(f"{path} name must match its directory")
    if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
        raise ValueError(f"{path} requires a description")
    if not body.strip():
        raise ValueError(f"{path} requires instructions")


def main() -> int:
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    manifest = _load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")
    mcp = _load_json(PLUGIN_ROOT / ".mcp.json")
    marketplace = _load_json(ROOT / ".agents" / "plugins" / "marketplace.json")

    if package["version"] != manifest["version"]:
        raise ValueError("package and plugin versions must match")
    if manifest["name"] != "mercury-finance":
        raise ValueError("unexpected plugin name")
    if manifest.get("mcpServers") != "./.mcp.json":
        raise ValueError("plugin must reference its bundled .mcp.json")

    expected_server = {
        "type": "http",
        "url": HOSTED_MCP_URL,
        "note": "Mercury Accounting and ERP connector platform.",
    }
    if mcp != {"mcpServers": {"mercury-finance": expected_server}}:
        raise ValueError("plugin must install exactly one hosted Mercury MCP")

    entries = marketplace.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("marketplace must contain exactly one plugin")
    entry = entries[0]
    expected_entry = {
        "name": "mercury-finance",
        "source": {"source": "local", "path": "./plugins/mercury-finance"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Finance",
    }
    if entry != expected_entry:
        raise ValueError("marketplace entry does not match the public plugin contract")

    skills = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    if len(skills) != 15:
        raise ValueError(f"expected 15 packaged Skills, found {len(skills)}")
    for skill in skills:
        _validate_skill(skill)

    logo = PLUGIN_ROOT / "assets" / "mercury-finance-logo.png"
    if not logo.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("plugin logo is not a PNG")

    public_paths = [
        ROOT / "README.md",
        ROOT / "docs",
        PLUGIN_ROOT,
        ROOT / "submission" / "openai-plugin",
        ROOT / "chatgpt-app-submission.json",
    ]
    for public_path in public_paths:
        paths = [public_path] if public_path.is_file() else public_path.rglob("*")
        for path in paths:
            if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                continue
            lowered = path.read_text(encoding="utf-8").lower()
            for term in BLOCKED_PUBLIC_TERMS:
                if term in lowered:
                    raise ValueError(f"{path} still references removed product path: {term}")

    print(
        f"Mercury plugin valid: version {package['version']}, "
        f"{len(skills)} Skills, one hosted MCP"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Mercury plugin validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
