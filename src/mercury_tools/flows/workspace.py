"""Workspace discovery and suite execution for Mercury Flows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
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
    output_dir: Path | None = None
    flows_order: list[str] = field(default_factory=list)
    continue_on_failure: bool = True
    sequential: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "config_path": str(self.config_path) if self.config_path else None,
            "flows": self.flows,
            "include_tags": sorted(self.include_tags),
            "exclude_tags": sorted(self.exclude_tags),
            "env": self.env,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "execution_order": {
                "continue_on_failure": self.continue_on_failure,
                "flows_order": self.flows_order,
            },
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

    @property
    def ordered_selected(self) -> list[FlowFileRecord]:
        selected = self.selected
        if not self.config.flows_order:
            return selected

        ordered: list[FlowFileRecord] = []
        used: set[Path] = set()
        for key in self.config.flows_order:
            match = next(
                (
                    record
                    for record in selected
                    if record.path not in used
                    and _record_matches_order_key(record, self.config.root, key)
                ),
                None,
            )
            if match:
                ordered.append(match)
                used.add(match.path)
        ordered.extend(record for record in selected if record.path not in used)
        return ordered

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.as_dict(),
            "flow_count": len(self.records),
            "selected_count": len(self.selected),
            "execution_order": [
                record.as_dict(root=self.config.root)["relative_path"]
                for record in self.ordered_selected
            ],
            "flows": [record.as_dict(root=self.config.root) for record in self.records],
        }


def workspace_manifest(
    workspace: FlowWorkspace,
    *,
    source: str = "local",
) -> dict[str, Any]:
    """Return an agent-facing workspace summary without raw env values."""
    records = []
    for record in workspace.records:
        item = record.as_dict(root=workspace.config.root)
        if source == "in-memory":
            item["path"] = item["relative_path"]
        records.append(item)
    selected = [record for record in records if record["selected"]]
    skipped = [record for record in records if not record["selected"]]
    invalid = [record for record in records if record["status"] != "valid"]
    all_tags = sorted({tag for record in records for tag in record.get("tags", [])})

    if not records:
        status = "empty"
    elif invalid:
        status = "needs_attention"
    elif not selected:
        status = "no_selected_flows"
    else:
        status = "ok"

    root_label = "." if source == "in-memory" else str(workspace.config.root)
    config_path = str(workspace.config.config_path) if workspace.config.config_path else None
    output_dir = str(workspace.config.output_dir) if workspace.config.output_dir else None
    if source == "in-memory":
        config_path = workspace.config.config_path.name if workspace.config.config_path else None
        if workspace.config.output_dir:
            try:
                output_dir = workspace.config.output_dir.relative_to(
                    workspace.config.root
                ).as_posix()
            except ValueError:
                output_dir = workspace.config.output_dir.name
    return {
        "status": status,
        "surface": "mcp-cli",
        "source": source,
        "runtime_boundary": {
            "primary_runtime": "MCP tools and CLI",
            "browser_console": "setup and sanitized evidence only",
            "host_agent": "Codex, Cursor, Claude, or another MCP client owns chat UX",
        },
        "workspace": {
            "root": root_label,
            "config_path": config_path,
            "config_present": workspace.config.config_path is not None,
            "flow_patterns": workspace.config.flows,
            "env_keys": sorted(workspace.config.env),
            "output_dir": output_dir,
        },
        "discovery": {
            "flow_count": len(records),
            "selected_count": len(selected),
            "skipped_count": len(skipped),
            "invalid_count": len(invalid),
            "tags": all_tags,
            "include_tags": sorted(workspace.config.include_tags),
            "exclude_tags": sorted(workspace.config.exclude_tags),
            "tag_logic": (
                "include/exclude tag filters use OR within each list; exclude removes "
                "matching flows after include selection."
            ),
        },
        "execution": {
            "sequential": workspace.config.sequential,
            "continue_on_failure": workspace.config.continue_on_failure,
            "ordered_flow_paths": [
                record.as_dict(root=workspace.config.root)["relative_path"]
                for record in workspace.ordered_selected
            ],
            "flows_order": workspace.config.flows_order,
        },
        "flows": records,
        "agent_handoff": {
            "mcp_tools": [
                "flow_cheat_sheet",
                "check_flow_syntax",
                "inspect_flow_files",
                "run_mercury_flow",
                "run_flow",
                "run_flow_files",
                "save_workspace_flow",
                "list_workspace_flows",
                "run_workspace_flow",
            ],
            "cli_examples": [
                f"mercury-tools flow list {root_label}",
                f"mercury-tools flow manifest {root_label} --json",
                f"mercury-tools flow run-suite {root_label} --dry-run",
                f"mercury-tools flow push {root_label} --url <remote> --client-token <mc_...>",
            ],
        },
    }


@dataclass(frozen=True)
class FlowSuiteRun:
    workspace: FlowWorkspace
    results: list[FlowRunResult]
    report_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        if not self.results:
            status = "empty"
        elif any(result.status == "error" for result in self.results):
            status = "failed"
        elif all(result.status == "planned" for result in self.results):
            status = "planned"
        else:
            status = "ok"
        return {
            "status": status,
            "workspace": self.workspace.as_dict(),
            "results": [result.as_dict() for result in self.results],
            "report_path": str(self.report_path) if self.report_path else None,
        }


def _record_matches_order_key(record: FlowFileRecord, root: Path, key: str) -> bool:
    clean = key.strip()
    try:
        relative = record.path.relative_to(root).as_posix()
    except ValueError:
        relative = record.path.as_posix()
    variants = {
        relative,
        Path(relative).with_suffix("").as_posix(),
        record.path.name,
        record.path.stem,
    }
    if record.name:
        variants.add(record.name)
    return clean in variants


def _resolve_output_dir(root: Path, value: Any) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FlowValidationError("testOutputDir must be a string.")
    raw = value.strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _error_result(record: FlowFileRecord, *, dry_run: bool, message: str) -> FlowRunResult:
    flow = parse_flow_path(record.path)
    return FlowRunResult(
        status="error",
        flow=flow,
        dry_run=dry_run,
        steps=[],
        variables={},
        artifacts=[{"status": "error", "message": message}],
    )


def _write_suite_report(suite: FlowSuiteRun) -> FlowSuiteRun:
    output_dir = suite.workspace.config.output_dir
    if not output_dir:
        return suite
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "suite-report.json"
    payload = {
        **suite.as_dict(),
        "report_path": str(report_path),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return FlowSuiteRun(
        workspace=suite.workspace,
        results=suite.results,
        report_path=report_path,
    )


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
testOutputDir: ".mercury/reports"
env:
  jurisdiction: {jurisdiction}
  connector: {connector}
  month: "{month}"
executionOrder:
  continueOnFailure: true
  flowsOrder:
    - company-health
    - vat-summary
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
- reports: `.mercury/reports/suite-report.json`
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
    execution_order = _as_mapping(raw.get("executionOrder"), label="executionOrder")
    flows = _as_string_list(raw.get("flows"), label="flows") or list(DEFAULT_FLOW_PATTERNS)
    return FlowWorkspaceConfig(
        root=root,
        config_path=config_path,
        flows=flows,
        include_tags=set(_as_string_list(raw.get("includeTags"), label="includeTags")),
        exclude_tags=set(_as_string_list(raw.get("excludeTags"), label="excludeTags")),
        env=_as_mapping(raw.get("env"), label="env"),
        output_dir=_resolve_output_dir(root, raw.get("testOutputDir") or raw.get("outputDir")),
        flows_order=_as_string_list(
            execution_order.get("flowsOrder"),
            label="executionOrder.flowsOrder",
        ),
        continue_on_failure=bool(execution_order.get("continueOnFailure", True)),
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
            output_dir=config.output_dir,
            flows_order=config.flows_order,
            continue_on_failure=config.continue_on_failure,
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
    env: dict[str, Any] | None = None,
    runner: MercuryFlowRunner | None = None,
) -> FlowSuiteRun:
    workspace = discover_workspace_flows(
        path,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    if env:
        workspace = FlowWorkspace(
            config=replace(
                workspace.config,
                env={**workspace.config.env, **env},
            ),
            records=workspace.records,
        )
    invalid = [record for record in workspace.selected if record.status == "invalid"]
    if invalid:
        first = invalid[0]
        raise FlowValidationError(f"Invalid workspace flow {first.path}: {first.error}")

    flow_runner = runner or create_default_runner(dry_run=dry_run)
    results: list[FlowRunResult] = []
    for record in workspace.ordered_selected:
        try:
            results.append(flow_runner.run_path(record.path, env=workspace.config.env))
        except (FlowValidationError, RuntimeError, ValueError) as exc:
            if not workspace.config.continue_on_failure:
                raise
            results.append(_error_result(record, dry_run=dry_run, message=str(exc)))
    return _write_suite_report(FlowSuiteRun(workspace=workspace, results=results))
