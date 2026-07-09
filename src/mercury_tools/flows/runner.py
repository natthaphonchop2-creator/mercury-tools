"""Interpreted execution engine for Mercury Flows."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mercury_tools.config import load_settings
from mercury_tools.flows.models import FlowCommand, FlowRunResult, FlowStepResult, MercuryFlow
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
    ) -> None:
        self.dry_run = dry_run
        self.rag_service_factory = rag_service_factory
        self.document_getter = document_getter
        self.connector_status_getter = connector_status_getter
        self.skill_runner = skill_runner

    def run_text(
        self,
        text: str,
        *,
        path: Path | None = None,
        env: dict[str, Any] | None = None,
    ) -> FlowRunResult:
        return self.run_flow(parse_flow_text(text, path=path), env=env)

    def run_path(self, path: Path, *, env: dict[str, Any] | None = None) -> FlowRunResult:
        return self.run_flow(parse_flow_path(path), env=env)

    def run_flow(self, flow: MercuryFlow, *, env: dict[str, Any] | None = None) -> FlowRunResult:
        variables: dict[str, Any] = {"env": {**flow.env, **(env or {})}}
        variables.update(variables["env"])
        steps: list[FlowStepResult] = []
        artifacts: list[dict[str, Any]] = []
        base_dir = flow.path.parent if flow.path else Path.cwd()

        for sequence, command in enumerate(flow.all_commands(), start=1):
            rendered_when = _interpolate(command.args.get("when"), variables)
            if not _condition_matches(rendered_when):
                steps.append(
                    FlowStepResult(
                        index=sequence,
                        command=command.name,
                        status="skipped",
                        source=command.source,
                        output_summary={
                            "reason": "when condition evaluated false",
                            "when": redact_json(rendered_when),
                        },
                    )
                )
                continue
            if command.name in {"repeat", "retry", "runFlow"}:
                rendered_args = _interpolate_group_args(
                    command.args,
                    variables,
                    preserve_while=command.name == "repeat",
                )
            else:
                rendered_args = _interpolate(command.args, variables)
            if self.dry_run and command.name not in {"repeat", "retry", "runFlow"}:
                output = self._planned_output(command, rendered_args)
            else:
                output = self._execute(
                    command,
                    rendered_args,
                    base_dir=base_dir,
                    parent_env=variables["env"],
                )
            save_as = rendered_args.get("saveAs") or rendered_args.get("save_as")
            if save_as:
                variables[str(save_as)] = output
            if command.name == "emitReport":
                artifacts.append(output)
            steps.append(
                FlowStepResult(
                    index=sequence,
                    command=command.name,
                    status="planned" if self.dry_run else "ok",
                    source=command.source,
                    saved_as=str(save_as) if save_as else None,
                    output_summary=_summary(output),
                )
            )

        return FlowRunResult(
            status="planned" if self.dry_run else "ok",
            flow=flow,
            dry_run=self.dry_run,
            steps=steps,
            variables=redact_json(variables),
            artifacts=redact_json(artifacts),
        )

    def _execute(
        self,
        command: FlowCommand,
        args: dict[str, Any],
        *,
        base_dir: Path,
        parent_env: dict[str, Any],
    ) -> Any:
        if command.name == "connectorStatus":
            if not self.connector_status_getter:
                raise FlowValidationError("connectorStatus is not configured for this runner.")
            return self.connector_status_getter()

        if command.name == "searchKnowledge":
            service = self._rag_service()
            query = str(args.get("query") or args.get("value") or "").strip()
            results = service.search(
                query,
                filters=_filters(args.get("filters")),
                top_k=int(args.get("topK") or args.get("top_k") or 8),
                mode=str(args.get("mode") or "hybrid"),
            )
            return {
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

        if command.name == "retrieveContextPack":
            service = self._rag_service()
            pack = service.context_pack(
                str(args.get("query") or "").strip(),
                task=args.get("task"),
                filters=_filters(args.get("filters")),
                max_chunks=int(args.get("maxChunks") or args.get("max_chunks") or 12),
            )
            return pack.as_dict()

        if command.name == "getDocument":
            if not self.document_getter:
                raise FlowValidationError("getDocument is not configured for this runner.")
            document_id = str(
                args.get("documentId") or args.get("document_id") or args.get("value") or ""
            )
            if not document_id:
                raise FlowValidationError("getDocument requires documentId.")
            document = self.document_getter(document_id)
            return {"status": "ok" if document else "not_found", "document": document}

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
            return self.skill_runner(skill_id, inputs, evidence_mode)

        if command.name == "assert":
            return self._assert(args)

        if command.name == "emitReport":
            return {
                "title": str(args.get("title") or "Mercury Flow Report"),
                "sections": args.get("sections") or [],
                "metadata": args.get("metadata") or {},
            }

        if command.name == "runFlow":
            return self._run_child_flow_command(
                command_name="runFlow",
                args=args,
                base_dir=base_dir,
                parent_env=parent_env,
            ).as_dict()

        if command.name == "repeat":
            return self._repeat(
                args=args,
                base_dir=base_dir,
                parent_env=parent_env,
            )

        if command.name == "retry":
            return self._retry(
                args=args,
                base_dir=base_dir,
                parent_env=parent_env,
            )

        raise FlowValidationError(f"Unsupported command: {command.name}")

    def _run_child_flow_command(
        self,
        *,
        command_name: str,
        args: dict[str, Any],
        base_dir: Path,
        parent_env: dict[str, Any],
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
            return self.run_flow(child_flow, env=child_env)
        if not raw_path:
            raise FlowValidationError(f"{command_name} requires file/path or commands.")
        child_path = (base_dir / raw_path).resolve()
        if not child_path.exists():
            raise FlowValidationError(f"Nested flow does not exist: {raw_path}")
        return self.run_path(child_path, env=child_env)

    def _repeat(
        self,
        *,
        args: dict[str, Any],
        base_dir: Path,
        parent_env: dict[str, Any],
    ) -> dict[str, Any]:
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
        elif not (base_dir / raw_path).resolve().exists():
            raise FlowValidationError(f"Nested flow does not exist: {raw_path}")

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
            )
            result_payload = result.as_dict()
            results.append(result_payload)
            history.append(
                {
                    "iteration": index + 1,
                    "index": index,
                    "status": result.status,
                }
            )

        return {
            "status": "planned" if self.dry_run else "ok",
            "iterations": len(results),
            "max_iterations": max_iterations,
            "stopped_reason": stopped_reason,
            "while": redact_json(rendered_while) if rendered_while is not None else None,
            "results": results,
            "iteration_history": history,
        }

    def _retry(
        self,
        *,
        args: dict[str, Any],
        base_dir: Path,
        parent_env: dict[str, Any],
    ) -> dict[str, Any]:
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
                )
                return {
                    "status": result.status,
                    "attempts": attempt,
                    "max_retries": max_retries,
                    "delay_ms": delay_ms,
                    "result": result.as_dict(),
                    "attempt_history": attempts,
                }
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


def create_default_runner(*, dry_run: bool = False) -> MercuryFlowRunner:
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

    def connector_status_getter():
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
            "note": "Mercury Flow returns a read-only skill package for the host agent.",
        }

    return MercuryFlowRunner(
        dry_run=dry_run,
        rag_service_factory=rag_service_factory,
        document_getter=document_getter,
        connector_status_getter=connector_status_getter,
        skill_runner=skill_runner,
    )
