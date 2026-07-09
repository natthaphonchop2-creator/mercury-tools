"""Data models for Mercury YAML flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_COMMANDS = {
    "assert",
    "connectorStatus",
    "emitReport",
    "getDocument",
    "repeat",
    "retrieveContextPack",
    "retry",
    "runFlow",
    "runSkill",
    "searchKnowledge",
}

COMMAND_ALIASES = {
    "connector_status": "connectorStatus",
    "emit_report": "emitReport",
    "get_document": "getDocument",
    "retrieve_context_pack": "retrieveContextPack",
    "run_accounting_skill": "runSkill",
    "run_flow": "runFlow",
    "run_skill": "runSkill",
    "search_knowledge": "searchKnowledge",
}

CAPABILITY_ARG_KEYS = (
    "capability",
    "requiredCapability",
    "required_capability",
    "requiredCapabilities",
    "required_capabilities",
)


def normalize_command_name(name: str) -> str:
    clean = name.strip()
    return COMMAND_ALIASES.get(clean, clean)


def declared_command_capabilities(args: dict[str, Any]) -> list[str]:
    for key in CAPABILITY_ARG_KEYS:
        raw = args.get(key)
        if isinstance(raw, str):
            value = raw.strip()
            return [value] if value else []
        if isinstance(raw, list | tuple | set):
            return [str(item).strip() for item in raw if str(item).strip()]
    return []


@dataclass(frozen=True)
class FlowCommand:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "commands"
    index: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "source": self.source,
            "index": self.index,
        }


@dataclass(frozen=True)
class MercuryFlow:
    name: str
    description: str | None
    tags: list[str]
    env: dict[str, Any]
    commands: list[FlowCommand]
    on_flow_start: list[FlowCommand] = field(default_factory=list)
    on_flow_complete: list[FlowCommand] = field(default_factory=list)
    path: Path | None = None

    def all_commands(self) -> list[FlowCommand]:
        return [*self.on_flow_start, *self.commands, *self.on_flow_complete]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "env": self.env,
            "path": str(self.path) if self.path else None,
            "command_count": len(self.commands),
            "on_flow_start_count": len(self.on_flow_start),
            "on_flow_complete_count": len(self.on_flow_complete),
            "commands": [command.as_dict() for command in self.commands],
        }


@dataclass(frozen=True)
class FlowStepResult:
    index: int
    command: str
    status: str
    source: str
    saved_as: str | None = None
    output_summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "command": self.command,
            "status": self.status,
            "source": self.source,
            "saved_as": self.saved_as,
            "output_summary": self.output_summary,
        }


@dataclass(frozen=True)
class FlowRunResult:
    status: str
    flow: MercuryFlow
    dry_run: bool
    steps: list[FlowStepResult]
    variables: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    capability: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "dry_run": self.dry_run,
            "flow": self.flow.as_dict(),
            "steps": [step.as_dict() for step in self.steps],
            "variables": self.variables,
            "artifacts": self.artifacts,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.capability is not None:
            payload["capability"] = self.capability
        return payload
