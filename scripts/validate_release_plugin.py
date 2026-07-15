#!/usr/bin/env python3
"""Validate the static Mercury Finance v0.2.1 release package offline."""

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
MARKETPLACE_PATH = Path(".agents/plugins/marketplace.json")
PYPROJECT_PATH = Path("pyproject.toml")
EXPECTED_ARGS = [
    "--from",
    "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.1",
    "mercury",
    "mcp",
    "serve-local",
]
EXPECTED_DEPENDENCIES = [
    "httpx==0.28.1",
    "mcp==1.26.0",
    "pydantic==2.13.4",
    "python-dotenv==1.2.2",
    "pyyaml==6.0.3",
    "starlette==1.3.1",
    "uvicorn==0.50.0",
]
PRIVATE_TOKEN_NAME_RE = re.compile(
    r"private(?:[_ -][a-z0-9]+)*[_ -]token",
    flags=re.IGNORECASE,
)
PLACEHOLDER_RE = re.compile(
    r"^(?:\$\{[A-Z0-9_]{1,80}\}|\[[^\]]{1,80}\]|<[^>]{1,80}>|"
    r"(?:your|replace|example|sample|dummy|demo|test)(?:[-_ ][A-Z0-9]+){0,5}|"
    r"\.{3}|\*{3,})$",
    flags=re.IGNORECASE,
)
BEARER_LITERAL_RE = re.compile(
    r"\bbearer\s+(?P<value>[A-Za-z0-9._~+/=-]{20,512})(?=$|[\s,;\"'])",
    flags=re.IGNORECASE,
)
JWT_LITERAL_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,256}\.[A-Za-z0-9_-]{3,256}\."
    r"[A-Za-z0-9_-]{3,256}\b"
)
TOKEN_PREFIX_RE = re.compile(
    r"\b(?:sk|ghp|glpat|xoxb|AKIA)[_-][A-Za-z0-9_-]{16,256}\b",
    flags=re.IGNORECASE,
)
CREDENTIAL_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"token|secret|password|authorization)",
    flags=re.IGNORECASE,
)
CREDENTIAL_LITERAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~+/=-]{11,511}")
MAX_SCAN_NODES = 10_000
MAX_SCAN_STRING_CHARS = 4_096


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


def _is_placeholder(value: str) -> bool:
    return PLACEHOLDER_RE.fullmatch(value.strip()) is not None


def _string_contains_credential_literal(key: str, value: str) -> bool:
    if len(value) > MAX_SCAN_STRING_CHARS:
        return True
    bearer = BEARER_LITERAL_RE.search(value)
    if bearer is not None and not _is_placeholder(bearer.group("value")):
        return True
    if JWT_LITERAL_RE.search(value) is not None:
        return True
    if TOKEN_PREFIX_RE.search(value) is not None:
        return True

    candidate = value.strip()
    return bool(
        CREDENTIAL_KEY_RE.search(key)
        and CREDENTIAL_LITERAL_RE.fullmatch(candidate)
        and not _is_placeholder(candidate)
    )


def _contains_credential_literal(payload: Any) -> bool:
    pending: list[tuple[str, Any]] = [("", payload)]
    for _ in range(MAX_SCAN_NODES):
        if not pending:
            return False
        key, value = pending.pop()
        if isinstance(value, dict):
            pending.extend((str(child_key), child) for child_key, child in value.items())
        elif isinstance(value, list):
            pending.extend((key, child) for child in value)
        elif isinstance(value, str) and _string_contains_credential_literal(key, value):
            return True
    return bool(pending)


def validate_release(root: Path) -> list[str]:
    """Return static release-contract failures without network or subprocess use."""

    errors: list[str] = []
    plugin = _read_json(root / PLUGIN_PATH, errors)
    mcp = _read_json(root / MCP_PATH, errors)
    marketplace = _read_json(root / MARKETPLACE_PATH, errors)
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

    if "url" in server or server.get("type") in {"http", "streamable-http"}:
        errors.append("mcp launcher must not declare an HTTP URL")
    if "env" in server or any(
        key in server for key in ("headers", "bearer_token_env_var", "token", "credential")
    ):
        errors.append("mcp launcher must not declare environment or credential values")
    if server.get("command") != "uvx":
        errors.append("mcp launcher command must be uvx")
    if server.get("args") != EXPECTED_ARGS:
        errors.append("mcp launcher must use the immutable v0.2.1 Git tag")
    if server.get("cwd") != ".":
        errors.append("mcp launcher cwd must be .")
    if server.get("tool_timeout_sec") != 900:
        errors.append("mcp launcher tool_timeout_sec must be 900")
    if set(server) != {"command", "args", "cwd", "tool_timeout_sec"}:
        errors.append("mcp launcher must not include additional transport or credential settings")

    release_manifests = {"plugin": plugin, "mcp": mcp, "marketplace": marketplace}
    serialized_manifest = json.dumps(release_manifests, sort_keys=True)
    if PRIVATE_TOKEN_NAME_RE.search(serialized_manifest):
        errors.append("plugin manifest must not contain private token names")
    if _contains_credential_literal(release_manifests):
        errors.append("plugin manifest must not contain credential literal values")

    interface = plugin.get("interface") if isinstance(plugin.get("interface"), dict) else {}
    if plugin.get("name") != "mercury-finance":
        errors.append("plugin name must be mercury-finance")
    if plugin.get("version") != "0.2.1+codex.20260713":
        errors.append("plugin version must be 0.2.1+codex.20260713")
    if plugin.get("mcpServers") != "./.mcp.json":
        errors.append("plugin must reference ./.mcp.json")
    if interface.get("capabilities") != ["Interactive", "Read", "Write"]:
        errors.append("plugin capabilities must be Interactive, Read, Write")

    marketplace_plugins = marketplace.get("plugins")
    if (
        not isinstance(marketplace_plugins, list)
        or len(marketplace_plugins) != 1
        or not isinstance(marketplace_plugins[0], dict)
        or marketplace_plugins[0].get("name") != "mercury-finance"
    ):
        errors.append("marketplace must contain exactly one mercury-finance plugin")
        marketplace_plugin: dict[str, Any] = {}
    else:
        marketplace_plugin = marketplace_plugins[0]

    marketplace_source = marketplace_plugin.get("source")
    if not isinstance(marketplace_source, dict) or marketplace_source != {
        "source": "local",
        "path": "./plugins/mercury-finance",
    }:
        errors.append("marketplace mercury-finance source must be local ./plugins/mercury-finance")

    marketplace_policy = marketplace_plugin.get("policy")
    if not isinstance(marketplace_policy, dict):
        marketplace_policy = {}
    if marketplace_policy.get("installation") != "AVAILABLE":
        errors.append("marketplace mercury-finance installation policy must be AVAILABLE")
    if marketplace_policy.get("authentication") != "ON_INSTALL":
        errors.append("marketplace mercury-finance authentication policy must be ON_INSTALL")

    project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    optional = (
        project.get("optional-dependencies")
        if isinstance(project.get("optional-dependencies"), dict)
        else {}
    )
    if project.get("version") != "0.2.1":
        errors.append("package version must be 0.2.1")
    if project.get("dependencies") != EXPECTED_DEPENDENCIES:
        errors.append("release runtime dependencies must be exact v0.2.1 pins")
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
        "(v0.2.1 static checks only; remote tag smoke is a post-review gate)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
