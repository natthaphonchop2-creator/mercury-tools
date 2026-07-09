"""Read-only helpers for an installed Mercury Agent runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mercury_tools.safety.redaction import redact_json


def mercury_home() -> Path:
    return Path(os.environ.get("MERCURY_HOME", Path.home() / ".mercury-agent")).expanduser()


def mercury_agent_path() -> Path | None:
    raw = os.environ.get("MERCURY_AGENT_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    default = Path.home() / "Desktop" / "mercury-agent"
    return default if default.exists() else None


def connector_status() -> dict[str, Any]:
    config_path = mercury_home() / "config.json"
    if not config_path.exists():
        return {"status": "not_configured", "home": str(mercury_home()), "connectors": {}}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "invalid_config", "home": str(mercury_home()), "connectors": {}}
    return redact_json(
        {
            "status": "ok",
            "home": str(mercury_home()),
            "selected_connector": data.get("selected_connector"),
            "environment": data.get("environment"),
            "connectors": data.get("connectors") or {},
        }
    )


def _runtime_roots() -> list[Path]:
    roots: list[Path] = []
    explicit_root = os.environ.get("MERCURY_TOOLS_ROOT", "").strip()
    if explicit_root:
        roots.append(Path(explicit_root).expanduser())
    roots.extend([Path.cwd(), Path("/app")])
    current_file = Path(__file__).resolve()
    roots.extend(current_file.parents)

    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)
    return unique_roots


def skill_markdown(skill_id: str) -> str | None:
    base = mercury_agent_path()
    candidates = []
    for repo_root in _runtime_roots():
        candidates.extend(
            [
                repo_root
                / "plugins"
                / "mercury-finance"
                / "skills"
                / skill_id
                / "SKILL.md",
                repo_root / "skills" / "accounting" / skill_id / "SKILL.md",
            ]
        )
    if base:
        candidates.extend(
            [
                base / "mercury_accounting" / "skills" / skill_id / "SKILL.md",
                base / "skills" / "accounting" / skill_id / "SKILL.md",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return None
