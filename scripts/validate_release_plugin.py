#!/usr/bin/env python3
"""Validate the static Mercury Finance v0.2.0 release package offline."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

PLUGIN_PATH = Path("plugins/mercury-finance/.codex-plugin/plugin.json")
MCP_PATH = Path("plugins/mercury-finance/.mcp.json")
PYPROJECT_PATH = Path("pyproject.toml")
EXPECTED_ARGS = [
    "--from",
    "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.0",
    "mercury",
    "mcp",
    "serve-local",
]
EXPECTED_DEPENDENCIES = [
    "cryptography==49.0.0",
    "httpx==0.28.1",
    "mcp==1.26.0",
    "pydantic==2.13.4",
    "python-dotenv==1.2.2",
    "pyyaml==6.0.3",
    "starlette==1.3.1",
    "uvicorn==0.50.0",
]
FORBIDDEN_CREDENTIAL_TEXT = re.compile(
    r"(?:private(?:[_ -][a-z0-9]+)*[_ -]token|bearer[_ -]?token|client[_ -]?secret|"
    r"api[_ -]?key|password|service[_ -]?role)",
    flags=re.IGNORECASE,
)


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read {path}: {error}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must contain a JSON object")
        return {}
    return value


def _read_pyproject(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"cannot read {path}: {error}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must contain a TOML object")
        return {}
    return value


def validate_release(root: Path) -> list[str]:
    """Return static release-contract failures without network or subprocess use."""

    errors: list[str] = []
    plugin = _read_json(root / PLUGIN_PATH, errors)
    mcp = _read_json(root / MCP_PATH, errors)
    pyproject = _read_pyproject(root / PYPROJECT_PATH, errors)

    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or list(servers) != ["mercury-finance"]:
        errors.append("mcp manifest must declare exactly one server named mercury-finance")
        server: dict[str, Any] = {}
    else:
        candidate = servers["mercury-finance"]
        if not isinstance(candidate, dict):
            errors.append("mercury-finance server must be a JSON object")
            server = {}
        else:
            server = candidate

    if server:
        if "url" in server or server.get("type") in {"http", "streamable-http"}:
            errors.append("mcp launcher must not declare an HTTP URL")
        if "env" in server or any(
            key in server for key in ("headers", "bearer_token_env_var", "token", "credential")
        ):
            errors.append("mcp launcher must not declare environment or credential values")
        if server.get("command") != "uvx":
            errors.append("mcp launcher command must be uvx")
        if server.get("args") != EXPECTED_ARGS:
            errors.append("mcp launcher must use the immutable v0.2.0 Git tag")
        if server.get("cwd") != ".":
            errors.append("mcp launcher cwd must be .")
        if server.get("tool_timeout_sec") != 900:
            errors.append("mcp launcher tool_timeout_sec must be 900")
        if set(server) != {"command", "args", "cwd", "tool_timeout_sec"}:
            errors.append(
                "mcp launcher must not include additional transport or credential settings"
            )

    serialized_manifest = json.dumps({"plugin": plugin, "mcp": mcp}, sort_keys=True)
    if FORBIDDEN_CREDENTIAL_TEXT.search(serialized_manifest):
        errors.append("plugin manifest must not contain private token names or credential values")

    interface = plugin.get("interface") if isinstance(plugin.get("interface"), dict) else {}
    if plugin.get("name") != "mercury-finance":
        errors.append("plugin name must be mercury-finance")
    if plugin.get("version") != "0.2.0+codex.20260711":
        errors.append("plugin version must be 0.2.0+codex.20260711")
    if plugin.get("mcpServers") != "./.mcp.json":
        errors.append("plugin must reference ./.mcp.json")
    if interface.get("capabilities") != ["Interactive", "Read", "Write"]:
        errors.append("plugin capabilities must be Interactive, Read, Write")

    project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    optional = (
        project.get("optional-dependencies")
        if isinstance(project.get("optional-dependencies"), dict)
        else {}
    )
    if project.get("version") != "0.2.0":
        errors.append("package version must be 0.2.0")
    if project.get("dependencies") != EXPECTED_DEPENDENCIES:
        errors.append("release runtime dependencies must be exact v0.2.0 pins")
    if optional.get("openai") != ["openai==2.44.0"]:
        errors.append("openai must be optional at exactly 2.44.0")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors = validate_release(args.root.resolve())
    if errors:
        print("release plugin validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "release plugin validation passed "
        "(static checks only; remote tag smoke deferred to Task18)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
