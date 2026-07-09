"""Workspace discovery and suite execution for Mercury Flows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from mercury_tools.flows.models import FlowRunResult
from mercury_tools.flows.parser import FlowValidationError, parse_flow_path
from mercury_tools.flows.runner import MercuryFlowRunner, create_default_runner
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE, VAT_SUMMARY_TEMPLATE

CONFIG_NAMES = ("mercury.yaml", "mercury.yml", "config.yaml", "config.yml")
DEFAULT_FLOW_PATTERNS = ("*.yaml", "*.yml", "flows/**/*.yaml", "flows/**/*.yml")


def _as_string_list(value: Any, *, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise FlowValidationError(f"{label} must be a string or list.")


def _as_mapping(value: Any, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    raise FlowValidationError(f"{label} must be a mapping.")


def _matches_tags(tags: list[str], *, include: set[str], exclude: set[str]) -> bool:
    tag_set = set(tags)
    if include and not tag_set.intersection(include):
        return False
    return not bool(tag_set.intersection(exclude))


@dataclass(frozen=True)
class FlowWorkspaceConfig:
    root: Path
    config_path: Path | None
    flows: list[str]
    include_tags: set[str]
    exclude_tags: set[str]
    env: dict[str, Any]
    sequential: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "config_path": str(self.config_path) if self.config_path else None,
            "flows": self.flows,
            "include_tags": sorted(self.include_tags),
            "exclude_tags": sorted(self.exclude_tags),
            "env": self.env,
            "sequential": self.sequential,
        }


@dataclass(frozen=True)
class FlowFileRecord:
    path: Path
    name: str | None
    tags: list[str]
    command_count: int
    selected: bool
    status: str
    error: str | None = None

    def as_dict(self, *, root: Path) -> dict[str, Any]:
        try:
            rel_path = str(self.path.relative_to(root))
        except ValueError:
            rel_path = str(self.path)
        return {
            "path": str(self.path),
            "relative_path": rel_path,
            "name": self.name,
            "tags": self.tags,
            "command_count": self.command_count,
            "selected": self.selected,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class FlowWorkspace:
    config: FlowWorkspaceConfig
    records: list[FlowFileRecord]

    @property
    def selected(self) -> list[FlowFileRecord]:
        return [record for record in self.records if record.selected]

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.as_dict(),
            "flow_count": len(self.records),
            "selected_count": len(self.selected),
            "flows": [record.as_dict(root=self.config.root) for record in self.records],
        }


@dataclass(frozen=True)
class FlowSuiteRun:
    workspace: FlowWorkspace
    results: list[FlowRunResult]

    def as_dict(self) -> dict[str, Any]:
        if not self.results:
            status = "empty"
        elif all(result.status == "planned" for result in self.results):
            status = "planned"
        else:
            status = "ok"
        return {
            "status": status,
            "workspace": self.workspace.as_dict(),
            "results": [result.as_dict() for result in self.results],
        }


def _render_starter_flow(template: str, *, connector: str, jurisdiction: str) -> str:
    return (
        template.replace("flowaccount", connector)
        .replace("jurisdiction: TH", f"jurisdiction: {jurisdiction}")
        .replace("jurisdiction: \"${jurisdiction}\"", 'jurisdiction: "${jurisdiction}"')
    )


def _starter_workspace_files(*, connector: str, jurisdiction: str, month: str) -> dict[str, str]:
    return {
        "config.yaml": f"""flows:
  - "flows/**/*.yaml"
includeTags: [accounting]
excludeTags: [disabled]
env:
  jurisdiction: {jurisdiction}
  connector: {connector}
  month: "{month}"
execution:
  sequential: true
""",
        "flows/company-health.yaml": _render_starter_flow(
            COMPANY_HEALTH_TEMPLATE,
            connector=connector,
            jurisdiction=jurisdiction,
        ),
        "flows/vat-summary.yaml": _render_starter_flow(
            VAT_SUMMARY_TEMPLATE,
            connector=connector,
            jurisdiction=jurisdiction,
        ),
        "README.md": f"""# Mercury Flow Workspace

This workspace follows the Mercury Flow layout inspired by Maestro workspaces:
configuration in `config.yaml`, tagged YAML flows under `flows/`, and runtime
execution through the Mercury CLI, HTTP API, or MCP tools.

## Commands

```bash
mercury-tools flow list .
mercury-tools flow run-suite . --dry-run
mercury-tools flow push . --dry-run
```

## Workspace defaults

- connector: `{connector}`
- jurisdiction: `{jurisdiction}`
- month: `{month}`
- discovery: `flows/**/*.yaml`
- include tags: `accounting`
- exclude tags: `disabled`
""",
    }


def create_workspace_scaffold(
    path: Path,
    *,
    connector: str = "flowaccount",
    jurisdiction: str = "TH",
    month: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    root = path.expanduser().resolve()
    selected_month = month or date.today().strftime("%Y-%m")
    files = _starter_workspace_files(
        connector=connector,
        jurisdiction=jurisdiction,
        month=selected_month,
    )
    conflicts = [relative for relative in files if (root / relative).exists()]
    if conflicts and not force:
        raise FlowValidationError(
            "Workspace files already exist: " + ", ".join(sorted(conflicts))
        )

    created: list[str] = []
    overwritten: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = root / relative
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if existed:
            overwritten.append(relative)
        else:
            created.append(relative)

    return {
        "root": str(root),
        "connector": connector,
        "jurisdiction": jurisdiction,
        "month": selected_month,
        "created": created,
        "overwritten": overwritten,
    }


def load_workspace_config(path: Path) -> FlowWorkspaceConfig:
    resolved = path.expanduser().resolve()
    if resolved.is_file() and resolved.name in CONFIG_NAMES:
        root = resolved.parent
        config_path: Path | None = resolved
    elif resolved.is_dir():
        root = resolved
        config_path = next((root / name for name in CONFIG_NAMES if (root / name).exists()), None)
    else:
        root = resolved.parent
        config_path = None

    raw: dict[str, Any] = {}
    if config_path:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise FlowValidationError("Workspace config must be a mapping.")
        raw = {str(key): value for key, value in loaded.items()}

    execution = _as_mapping(raw.get("execution"), label="execution")
    flows = _as_string_list(raw.get("flows"), label="flows") or list(DEFAULT_FLOW_PATTERNS)
    return FlowWorkspaceConfig(
        root=root,
        config_path=config_path,
        flows=flows,
        include_tags=set(_as_string_list(raw.get("includeTags"), label="includeTags")),
        exclude_tags=set(_as_string_list(raw.get("excludeTags"), label="excludeTags")),
        env=_as_mapping(raw.get("env"), label="env"),
        sequential=bool(execution.get("sequential", True)),
    )


def discover_workspace_flows(
    path: Path,
    *,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
) -> FlowWorkspace:
    if path.expanduser().resolve().is_file() and path.name not in CONFIG_NAMES:
        config = FlowWorkspaceConfig(
            root=path.expanduser().resolve().parent,
            config_path=None,
            flows=[path.expanduser().resolve().name],
            include_tags=set(include_tags or []),
            exclude_tags=set(exclude_tags or []),
            env={},
        )
    else:
        config = load_workspace_config(path)
        config = FlowWorkspaceConfig(
            root=config.root,
            config_path=config.config_path,
            flows=config.flows,
            include_tags=config.include_tags.union(include_tags or []),
            exclude_tags=config.exclude_tags.union(exclude_tags or []),
            env=config.env,
            sequential=config.sequential,
        )

    candidates: dict[Path, None] = {}
    for pattern in config.flows:
        for candidate in sorted(config.root.glob(pattern)):
            if candidate.is_file() and candidate.name not in CONFIG_NAMES:
                candidates[candidate.resolve()] = None

    records: list[FlowFileRecord] = []
    for candidate in candidates:
        try:
            flow = parse_flow_path(candidate)
            selected = _matches_tags(
                flow.tags,
                include=config.include_tags,
                exclude=config.exclude_tags,
            )
            records.append(
                FlowFileRecord(
                    path=candidate,
                    name=flow.name,
                    tags=flow.tags,
                    command_count=len(flow.commands),
                    selected=selected,
                    status="valid",
                )
            )
        except FlowValidationError as exc:
            selected = not config.include_tags
            records.append(
                FlowFileRecord(
                    path=candidate,
                    name=None,
                    tags=[],
                    command_count=0,
                    selected=selected,
                    status="invalid",
                    error=str(exc),
                )
            )

    return FlowWorkspace(config=config, records=records)


def run_workspace_flows(
    path: Path,
    *,
    dry_run: bool = False,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    runner: MercuryFlowRunner | None = None,
) -> FlowSuiteRun:
    workspace = discover_workspace_flows(
        path,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    invalid = [record for record in workspace.selected if record.status == "invalid"]
    if invalid:
        first = invalid[0]
        raise FlowValidationError(f"Invalid workspace flow {first.path}: {first.error}")

    flow_runner = runner or create_default_runner(dry_run=dry_run)
    results = [
        flow_runner.run_path(record.path, env=workspace.config.env)
        for record in workspace.selected
    ]
    return FlowSuiteRun(workspace=workspace, results=results)
