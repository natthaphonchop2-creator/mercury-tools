"""Interpreted execution engine for Mercury Flows."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mercury_tools.config import load_settings
from mercury_tools.flows.models import FlowCommand, FlowRunResult, FlowStepResult, MercuryFlow
from mercury_tools.flows.parser import FlowValidationError, parse_flow_path, parse_flow_text
from mercury_tools.rag.models import SearchFilters
from mercury_tools.safety.redaction import redact_json

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}|\{\{\s*([^}]+?)\s*\}\}")


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
            rendered_args = _interpolate(command.args, variables)
            if self.dry_run:
                output = self._planned_output(command, rendered_args)
            else:
                output = self._execute(command, rendered_args, base_dir=base_dir)
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

    def _execute(self, command: FlowCommand, args: dict[str, Any], *, base_dir: Path) -> Any:
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
            raw_path = str(args.get("path") or args.get("value") or "").strip()
            if not raw_path:
                raise FlowValidationError("runFlow requires path.")
            child_path = (base_dir / raw_path).resolve()
            if not child_path.exists():
                raise FlowValidationError(f"Nested flow does not exist: {raw_path}")
            child_env = args.get("env") or {}
            if not isinstance(child_env, dict):
                raise FlowValidationError("runFlow env must be a mapping.")
            return self.run_path(child_path, env=child_env).as_dict()

        raise FlowValidationError(f"Unsupported command: {command.name}")

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
        if "exists" in args and not args["exists"]:
            raise FlowValidationError("assert exists failed.")
        if "minCount" in args or "min_count" in args:
            raw = args.get("minCount") or args.get("min_count")
            if not isinstance(raw, dict):
                raise FlowValidationError("assert minCount must be a mapping.")
            value = raw.get("value")
            count = int(raw.get("count") or 1)
            if not isinstance(value, list) or len(value) < count:
                raise FlowValidationError(f"assert minCount failed: expected at least {count}.")
        return {"status": "ok"}


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
