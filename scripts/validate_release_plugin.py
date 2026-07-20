#!/usr/bin/env python3
"""Validate the static hosted Mercury Finance plugin package offline."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

PLUGIN_PATH = Path("plugins/mercury-finance/.codex-plugin/plugin.json")
MCP_PATH = Path("plugins/mercury-finance/.mcp.json")
MARKETPLACE_PATH = Path(".agents/plugins/marketplace.json")
PUBLIC_PLUGIN_DIRECTORY = Path("plugins/mercury-finance")
PYPROJECT_PATH = Path("pyproject.toml")
ADVANCED_LOCAL_ERP_PATH = PUBLIC_PLUGIN_DIRECTORY / "docs/ADVANCED_LOCAL_ERP.md"
ADVANCED_LOCAL_ERP_COMMAND = "mercury mcp serve-local"
EXPECTED_HOSTED_SERVER = {
    "type": "http",
    "url": "https://mercury-tools-mcp.onrender.com/mcp",
    "note": "Mercury Accounting and ERP connector platform.",
}
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
LOCAL_LAUNCHER_FIELD_NAMES = frozenset({"command", "args", "cwd", "env"})
CODEX_MARKETPLACE_AUTHENTICATION_VALUES = frozenset({"ON_INSTALL", "ON_USE"})
LOCAL_ONLY_TOOL_NAMES = frozenset(
    {
        "credential_status",
        "execute_erp_create",
        "execute_erp_update",
        "execute_sensitive_erp_action",
        "get_erp_action_schema",
        "get_erp_request_status",
        "import_erp_spec",
        "list_connector_drivers",
        "prepare_erp_mutation",
        "run_erp_read",
        "run_mercury_flow",
        "search_erp_actions",
    }
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


def _contains_public_mcp_forbidden_field(payload: Any) -> bool:
    pending: list[Any] = [payload]
    for _ in range(MAX_SCAN_NODES):
        if not pending:
            return False
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = str(key).casefold()
                if (
                    normalized_key in LOCAL_LAUNCHER_FIELD_NAMES
                    or CREDENTIAL_KEY_RE.search(normalized_key) is not None
                ):
                    return True
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)
    return bool(pending)


def _text_contains_high_confidence_credential_literal(text: str) -> bool:
    bearer = BEARER_LITERAL_RE.search(text)
    if bearer is not None and not _is_placeholder(bearer.group("value")):
        return True
    return JWT_LITERAL_RE.search(text) is not None or TOKEN_PREFIX_RE.search(text) is not None


def _scan_public_plugin_files(root: Path, errors: list[str]) -> None:
    plugin_directory = root / PUBLIC_PLUGIN_DIRECTORY
    if not plugin_directory.is_dir():
        errors.append("public plugin directory must exist for recursive credential scanning")
        return

    files = sorted(candidate for candidate in plugin_directory.rglob("*") if candidate.is_file())
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            errors.append(f"cannot scan public plugin file {path.relative_to(root)}: {error}")
            continue
        if _text_contains_high_confidence_credential_literal(text):
            errors.append(
                "public plugin recursive scan found a high-confidence credential literal: "
                f"{path.relative_to(root)}"
            )


def _validate_advanced_local_handoff_boundary(root: Path, errors: list[str]) -> None:
    plugin_directory = root / PUBLIC_PLUGIN_DIRECTORY
    advanced_guide = root / ADVANCED_LOCAL_ERP_PATH
    if not advanced_guide.is_file():
        errors.append("public plugin must package docs/ADVANCED_LOCAL_ERP.md")
        return

    handoff_references: list[Path] = []
    local_terms = {ADVANCED_LOCAL_ERP_COMMAND, *LOCAL_ONLY_TOOL_NAMES}
    files = sorted(candidate for candidate in plugin_directory.rglob("*") if candidate.is_file())
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            errors.append(
                f"cannot inspect public plugin boundary file {path.relative_to(root)}: {error}"
            )
            continue
        if path == advanced_guide:
            continue
        if path.match("*/skills/*/SKILL.md") and "docs/ADVANCED_LOCAL_ERP.md" in text:
            handoff_references.append(path)
            if "handoff" not in text.casefold():
                errors.append(
                    "public Skill references docs/ADVANCED_LOCAL_ERP.md "
                    "without an explicit handoff: "
                    f"{path.relative_to(root)}"
                )
        leaked = sorted(term for term in local_terms if term in text)
        if leaked:
            errors.append(
                "local command or tool name may appear only in "
                f"plugins/mercury-finance/docs/ADVANCED_LOCAL_ERP.md: "
                f"{path.relative_to(root)} ({', '.join(leaked)})"
            )
    if not handoff_references:
        errors.append("public plugin must reference docs/ADVANCED_LOCAL_ERP.md as a handoff")


def _run_codex_json(
    command: list[str], environment: dict[str, str], errors: list[str], label: str
) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            input="",
            text=True,
            timeout=60,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(f"Codex CLI {label} could not complete noninteractively: {error}")
        return None
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        errors.append(
            f"Codex CLI {label} failed with exit {result.returncode}: {output[:2_000]}"
        )
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        errors.append(f"Codex CLI {label} did not return JSON: {error}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"Codex CLI {label} returned a non-object JSON value")
        return None
    return payload


def validate_codex_cli_install(root: Path) -> list[str]:
    """Validate marketplace schema and installation through an isolated local Codex CLI."""

    errors: list[str] = []
    codex = shutil.which("codex")
    if codex is None:
        return ["Codex CLI validation requested but the `codex` executable is unavailable"]

    source_marketplace = root / MARKETPLACE_PATH
    source_plugin = root / PUBLIC_PLUGIN_DIRECTORY
    if not source_marketplace.is_file() or not source_plugin.is_dir():
        return [
            "Codex CLI validation requires the marketplace manifest and public plugin directory"
        ]

    with tempfile.TemporaryDirectory(prefix="mercury-codex-cli-") as temporary:
        temporary_root = Path(temporary)
        marketplace_root = temporary_root / "marketplace"
        marketplace_manifest = marketplace_root / MARKETPLACE_PATH
        marketplace_manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_marketplace, marketplace_manifest)
        shutil.copytree(source_plugin, marketplace_root / PUBLIC_PLUGIN_DIRECTORY)

        home = temporary_root / "home"
        codex_home = temporary_root / "codex-home"
        home.mkdir()
        codex_home.mkdir()
        environment = dict(os.environ)
        environment.pop("CODEX_CONFIG", None)
        environment["HOME"] = str(home)
        environment["CODEX_HOME"] = str(codex_home)

        marketplace = _run_codex_json(
            [codex, "plugin", "marketplace", "add", str(marketplace_root), "--json"],
            environment,
            errors,
            "marketplace add",
        )
        if marketplace is None:
            return errors
        if marketplace.get("marketplaceName") != "mercury-tools":
            errors.append("Codex CLI marketplace add returned an unexpected marketplace name")

        installed = _run_codex_json(
            [codex, "plugin", "add", "mercury-finance@mercury-tools", "--json"],
            environment,
            errors,
            "plugin add",
        )
        if installed is None:
            return errors
        if installed.get("pluginId") != "mercury-finance@mercury-tools":
            errors.append("Codex CLI plugin add returned an unexpected plugin ID")
            return errors

        installed_path_value = installed.get("installedPath")
        if not isinstance(installed_path_value, str):
            errors.append("Codex CLI plugin add did not report an installedPath")
            return errors
        installed_path = Path(installed_path_value)
        is_isolated_install = installed_path.is_dir() and installed_path.resolve().is_relative_to(
            codex_home.resolve()
        )
        if not is_isolated_install:
            errors.append("Codex CLI plugin installed outside the isolated CODEX_HOME cache")
            return errors
        installed_mcp = _read_json(installed_path / ".mcp.json", errors)
        if installed_mcp != {"mcpServers": {"mercury-finance": EXPECTED_HOSTED_SERVER}}:
            errors.append("Codex CLI installed package must expose exactly one hosted MCP")
    return errors


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

    if _contains_public_mcp_forbidden_field(mcp):
        errors.append("mcp manifest must not declare local launcher or credential fields")
    if server != EXPECTED_HOSTED_SERVER:
        errors.append("mercury-finance server must be exactly the hosted HTTPS Render /mcp entry")

    release_manifests = {"plugin": plugin, "mcp": mcp, "marketplace": marketplace}
    serialized_manifest = json.dumps(release_manifests, sort_keys=True)
    if PRIVATE_TOKEN_NAME_RE.search(serialized_manifest):
        errors.append("plugin manifest must not contain private token names")
    if _contains_credential_literal(release_manifests):
        errors.append("plugin manifest must not contain credential literal values")
    _scan_public_plugin_files(root, errors)
    _validate_advanced_local_handoff_boundary(root, errors)

    interface = plugin.get("interface") if isinstance(plugin.get("interface"), dict) else {}
    if plugin.get("name") != "mercury-finance":
        errors.append("plugin name must be mercury-finance")
    if plugin.get("version") != "0.2.2+codex.20260717":
        errors.append("plugin version must be 0.2.2+codex.20260717")
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
    if "authentication" in marketplace_policy:
        if marketplace_policy["authentication"] not in CODEX_MARKETPLACE_AUTHENTICATION_VALUES:
            errors.append(
                "marketplace authentication policy must use only Codex-supported values "
                "ON_INSTALL or ON_USE"
            )
        else:
            errors.append("marketplace no-auth hosted plugin must omit authentication")

    project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    optional = (
        project.get("optional-dependencies")
        if isinstance(project.get("optional-dependencies"), dict)
        else {}
    )
    if project.get("version") != "0.2.2":
        errors.append("package version must be 0.2.2")
    if project.get("dependencies") != EXPECTED_DEPENDENCIES:
        errors.append("release runtime dependencies must be exact v0.2.2 pins")
    if optional.get("openai") != ["openai==2.44.0"]:
        errors.append("openai must be optional at exactly 2.44.0")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--codex-cli",
        action="store_true",
        help="run the isolated local Codex marketplace add/install gate after static validation",
    )
    args = parser.parse_args()
    errors = validate_release(args.root.resolve())
    if errors:
        print("release plugin static validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "release plugin static validation passed "
        "(hosted MCP contract and public-package boundary checks only)"
    )
    if args.codex_cli:
        cli_errors = validate_codex_cli_install(args.root.resolve())
        if cli_errors:
            print("release plugin Codex CLI validation failed:")
            for error in cli_errors:
                print(f"- {error}")
            return 1
        print(
            "release plugin Codex CLI validation passed "
            "(isolated local marketplace add/install; no network)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
