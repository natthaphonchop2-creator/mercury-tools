"""Mercury Tools MCP server."""

# ruff: noqa: E501

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from mercury_tools.config import load_settings
from mercury_tools.db.product import SupabaseProductStore, slugify
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.flows.parser import FlowValidationError, validate_flow_text
from mercury_tools.flows.runner import create_default_runner
from mercury_tools.flows.templates import FLOW_CHEAT_SHEET
from mercury_tools.mercury_runtime import connector_status as read_connector_status
from mercury_tools.mercury_runtime import skill_markdown
from mercury_tools.product import (
    build_connection_payload,
    create_client_token,
    is_authorized_bearer,
    validate_connect_request,
    verify_client_token,
)
from mercury_tools.product_ui import render_connect_html
from mercury_tools.prompts import get_prompt
from mercury_tools.rag.chunking import chunk_document, sha256_text
from mercury_tools.rag.embeddings import create_embedding_provider
from mercury_tools.rag.models import KnowledgeDocument, SearchFilters
from mercury_tools.rag.service import RagService
from mercury_tools.safety.redaction import redact_json

mcp = FastMCP("Mercury Tools")

MAX_MCP_FLOW_FILES = 50
MAX_MCP_FLOW_FILE_CHARS = 500_000


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, protected_path: str):
        super().__init__(app)
        self.protected_path = protected_path.rstrip("/") or "/"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        protected = path == self.protected_path or path.startswith(f"{self.protected_path}/")
        if request.method == "OPTIONS" or not protected:
            return await call_next(request)

        if not is_authorized_bearer(load_settings(), request.headers.get("authorization")):
            return JSONResponse(
                {"error": "unauthorized", "message": "Valid bearer token is required."},
                status_code=401,
            )
        return await call_next(request)


def _service() -> RagService:
    settings = load_settings()
    return RagService(
        store=SupabaseRagStore(settings),
        embedder=create_embedding_provider(settings),
    )


def _audit(tool_name: str, input_payload: dict[str, Any], output_summary: dict[str, Any]) -> None:
    try:
        settings = load_settings()
        SupabaseRagStore(settings).record_audit_event(
            {
                "tool_name": tool_name,
                "input": input_payload,
                "output_summary": output_summary,
                "status": "ok",
                "metadata": {"runtime": "mcp"},
            }
        )
    except Exception:
        pass


def _filters(filters: dict[str, Any] | None) -> SearchFilters:
    filters = filters or {}
    return SearchFilters(
        jurisdiction=filters.get("jurisdiction"),
        connector=filters.get("connector"),
        doc_type=filters.get("doc_type"),
        review_status=filters.get("review_status"),
        effective_date=filters.get("effective_date"),
    )


def _env_overrides_from_payload(raw: Any) -> dict[str, str]:
    """Normalize Maestro-style runtime env values without preserving object secrets."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("env must be an object.")
    env: dict[str, str] = {}
    for key, value in raw.items():
        clean_key = str(key).strip()
        if not clean_key:
            raise ValueError("env keys cannot be empty.")
        env[clean_key] = "" if value is None else str(value)
    return env


def _env_keys(env: dict[str, str]) -> list[str]:
    return sorted(env)


def _string_list_from_payload(raw: Any, *, label: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    raise ValueError(f"{label} must be a string or list.")


def _matches_tag_filter(tags: list[str], *, include: set[str], exclude: set[str]) -> bool:
    tag_set = set(tags)
    if include and not tag_set.intersection(include):
        return False
    return not bool(tag_set.intersection(exclude))


def _safe_flow_file_path(raw: Any) -> str:
    path = str(raw or "").strip().replace("\\", "/")
    if not path:
        raise ValueError("flow file path is required.")
    candidate = Path(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"flow file path must be relative and safe: {path}")
    return candidate.as_posix()


def _flow_files_from_payload(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, dict):
        items = [
            {"path": _safe_flow_file_path(path), "flow_yaml": str(flow_yaml or "")}
            for path, flow_yaml in raw.items()
        ]
    elif isinstance(raw, list):
        items = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"flow_files[{index}] must be an object.")
            path = item.get("path") or item.get("filename") or item.get("name")
            flow_yaml = item.get("flow_yaml")
            if flow_yaml is None:
                flow_yaml = item.get("yaml")
            if flow_yaml is None:
                flow_yaml = item.get("content")
            items.append(
                {
                    "path": _safe_flow_file_path(path),
                    "flow_yaml": str(flow_yaml or ""),
                }
            )
    else:
        raise ValueError("flow_files must be an object or list.")

    if not items:
        raise ValueError("flow_files must include at least one flow file.")
    if len(items) > MAX_MCP_FLOW_FILES:
        raise ValueError(f"flow_files may include at most {MAX_MCP_FLOW_FILES} files.")
    total_chars = sum(len(item["flow_yaml"]) for item in items)
    if total_chars > MAX_MCP_FLOW_FILE_CHARS:
        raise ValueError(
            f"flow_files total content may be at most {MAX_MCP_FLOW_FILE_CHARS} characters."
        )
    return items


def _suite_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "empty"
    if any(result.get("status") == "error" for result in results):
        return "failed"
    if all(result.get("status") == "planned" for result in results):
        return "planned"
    return "ok"


def _flow_file_error_result(
    *,
    path: str,
    message: str,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "status": "error",
        "dry_run": dry_run,
        "flow": {
            "name": Path(path).stem or "Mercury Flow",
            "description": None,
            "tags": [],
            "env": {},
            "path": path,
            "command_count": 0,
            "on_flow_start_count": 0,
            "on_flow_complete_count": 0,
            "commands": [],
        },
        "steps": [],
        "variables": {},
        "artifacts": [{"status": "error", "message": message}],
    }


def _relativize_temp_paths(value: Any, *, root: Path) -> Any:
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if key == "path" and isinstance(item, str):
                try:
                    converted[key] = Path(item).resolve().relative_to(root).as_posix()
                    continue
                except (OSError, ValueError):
                    pass
            converted[key] = _relativize_temp_paths(item, root=root)
        return converted
    if isinstance(value, list):
        return [_relativize_temp_paths(item, root=root) for item in value]
    return value


def _client_token_audit_ref(client_token: str) -> dict[str, str]:
    token = client_token.strip()
    return {
        "client_token_prefix": token[:3],
        "client_token_hash": sha256_text(token)[:16],
    }


def _client_token_payload(request: Request) -> dict[str, Any]:
    settings = load_settings()
    authorization = request.headers.get("authorization") or ""
    if not authorization.startswith("Bearer "):
        raise PermissionError("Mercury client token is required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith("mc_"):
        raise PermissionError("Mercury client token is required for product APIs.")
    return verify_client_token(settings, token)


def _product_store(settings=None) -> SupabaseProductStore:
    return SupabaseProductStore(settings or load_settings())


def _client_token_payload_from_value(client_token: str) -> dict[str, Any]:
    token = client_token.strip()
    if not token.startswith("mc_"):
        raise PermissionError("Mercury client token must start with mc_.")
    return verify_client_token(load_settings(), token)


def _public_flow_summary(flow: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in flow.items()
        if key
        in {
            "flow_id",
            "title",
            "name",
            "description",
            "tags",
            "command_count",
            "on_flow_start_count",
            "on_flow_complete_count",
            "sha256",
            "status",
            "metadata",
            "updated_at",
        }
    }


def _fallback_dashboard(token_payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "status": "degraded",
        "reason": reason,
        "workspace": {
            "name": token_payload.get("company"),
            "host_app": token_payload.get("host_app"),
        },
        "member": {"email": token_payload.get("sub")},
        "connectors": [],
        "connector_profiles": [],
        "skills": [],
        "flows": [],
        "events": [],
    }


def _json_error(error: str, message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse({"error": error, "message": message}, status_code=status_code)


@mcp.tool()
def search_knowledge(
    query: str,
    filters: dict[str, Any] | None = None,
    top_k: int = 8,
    mode: str = "hybrid",
) -> dict[str, Any]:
    """Search Mercury accounting knowledge and return citation-bearing chunks."""
    results = _service().search(query, filters=_filters(filters), top_k=top_k, mode=mode)
    payload = {
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
    payload = redact_json(payload)
    _audit(
        "search_knowledge",
        {"query": query, "filters": filters, "top_k": top_k},
        {"count": len(results)},
    )
    return payload


@mcp.tool()
def retrieve_context_pack(
    query: str,
    task: str | None = None,
    filters: dict[str, Any] | None = None,
    max_chunks: int = 12,
) -> dict[str, Any]:
    """Return a context pack with citations for the host agent to answer with."""
    pack = _service().context_pack(
        query,
        task=task,
        filters=_filters(filters),
        max_chunks=max_chunks,
    )
    payload = redact_json(pack.as_dict())
    _audit("retrieve_context_pack", {"query": query, "task": task}, {"count": len(pack.results)})
    return payload


@mcp.tool()
def get_document(document_id: str) -> dict[str, Any]:
    """Fetch one indexed knowledge document by UUID or document URI."""
    document = SupabaseRagStore(load_settings()).get_document(document_id)
    payload = redact_json({"status": "ok" if document else "not_found", "document": document})
    _audit("get_document", {"document_id": document_id}, {"found": bool(document)})
    return payload


@mcp.tool()
def connector_status() -> dict[str, Any]:
    """Read sanitized Mercury connector status from Mercury runtime metadata."""
    payload = read_connector_status()
    _audit("connector_status", {}, {"status": payload.get("status")})
    return payload


@mcp.tool()
def run_accounting_skill(
    skill_id: str,
    inputs: dict[str, Any],
    evidence_mode: bool = False,
) -> dict[str, Any]:
    """Return a read-only accounting skill execution package for the host agent."""
    markdown = skill_markdown(skill_id)
    payload = redact_json(
        {
            "status": "ok" if markdown else "not_found",
            "skill_id": skill_id,
            "inputs": inputs,
            "evidence_mode": evidence_mode,
            "skill_markdown": markdown,
            "note": "v1 returns a read-only skill package; production writes are blocked.",
        }
    )
    _audit(
        "run_accounting_skill",
        {"skill_id": skill_id, "inputs": inputs},
        {"status": payload["status"]},
    )
    return payload


@mcp.tool()
def flow_cheat_sheet() -> dict[str, Any]:
    """Return Mercury Flow command syntax and examples."""
    payload = {"status": "ok", "cheat_sheet": FLOW_CHEAT_SHEET}
    _audit("flow_cheat_sheet", {}, {"status": "ok"})
    return payload


@mcp.tool()
def check_flow_syntax(flow_yaml: str) -> dict[str, Any]:
    """Validate a Mercury YAML flow without executing it."""
    try:
        payload = validate_flow_text(flow_yaml)
        _audit(
            "check_flow_syntax",
            {"flow_yaml_length": len(flow_yaml)},
            {"status": "ok", "command_count": payload["flow"]["command_count"]},
        )
        return payload
    except FlowValidationError as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit("check_flow_syntax", {"flow_yaml_length": len(flow_yaml)}, payload)
        return payload


@mcp.tool()
def run_flow(
    flow_yaml: str,
    dry_run: bool = False,
    env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a Mercury YAML flow or return an execution plan when dry_run is true."""
    try:
        env_overrides = _env_overrides_from_payload(env)
        result = create_default_runner(dry_run=dry_run).run_text(flow_yaml, env=env_overrides)
        payload = redact_json(result.as_dict())
        _audit(
            "run_flow",
            {
                "flow_yaml_length": len(flow_yaml),
                "dry_run": dry_run,
                "env_keys": _env_keys(env_overrides),
            },
            {
                "status": payload["status"],
                "step_count": len(payload["steps"]),
                "dry_run": dry_run,
            },
        )
        return payload
    except (FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc), "dry_run": dry_run}
        safe_env_keys = _env_keys(env) if isinstance(env, dict) else []
        _audit(
            "run_flow",
            {
                "flow_yaml_length": len(flow_yaml),
                "dry_run": dry_run,
                "env_keys": safe_env_keys,
            },
            payload,
        )
        return payload


@mcp.tool()
def run_flow_files(
    flow_files: dict[str, str] | list[dict[str, Any]],
    dry_run: bool = False,
    env: dict[str, Any] | None = None,
    include_tags: list[str] | str | None = None,
    exclude_tags: list[str] | str | None = None,
    continue_on_failure: bool = True,
) -> dict[str, Any]:
    """Run multiple Mercury YAML flow files as an in-memory suite."""
    normalized_files: list[dict[str, str]] = []
    env_overrides: dict[str, str] = {}
    try:
        normalized_files = _flow_files_from_payload(flow_files)
        env_overrides = _env_overrides_from_payload(env)
        include = set(_string_list_from_payload(include_tags, label="include_tags"))
        exclude = set(_string_list_from_payload(exclude_tags, label="exclude_tags"))
        file_records: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []

        with TemporaryDirectory(prefix="mercury-flow-files-") as temp_dir:
            root = Path(temp_dir).resolve()
            for item in normalized_files:
                target = root / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["flow_yaml"], encoding="utf-8")

            runner = create_default_runner(dry_run=dry_run)
            for item in normalized_files:
                relative_path = item["path"]
                target = root / relative_path
                try:
                    syntax = validate_flow_text(item["flow_yaml"], path=target)
                    flow_summary = syntax["flow"]
                    tags = [str(tag) for tag in flow_summary.get("tags") or []]
                    selected = _matches_tag_filter(tags, include=include, exclude=exclude)
                    file_records.append(
                        {
                            "path": relative_path,
                            "name": flow_summary.get("name"),
                            "tags": tags,
                            "command_count": flow_summary.get("command_count") or 0,
                            "selected": selected,
                            "status": "valid",
                        }
                    )
                    if not selected:
                        continue
                    result = runner.run_path(target, env=env_overrides).as_dict()
                    results.append(_relativize_temp_paths(result, root=root))
                except (FlowValidationError, RuntimeError, ValueError) as exc:
                    selected = not include
                    file_records.append(
                        {
                            "path": relative_path,
                            "name": None,
                            "tags": [],
                            "command_count": 0,
                            "selected": selected,
                            "status": "invalid" if isinstance(exc, FlowValidationError) else "error",
                            "error": str(exc),
                        }
                    )
                    if not selected:
                        continue
                    if not continue_on_failure:
                        raise
                    results.append(
                        _flow_file_error_result(
                            path=relative_path,
                            message=str(exc),
                            dry_run=dry_run,
                        )
                    )

        payload = redact_json(
            {
                "status": _suite_status(results),
                "dry_run": dry_run,
                "flow_count": len(normalized_files),
                "selected_count": len([item for item in file_records if item.get("selected")]),
                "skipped_count": len([item for item in file_records if not item.get("selected")]),
                "env_keys": _env_keys(env_overrides),
                "include_tags": sorted(include),
                "exclude_tags": sorted(exclude),
                "flows": file_records,
                "results": results,
            }
        )
        _audit(
            "run_flow_files",
            {
                "flow_count": len(normalized_files),
                "selected_count": payload["selected_count"],
                "dry_run": dry_run,
                "env_keys": _env_keys(env_overrides),
                "include_tags": sorted(include),
                "exclude_tags": sorted(exclude),
                "total_flow_yaml_length": sum(len(item["flow_yaml"]) for item in normalized_files),
            },
            {
                "status": payload["status"],
                "result_count": len(payload["results"]),
                "dry_run": dry_run,
            },
        )
        return payload
    except (FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc), "dry_run": dry_run}
        _audit(
            "run_flow_files",
            {
                "flow_count": len(normalized_files),
                "dry_run": dry_run,
                "env_keys": _env_keys(env_overrides),
            },
            payload,
        )
        return payload


@mcp.tool()
def list_workspace_flows(client_token: str) -> dict[str, Any]:
    """List saved Mercury workspace flows for a connected host token."""
    try:
        settings = load_settings()
        if not settings.supabase_configured:
            raise RuntimeError("Supabase is required to list saved workspace flows.")
        token_payload = _client_token_payload_from_value(client_token)
        dashboard_payload = _product_store(settings).dashboard(token_payload)
        flows = [_public_flow_summary(flow) for flow in dashboard_payload.get("flows", [])]
        payload = redact_json(
            {
                "status": "ok",
                "workspace": dashboard_payload.get("workspace") or {},
                "flow_count": len(flows),
                "flows": flows,
            }
        )
        _audit("list_workspace_flows", _client_token_audit_ref(client_token), {"flow_count": len(flows)})
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit("list_workspace_flows", _client_token_audit_ref(client_token), payload)
        return payload


@mcp.tool(name="run_workspace_flow")
def run_workspace_flow_tool(
    client_token: str,
    flow_id: str,
    dry_run: bool = True,
    env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one saved Mercury workspace flow by id, or return a dry-run plan."""
    try:
        env_overrides = _env_overrides_from_payload(env)
        settings = load_settings()
        if not settings.supabase_configured:
            raise RuntimeError("Supabase is required to load saved workspace flows.")
        token_payload = _client_token_payload_from_value(client_token)
        store = _product_store(settings)
        flow = store.get_flow(token_payload=token_payload, flow_id=flow_id)
        if not flow:
            return {"status": "not_found", "message": f"Workspace flow not found: {flow_id}"}
        result = create_default_runner(dry_run=dry_run).run_text(
            str(flow.get("yaml") or ""),
            env=env_overrides,
        )
        payload = redact_json(
            {
                **result.as_dict(),
                "workspace_flow": _public_flow_summary(flow),
            }
        )
        try:
            payload["run_record"] = store.record_flow_run(
                token_payload=token_payload,
                flow_id=flow_id,
                title=str(flow.get("title") or flow.get("name") or ""),
                result_payload=payload,
                dry_run=dry_run,
                env_keys=_env_keys(env_overrides),
            )
        except (AttributeError, RuntimeError, ValueError) as exc:
            payload["run_history"] = {"status": "not_recorded", "message": str(exc)}
        _audit(
            "run_workspace_flow",
            {
                **_client_token_audit_ref(client_token),
                "flow_id": flow_id,
                "dry_run": dry_run,
                "env_keys": _env_keys(env_overrides),
            },
            {
                "status": payload["status"],
                "flow_id": flow_id,
                "step_count": len(payload["steps"]),
                "dry_run": dry_run,
            },
        )
        return payload
    except (PermissionError, FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc), "dry_run": dry_run}
        safe_env_keys = _env_keys(env) if isinstance(env, dict) else []
        _audit(
            "run_workspace_flow",
            {
                **_client_token_audit_ref(client_token),
                "flow_id": flow_id,
                "dry_run": dry_run,
                "env_keys": safe_env_keys,
            },
            payload,
        )
        return payload


@mcp.tool(name="save_workspace_flow")
def save_workspace_flow_tool(
    client_token: str,
    title: str,
    flow_yaml: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save one Mercury flow into the connected workspace."""
    try:
        settings = load_settings()
        if not settings.supabase_configured:
            raise RuntimeError("Supabase is required to save workspace flows.")
        token_payload = _client_token_payload_from_value(client_token)
        flow = _product_store(settings).save_flow(
            token_payload=token_payload,
            title=title,
            flow_yaml=flow_yaml,
            metadata=metadata or {"source": "mcp"},
        )
        payload = redact_json({"status": "ok", "flow": _public_flow_summary(flow)})
        _audit(
            "save_workspace_flow",
            {
                **_client_token_audit_ref(client_token),
                "title": title,
                "flow_yaml_length": len(flow_yaml),
            },
            {"status": "ok", "flow_id": flow["flow_id"]},
        )
        return payload
    except (PermissionError, FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "save_workspace_flow",
            {
                **_client_token_audit_ref(client_token),
                "title": title,
                "flow_yaml_length": len(flow_yaml),
            },
            payload,
        )
        return payload


@mcp.resource("mercury://wiki/index")
def wiki_index() -> str:
    """Return the Mercury LLM Wiki index prompt resource."""
    return (
        "Use search_knowledge or retrieve_context_pack to fetch indexed wiki content "
        "with citations."
    )


@mcp.resource("mercury://wiki/doc/{document_id}")
def wiki_document(document_id: str) -> str:
    """Return one knowledge document body."""
    document = SupabaseRagStore(load_settings()).get_document(document_id)
    if not document:
        return "Document not found."
    return str(document.get("body") or "")


@mcp.resource("mercury://skills/{skill_id}")
def skill_resource(skill_id: str) -> str:
    """Return bundled Mercury skill markdown if available locally."""
    return skill_markdown(skill_id) or "Skill not found."


@mcp.resource("mercury://flows/cheat-sheet")
def flows_cheat_sheet_resource() -> str:
    """Return Mercury Flow syntax and examples."""
    return FLOW_CHEAT_SHEET


@mcp.resource("mercury://connectors")
def connectors_resource() -> str:
    """Return sanitized connector status."""
    return str(connector_status())


@mcp.resource("mercury://audit/{event_id}")
def audit_resource(event_id: str) -> str:
    """Return one sanitized MCP audit event from Supabase."""
    event = SupabaseRagStore(load_settings()).get_audit_event(event_id)
    if not event:
        return "Audit event not found."
    return str(redact_json(event))


@mcp.prompt()
def company_health_check_th() -> str:
    return get_prompt("company_health_check_th")


@mcp.prompt()
def vat_summary_th() -> str:
    return get_prompt("vat_summary_th")


@mcp.prompt()
def invoice_review_th() -> str:
    return get_prompt("invoice_review_th")


@mcp.prompt()
def management_report_th() -> str:
    return get_prompt("management_report_th")


@mcp.prompt()
def connector_setup_guide_th() -> str:
    return get_prompt("connector_setup_guide_th")


async def root(request: Request) -> Response:
    page = request.url.path.strip("/") or "start"
    return HTMLResponse(render_connect_html(page))


async def status(_: Request) -> Response:
    settings = load_settings()
    return JSONResponse(
        {
            "name": "Mercury Tools MCP",
            "status": "ok",
            "supabase": settings.supabase_configured,
            "openai": settings.openai_configured,
            "embedding_provider": settings.embedding_provider,
            "embedding_configured": settings.embedding_configured,
            "transport": "streamable-http",
            "mcp_path": settings.mcp_path,
            "mcp_endpoint": settings.mcp_endpoint,
            "health": "/healthz",
            "console": {
                "purpose": "setup-console",
                "product_surface": "mcp-host",
                "note": "Pages configure Mercury tools and context; Codex, Cursor, Claude, or another host owns chat.",
            },
            "pages": {
                "start": "/",
                "connect": "/connect",
                "workspace": "/workspace",
                "connectors": "/connectors",
                "knowledge": "/knowledge",
                "skills": "/skills",
                "flows": "/flows",
                "mcp_api": "/mcp-api",
                "audit": "/audit",
            },
            "connect": "/api/connect",
            "dashboard": "/api/dashboard",
            "connector_setup": "/api/connectors/setup",
            "connector_credentials": "/api/connectors/credentials",
            "team_invite": "/api/team/invite",
            "skill_enable": "/api/skills/enable",
            "skill_upload": "/api/skills/upload",
            "flow_validate": "/api/flows/validate",
            "flow_save": "/api/flows/save",
            "flow_import": "/api/flows/import",
            "flow_run": "/api/flows/run",
            "flow_tools": [
                "flow_cheat_sheet",
                "check_flow_syntax",
                "run_flow",
                "run_flow_files",
                "save_workspace_flow",
                "list_workspace_flows",
                "run_workspace_flow",
            ],
            "http_auth_configured": settings.http_auth_configured,
            "invite_required": bool(settings.connect_invite_code),
        }
    )


async def connect(request: Request) -> Response:
    settings = load_settings()
    try:
        data = await request.json()
        connect_request = validate_connect_request(settings, data)
        token = create_client_token(settings, connect_request)
        token_payload = verify_client_token(settings, token)
        payload = build_connection_payload(
            public_base_url=settings.public_base_url or str(request.base_url).rstrip("/"),
            mcp_path=settings.mcp_path,
            token=token,
            email=connect_request.email,
            company=connect_request.company,
            host_app=connect_request.host_app,
        )
        payload["dashboard"] = {"api": "/api/dashboard", "url": str(request.base_url).rstrip("/")}
        if settings.supabase_configured:
            try:
                persisted = _product_store(settings).upsert_connection(connect_request, token_payload)
                payload["persistence"] = {
                    "status": "ok",
                    "workspace_id": persisted["workspace"]["id"],
                    "member_id": persisted["member"]["id"],
                }
            except RuntimeError as exc:
                payload["persistence"] = {
                    "status": "degraded",
                    "reason": str(exc),
                }
        else:
            payload["persistence"] = {
                "status": "degraded",
                "reason": "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are not configured.",
            }
        return JSONResponse(payload)
    except PermissionError as exc:
        return JSONResponse({"error": "forbidden", "message": str(exc)}, status_code=403)
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)


async def dashboard(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return JSONResponse(
                _fallback_dashboard(token_payload, reason="Supabase is not configured.")
            )
        store = _product_store(settings)
        store.seed_skill_catalog()
        return JSONResponse(redact_json(store.dashboard(token_payload)))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return JSONResponse(_fallback_dashboard(token_payload, reason=str(exc)))


async def validate_workspace_flow(request: Request) -> Response:
    try:
        _client_token_payload(request)
        data = await request.json()
        flow_yaml = str(data.get("flow_yaml") or "")
        return JSONResponse(validate_flow_text(flow_yaml))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except FlowValidationError as exc:
        return _json_error("invalid_flow", str(exc), status_code=400)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)


async def save_workspace_flow(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to save workspace flows.",
                status_code=503,
            )
        data = await request.json()
        flow = _product_store(settings).save_flow(
            token_payload=token_payload,
            title=str(data.get("title") or "").strip() or None,
            flow_yaml=str(data.get("flow_yaml") or ""),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )
        return JSONResponse(redact_json({"status": "ok", "flow": flow}))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except FlowValidationError as exc:
        return _json_error("invalid_flow", str(exc), status_code=400)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("server_error", str(exc), status_code=500)


async def import_workspace_flows(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to import workspace flows.",
                status_code=503,
            )
        data = await request.json()
        flows = data.get("flows") or []
        if not isinstance(flows, list) or not flows:
            raise ValueError("flows must be a non-empty list.")
        if len(flows) > 50:
            raise ValueError("A single import may include at most 50 flows.")

        normalized_flows: list[dict[str, Any]] = []
        for index, item in enumerate(flows):
            if not isinstance(item, dict):
                raise ValueError(f"flows[{index}] must be an object.")
            flow_yaml = str(item.get("flow_yaml") or "")
            validate_flow_text(flow_yaml)
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            normalized_flows.append(
                {
                    "title": str(item.get("title") or "").strip() or None,
                    "flow_yaml": flow_yaml,
                    "metadata": {
                        "source": "flow-import",
                        **metadata,
                    },
                }
            )

        store = _product_store(settings)
        saved = [
            store.save_flow(
                token_payload=token_payload,
                title=item["title"],
                flow_yaml=item["flow_yaml"],
                metadata=item["metadata"],
            )
            for item in normalized_flows
        ]
        return JSONResponse(
            redact_json(
                {
                    "status": "ok",
                    "imported_count": len(saved),
                    "workspace": data.get("workspace") if isinstance(data.get("workspace"), dict) else {},
                    "flows": [_public_flow_summary(flow) for flow in saved],
                }
            )
        )
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except FlowValidationError as exc:
        return _json_error("invalid_flow", str(exc), status_code=400)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("server_error", str(exc), status_code=500)


async def run_workspace_flow(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        data = await request.json()
        dry_run = bool(data.get("dry_run", True))
        env_overrides = _env_overrides_from_payload(data.get("env"))
        flow_yaml = str(data.get("flow_yaml") or "")
        flow_id = str(data.get("flow_id") or "").strip()
        flow_title = str(data.get("title") or "").strip()
        flow: dict[str, Any] | None = None
        if flow_id and not flow_yaml:
            if not settings.supabase_configured:
                return _json_error(
                    "service_unavailable",
                    "Supabase is required to load saved workspace flows.",
                    status_code=503,
                )
            flow = _product_store(settings).get_flow(token_payload=token_payload, flow_id=flow_id)
            if not flow:
                return _json_error("not_found", f"Workspace flow not found: {flow_id}", status_code=404)
            flow_yaml = str(flow.get("yaml") or "")
            flow_title = str(flow.get("title") or flow.get("name") or flow_title)
        result = create_default_runner(dry_run=dry_run).run_text(flow_yaml, env=env_overrides)
        payload = redact_json(result.as_dict())
        if flow:
            payload["workspace_flow"] = _public_flow_summary(flow)
        if settings.supabase_configured:
            try:
                payload["run_record"] = _product_store(settings).record_flow_run(
                    token_payload=token_payload,
                    flow_id=flow_id or None,
                    title=flow_title or str(payload.get("flow", {}).get("name") or ""),
                    result_payload=payload,
                    dry_run=dry_run,
                    env_keys=_env_keys(env_overrides),
                )
            except (AttributeError, RuntimeError, ValueError) as exc:
                payload["run_history"] = {"status": "not_recorded", "message": str(exc)}
        return JSONResponse(redact_json(payload))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except (FlowValidationError, RuntimeError, ValueError) as exc:
        return _json_error("bad_request", str(exc), status_code=400)


async def setup_connector(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to save connector profiles.",
                status_code=503,
            )
        data = await request.json()
        profile = _product_store(settings).set_connector_profile(
            token_payload=token_payload,
            connector_id=str(data.get("connector_id") or "").strip().lower(),
            environment=str(data.get("environment") or "").strip().lower(),
            company_name=str(data.get("company_name") or "").strip(),
            metadata={"source": "connect-ui"},
        )
        return JSONResponse(redact_json({"status": "ok", "profile": profile}))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def setup_connector_credentials(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to save connector credentials.",
                status_code=503,
            )
        data = await request.json()
        credentials = data.get("credentials") or {}
        if not isinstance(credentials, dict):
            raise ValueError("credentials must be an object.")
        result = _product_store(settings).set_connector_credentials(
            token_payload=token_payload,
            connector_id=str(data.get("connector_id") or "").strip().lower(),
            environment=str(data.get("environment") or "").strip().lower(),
            credentials={str(key): str(value) for key, value in credentials.items()},
        )
        return JSONResponse(redact_json({"status": "ok", "credentials": result}))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def enable_skill(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to manage workspace skills.",
                status_code=503,
            )
        data = await request.json()
        row = _product_store(settings).set_skill_enabled(
            token_payload=token_payload,
            skill_id=str(data.get("skill_id") or "").strip(),
            enabled=bool(data.get("enabled")),
        )
        return JSONResponse(redact_json({"status": "ok", "skill": row}))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def invite_member(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to invite workspace members.",
                status_code=503,
            )
        data = await request.json()
        member = _product_store(settings).invite_member(
            token_payload=token_payload,
            email=str(data.get("email") or "").strip(),
            role=str(data.get("role") or "member").strip().lower(),
        )
        return JSONResponse(redact_json({"status": "ok", "member": member}))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def upload_skill(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to upload workspace skills.",
                status_code=503,
            )
        data = await request.json()
        title = str(data.get("title") or "").strip()
        markdown = str(data.get("markdown") or "").strip()
        if not title:
            raise ValueError("Skill title is required.")
        if len(markdown) < 20:
            raise ValueError("Skill Markdown is too short.")

        context = _product_store(settings).workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        skill_hash = sha256_text(f"{title}\n{markdown}")[:8]
        skill_id = f"workspace-{slugify(title, fallback='skill')}-{skill_hash}"
        document_uri = f"mercury://workspace/{context['workspace']['id']}/skills/{skill_id}"
        document = KnowledgeDocument(
            document_uri=document_uri,
            title=title,
            body=markdown,
            sha256=sha256_text(markdown),
            source_uri=document_uri,
            source_title=title,
            jurisdiction=str(data.get("jurisdiction") or "TH"),
            connector=str(data.get("connector") or "") or None,
            doc_type="skill",
            review_status="draft",
            metadata={
                "workspace_id": context["workspace"]["id"],
                "skill_id": skill_id,
                "source": "connect-ui-upload",
            },
        )
        chunks = chunk_document(document)
        embeddings = create_embedding_provider(settings).embed_texts([chunk.text for chunk in chunks])
        SupabaseRagStore(settings).upsert_document_with_chunks(document, chunks, embeddings)
        upload = _product_store(settings).record_uploaded_skill(
            token_payload=token_payload,
            skill_id=skill_id,
            title=title,
            markdown=markdown,
            metadata={
                "category": str(data.get("category") or "custom"),
                "document_uri": document_uri,
                "chunks": len(chunks),
            },
        )
        return JSONResponse(
            redact_json(
                {
                    "status": "ok",
                    "skill_id": skill_id,
                    "document_uri": document_uri,
                    "chunks": len(chunks),
                    "upload": upload,
                }
            )
        )
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def healthz(_: Request) -> Response:
    settings = load_settings()
    return JSONResponse(
        {
            "status": "ok",
            "supabase": settings.supabase_configured,
            "openai": settings.openai_configured,
            "embedding_provider": settings.embedding_provider,
            "embedding_configured": settings.embedding_configured,
            "mcp_path": settings.mcp_path,
            "http_auth_required": settings.http_require_auth,
            "http_auth_configured": settings.http_auth_configured,
        }
    )


def create_http_app(*, require_auth: bool | None = None):
    settings = load_settings()
    mcp.settings.streamable_http_path = settings.mcp_path
    if settings.public_base_url:
        public_url = urlparse(settings.public_base_url)
        allowed_host = public_url.netloc
        allowed_origin = f"{public_url.scheme}://{public_url.netloc}"
        if allowed_host and allowed_host not in mcp.settings.transport_security.allowed_hosts:
            mcp.settings.transport_security.allowed_hosts.append(allowed_host)
        if allowed_origin and allowed_origin not in mcp.settings.transport_security.allowed_origins:
            mcp.settings.transport_security.allowed_origins.append(allowed_origin)
    app = mcp.streamable_http_app()
    for page_path in (
        "/",
        "/start",
        "/connect",
        "/workspace",
        "/connectors",
        "/knowledge",
        "/skills",
        "/flows",
        "/mcp-api",
        "/audit",
    ):
        app.add_route(page_path, root, methods=["GET"])
    app.add_route("/api/status", status, methods=["GET"])
    app.add_route("/api/connect", connect, methods=["POST"])
    app.add_route("/api/dashboard", dashboard, methods=["GET"])
    app.add_route("/api/connectors/setup", setup_connector, methods=["POST"])
    app.add_route("/api/connectors/credentials", setup_connector_credentials, methods=["POST"])
    app.add_route("/api/team/invite", invite_member, methods=["POST"])
    app.add_route("/api/skills/enable", enable_skill, methods=["POST"])
    app.add_route("/api/skills/upload", upload_skill, methods=["POST"])
    app.add_route("/api/flows/validate", validate_workspace_flow, methods=["POST"])
    app.add_route("/api/flows/save", save_workspace_flow, methods=["POST"])
    app.add_route("/api/flows/import", import_workspace_flows, methods=["POST"])
    app.add_route("/api/flows/run", run_workspace_flow, methods=["POST"])
    app.add_route("/healthz", healthz, methods=["GET"])

    should_require_auth = settings.http_require_auth if require_auth is None else require_auth
    if should_require_auth:
        if not settings.http_auth_configured:
            raise RuntimeError(
                "MERCURY_TOOLS_HTTP_BEARER_TOKEN or MERCURY_CONNECT_SIGNING_SECRET is required when HTTP auth is enabled."
            )
        app.add_middleware(
            BearerAuthMiddleware,
            protected_path=settings.mcp_path,
        )
    return app


def serve(
    *,
    transport: str = "streamable-http",
    host: str = "0.0.0.0",
    port: int = 8000,
    require_auth: bool | None = None,
) -> None:
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport in {"http", "streamable-http"}:
        import uvicorn

        uvicorn.run(create_http_app(require_auth=require_auth), host=host, port=port)
        return
    raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    serve()
