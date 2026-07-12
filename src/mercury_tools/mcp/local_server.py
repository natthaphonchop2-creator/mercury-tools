"""One host-visible local MCP surface for Mercury Finance."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import stat
from collections.abc import Coroutine, Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from mercury_tools.catalog.models import HttpMethod, RiskTier
from mercury_tools.execution.executor import ExecutionPolicyError
from mercury_tools.flows.parser import FlowValidationError, parse_flow_text
from mercury_tools.flows.runner import MercuryFlowRunner, repository_flow_loader
from mercury_tools.local.repository import (
    RepositoryContext,
    ensure_repository_state,
    resolve_repository_root,
    root_paths,
)
from mercury_tools.mcp.local_runtime import LocalMercuryRuntime
from mercury_tools.prompts import PROMPTS, get_prompt
from mercury_tools.safety.redaction import redact_json

local_mcp = FastMCP("Mercury Finance")

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
_NON_DESTRUCTIVE_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
_DESTRUCTIVE_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
_STATUS_CODE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_FLOW_SUFFIXES = {".yaml", ".yml"}
_MAX_FLOW_CHARS = 500_000
_SCHEMA_VALUE_KEYS = frozenset({"const", "default", "enum", "example", "examples"})


async def active_root_paths(ctx: Context) -> tuple[Path, ...]:
    """Return only canonical paths advertised by the active MCP session."""

    result = await ctx.session.list_roots()
    return root_paths(tuple(str(root.uri) for root in result.roots))


async def repository_from_context(
    ctx: Context,
    repo_root: str | None,
) -> RepositoryContext:
    """Resolve every repository-bound operation through active MCP roots."""

    roots = await active_root_paths(ctx)
    selected = resolve_repository_root(repo_root, roots)
    if repo_root is not None and selected not in roots:
        raise ValueError("repo_root_not_active")
    return ensure_repository_state(selected)


@asynccontextmanager
async def _request_runtime(ctx: Context, repo_root: str | None):
    repository = await repository_from_context(ctx, repo_root)
    runtime = LocalMercuryRuntime.for_repository(repository)
    try:
        yield runtime
    finally:
        await runtime.aclose()


def _error_payload(error: Exception, *, fallback: str = "operation_failed") -> dict[str, Any]:
    if isinstance(error, httpx.HTTPError):
        return {"status": "cloud_request_failed"}
    code = str(error).strip("'\"")
    if _STATUS_CODE.fullmatch(code):
        return {"status": code}
    if isinstance(error, FlowValidationError):
        return {"status": "flow_invalid"}
    return {"status": fallback}


def _action_summary(action: Any) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "version_id": action.version_id,
        "connector_id": action.connector_id,
        "method": action.method.value,
        "capability": action.capability,
        "description": action.description,
        "risk_tier": int(action.risk_tier),
        "required_confirmations": action.required_confirmations,
        "confidence": action.confidence.value,
        "observed_state": action.observed_state.value,
    }


def _public_action_schema(action: Any) -> dict[str, Any]:
    """Project validated catalog metadata without treating schema field names as values."""
    payload = action.model_dump(mode="json")
    if not isinstance(payload, Mapping):
        raise ValueError("catalog_action_schema_invalid")
    projected = dict(payload)
    projected["input_schema"] = _public_executable_schema(projected.get("input_schema"))
    projected["examples"] = []
    return projected


def _public_executable_schema(value: Any, *, field_map: bool = False) -> Any:
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("catalog_action_schema_invalid")
            normalized = key.casefold()
            if field_map:
                projected[key] = _public_executable_schema(item)
            elif normalized in _SCHEMA_VALUE_KEYS:
                projected[key] = [] if normalized in {"enum", "examples"} else "[REDACTED]"
            elif normalized in {"files", "headers", "path", "properties", "query"}:
                projected[key] = _public_executable_schema(item, field_map=True)
            else:
                projected[key] = _public_executable_schema(item)
        return projected
    if isinstance(value, list | tuple):
        return [_public_executable_schema(item) for item in value]
    return value


@local_mcp.tool(annotations=_READ_ONLY)
async def search_knowledge(
    query: str,
    ctx: Context,
    filters: dict[str, str] | None = None,
    top_k: int = 8,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Search reviewed Mercury Cloud knowledge with strict public responses."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            results = await runtime.search_knowledge(query, filters=filters, top_k=top_k)
        return redact_json(
            {
                "status": "ok" if results else "no_relevant_knowledge",
                "query": query,
                "results": list(results),
            }
        )
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def retrieve_context_pack(
    query: str,
    ctx: Context,
    task: str | None = None,
    filters: dict[str, str] | None = None,
    max_chunks: int = 12,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Return citation-bearing Cloud context without invoking an LLM."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            results = await runtime.search_knowledge(
                query,
                filters=filters,
                top_k=max_chunks,
            )
        return redact_json(
            {
                "status": "ok" if results else "no_relevant_knowledge",
                "query": query,
                "task": task,
                "context": list(results),
            }
        )
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def get_document(
    document_id: str,
    ctx: Context,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Return one strict public Cloud document."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            document = await runtime.get_document(document_id)
        return redact_json(
            {
                "status": "ok" if document else "document_not_found",
                "document": document,
            }
        )
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def connector_status(
    ctx: Context,
    connector: str | None = None,
    environment: str | None = None,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Combine non-secret driver, capability, credential, and validation metadata."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            await runtime.refresh_catalog()
            rows = runtime.connector_summaries(
                connector=connector,
                environment=environment,
            )
        return {"status": "ok", "connectors": redact_json(rows)}
    except (LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def run_accounting_skill(
    skill_id: str,
    ctx: Context,
    inputs: dict[str, Any] | None = None,
    evidence_mode: bool = True,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Return a canonical Skill, cited context, and a generic ordered tool plan."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            return await runtime.run_accounting_skill(
                skill_id,
                inputs=inputs,
                evidence_mode=evidence_mode,
            )
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_NON_DESTRUCTIVE_WRITE)
async def run_mercury_flow(
    flow_yaml: str,
    ctx: Context,
    env: dict[str, Any] | None = None,
    dry_run: bool = False,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Run one local Mercury Flow with read and write-preview callbacks only."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            return await _run_local_flow(
                runtime,
                flow_yaml=flow_yaml,
                flow_path=None,
                env=env,
                dry_run=dry_run,
            )
    except (
        ExecutionPolicyError,
        FlowValidationError,
        LookupError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        return _error_payload(error, fallback="flow_failed")


@local_mcp.tool(annotations=_READ_ONLY)
async def list_workspace_flows(
    ctx: Context,
    path: str = ".",
    repo_root: str | None = None,
) -> dict[str, Any]:
    """List YAML flows below one repository-contained directory."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            directory = _repository_path(runtime.repository, path, expected="directory")
            flows = await asyncio.to_thread(
                _flow_summaries,
                runtime.repository,
                directory,
            )
        return {"status": "ok", "flow_count": len(flows), "flows": flows}
    except (FlowValidationError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_NON_DESTRUCTIVE_WRITE)
async def save_workspace_flow(
    path: str,
    content: str,
    ctx: Context,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Validate and atomically save one repository-contained flow file."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            destination = _repository_path(runtime.repository, path, expected="save")
            if not isinstance(content, str) or len(content) > _MAX_FLOW_CHARS:
                raise ValueError("flow_content_invalid")
            flow = parse_flow_text(content, path=destination)
            await asyncio.to_thread(
                _write_flow_file,
                runtime.repository,
                destination,
                content,
            )
        return {
            "status": "saved",
            "flow": {
                "path": str(destination.relative_to(runtime.repository.root)),
                "name": flow.name,
                "command_count": len(flow.commands),
            },
        }
    except (FlowValidationError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_NON_DESTRUCTIVE_WRITE)
async def run_workspace_flow(
    path: str,
    ctx: Context,
    env: dict[str, Any] | None = None,
    dry_run: bool = False,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Run one repository-contained flow and keep nested paths inside the root."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            flow_path = _repository_path(runtime.repository, path, expected="file")
            return await _run_local_flow(
                runtime,
                flow_yaml=None,
                flow_path=flow_path,
                env=env,
                dry_run=dry_run,
            )
    except (
        ExecutionPolicyError,
        FlowValidationError,
        LookupError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        return _error_payload(error, fallback="flow_failed")


@local_mcp.tool(annotations=_READ_ONLY)
async def search_erp_actions(
    query: str,
    ctx: Context,
    connector: str | None = None,
    method: str | None = None,
    risk_tier: int | None = None,
    top_k: int = 8,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Rank merged global and local actions, preserving ambiguity."""

    try:
        selected_method = HttpMethod(method) if method is not None else None
        selected_risk = RiskTier(risk_tier) if risk_tier is not None else None
        async with _request_runtime(ctx, repo_root) as runtime:
            result = await runtime.search_actions(
                query,
                connector=connector,
                method=selected_method,
                risk_tier=selected_risk,
                top_k=top_k,
            )
        candidates = [
            {
                **_action_summary(match.action),
                "rank_bucket": match.rank_bucket,
                "score": match.score,
                "reasons": list(match.reasons),
            }
            for match in result.matches
        ]
        payload: dict[str, Any] = {
            "status": "ambiguous" if result.ambiguous else "ok",
            "candidates": candidates,
        }
        if not result.ambiguous and len(candidates) == 1:
            payload["action"] = candidates[0]
        return redact_json(payload)
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def get_erp_action_schema(
    action_id: str,
    ctx: Context,
    version: str | None = None,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Return one active merged action schema."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            await runtime.refresh_catalog()
            action = (
                runtime.catalog.require_version(action_id, version)
                if version
                else runtime.catalog.require(action_id)
            )
        return {"status": "ok", "action": _public_action_schema(action)}
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def run_erp_read(
    action_id: str,
    inputs: dict[str, Any],
    ctx: Context,
    environment: str = "production",
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Execute only an effective Tier 0 action through the local executor."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            await runtime.refresh_catalog()
            return await runtime.run_read(action_id, inputs, environment)
    except (
        ExecutionPolicyError,
        httpx.HTTPError,
        LookupError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_NON_DESTRUCTIVE_WRITE)
async def preview_erp_write(
    action_id: str,
    inputs: dict[str, Any],
    ctx: Context,
    environment: str = "production",
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Bind one write preview in the existing local request store."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            await runtime.refresh_catalog()
            return await runtime.preview_write(action_id, inputs, environment)
    except (
        ExecutionPolicyError,
        httpx.HTTPError,
        LookupError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_NON_DESTRUCTIVE_WRITE)
async def confirm_erp_write(
    request_id: str,
    payload_hash: str,
    ctx: Context,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Confirm an immutable preview without accepting replacement payload data."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            prepared = runtime.executor.confirm_write(request_id, payload_hash)
        return redact_json(prepared.public_dict())
    except (LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_DESTRUCTIVE_WRITE)
async def execute_erp_write(
    request_id: str,
    ctx: Context,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Refresh action versions and execute one confirmed local request."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            await runtime.refresh_catalog()
            result = await runtime.executor.execute_write(request_id)
        return redact_json(result.public_dict())
    except (
        ExecutionPolicyError,
        httpx.HTTPError,
        LookupError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def get_erp_request_status(
    request_id: str,
    ctx: Context,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Return the existing request store's public state only."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            return redact_json(runtime.executor.get_request_status(request_id))
    except (LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_NON_DESTRUCTIVE_WRITE)
async def import_erp_spec(
    connector_id: str,
    ctx: Context,
    source_path: str | None = None,
    source_url: str | None = None,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Import one repository-contained or explicit HTTPS ERP specification."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            result = await runtime.import_catalog_spec(
                connector_id=connector_id,
                source_path=source_path,
                source_url=source_url,
            )
        return {
            "status": "imported",
            "source_id": result.source.source_id,
            "source_type": result.source.source_type,
            "action_count": len(result.actions),
            "actions": [_action_summary(action) for action in result.actions],
            "sanitization": redact_json(result.sanitization.model_dump(mode="json")),
        }
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def list_connector_drivers(
    ctx: Context,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """List built-in and repository-configured driver metadata without secrets."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            drivers = runtime.drivers.public_summaries()
        return {"status": "ok", "drivers": redact_json(drivers)}
    except (LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.tool(annotations=_READ_ONLY)
async def credential_status(
    connector: str,
    environment: str,
    ctx: Context,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Return required, present, and missing field names without values."""

    try:
        async with _request_runtime(ctx, repo_root) as runtime:
            return redact_json(runtime.credential_summary(connector, environment))
    except (LookupError, OSError, RuntimeError, ValueError) as error:
        return _error_payload(error)


@local_mcp.resource("mercury://wiki/index")
def wiki_index() -> str:
    return (
        "Use search_knowledge or retrieve_context_pack to fetch reviewed Mercury "
        "Cloud knowledge with citations."
    )


@local_mcp.resource("mercury://wiki/doc/{document_id}")
async def wiki_document(document_id: str, ctx: Context) -> str:
    payload = await get_document(document_id=document_id, ctx=ctx)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@local_mcp.resource("mercury://skills/{skill_id}")
async def skill_resource(skill_id: str, ctx: Context) -> str:
    try:
        async with _request_runtime(ctx, None) as runtime:
            skill = await runtime.cloud.get_skill(skill_id)
        payload = {"status": "ok", "skill": skill} if skill else {"status": "skill_not_found"}
    except (httpx.HTTPError, LookupError, OSError, RuntimeError, ValueError) as error:
        payload = _error_payload(error)
    return json.dumps(redact_json(payload), ensure_ascii=False, sort_keys=True)


@local_mcp.resource("mercury://connectors")
async def connectors_resource(ctx: Context) -> str:
    payload = await connector_status(ctx=ctx)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


@local_mcp.resource("mercury://audit/{event_id}")
async def audit_resource(event_id: str, ctx: Context) -> str:
    event = None
    try:
        async with _request_runtime(ctx, None) as runtime:
            event = await asyncio.to_thread(runtime.audit.get, event_id)
        payload = {"status": "ok", "event": event} if event else {"status": "audit_not_found"}
    except (LookupError, OSError, RuntimeError, ValueError) as error:
        payload = _error_payload(error)
    sanitized = redact_json(payload)
    if event is not None and isinstance(sanitized.get("event"), dict):
        sanitized["event"]["event_id"] = event_id
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)


def _register_prompts() -> None:
    for prompt_name in PROMPTS:
        local_mcp.prompt(name=prompt_name)(_prompt_factory(prompt_name))


def _prompt_factory(prompt_name: str):
    def prompt() -> str:
        return get_prompt(prompt_name)

    prompt.__name__ = prompt_name
    return prompt


_register_prompts()


class _FlowContextPack:
    def __init__(self, query: str, task: str | None, results: list[Any]) -> None:
        self.query = query
        self.task = task
        self.results = results

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "task": self.task,
            "context": [vars(result) for result in self.results],
        }


class _FlowCloudAdapter:
    def __init__(
        self,
        runtime: LocalMercuryRuntime,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.runtime = runtime
        self.loop = loop

    def search(self, query: str, *, filters: Any, top_k: int, mode: str) -> list[Any]:
        del mode
        results = self._wait(
            self.runtime.search_knowledge(
                query,
                filters=_flow_filters(filters),
                top_k=top_k,
            )
        )
        return [_flow_result(result) for result in results]

    def context_pack(
        self,
        query: str,
        *,
        task: str | None,
        filters: Any,
        max_chunks: int,
    ) -> _FlowContextPack:
        return _FlowContextPack(
            query,
            task,
            self.search(query, filters=filters, top_k=max_chunks, mode="keyword"),
        )

    def _wait(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()


async def _run_local_flow(
    runtime: LocalMercuryRuntime,
    *,
    flow_yaml: str | None,
    flow_path: Path | None,
    env: dict[str, Any] | None,
    dry_run: bool,
) -> dict[str, Any]:
    await runtime.refresh_catalog()
    loop = asyncio.get_running_loop()
    cloud_adapter = _FlowCloudAdapter(runtime, loop)

    def wait(coroutine: Coroutine[Any, Any, Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result()

    runner = MercuryFlowRunner(
        dry_run=dry_run,
        rag_service_factory=lambda: cloud_adapter,
        document_getter=lambda document_id: wait(runtime.get_document(document_id)),
        connector_status_getter=lambda: {
            "status": "ok",
            "connectors": runtime.connector_summaries(),
        },
        skill_runner=lambda skill_id, inputs, evidence_mode: wait(
            runtime.run_accounting_skill(
                skill_id,
                inputs=inputs,
                evidence_mode=evidence_mode,
            )
        ),
        erp_read_callback=lambda action_id, inputs, environment: wait(
            runtime.run_read(action_id, inputs, environment)
        ),
        erp_write_preview_callback=lambda action_id, inputs, environment: wait(
            runtime.preview_write(action_id, inputs, environment)
        ),
        flow_loader=repository_flow_loader(runtime.repository.root),
        capability_gate=None,
    )
    if flow_path is not None:
        relative_path = flow_path.relative_to(runtime.repository.root).as_posix()
        loader = runner.flow_loader
        if loader is None:
            raise FlowValidationError("flow_path_invalid")
        result = await asyncio.to_thread(
            lambda: runner.run_flow(loader.load_path(relative_path), env=env)
        )
    else:
        if flow_yaml is None:
            raise FlowValidationError("Flow YAML is required.")
        inline_path = runtime.repository.root / ".mercury-inline-flow.yaml"
        result = await asyncio.to_thread(
            runner.run_text,
            flow_yaml,
            path=inline_path,
            env=env,
        )
    return redact_json(result.as_dict())


def _flow_filters(filters: Any) -> dict[str, str]:
    values = {
        name: getattr(filters, name, None)
        for name in (
            "jurisdiction",
            "connector",
            "doc_type",
            "review_status",
            "effective_date",
        )
    }
    return {name: value for name, value in values.items() if isinstance(value, str) and value}


def _flow_result(result: Mapping[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=result.get("chunk_id"),
        document_id=result.get("document_id"),
        document_uri=result.get("document_uri"),
        chunk_uri=result.get("chunk_uri"),
        score=result.get("score"),
        text=result.get("text"),
        citation=result.get("citation"),
        source_title=result.get("source_title"),
        source_uri=result.get("source_uri"),
        source_url=result.get("source_url"),
        source_path=None,
        metadata={},
    )


def _repository_path(
    repository: RepositoryContext,
    raw_path: str,
    *,
    expected: str,
) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("path_outside_repository_root")
    if raw_path != "." and any(not part for part in raw_path.split("/")):
        raise ValueError("path_outside_repository_root")
    requested = Path(raw_path)
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError("path_outside_repository_root")
    if expected == "directory" and raw_path == ".":
        return repository.root
    if not requested.parts or any(part in {".", ".."} for part in requested.parts):
        raise ValueError("path_outside_repository_root")
    candidate = repository.root.joinpath(*requested.parts)
    checked = repository.root
    for component in requested.parts:
        checked = checked / component
        if checked.is_symlink():
            raise ValueError("path_outside_repository_root")
    if expected in {"file", "save"} and candidate.suffix.casefold() not in _FLOW_SUFFIXES:
        raise ValueError("flow_path_invalid")
    return candidate


def _flow_summaries(repository: RepositoryContext, directory: Path) -> list[dict[str, Any]]:
    relative_directory = directory.relative_to(repository.root)
    loader = repository_flow_loader(repository.root)
    raw_directory = relative_directory.as_posix() if relative_directory.parts else None
    flows: list[dict[str, Any]] = []
    for relative_path, flow in loader.list_flows(raw_directory):
        flows.append(
            {
                "path": relative_path.as_posix(),
                "name": flow.name,
                "tags": list(flow.tags),
                "command_count": len(flow.commands),
            }
        )
    return flows


def _write_flow_file(
    repository: RepositoryContext,
    destination: Path,
    content: str,
) -> None:
    relative = destination.relative_to(repository.root)
    if os.name != "posix" or any(
        not hasattr(os, name) for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    ):
        raise ValueError("flow_path_invalid")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(repository.root, directory_flags)
    parent_fd = root_fd
    opened: list[int] = []
    temporary_name = f".flow-{secrets.token_hex(12)}.tmp"
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            opened.append(next_fd)
            parent_fd = next_fd
        name = relative.name
        try:
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ValueError("flow_path_invalid")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            descriptor = -1
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        os.replace(temporary_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as error:
        raise ValueError("flow_path_invalid") from error
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=parent_fd)
        for descriptor in reversed(opened):
            os.close(descriptor)
        os.close(root_fd)
def serve_local() -> None:
    local_mcp.run(transport="stdio")
