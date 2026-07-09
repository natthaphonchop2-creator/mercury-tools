"""YAML parser and validator for Mercury Flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mercury_tools.flows.models import (
    SUPPORTED_COMMANDS,
    FlowCommand,
    MercuryFlow,
    normalize_command_name,
)


class FlowValidationError(ValueError):
    """Raised when a Mercury Flow cannot be parsed or validated."""


def _ensure_mapping(value: Any, *, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FlowValidationError(f"{label} must be a mapping.")
    return {str(key): val for key, val in value.items()}


def _string_list(value: Any, *, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise FlowValidationError(f"{label} must be a list.")
    return [str(item) for item in value]


def _parse_command(raw: Any, *, source: str, index: int) -> FlowCommand:
    if isinstance(raw, str):
        name = normalize_command_name(raw)
        args: dict[str, Any] = {}
    elif isinstance(raw, dict):
        if len(raw) != 1:
            raise FlowValidationError(
                f"{source}[{index}] must contain exactly one command name."
            )
        raw_name, raw_args = next(iter(raw.items()))
        name = normalize_command_name(str(raw_name))
        if raw_args is None:
            args = {}
        elif isinstance(raw_args, dict):
            args = {str(key): value for key, value in raw_args.items()}
        else:
            args = {"value": raw_args}
    else:
        raise FlowValidationError(f"{source}[{index}] must be a string or one-command mapping.")

    if name not in SUPPORTED_COMMANDS:
        supported = ", ".join(sorted(SUPPORTED_COMMANDS))
        raise FlowValidationError(f"Unsupported command '{name}'. Supported commands: {supported}.")
    return FlowCommand(name=name, args=args, source=source, index=index)


def _parse_commands(raw: Any, *, source: str) -> list[FlowCommand]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise FlowValidationError(f"{source} must be a list of commands.")
    return [_parse_command(item, source=source, index=index) for index, item in enumerate(raw)]


def _split_flow_documents(text: str) -> tuple[dict[str, Any], list[Any]]:
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise FlowValidationError(f"Invalid YAML: {exc}") from exc

    docs = [doc for doc in docs if doc is not None]
    if not docs:
        raise FlowValidationError("Flow YAML is empty.")
    if len(docs) == 1:
        doc = docs[0]
        if not isinstance(doc, dict):
            raise FlowValidationError("Single-document flow must be a mapping with commands.")
        config = _ensure_mapping(doc, label="flow")
        commands = config.pop("commands", None)
        if commands is None:
            raise FlowValidationError("Single-document flow must include a commands list.")
        return config, commands
    if len(docs) == 2:
        config = _ensure_mapping(docs[0], label="flow config")
        commands = docs[1]
        if not isinstance(commands, list):
            raise FlowValidationError("Flow commands after --- must be a list.")
        return config, commands
    raise FlowValidationError("Flow YAML must contain one document or config + commands documents.")


def parse_flow_text(text: str, *, path: Path | None = None) -> MercuryFlow:
    config, commands_raw = _split_flow_documents(text)
    name = str(config.get("name") or (path.stem if path else "Mercury Flow")).strip()
    if not name:
        raise FlowValidationError("Flow name is required.")
    env = _ensure_mapping(config.get("env"), label="env")
    tags = _string_list(config.get("tags"), label="tags")
    commands = _parse_commands(commands_raw, source="commands")
    if not commands:
        raise FlowValidationError("Flow must include at least one command.")

    return MercuryFlow(
        name=name,
        description=str(config["description"]) if config.get("description") else None,
        tags=tags,
        env=env,
        commands=commands,
        on_flow_start=_parse_commands(config.get("onFlowStart"), source="onFlowStart"),
        on_flow_complete=_parse_commands(config.get("onFlowComplete"), source="onFlowComplete"),
        path=path,
    )


def parse_flow_path(path: Path) -> MercuryFlow:
    return parse_flow_text(path.read_text(encoding="utf-8"), path=path)


def validate_flow_text(text: str, *, path: Path | None = None) -> dict[str, Any]:
    flow = parse_flow_text(text, path=path)
    return {"status": "ok", "flow": flow.as_dict()}


def validate_flow_path(path: Path) -> dict[str, Any]:
    return validate_flow_text(path.read_text(encoding="utf-8"), path=path)
