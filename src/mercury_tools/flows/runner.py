"""Interpreted execution engine for Mercury Flows."""

from __future__ import annotations

import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from mercury_tools.config import load_settings
from mercury_tools.connectors.catalog import public_capability_gate
from mercury_tools.flows.models import (
    FlowCommand,
    FlowRunResult,
    FlowStepResult,
    MercuryFlow,
    declared_command_capabilities,
)
from mercury_tools.flows.parser import (
    FlowValidationError,
    parse_flow_path,
    parse_flow_text,
    parse_inline_commands,
)
from mercury_tools.rag.models import SearchFilters
from mercury_tools.safety.redaction import redact_json

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}|\{\{\s*([^}]+?)\s*\}\}")
_TEMPLATE_EXACT_PATTERN = re.compile(r"^\s*(?:\$\{([^}]+)\}|\{\{\s*([^}]+?)\s*\}\})\s*$")

ErpReadCallback = Callable[[str, dict[str, Any], str], dict[str, Any]]
ErpWritePreviewCallback = Callable[[str, dict[str, Any], str], dict[str, Any]]
FlowPathResolver = Callable[[Path, str], Path]
FlowLoader = Callable[[Path, str], MercuryFlow]
CapabilityGate = Callable[[str], dict[str, Any] | None]

_ERP_TO_CLOUD_TAINT_REASON = "erp_to_cloud_taint"
_CLOUD_BOUND_COMMANDS = frozenset(
    {"getDocument", "retrieveContextPack", "runSkill", "searchKnowledge"}
)
_TAINTED_VALUE_SUMMARY = {"status": "erp_derived_value_withheld"}
_MAX_FLOW_BYTES = 500_000


@dataclass(frozen=True)
class _CommandOutput:
    value: Any
    tainted: bool = False
    blocked_reason: str | None = None


class _TaintState:
    """Tracks variables whose values originate from an ERP read."""

    def __init__(self, paths: set[str] | None = None) -> None:
        self._paths = set(paths or ())

    def mark(self, path: str) -> None:
        clean = path.strip(".")
        if clean:
            self._paths.add(clean)

    @property
    def any(self) -> bool:
        return bool(self._paths)

    def references(self, path: str) -> bool:
        clean = path.strip(".")
        return any(
            clean == tainted
            or clean.startswith(f"{tainted}.")
            or tainted.startswith(f"{clean}.")
            for tainted in self._paths
        )



def _child_taint_state(
    child_env: dict[str, Any],
    parent_taint: _TaintState,
    tainted_env_keys: set[str],
) -> _TaintState:
    child_taint = _TaintState()
    for key in child_env:
        clean = str(key)
        if clean in tainted_env_keys or parent_taint.references(clean):
            child_taint.mark(clean)
            child_taint.mark(f"env.{clean}")
    return child_taint


class RepositoryFlowLoader:
    """Read repository-contained flows through descriptor-pinned paths only."""

    def __init__(self, repository_root: Path, *, max_bytes: int = _MAX_FLOW_BYTES) -> None:
        self.root = Path(repository_root).expanduser()
        self.max_bytes = max_bytes
        self._require_secure_openat()

    def load_path(self, raw_path: str) -> MercuryFlow:
        components = self._components(raw_path)
        return self._load_components(components)

    def __call__(self, base_dir: Path, raw_path: str) -> MercuryFlow:
        try:
            relative_base = Path(base_dir).relative_to(self.root)
        except ValueError as exc:
            raise FlowValidationError("flow_path_invalid") from exc
        return self._load_components((*relative_base.parts, *self._components(raw_path)))

    def list_flows(self, raw_directory: str | None = None) -> list[tuple[Path, MercuryFlow]]:
        components = () if raw_directory is None else self._components(raw_directory)
        descriptor = self._open_directory(components)
        try:
            return self._walk(descriptor, components)
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_secure_openat() -> None:
        required = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
        if os.name != "posix" or any(not hasattr(os, name) for name in required):
            raise FlowValidationError("flow_path_invalid")
        if os.open not in os.supports_dir_fd:
            raise FlowValidationError("flow_path_invalid")

    @staticmethod
    def _components(raw_path: str) -> tuple[str, ...]:
        if not isinstance(raw_path, str) or not raw_path or raw_path.strip() != raw_path:
            raise FlowValidationError("flow_path_invalid")
        requested = Path(raw_path)
        raw_parts = raw_path.split("/")
        if (
            requested.is_absolute()
            or not requested.parts
            or any(part in {"", ".", ".."} for part in raw_parts)
            or any(part in {".", ".."} for part in requested.parts)
        ):
            raise FlowValidationError("flow_path_invalid")
        return tuple(str(part) for part in requested.parts)

    def _load_components(self, components: tuple[str, ...]) -> MercuryFlow:
        payload = self._read_file(components)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FlowValidationError("flow_path_invalid") from exc
        return parse_flow_text(text, path=self.root.joinpath(*components))

    def _walk(
        self,
        directory_fd: int,
        directory_components: tuple[str, ...],
    ) -> list[tuple[Path, MercuryFlow]]:
        found: list[tuple[Path, MercuryFlow]] = []
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise FlowValidationError("flow_path_invalid") from exc
        for name in names:
            try:
                entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise FlowValidationError("flow_path_invalid") from exc
            components = (*directory_components, name)
            if stat.S_ISLNK(entry.st_mode):
                raise FlowValidationError("flow_path_invalid")
            if stat.S_ISDIR(entry.st_mode):
                child_fd = self._open_child_directory(directory_fd, name)
                try:
                    found.extend(self._walk(child_fd, components))
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(entry.st_mode) and Path(name).suffix.casefold() in {".yaml", ".yml"}:
                payload = self._read_file_from_parent(directory_fd, name)
                try:
                    text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise FlowValidationError("flow_path_invalid") from exc
                flow = parse_flow_text(text, path=self.root.joinpath(*components))
                found.append((Path(*components), flow))
        return found

    def _open_directory(self, components: tuple[str, ...]) -> int:
        descriptor = self._open_root_directory()
        try:
            for component in components:
                child_fd = self._open_child_directory(descriptor, component)
                os.close(descriptor)
                descriptor = child_fd
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _open_root_directory(self) -> int:
        try:
            descriptor = os.open(self.root, self._directory_flags())
        except OSError as exc:
            raise FlowValidationError("flow_path_invalid") from exc
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise FlowValidationError("flow_path_invalid")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _open_child_directory(self, parent_fd: int, name: str) -> int:
        try:
            descriptor = os.open(name, self._directory_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise FlowValidationError("flow_path_invalid") from exc
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise FlowValidationError("flow_path_invalid")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _read_file(self, components: tuple[str, ...]) -> bytes:
        parent_fd = self._open_directory(components[:-1])
        try:
            return self._read_file_from_parent(parent_fd, components[-1])
        finally:
            os.close(parent_fd)

    def _read_file_from_parent(self, parent_fd: int, name: str) -> bytes:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise FlowValidationError("flow_path_invalid") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > self.max_bytes:
                raise FlowValidationError("flow_path_invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                payload = handle.read(self.max_bytes + 1)
            if len(payload) > self.max_bytes:
                raise FlowValidationError("flow_path_invalid")
            return payload
        finally:
            os.close(descriptor)

    @staticmethod
    def _directory_flags() -> int:
        return os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def repository_flow_loader(repository_root: Path) -> RepositoryFlowLoader:
    return RepositoryFlowLoader(repository_root)


class _RetryMutationError(FlowValidationError):
    """A policy rejection that retry must propagate without another attempt."""


def repository_flow_path_resolver(repository_root: Path) -> FlowPathResolver:
    """Return a resolver that permits only nested flows inside one repository root."""

    root = Path(repository_root).expanduser().resolve()

    def resolve_nested_flow(base_dir: Path, raw_path: str) -> Path:
        relative_path = Path(raw_path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise FlowValidationError("Nested flow path traversal is not allowed.")
        resolved_base = Path(base_dir).expanduser().resolve()
        if not resolved_base.is_relative_to(root):
            raise FlowValidationError("Nested flow base is outside repository root.")
        resolved_path = (resolved_base / relative_path).resolve()
        if not resolved_path.is_relative_to(root):
            raise FlowValidationError("Nested flow path is outside repository root.")
        return resolved_path

    return resolve_nested_flow


def _legacy_flow_path_resolver(base_dir: Path, raw_path: str) -> Path:
    """Keep hosted relative nested-flow support behind the same containment checks."""

    return repository_flow_path_resolver(base_dir)(base_dir, raw_path)


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise FlowValidationError(f"Unknown template variable: {path}")
    return current


def _interpolate(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        exact_match = _TEMPLATE_EXACT_PATTERN.match(value)
        if exact_match:
            key = (exact_match.group(1) or exact_match.group(2) or "").strip()
            return _get_path(variables, key)

        def replace(match: re.Match[str]) -> str:
            key = (match.group(1) or match.group(2) or "").strip()
            replacement = _get_path(variables, key)
            return str(replacement)

        return _TEMPLATE_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_interpolate(item, variables) for item in value]
    if isinstance(value, dict):
        return {str(key): _interpolate(item, variables) for key, item in value.items()}
    return value


def _interpolate_with_taint(
    value: Any,
    variables: dict[str, Any],
    taint: _TaintState,
) -> tuple[Any, bool]:
    if isinstance(value, str):
        exact_match = _TEMPLATE_EXACT_PATTERN.match(value)
        if exact_match:
            key = (exact_match.group(1) or exact_match.group(2) or "").strip()
            return _get_path(variables, key), taint.references(key)

        tainted = False

        def replace(match: re.Match[str]) -> str:
            nonlocal tainted
            key = (match.group(1) or match.group(2) or "").strip()
            replacement = _get_path(variables, key)
            tainted = tainted or taint.references(key)
            return str(replacement)

        return _TEMPLATE_PATTERN.sub(replace, value), tainted
    if isinstance(value, list):
        rendered: list[Any] = []
        tainted = False
        for item in value:
            item_value, item_tainted = _interpolate_with_taint(item, variables, taint)
            rendered.append(item_value)
            tainted = tainted or item_tainted
        return rendered, tainted
    if isinstance(value, dict):
        rendered_dict: dict[str, Any] = {}
        tainted = False
        for key, item in value.items():
            item_value, item_tainted = _interpolate_with_taint(item, variables, taint)
            rendered_dict[str(key)] = item_value
            tainted = tainted or item_tainted
        return rendered_dict, tainted
    return value, False


def _interpolate_group_args(
    args: dict[str, Any],
    variables: dict[str, Any],
    *,
    preserve_while: bool = False,
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for key, value in args.items():
        if key == "commands" or (preserve_while and key == "while"):
            rendered[key] = value
        else:
            rendered[key] = _interpolate(value, variables)
    return rendered


def _interpolate_group_args_with_taint(
    args: dict[str, Any],
    variables: dict[str, Any],
    taint: _TaintState,
    *,
    preserve_while: bool = False,
) -> tuple[dict[str, Any], bool]:
    rendered: dict[str, Any] = {}
    args_tainted = False
    for key, value in args.items():
        if key == "commands" or (preserve_while and key == "while"):
            rendered[key] = value
            continue
        item, item_tainted = _interpolate_with_taint(value, variables, taint)
        rendered[key] = item
        args_tainted = args_tainted or item_tainted
    return rendered, args_tainted


def _tainted_mapping_keys(
    value: Any,
    variables: dict[str, Any],
    taint: _TaintState,
) -> set[str]:
    if not isinstance(value, dict):
        return set()
    keys: set[str] = set()
    for key, item in value.items():
        _, item_tainted = _interpolate_with_taint(item, variables, taint)
        if item_tainted:
            keys.add(str(key))
    return keys


def _safe_tainted_variables(
    variables: dict[str, Any],
    taint: _TaintState,
) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in variables.items():
        if taint.references(str(key)):
            safe[str(key)] = dict(_TAINTED_VALUE_SUMMARY)
        elif isinstance(value, dict):
            safe[str(key)] = _safe_tainted_variables(value, taint)
        else:
            safe[str(key)] = value
    return redact_json(safe)


def _safe_tainted_steps(
    steps: list[FlowStepResult],
    tainted_sequences: set[int],
) -> list[FlowStepResult]:
    return [
        replace(step, output_summary=dict(_TAINTED_VALUE_SUMMARY))
        if step.index in tainted_sequences
        else step
        for step in steps
    ]


def _bounded_int(
    raw: Any,
    *,
    label: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise FlowValidationError(f"{label} must be an integer.") from exc
    if value < minimum or value > maximum:
        raise FlowValidationError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _summary(payload: Any) -> dict[str, Any]:
    redacted = redact_json(payload)
    if isinstance(redacted, dict):
        if redacted.get("status") == "confirmation_required":
            summary = {"status": "confirmation_required"}
            for key in ("request_id", "payload_hash"):
                value = redacted.get(key)
                if isinstance(value, str) and value:
                    summary[key] = value
            return summary
        summary: dict[str, Any] = {"keys": sorted(redacted.keys())[:12]}
        if "status" in redacted:
            summary["status"] = redacted["status"]
        if "results" in redacted and isinstance(redacted["results"], list):
            summary["result_count"] = len(redacted["results"])
        if "context" in redacted and isinstance(redacted["context"], list):
            summary["context_count"] = len(redacted["context"])
        if "skill_id" in redacted:
            summary["skill_id"] = redacted["skill_id"]
        if "title" in redacted:
            summary["title"] = redacted["title"]
        if "attempts" in redacted:
            summary["attempts"] = redacted["attempts"]
        if "max_retries" in redacted:
            summary["max_retries"] = redacted["max_retries"]
        if "iterations" in redacted:
            summary["iterations"] = redacted["iterations"]
        if "max_iterations" in redacted:
            summary["max_iterations"] = redacted["max_iterations"]
        if "reason" in redacted:
            summary["reason"] = redacted["reason"]
        if "capability" in redacted:
            summary["capability"] = redacted["capability"]
        return summary
    if isinstance(redacted, list):
        return {"count": len(redacted)}
    return {"value": str(redacted)[:160]}


def _filters(raw: dict[str, Any] | None) -> SearchFilters:
    raw = raw or {}
    return SearchFilters(
        jurisdiction=raw.get("jurisdiction"),
        connector=raw.get("connector"),
        doc_type=raw.get("doc_type"),
        review_status=raw.get("review_status"),
        effective_date=raw.get("effective_date"),
    )


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | dict | tuple | set):
        return bool(value)
    return True


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        clean = value.strip().lower()
        if clean in {"true", "1", "yes", "y", "on"}:
            return True
        if clean in {"", "false", "0", "no", "n", "off", "none", "null"}:
            return False
    return bool(value)


def _condition_pair(value: Any, *, label: str) -> tuple[Any, Any]:
    if isinstance(value, list) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, dict):
        if "value" not in value:
            raise FlowValidationError(f"when.{label} requires value.")
        if "expected" in value:
            return value["value"], value["expected"]
        if "equals" in value:
            return value["value"], value["equals"]
        if "notEquals" in value:
            return value["value"], value["notEquals"]
        if "not_equals" in value:
            return value["value"], value["not_equals"]
        raise FlowValidationError(f"when.{label} requires expected.")
    raise FlowValidationError(f"when.{label} must be a mapping or two-item list.")


def _assert_pair(value: Any, *, label: str) -> tuple[Any, Any]:
    if isinstance(value, list) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, dict):
        if "value" not in value:
            raise FlowValidationError(f"assert {label} requires value.")
        for key in ("expected", "equals", "notEquals", "not_equals", "contains", "item"):
            if key in value:
                return value["value"], value[key]
        raise FlowValidationError(f"assert {label} requires expected.")
    raise FlowValidationError(f"assert {label} must be a mapping or two-item list.")


def _value_count(value: Any) -> int:
    if isinstance(value, str | list | tuple | set | dict):
        return len(value)
    raise FlowValidationError("assert minCount value must be countable.")


def _contains(value: Any, expected: Any) -> bool:
    if isinstance(value, str):
        return str(expected) in value
    if isinstance(value, dict):
        return expected in value
    if isinstance(value, list | tuple | set):
        return expected in value
    raise FlowValidationError("assert contains value must be string, mapping, or collection.")


def _condition_matches(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, bool | str | int | float):
        return _as_bool(raw)
    if not isinstance(raw, dict):
        raise FlowValidationError("when must be a boolean, string, number, or mapping.")

    for key, value in raw.items():
        condition = str(key)
        if condition.lower() == "true":
            condition = "true"
        if condition == "true":
            if not _as_bool(value):
                return False
        elif condition == "exists":
            if not _is_present(value):
                return False
        elif condition in {"notExists", "not_exists"}:
            if _is_present(value):
                return False
        elif condition == "equals":
            left, right = _condition_pair(value, label=condition)
            if str(left) != str(right):
                return False
        elif condition in {"notEquals", "not_equals"}:
            left, right = _condition_pair(value, label=condition)
            if str(left) == str(right):
                return False
        else:
            raise FlowValidationError(f"Unsupported when condition: {condition}")
    return True


class MercuryFlowRunner:
    """Executes Mercury YAML flows one command at a time."""

    def __init__(
        self,
        *,
        dry_run: bool = False,
        rag_service_factory: Callable[[], Any] | None = None,
        document_getter: Callable[[str], dict[str, Any] | None] | None = None,
        connector_status_getter: Callable[[], dict[str, Any]] | None = None,
        skill_runner: Callable[[str, dict[str, Any], bool], dict[str, Any]] | None = None,
        erp_read_callback: ErpReadCallback | None = None,
        erp_write_preview_callback: ErpWritePreviewCallback | None = None,
        flow_path_resolver: FlowPathResolver | None = None,
        flow_loader: FlowLoader | None = None,
        capability_gate: CapabilityGate | None = public_capability_gate,
    ) -> None:
        self.dry_run = dry_run
        self.rag_service_factory = rag_service_factory
        self.document_getter = document_getter
        self.connector_status_getter = connector_status_getter
        self.skill_runner = skill_runner
        self.erp_read_callback = erp_read_callback
        self.erp_write_preview_callback = erp_write_preview_callback
        self.flow_path_resolver = flow_path_resolver or _legacy_flow_path_resolver
        self.flow_loader = flow_loader
        self.capability_gate = capability_gate

    def run_text(
        self,
        text: str,
        *,
        path: Path | None = None,
        env: dict[str, Any] | None = None,
    ) -> FlowRunResult:
        return self.run_flow(parse_flow_text(text, path=path), env=env)

    def run_path(
        self,
        path: Path,
        *,
        env: dict[str, Any] | None = None,
        _retry_context: bool = False,
        _taint: _TaintState | None = None,
    ) -> FlowRunResult:
        return self.run_flow(
            parse_flow_path(path),
            env=env,
            _retry_context=_retry_context,
            _taint=_taint,
        )

    def run_flow(
        self,
        flow: MercuryFlow,
        *,
        env: dict[str, Any] | None = None,
        _retry_context: bool = False,
        _taint: _TaintState | None = None,
    ) -> FlowRunResult:
        variables: dict[str, Any] = {"env": {**flow.env, **(env or {})}}
        variables.update(variables["env"])
        steps: list[FlowStepResult] = []
        artifacts: list[dict[str, Any]] = []
        artifact_taints: list[bool] = []
        taint = _taint or _TaintState()
        tainted_sequences: set[int] = set()
        base_dir = flow.path.parent if flow.path else Path.cwd()

        for sequence, command in enumerate(flow.all_commands(), start=1):
            rendered_when, when_tainted = _interpolate_with_taint(
                command.args.get("when"),
                variables,
                taint,
            )
            if not _condition_matches(rendered_when):
                if when_tainted:
                    tainted_sequences.add(sequence)
                steps.append(
                    FlowStepResult(
                        index=sequence,
                        command=command.name,
                        status="skipped",
                        source=command.source,
                        output_summary={
                            "reason": "when condition evaluated false",
                            "when": (
                                dict(_TAINTED_VALUE_SUMMARY)
                                if when_tainted
                                else redact_json(rendered_when)
                            ),
                        },
                    )
                )
                continue
            if command.name in {"repeat", "retry", "runFlow"}:
                rendered_args, rendered_tainted = _interpolate_group_args_with_taint(
                    command.args,
                    variables,
                    taint,
                    preserve_while=command.name == "repeat",
                )
            else:
                rendered_args, rendered_tainted = _interpolate_with_taint(
                    command.args,
                    variables,
                    taint,
                )
            tainted_child_env_keys = _tainted_mapping_keys(
                command.args.get("env"),
                variables,
                taint,
            )
            for capability in declared_command_capabilities(rendered_args):
                blocked = self.capability_gate(capability) if self.capability_gate else None
                if blocked is None:
                    continue
                save_as = rendered_args.get("saveAs") or rendered_args.get("save_as")
                if save_as:
                    variables[str(save_as)] = blocked
                steps.append(
                    FlowStepResult(
                        index=sequence,
                        command=command.name,
                        status="blocked",
                        source=command.source,
                        saved_as=str(save_as) if save_as else None,
                        output_summary=_summary(blocked),
                    )
                )
                return FlowRunResult(
                    status="blocked",
                    flow=flow,
                    dry_run=self.dry_run,
                    steps=steps,
                    variables=redact_json(variables),
                    artifacts=redact_json(artifacts),
                    reason=str(blocked["reason"]),
                    capability=str(blocked["capability"]),
                )
            if command.name in _CLOUD_BOUND_COMMANDS and rendered_tainted:
                tainted_sequences.add(sequence)
                blocked = {
                    "status": "blocked",
                    "reason": _ERP_TO_CLOUD_TAINT_REASON,
                    "command": command.name,
                }
                steps.append(
                    FlowStepResult(
                        index=sequence,
                        command=command.name,
                        status="blocked",
                        source=command.source,
                        output_summary=_summary(blocked),
                    )
                )
                return FlowRunResult(
                    status="blocked",
                    flow=flow,
                    dry_run=self.dry_run,
                    steps=_safe_tainted_steps(steps, tainted_sequences),
                    variables=_safe_tainted_variables(variables, taint),
                    artifacts=[
                        dict(_TAINTED_VALUE_SUMMARY) if item_tainted else redact_json(artifact)
                        for artifact, item_tainted in zip(artifacts, artifact_taints, strict=True)
                    ],
                    reason=_ERP_TO_CLOUD_TAINT_REASON,
                    tainted=True,
                )
            if self.dry_run and command.name not in {"repeat", "retry", "runFlow"}:
                execution = _CommandOutput(self._planned_output(command, rendered_args))
            else:
                execution = self._execute(
                    command,
                    rendered_args,
                    base_dir=base_dir,
                    parent_env=variables["env"],
                    retry_context=_retry_context,
                    taint=taint,
                    tainted_child_env_keys=tainted_child_env_keys,
                )
            output = execution.value
            save_as = rendered_args.get("saveAs") or rendered_args.get("save_as")
            if save_as:
                variables[str(save_as)] = output
                if execution.tainted or rendered_tainted:
                    taint.mark(str(save_as))
            if command.name == "emitReport":
                artifacts.append(output)
                artifact_taints.append(execution.tainted or rendered_tainted)
            if execution.tainted or rendered_tainted:
                tainted_sequences.add(sequence)
            terminal_confirmation = (
                isinstance(output, dict) and output.get("status") == "confirmation_required"
            )
            terminal_taint_block = execution.blocked_reason == _ERP_TO_CLOUD_TAINT_REASON
            steps.append(
                FlowStepResult(
                    index=sequence,
                    command=command.name,
                    status=(
                        "confirmation_required"
                        if terminal_confirmation
                        else "planned"
                        if self.dry_run
                        else "ok"
                    ),
                    source=command.source,
                    saved_as=str(save_as) if save_as else None,
                    output_summary=_summary(output),
                )
            )
            if terminal_taint_block:
                return FlowRunResult(
                    status="blocked",
                    flow=flow,
                    dry_run=self.dry_run,
                    steps=_safe_tainted_steps(steps, tainted_sequences),
                    variables=_safe_tainted_variables(variables, taint),
                    artifacts=[
                        dict(_TAINTED_VALUE_SUMMARY) if item_tainted else redact_json(artifact)
                        for artifact, item_tainted in zip(artifacts, artifact_taints, strict=True)
                    ],
                    reason=_ERP_TO_CLOUD_TAINT_REASON,
                    tainted=True,
                )
            if terminal_confirmation:
                return FlowRunResult(
                    status="confirmation_required",
                    flow=flow,
                    dry_run=self.dry_run,
                    steps=steps,
                    variables=redact_json(variables),
                    artifacts=redact_json(artifacts),
                    reason="confirmation_required",
                    tainted=taint.any,
                )

        return FlowRunResult(
            status="planned" if self.dry_run else "ok",
            flow=flow,
            dry_run=self.dry_run,
            steps=steps,
            variables=redact_json(variables),
            artifacts=redact_json(artifacts),
            tainted=taint.any,
        )

    def _execute(
        self,
        command: FlowCommand,
        args: dict[str, Any],
        *,
        base_dir: Path,
        parent_env: dict[str, Any],
        retry_context: bool,
        taint: _TaintState,
        tainted_child_env_keys: set[str],
    ) -> _CommandOutput:
        if command.name == "connectorStatus":
            if not self.connector_status_getter:
                raise FlowValidationError("connectorStatus is not configured for this runner.")
            return _CommandOutput(self.connector_status_getter())

        if command.name == "searchKnowledge":
            service = self._rag_service()
            query = str(args.get("query") or args.get("value") or "").strip()
            results = service.search(
                query,
                filters=_filters(args.get("filters")),
                top_k=int(args.get("topK") or args.get("top_k") or 8),
                mode=str(args.get("mode") or "hybrid"),
            )
            return _CommandOutput(
                {
                    "query": query,
                    "results": [
                        {
                            "chunk_id": result.chunk_id,
                            "document_uri": result.document_uri,
                            "score": result.score,
                            "text": result.text,
                            "citation": result.citation,
                            "source_title": result.source_title,
                            "source_uri": result.source_uri,
                            "source_url": result.source_url,
                            "source_path": result.source_path,
                        }
                        for result in results
                    ],
                }
            )

        if command.name == "retrieveContextPack":
            service = self._rag_service()
            pack = service.context_pack(
                str(args.get("query") or "").strip(),
                task=args.get("task"),
                filters=_filters(args.get("filters")),
                max_chunks=int(args.get("maxChunks") or args.get("max_chunks") or 12),
            )
            return _CommandOutput(pack.as_dict())

        if command.name == "getDocument":
            if not self.document_getter:
                raise FlowValidationError("getDocument is not configured for this runner.")
            document_id = str(
                args.get("documentId") or args.get("document_id") or args.get("value") or ""
            )
            if not document_id:
                raise FlowValidationError("getDocument requires documentId.")
            document = self.document_getter(document_id)
            return _CommandOutput(
                {"status": "ok" if document else "not_found", "document": document}
            )

        if command.name == "runSkill":
            if not self.skill_runner:
                raise FlowValidationError("runSkill is not configured for this runner.")
            skill_id = str(args.get("skillId") or args.get("skill_id") or args.get("value") or "")
            if not skill_id:
                raise FlowValidationError("runSkill requires skillId.")
            inputs = args.get("inputs") or {}
            if not isinstance(inputs, dict):
                raise FlowValidationError("runSkill inputs must be a mapping.")
            evidence_mode = bool(args.get("evidenceMode") or args.get("evidence_mode"))
            return _CommandOutput(self.skill_runner(skill_id, inputs, evidence_mode))

        if command.name == "erpRead":
            if not self.erp_read_callback:
                raise FlowValidationError("erpRead is not configured for this runner.")
            action_id, inputs, environment = self._erp_callback_args(args, parent_env, command.name)
            return _CommandOutput(
                self.erp_read_callback(action_id, inputs, environment),
                tainted=True,
            )

        if command.name == "erpWritePreview":
            if retry_context:
                raise _RetryMutationError("erpWritePreview cannot run inside retry.")
            if not self.erp_write_preview_callback:
                raise FlowValidationError("erpWritePreview is not configured for this runner.")
            action_id, inputs, environment = self._erp_callback_args(args, parent_env, command.name)
            return _CommandOutput(
                self._confirmation_required_output(
                    self.erp_write_preview_callback(action_id, inputs, environment)
                )
            )

        if command.name == "assert":
            return _CommandOutput(self._assert(args))

        if command.name == "emitReport":
            return _CommandOutput(
                {
                    "title": str(args.get("title") or "Mercury Flow Report"),
                    "sections": args.get("sections") or [],
                    "metadata": args.get("metadata") or {},
                }
            )

        if command.name == "runFlow":
            child_result = self._run_child_flow_command(
                command_name="runFlow",
                args=args,
                base_dir=base_dir,
                parent_env=parent_env,
                retry_context=retry_context,
                taint=taint,
                tainted_child_env_keys=tainted_child_env_keys,
            )
            return _CommandOutput(
                self._child_flow_output(child_result),
                tainted=child_result.tainted,
                blocked_reason=child_result.reason if child_result.status == "blocked" else None,
            )

        if command.name == "repeat":
            return self._repeat(
                args=args,
                base_dir=base_dir,
                parent_env=parent_env,
                retry_context=retry_context,
                taint=taint,
                tainted_child_env_keys=tainted_child_env_keys,
            )

        if command.name == "retry":
            return self._retry(
                args=args,
                base_dir=base_dir,
                parent_env=parent_env,
                taint=taint,
                tainted_child_env_keys=tainted_child_env_keys,
            )

        raise FlowValidationError(f"Unsupported command: {command.name}")

    @staticmethod
    def _erp_callback_args(
        args: dict[str, Any],
        parent_env: dict[str, Any],
        command_name: str,
    ) -> tuple[str, dict[str, Any], str]:
        action_id = str(
            args.get("actionId") or args.get("action_id") or args.get("value") or ""
        ).strip()
        if not action_id:
            raise FlowValidationError(f"{command_name} requires actionId.")
        inputs = args.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise FlowValidationError(f"{command_name} inputs must be a mapping.")
        environment = str(
            args.get("environment") or parent_env.get("environment") or "production"
        ).strip()
        if not environment:
            raise FlowValidationError(f"{command_name} environment must not be empty.")
        return action_id, inputs, environment

    @staticmethod
    def _confirmation_required_output(preview: dict[str, Any]) -> dict[str, str]:
        request_id = preview.get("request_id")
        payload_hash = preview.get("payload_hash")
        if not isinstance(request_id, str) or not request_id:
            raise FlowValidationError("erpWritePreview callback did not return request_id.")
        if not isinstance(payload_hash, str) or not payload_hash:
            raise FlowValidationError("erpWritePreview callback did not return payload_hash.")
        return {
            "status": "confirmation_required",
            "request_id": request_id,
            "payload_hash": payload_hash,
        }

    @staticmethod
    def _confirmation_summary_fields(result: FlowRunResult) -> dict[str, str]:
        if result.status != "confirmation_required" or not result.steps:
            return {}
        summary = result.steps[-1].output_summary
        fields: dict[str, str] = {}
        for key in ("request_id", "payload_hash"):
            value = summary.get(key)
            if isinstance(value, str) and value:
                fields[key] = value
        return fields

    def _child_flow_output(self, result: FlowRunResult) -> dict[str, Any]:
        payload = result.as_dict()
        payload.update(self._confirmation_summary_fields(result))
        return payload

    def _run_child_flow_command(
        self,
        *,
        command_name: str,
        args: dict[str, Any],
        base_dir: Path,
        parent_env: dict[str, Any],
        retry_context: bool,
        taint: _TaintState,
        tainted_child_env_keys: set[str],
    ) -> FlowRunResult:
        child_env = args.get("env") or {}
        if not isinstance(child_env, dict):
            raise FlowValidationError(f"{command_name} env must be a mapping.")
        child_env = {**parent_env, **child_env}
        inline_commands = args.get("commands")
        raw_path = str(args.get("file") or args.get("path") or args.get("value") or "").strip()
        if inline_commands is not None and raw_path:
            raise FlowValidationError(
                f"{command_name} accepts either file/path or commands, not both."
            )
        if inline_commands is not None:
            commands = parse_inline_commands(inline_commands, source=f"{command_name}.commands")
            if not commands:
                raise FlowValidationError(
                    f"{command_name} commands must include at least one command."
                )
            label = str(args.get("label") or "Inline Flow").strip() or "Inline Flow"
            suffix = command_name if command_name in {"repeat", "retry"} else "inline"
            child_flow = MercuryFlow(
                name=label,
                description=f"Inline Mercury {command_name} flow",
                tags=[],
                env=child_env,
                commands=commands,
                path=base_dir / f"{label}.{suffix}.yaml",
            )
            child_taint = _child_taint_state(child_env, taint, tainted_child_env_keys)
            return self.run_flow(
                child_flow,
                env=child_env,
                _retry_context=retry_context,
                _taint=child_taint,
            )
        if not raw_path:
            raise FlowValidationError(f"{command_name} requires file/path or commands.")
        child_taint = _child_taint_state(child_env, taint, tainted_child_env_keys)
        if self.flow_loader:
            return self.run_flow(
                self.flow_loader(base_dir, raw_path),
                env=child_env,
                _retry_context=retry_context,
                _taint=child_taint,
            )
        child_path = self._resolve_nested_flow_path(base_dir, raw_path)
        return self.run_path(
            child_path,
            env=child_env,
            _retry_context=retry_context,
            _taint=child_taint,
        )

    def _resolve_nested_flow_path(self, base_dir: Path, raw_path: str) -> Path:
        child_path = self.flow_path_resolver(base_dir, raw_path)
        if not isinstance(child_path, Path):
            raise FlowValidationError("Nested flow resolver must return a Path.")
        if not child_path.is_file():
            raise FlowValidationError(f"Nested flow does not exist: {raw_path}")
        return child_path

    def _repeat(
        self,
        *,
        args: dict[str, Any],
        base_dir: Path,
        parent_env: dict[str, Any],
        retry_context: bool,
        taint: _TaintState,
        tainted_child_env_keys: set[str],
    ) -> _CommandOutput:
        times_raw = args.get("times")
        while_condition = args.get("while")
        has_times = times_raw is not None and times_raw != ""
        if not has_times and while_condition is None:
            raise FlowValidationError("repeat requires times or while.")

        inline_commands = args.get("commands")
        raw_path = str(args.get("file") or args.get("path") or args.get("value") or "").strip()
        if inline_commands is not None and raw_path:
            raise FlowValidationError("repeat accepts either file/path or commands, not both.")
        if inline_commands is None and not raw_path:
            raise FlowValidationError("repeat requires file/path or commands.")
        if inline_commands is not None:
            commands = parse_inline_commands(inline_commands, source="repeat.commands")
            if not commands:
                raise FlowValidationError("repeat commands must include at least one command.")
        elif self.flow_loader is None:
            self._resolve_nested_flow_path(base_dir, raw_path)

        if has_times:
            max_iterations = _bounded_int(
                times_raw,
                label="repeat times",
                default=1,
                minimum=0,
                maximum=100,
            )
        else:
            max_iterations = _bounded_int(
                args.get("maxIterations", args.get("max_iterations")),
                label="repeat maxIterations",
                default=10,
                minimum=1,
                maximum=100,
            )

        results: list[dict[str, Any]] = []
        result_taints: list[bool] = []
        history: list[dict[str, Any]] = []
        stopped_reason = "times exhausted" if has_times else "maxIterations exhausted"
        rendered_while: Any = None

        for index in range(max_iterations):
            repeat_state = {
                "index": index,
                "iteration": index + 1,
                "remaining": max_iterations - index,
            }
            repeat_env = {**parent_env, "repeat": repeat_state}
            if while_condition is not None:
                condition_variables = {"env": repeat_env, **repeat_env}
                rendered_while = _interpolate(while_condition, condition_variables)
                if not _condition_matches(rendered_while):
                    stopped_reason = "while condition evaluated false"
                    break

            result = self._run_child_flow_command(
                command_name="repeat",
                args=args,
                base_dir=base_dir,
                parent_env=repeat_env,
                retry_context=retry_context,
                taint=taint,
                tainted_child_env_keys=tainted_child_env_keys,
            )
            result_payload = self._child_flow_output(result)
            results.append(result_payload)
            result_taints.append(result.tainted)
            history.append(
                {
                    "iteration": index + 1,
                    "index": index,
                    "status": result.status,
                }
            )
            if result.status == "confirmation_required":
                stopped_reason = "confirmation required"
                break
            if result.status == "blocked" and result.reason == _ERP_TO_CLOUD_TAINT_REASON:
                stopped_reason = _ERP_TO_CLOUD_TAINT_REASON
                break

        output_status = (
            "confirmation_required"
            if results and results[-1]["status"] == "confirmation_required"
            else "blocked"
            if results and results[-1]["status"] == "blocked"
            else "planned"
            if self.dry_run
            else "ok"
        )
        output = {
            "status": output_status,
            "iterations": len(results),
            "max_iterations": max_iterations,
            "stopped_reason": stopped_reason,
            "while": redact_json(rendered_while) if rendered_while is not None else None,
            "results": results,
            "iteration_history": history,
        }
        if results and results[-1]["status"] == "confirmation_required":
            output.update(self._confirmation_summary_fields(result))
        return _CommandOutput(
            output,
            tainted=any(result_taints),
            blocked_reason=(
                _ERP_TO_CLOUD_TAINT_REASON
                if output_status == "blocked" and stopped_reason == _ERP_TO_CLOUD_TAINT_REASON
                else None
            ),
        )

    def _retry(
        self,
        *,
        args: dict[str, Any],
        base_dir: Path,
        parent_env: dict[str, Any],
        taint: _TaintState,
        tainted_child_env_keys: set[str],
    ) -> _CommandOutput:
        max_retries = _bounded_int(
            args.get("maxRetries", args.get("max_retries")),
            label="retry maxRetries",
            default=1,
            minimum=0,
            maximum=3,
        )
        delay_ms = _bounded_int(
            args.get("delayMs", args.get("delay_ms")),
            label="retry delayMs",
            default=0,
            minimum=0,
            maximum=30000,
        )
        attempts_allowed = max_retries + 1
        attempts: list[dict[str, Any]] = []

        for attempt in range(1, attempts_allowed + 1):
            try:
                result = self._run_child_flow_command(
                    command_name="retry",
                    args=args,
                    base_dir=base_dir,
                    parent_env=parent_env,
                    retry_context=True,
                    taint=taint,
                    tainted_child_env_keys=tainted_child_env_keys,
                )
                output = {
                    "status": result.status,
                    "attempts": attempt,
                    "max_retries": max_retries,
                    "delay_ms": delay_ms,
                    "result": self._child_flow_output(result),
                    "attempt_history": attempts,
                }
                output.update(self._confirmation_summary_fields(result))
                return _CommandOutput(
                    output,
                    tainted=result.tainted,
                    blocked_reason=(
                        result.reason if result.status == "blocked" else None
                    ),
                )
            except _RetryMutationError:
                raise
            except Exception as exc:
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "error",
                        "message": str(redact_json(str(exc)))[:300],
                    }
                )
                if attempt >= attempts_allowed:
                    break
                if not self.dry_run and delay_ms:
                    time.sleep(delay_ms / 1000)
                if self.dry_run:
                    break

        last = attempts[-1]["message"] if attempts else "unknown error"
        raise FlowValidationError(
            f"retry failed after {len(attempts)} attempt(s): {last}"
        )

    def _planned_output(self, command: FlowCommand, args: dict[str, Any]) -> dict[str, Any]:
        output: dict[str, Any] = {
            "status": "planned",
            "command": command.name,
            "args": redact_json(args),
        }
        if command.name in {"searchKnowledge", "retrieveContextPack"}:
            output["query"] = str(args.get("query") or "")
            output["task"] = args.get("task")
            output["results"] = []
            output["context"] = []
        elif command.name == "connectorStatus":
            output["connectors"] = []
        elif command.name == "runSkill":
            output["skill_id"] = str(args.get("skillId") or args.get("skill_id") or "")
            output["inputs"] = args.get("inputs") or {}
            output["evidence_mode"] = bool(args.get("evidenceMode") or args.get("evidence_mode"))
        elif command.name == "emitReport":
            output["title"] = str(args.get("title") or "Mercury Flow Report")
            output["sections"] = args.get("sections") or []
            output["metadata"] = args.get("metadata") or {}
        elif command.name == "getDocument":
            output["document"] = None
        return output

    def _rag_service(self):
        if not self.rag_service_factory:
            raise FlowValidationError("RAG service is not configured for this runner.")
        return self.rag_service_factory()

    @staticmethod
    def _assert(args: dict[str, Any]) -> dict[str, Any]:
        assertions: list[str] = []
        if "exists" in args:
            assertions.append("exists")
            if not _as_bool(args["exists"]):
                raise FlowValidationError("assert exists failed.")
        if "notExists" in args or "not_exists" in args:
            assertions.append("notExists")
            value = args.get("notExists", args.get("not_exists"))
            if _as_bool(value):
                raise FlowValidationError("assert notExists failed.")
        if "equals" in args:
            assertions.append("equals")
            left, right = _assert_pair(args["equals"], label="equals")
            if str(left) != str(right):
                raise FlowValidationError(f"assert equals failed: {left!r} != {right!r}.")
        if "notEquals" in args or "not_equals" in args:
            assertions.append("notEquals")
            raw = args.get("notEquals", args.get("not_equals"))
            left, right = _assert_pair(raw, label="notEquals")
            if str(left) == str(right):
                raise FlowValidationError(f"assert notEquals failed: {left!r} == {right!r}.")
        if "contains" in args:
            assertions.append("contains")
            value, expected = _assert_pair(args["contains"], label="contains")
            if not _contains(value, expected):
                raise FlowValidationError(f"assert contains failed: expected {expected!r}.")
        if "status" in args:
            assertions.append("status")
            raw_status = args["status"]
            if isinstance(raw_status, dict):
                status_value = raw_status.get("value", raw_status.get("status"))
                expected_status = raw_status.get("expected", "ok")
            else:
                status_value = raw_status
                expected_status = "ok"
            if str(status_value).lower() != str(expected_status).lower():
                raise FlowValidationError(
                    f"assert status failed: {status_value!r} != {expected_status!r}."
                )
        if "minCount" in args or "min_count" in args:
            assertions.append("minCount")
            raw = args.get("minCount") or args.get("min_count")
            if not isinstance(raw, dict):
                raise FlowValidationError("assert minCount must be a mapping.")
            value = raw.get("value")
            count = int(raw.get("count") or 1)
            if _value_count(value) < count:
                raise FlowValidationError(f"assert minCount failed: expected at least {count}.")
        if not assertions:
            raise FlowValidationError("assert requires at least one assertion.")
        return {"status": "ok", "assertions": assertions}


def create_default_runner(
    *,
    dry_run: bool = False,
    connector_status_getter: Callable[[], dict[str, Any]] | None = None,
) -> MercuryFlowRunner:
    settings = load_settings()

    def rag_service_factory():
        from mercury_tools.db.supabase import SupabaseRagStore
        from mercury_tools.rag.embeddings import create_embedding_provider
        from mercury_tools.rag.service import RagService

        return RagService(
            store=SupabaseRagStore(settings),
            embedder=create_embedding_provider(settings),
        )

    def document_getter(document_id: str):
        from mercury_tools.db.supabase import SupabaseRagStore

        return SupabaseRagStore(settings).get_document(document_id)

    def local_connector_status_getter():
        from mercury_tools.mercury_runtime import connector_status

        return connector_status()

    def skill_runner(skill_id: str, inputs: dict[str, Any], evidence_mode: bool):
        from mercury_tools.mercury_runtime import skill_markdown

        markdown = skill_markdown(skill_id)
        return {
            "status": "ok" if markdown else "not_found",
            "skill_id": skill_id,
            "inputs": inputs,
            "evidence_mode": evidence_mode,
            "skill_markdown": markdown,
            "note": (
                "Mercury Flow returns a guided skill package. Endpoint actions are "
                "gated by connector capability, preview, approval, and audit policy."
            ),
        }

    return MercuryFlowRunner(
        dry_run=dry_run,
        rag_service_factory=rag_service_factory,
        document_getter=document_getter,
        connector_status_getter=connector_status_getter or local_connector_status_getter,
        skill_runner=skill_runner,
    )
