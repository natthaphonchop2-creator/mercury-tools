"""Mercury Tools MCP server."""

# ruff: noqa: E501

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from mercury_tools import __version__
from mercury_tools.cloud.api import CloudDependencies, cloud_routes
from mercury_tools.config import load_settings
from mercury_tools.connectors.catalog import (
    connector_by_id,
    list_connector_public_summaries,
    public_capability_gate,
)
from mercury_tools.db.product import (
    SupabaseProductStore,
    public_connector_profile,
    public_connector_profiles,
    slugify,
)
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.flows.parser import (
    FlowValidationError,
    parse_flow_text,
    parse_inline_commands,
    validate_flow_text,
)
from mercury_tools.flows.runner import create_default_runner
from mercury_tools.flows.templates import FLOW_CHEAT_SHEET
from mercury_tools.flows.workspace import discover_workspace_flows, workspace_manifest
from mercury_tools.mercury_runtime import skill_markdown
from mercury_tools.product import (
    build_connection_payload,
    create_client_token,
    is_authorized_bearer,
    validate_connect_request,
    verify_client_token,
)
from mercury_tools.prompts import get_prompt
from mercury_tools.rag.chunking import chunk_document, sha256_text
from mercury_tools.rag.embeddings import create_embedding_provider
from mercury_tools.rag.models import (
    ContextPack,
    KnowledgeDocument,
    SearchFilters,
    SearchResult,
    public_search_result_payload,
)
from mercury_tools.rag.routing import apply_knowledge_routing, infer_knowledge_domain
from mercury_tools.rag.service import MIN_RELEVANCE_SCORE, RagService
from mercury_tools.safety.redaction import redact_json
from mercury_tools.workspaces import (
    normalize_public_workspace_id,
    public_workspace_token_payload,
)

mcp = FastMCP("Mercury Tools")

_SEARCH_FILTER_FIELDS = frozenset(SearchFilters.__dataclass_fields__)
MAX_MCP_FLOW_FILES = 50
MAX_MCP_FLOW_FILE_CHARS = 500_000
CONNECTOR_ENV_KEYS = ("connector", "connector_id", "accounting_connector", "erp_connector")
ENVIRONMENT_ENV_KEYS = ("environment", "connector_environment", "connector_env")
CAPABILITY_KEYS = ("required_capabilities", "requiredCapabilities", "capabilities")
CONNECTOR_BACKED_COMMANDS = {"connectorStatus"}
CONNECTOR_TAGS = {"connector", "connectors", "connector-backed", "accounting-connector", "erp-connector"}
_LOWER_HEX = frozenset("0123456789abcdef")


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
    if filters is None:
        return SearchFilters()
    if not isinstance(filters, Mapping) or set(filters) - _SEARCH_FILTER_FIELDS:
        raise ValueError("search_filters_invalid")
    try:
        return SearchFilters(**dict(filters))
    except (TypeError, ValueError):
        raise ValueError("search_filters_invalid") from None


def _serialize_search_results(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [public_search_result_payload(result) for result in results]


def _merge_search_results(
    *result_sets: list[SearchResult],
    max_chunks: int,
) -> list[SearchResult]:
    by_chunk: dict[str, SearchResult] = {}
    for results in result_sets:
        for result in results:
            current = by_chunk.get(result.chunk_id)
            if current is None or result.score > current.score:
                by_chunk[result.chunk_id] = result
    return sorted(by_chunk.values(), key=lambda result: result.score, reverse=True)[
        :max_chunks
    ]


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


def _validate_config_yaml(config_yaml: str | None) -> str | None:
    if config_yaml is None:
        return None
    value = str(config_yaml)
    if len(value) > MAX_MCP_FLOW_FILE_CHARS:
        raise ValueError(f"config_yaml may be at most {MAX_MCP_FLOW_FILE_CHARS} characters.")
    return value


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


def _client_token_audit_ref_optional(client_token: str | None) -> dict[str, str]:
    if not isinstance(client_token, str) or not client_token:
        return {}
    return _client_token_audit_ref(client_token)


def _public_workspace_audit_ref(workspace_id: str) -> dict[str, str]:
    raw = str(workspace_id).strip()
    try:
        normalized = normalize_public_workspace_id(raw)
    except ValueError:
        return {
            "workspace_id_hash": sha256_text(raw)[:16],
            "workspace_id_valid": "false",
        }
    return {
        "workspace_id_prefix": normalized[:6],
        "workspace_id_hash": sha256_text(normalized)[:16],
    }


def _public_workspace_audit_ref_optional(workspace_id: str | None) -> dict[str, str]:
    if not isinstance(workspace_id, str) or not workspace_id:
        return {}
    return _public_workspace_audit_ref(workspace_id)


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


def _selected_flow_input_modes(
    *,
    flow_yaml: str | None,
    flow_files: Any,
    workspace_flow_id: str | None,
) -> list[str]:
    modes: list[str] = []
    if flow_yaml is not None:
        modes.append("flow_yaml")
    if flow_files is not None:
        modes.append("flow_files")
    if workspace_flow_id:
        modes.append("workspace_flow_id")
    return modes


def _client_token_payload_from_value(client_token: str) -> dict[str, Any]:
    token = client_token.strip()
    if not token.startswith("mc_"):
        raise PermissionError("Mercury client token must start with mc_.")
    return verify_client_token(load_settings(), token)


def _public_workspace_payload_from_value(workspace_id: str) -> dict[str, Any]:
    return public_workspace_token_payload(normalize_public_workspace_id(workspace_id))


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


def _profile_enabled_capabilities(profile: dict[str, Any]) -> list[Any]:
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    raw = metadata.get("enabled_capabilities") or profile.get("enabled_capabilities") or []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list | tuple | set):
        return [item for item in raw if str(item).strip()]
    return []


def _manifest_has_connection_healthcheck_adapter(manifest: Any) -> bool:
    validation = getattr(manifest, "validation", None)
    method = str(getattr(validation, "method", "") or "").strip().lower()
    endpoint = str(
        getattr(validation, "healthcheck_endpoint", "")
        or getattr(validation, "read_only_endpoint", "")
        or ""
    ).strip()
    return bool(
        (
            getattr(validation, "safe_probe", False)
            or getattr(validation, "read_only", False)
        )
        and method
        and method != "manual"
        and endpoint
    )


def _clean_selector(value: Any) -> str | None:
    if value is None:
        return None
    clean = str(value).strip().lower()
    return clean or None


def _first_mapping_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        selected = _clean_selector(mapping.get(key))
        if selected:
            return selected
    return None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        clean = value.strip()
        return [clean] if clean else []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _workspace_flow_from_dashboard(
    dashboard_payload: dict[str, Any],
    flow_id: str,
) -> dict[str, Any] | None:
    for flow in dashboard_payload.get("flows") or []:
        if isinstance(flow, dict) and str(flow.get("flow_id") or "") == flow_id:
            return flow
    return None


def _saved_flow_env(flow: dict[str, Any] | None) -> dict[str, Any]:
    if not flow:
        return {}
    env = dict(_mapping(flow.get("env")))
    flow_yaml = flow.get("yaml")
    if isinstance(flow_yaml, str) and flow_yaml.strip():
        with suppress(FlowValidationError):
            env.update(parse_flow_text(flow_yaml).env)
    return env


def _connector_from_flow_tags(flow: dict[str, Any] | None) -> str | None:
    if not flow:
        return None
    return _connector_from_tags(flow.get("tags") or [])


def _connector_from_tags(tags: Any) -> str | None:
    for tag in tags or []:
        connector_id = _clean_selector(tag)
        if connector_id and connector_by_id(connector_id):
            return connector_id
        if connector_id:
            for separator in (":", "="):
                if separator not in connector_id:
                    continue
                key, value = connector_id.split(separator, 1)
                if key.strip() in CONNECTOR_ENV_KEYS and connector_by_id(value.strip()):
                    return value.strip()
    return None


def _has_connector_tag(tags: Any) -> bool:
    for tag in tags or []:
        value = _clean_selector(tag)
        if value and (value in CONNECTOR_TAGS or connector_by_id(value)):
            return True
    return False


def _known_connector_from_mapping(mapping: dict[str, Any]) -> str | None:
    selected = _first_mapping_value(mapping, CONNECTOR_ENV_KEYS)
    if selected and connector_by_id(selected):
        return selected
    return None


def _iter_flow_commands(commands: list[Any]) -> list[Any]:
    all_commands: list[Any] = []
    pending = list(commands)
    while pending:
        command = pending.pop(0)
        all_commands.append(command)
        inline_commands = _mapping(getattr(command, "args", {})).get("commands")
        if inline_commands is None:
            continue
        with suppress(FlowValidationError):
            pending.extend(
                parse_inline_commands(
                    inline_commands,
                    source=f"{getattr(command, 'name', 'command')}.commands",
                )
            )
    return all_commands


def _flow_has_connector_backed_command(flow: Any) -> bool:
    return any(
        getattr(command, "name", "") in CONNECTOR_BACKED_COMMANDS
        for command in _iter_flow_commands(flow.all_commands())
    )


def _connector_from_flow_commands(flow: Any) -> str | None:
    for command in _iter_flow_commands(flow.all_commands()):
        selected = _known_connector_from_mapping(_mapping(getattr(command, "args", {})))
        if selected:
            return selected
    return None


def _raw_flow_readiness_selection(
    flow: Any,
    *,
    env_overrides: dict[str, str],
) -> dict[str, Any]:
    effective_env = {**_mapping(getattr(flow, "env", {})), **env_overrides}
    connector_id = (
        _first_mapping_value(effective_env, CONNECTOR_ENV_KEYS)
        or _connector_from_tags(getattr(flow, "tags", []))
        or _connector_from_flow_commands(flow)
    )
    connector_backed = bool(
        connector_id
        or _has_connector_tag(getattr(flow, "tags", []))
        or _flow_has_connector_backed_command(flow)
    )
    return {
        "connector_backed": connector_backed,
        "connector_id": connector_id,
        "environment": _first_mapping_value(effective_env, ENVIRONMENT_ENV_KEYS),
        "required_capabilities": _required_capabilities_from_sources(
            metadata={},
            env=effective_env,
        ),
    }


def _raw_mcp_connector_setup_block(
    flow: Any,
    *,
    env_overrides: dict[str, str],
    workspace_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    readiness_selection = _raw_flow_readiness_selection(
        flow,
        env_overrides=env_overrides,
    )
    if not readiness_selection["connector_backed"]:
        return None, readiness_selection

    for capability in readiness_selection["required_capabilities"]:
        blocked = public_capability_gate(capability)
        if blocked is not None:
            return blocked, readiness_selection

    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return _public_workspace_required_payload(), readiness_selection

    settings = load_settings()
    if not settings.supabase_configured:
        return _connector_setup_block_payload(), readiness_selection

    dashboard_payload = _product_store(settings).public_dashboard(workspace_id)
    if not readiness_selection["connector_id"] or not workspace_connector_ready(
        dashboard_payload,
        connector_id=readiness_selection["connector_id"],
        environment=readiness_selection["environment"],
        required_capabilities=readiness_selection["required_capabilities"],
    ):
        return _connector_setup_block_payload(), readiness_selection
    return None, readiness_selection


def _required_capabilities_from_sources(
    *,
    metadata: dict[str, Any],
    env: dict[str, Any],
) -> list[str]:
    for source in (env, metadata):
        for key in CAPABILITY_KEYS:
            capabilities = _string_list(source.get(key))
            if capabilities:
                return capabilities
    return []


def _workspace_flow_readiness_selection(
    dashboard_payload: dict[str, Any],
    *,
    flow_id: str,
    env_overrides: dict[str, str],
) -> dict[str, Any]:
    flow = _workspace_flow_from_dashboard(dashboard_payload, flow_id)
    metadata = _mapping((flow or {}).get("metadata"))
    metadata_env = _mapping(metadata.get("env"))
    flow_env = _saved_flow_env(flow)
    effective_env = {**metadata_env, **flow_env, **env_overrides}
    return {
        "connector_id": (
            _first_mapping_value(effective_env, CONNECTOR_ENV_KEYS)
            or _first_mapping_value(metadata, CONNECTOR_ENV_KEYS)
            or _connector_from_flow_tags(flow)
        ),
        "environment": (
            _first_mapping_value(effective_env, ENVIRONMENT_ENV_KEYS)
            or _first_mapping_value(metadata, ENVIRONMENT_ENV_KEYS)
        ),
        "required_capabilities": _required_capabilities_from_sources(
            metadata=metadata,
            env=effective_env,
        ),
    }


def _workspace_connector_ready(
    dashboard_payload: dict[str, Any],
    *,
    connector_id: str | None = None,
    environment: str | None = None,
    required_capabilities: list[str] | None = None,
) -> bool:
    profiles = dashboard_payload.get("connector_profiles")
    if not isinstance(profiles, list) or not profiles:
        return False
    selected_connector = _clean_selector(connector_id)
    selected_environment = _clean_selector(environment)
    if not selected_connector or not selected_environment:
        return False
    manifest = connector_by_id(selected_connector)
    if not manifest:
        return False
    if _clean_selector(manifest.status) != "available":
        return False
    manifest_environments = {
        str(item).strip().lower()
        for item in manifest.environments
        if str(item).strip()
    }
    if selected_environment not in manifest_environments:
        return False
    if not _manifest_has_connection_healthcheck_adapter(manifest):
        return False
    manifest_capabilities = {
        str(capability).strip()
        for capability in manifest.capabilities
        if str(capability).strip()
    }
    if not manifest_capabilities:
        return False
    required = {str(capability).strip() for capability in required_capabilities or [] if str(capability).strip()}
    if required and not required.issubset(manifest_capabilities):
        return False
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        profile_connector = _clean_selector(profile.get("connector_id"))
        profile_environment = _clean_selector(profile.get("environment"))
        if selected_connector and profile_connector != selected_connector:
            continue
        if selected_environment and profile_environment != selected_environment:
            continue
        if _clean_selector(profile.get("status")) not in {"connected", "connected_read_only"}:
            continue
        metadata = profile.get("metadata") or {}
        setup_state = _clean_selector(metadata.get("setup_state")) if isinstance(metadata, dict) else None
        if setup_state != "ready":
            continue
        enabled_capabilities = {str(item).strip() for item in _profile_enabled_capabilities(profile)}
        if not enabled_capabilities:
            continue
        if not enabled_capabilities.issubset(manifest_capabilities):
            continue
        if required and not required.issubset(enabled_capabilities):
            continue
        return True
    return False


def workspace_connector_ready(
    dashboard_payload: dict[str, Any],
    *,
    connector_id: str | None = None,
    environment: str | None = None,
    required_capabilities: list[str] | None = None,
) -> bool:
    return _workspace_connector_ready(
        dashboard_payload,
        connector_id=connector_id,
        environment=environment,
        required_capabilities=required_capabilities,
    )


def _active_workspace_connector_profile(dashboard_payload: dict[str, Any]) -> dict[str, Any] | None:
    profiles = dashboard_payload.get("connector_profiles")
    if not isinstance(profiles, list):
        return None
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        connector_id = _clean_selector(profile.get("connector_id"))
        environment = _clean_selector(profile.get("environment"))
        if not connector_id or not environment:
            continue
        if _workspace_connector_ready(
            {"connector_profiles": [profile]},
            connector_id=connector_id,
            environment=environment,
        ):
            return profile
    return None


def _connector_context_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    metadata = _mapping(profile.get("metadata"))
    context = {
        "connector_id": str(profile.get("connector_id") or "").strip().lower(),
        "environment": str(profile.get("environment") or "").strip().lower(),
        "status": str(profile.get("status") or metadata.get("setup_state") or "ready"),
        "enabled_capabilities": sorted(
            str(item).strip() for item in _profile_enabled_capabilities(profile)
        ),
    }
    setup_state = metadata.get("setup_state")
    if setup_state:
        context["setup_state"] = str(setup_state)
    return context


def _workspace_context_setup_required_payload() -> dict[str, Any]:
    return {
        "status": "requires_setup",
        "message": (
            "connector credential setup is required before retrieving workspace-specific "
            "accounting context."
        ),
        "next_tool": "start_connector_setup",
        "next_skill": "connector-credential-setup-th",
    }


def _public_workspace_required_payload() -> dict[str, Any]:
    return {
        "status": "requires_workspace",
        "message": "Create a Mercury public workspace before using workspace features.",
        "next_tool": "create_public_workspace",
    }


def _connector_setup_block_payload() -> dict[str, Any]:
    return {
        "status": "blocked",
        "message": (
            "connector credential setup is required before running connector-backed "
            "accounting workflows."
        ),
        "next_tool": "start_connector_setup",
        "next_skill": "connector-credential-setup-th",
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
    applied_filters, inferred_connector, inferred_domain = apply_knowledge_routing(
        query, filters
    )
    results = _service().search(
        query,
        filters=_filters(applied_filters),
        top_k=top_k,
        mode=mode,
    )
    payload = {
        "status": "ok" if results else "no_relevant_knowledge",
        "query": query,
        "applied_filters": applied_filters,
        "inferred_connector": inferred_connector,
        "inferred_domain": inferred_domain,
        "minimum_score": MIN_RELEVANCE_SCORE,
        "results": _serialize_search_results(results),
    }
    payload = redact_json(payload)
    _audit(
        "search_knowledge",
        {"query": query, "filters": applied_filters, "top_k": top_k},
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
    applied_filters, inferred_connector, inferred_domain = apply_knowledge_routing(
        query, filters
    )
    pack = _service().context_pack(
        query,
        task=task,
        filters=_filters(applied_filters),
        max_chunks=max_chunks,
    )
    payload = pack.as_dict()
    payload.update(
        {
            "status": "ok" if pack.results else "no_relevant_knowledge",
            "applied_filters": applied_filters,
            "inferred_connector": inferred_connector,
            "inferred_domain": inferred_domain,
            "minimum_score": MIN_RELEVANCE_SCORE,
        }
    )
    payload = redact_json(payload)
    _audit(
        "retrieve_context_pack",
        {"query": query, "task": task, "filters": applied_filters},
        {"count": len(pack.results)},
    )
    return payload


@mcp.tool()
def retrieve_workspace_context_pack(
    workspace_id: str,
    query: str,
    task: str | None = None,
    max_chunks: int = 12,
) -> dict[str, Any]:
    """Return a cited context pack filtered to the workspace's ready connector."""
    audit_input = {
        **_public_workspace_audit_ref(workspace_id),
        "query": query,
        "task": task,
        "max_chunks": max_chunks,
    }
    try:
        settings = load_settings()
        dashboard_payload = _product_store(settings).public_dashboard(workspace_id)
        profile = _active_workspace_connector_profile(dashboard_payload)
        if profile is None:
            payload = redact_json(_workspace_context_setup_required_payload())
            _audit(
                "retrieve_workspace_context_pack",
                audit_input,
                {"status": "requires_setup"},
            )
            return payload
        connector_context = _connector_context_from_profile(profile)
        connector_id = connector_context["connector_id"]
        domain = infer_knowledge_domain(query)
        service = _service()
        retrieval_scopes = [f"connector:{connector_id}"]
        if domain in {"accounting_standard", "tax"}:
            connector_limit = max(1, max_chunks // 2)
            knowledge_limit = max(1, max_chunks - connector_limit)
            connector_pack = service.context_pack(
                query,
                task=task,
                filters=_filters(
                    {
                        "connector": connector_id,
                        "review_status": "reviewed",
                    }
                ),
                max_chunks=connector_limit,
            )
            knowledge_pack = service.context_pack(
                query,
                task=task,
                filters=_filters(
                    {
                        "jurisdiction": "TH",
                        "doc_type": domain,
                        "review_status": "reviewed",
                    }
                ),
                max_chunks=knowledge_limit,
            )
            pack = ContextPack(
                query=query,
                task=task,
                results=_merge_search_results(
                    connector_pack.results,
                    knowledge_pack.results,
                    max_chunks=max_chunks,
                ),
            )
            retrieval_scopes.append(f"{domain}:TH")
        else:
            pack = service.context_pack(
                query,
                task=task,
                filters=_filters(
                    {
                        "connector": connector_id,
                        "review_status": "reviewed",
                    }
                ),
                max_chunks=max_chunks,
            )
        payload = pack.as_dict()
        payload.update(
            {
                "status": "ok" if pack.results else "no_relevant_knowledge",
                "connector_context": connector_context,
                "inferred_domain": domain,
                "minimum_score": MIN_RELEVANCE_SCORE,
                "retrieval_scopes": retrieval_scopes,
            }
        )
        payload = redact_json(payload)
        _audit(
            "retrieve_workspace_context_pack",
            audit_input,
            {
                "status": "ok",
                "connector_id": connector_id,
                "environment": connector_context["environment"],
                "count": len(pack.results),
            },
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit("retrieve_workspace_context_pack", audit_input, payload)
        return payload


@mcp.tool()
def get_document(document_id: str) -> dict[str, Any]:
    """Fetch one indexed knowledge document by UUID or document URI."""
    document = SupabaseRagStore(load_settings()).get_document(document_id)
    payload = redact_json({"status": "ok" if document else "not_found", "document": document})
    _audit("get_document", {"document_id": document_id}, {"found": bool(document)})
    return payload


@mcp.tool()
def create_public_workspace(company_name: str | None = None) -> dict[str, Any]:
    """Create an opaque public Mercury workspace for the contest experience."""
    try:
        settings = load_settings()
        if not settings.supabase_configured:
            raise RuntimeError("Supabase is required to create a public workspace.")
        payload = redact_json(
            _product_store(settings).create_public_workspace(company_name)
        )
        _audit(
            "create_public_workspace",
            {"company_name_present": bool((company_name or "").strip())},
            {
                "status": payload.get("status"),
                **_public_workspace_audit_ref(str(payload["workspace_id"])),
            },
        )
        return payload
    except (RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit("create_public_workspace", {}, payload)
        return payload


@mcp.tool()
def get_public_workspace(workspace_id: str) -> dict[str, Any]:
    """Return sanitized Mercury workspace, connector, and flow state."""
    audit_input = _public_workspace_audit_ref(workspace_id)
    try:
        settings = load_settings()
        if not settings.supabase_configured:
            raise RuntimeError("Supabase is required to load a public workspace.")
        payload = redact_json(
            _product_store(settings).public_dashboard(workspace_id)
        )
        _audit(
            "get_public_workspace",
            audit_input,
            {
                "status": payload.get("status"),
                "profile_count": len(payload.get("connector_profiles") or []),
                "flow_count": len(payload.get("flows") or []),
            },
        )
        return payload
    except (RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit("get_public_workspace", audit_input, payload)
        return payload


@mcp.tool()
def list_connectors() -> dict[str, Any]:
    """List Mercury accounting and ERP connector options without secrets."""
    payload = {"status": "ok", "connectors": list_connector_public_summaries()}
    _audit("list_connectors", {}, {"count": len(payload["connectors"])})
    return payload


@mcp.tool()
def connector_capabilities(connector_id: str) -> dict[str, Any]:
    """List declared capabilities for one Mercury connector."""
    manifest = connector_by_id(connector_id)
    if manifest is None:
        payload = {"status": "not_found", "connector_id": connector_id}
        _audit("connector_capabilities", {"connector_id": connector_id}, payload)
        return payload
    payload = {
        "status": "ok",
        "connector_id": manifest.connector_id,
        "capabilities": list(manifest.capabilities),
        "read_capabilities": manifest.read_capabilities,
        "blocked_capabilities": manifest.blocked_capabilities,
        "public_policy": "read_only_validation",
    }
    _audit(
        "connector_capabilities",
        {"connector_id": manifest.connector_id},
        {"status": "ok", "capability_count": len(manifest.capabilities)},
    )
    return payload


@mcp.tool()
def start_connector_setup(
    workspace_id: str,
    connector_id: str,
    environment: str,
    company_name: str | None = None,
) -> dict[str, Any]:
    """Start gated connector setup for one workspace."""
    try:
        settings = load_settings()
        token_payload = _public_workspace_payload_from_value(workspace_id)
        profile = _product_store(settings).start_connector_setup(
            token_payload=token_payload,
            connector_id=connector_id,
            environment=environment,
            company_name=company_name,
        )
        payload = redact_json({"status": "ok", "profile": profile})
        _audit(
            "start_connector_setup",
            {**_public_workspace_audit_ref(workspace_id), "connector_id": connector_id},
            {
                "status": "ok",
                "connector_id": connector_id,
                "environment": environment,
            },
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "start_connector_setup",
            {
                **_public_workspace_audit_ref_optional(workspace_id),
                "connector_id": connector_id,
            },
            payload,
        )
        return payload


@mcp.tool()
def connector_status(workspace_id: str | None = None) -> dict[str, Any]:
    """Read sanitized connector status for a Mercury public workspace."""
    if not workspace_id:
        payload = {
            **_public_workspace_required_payload(),
            "connectors": list_connector_public_summaries(),
        }
        _audit("connector_status", {}, {"status": payload["status"]})
        return payload

    audit_input = _public_workspace_audit_ref_optional(workspace_id)
    try:
        settings = load_settings()
        dashboard_payload = _product_store(settings).public_dashboard(workspace_id)
        public_profiles = public_connector_profiles(
            dashboard_payload.get("connector_profiles") or []
        )
        active_profile = _active_workspace_connector_profile(dashboard_payload)
        active_context = (
            _connector_context_from_profile(active_profile)
            if active_profile is not None
            else None
        )
        payload: dict[str, Any] = {
            "status": "ok" if active_context else "requires_setup",
            "workspace": {
                "name": (dashboard_payload.get("workspace") or {}).get("name"),
                "host_app": "generic",
            },
            "connector_profiles": public_profiles,
            "active_connector": active_context,
            "setup_required": active_context is None,
            "next_tool": "start_connector_setup" if active_context is None else None,
            "next_skill": (
                "connector-credential-setup-th" if active_context is None else None
            ),
        }
        payload = redact_json(payload)
        _audit(
            "connector_status",
            audit_input,
            {
                "status": payload["status"],
                "profile_count": len(public_profiles),
                "active_connector": (
                    active_context.get("connector_id") if active_context else None
                ),
            },
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit("connector_status", audit_input, payload)
        return payload


@mcp.tool()
def run_accounting_skill(
    skill_id: str,
    inputs: dict[str, Any],
    evidence_mode: bool = False,
) -> dict[str, Any]:
    """Return an accounting skill execution package for the host agent."""
    markdown = skill_markdown(skill_id)
    payload = redact_json(
        {
            "status": "ok" if markdown else "not_found",
            "skill_id": skill_id,
            "inputs": inputs,
            "evidence_mode": evidence_mode,
            "skill_markdown": markdown,
            "note": (
                "v1 returns a guided skill package. Endpoint actions are gated by "
                "connector capability, workflow preview, user approval, and audit policy."
            ),
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
def inspect_flow_files(
    flow_files: dict[str, str] | list[dict[str, Any]],
    config_yaml: str | None = None,
    include_tags: list[str] | str | None = None,
    exclude_tags: list[str] | str | None = None,
) -> dict[str, Any]:
    """Inspect an in-memory Mercury flow workspace for an MCP host agent."""
    normalized_files: list[dict[str, str]] = []
    try:
        normalized_files = _flow_files_from_payload(flow_files)
        config_yaml = _validate_config_yaml(config_yaml)
        include = _string_list_from_payload(include_tags, label="include_tags")
        exclude = _string_list_from_payload(exclude_tags, label="exclude_tags")

        with TemporaryDirectory(prefix="mercury-flow-inspect-") as temp_dir:
            root = Path(temp_dir).resolve()
            if config_yaml:
                (root / "config.yaml").write_text(config_yaml, encoding="utf-8")
            for item in normalized_files:
                target = root / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["flow_yaml"], encoding="utf-8")

            workspace = discover_workspace_flows(
                root,
                include_tags=include,
                exclude_tags=exclude,
            )
            payload = redact_json(workspace_manifest(workspace, source="in-memory"))

        payload["input"] = {
            "flow_count": len(normalized_files),
            "config_yaml_present": bool(config_yaml),
            "include_tags": include,
            "exclude_tags": exclude,
        }
        _audit(
            "inspect_flow_files",
            {
                "flow_count": len(normalized_files),
                "config_yaml_present": bool(config_yaml),
                "include_tags": include,
                "exclude_tags": exclude,
                "total_flow_yaml_length": sum(len(item["flow_yaml"]) for item in normalized_files),
            },
            {
                "status": payload["status"],
                "selected_count": payload["discovery"]["selected_count"],
            },
        )
        return payload
    except (FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "inspect_flow_files",
            {
                "flow_count": len(normalized_files),
                "config_yaml_present": bool(config_yaml),
            },
            payload,
        )
        return payload


@mcp.tool()
def run_flow(
    flow_yaml: str,
    dry_run: bool = False,
    env: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Run a Mercury YAML flow or return an execution plan when dry_run is true."""
    try:
        env_overrides = _env_overrides_from_payload(env)
        parsed_flow = parse_flow_text(flow_yaml)
        block_payload, readiness_selection = _raw_mcp_connector_setup_block(
            parsed_flow,
            env_overrides=env_overrides,
            workspace_id=workspace_id,
        )
        if block_payload:
            payload = redact_json(block_payload)
            _audit(
                "run_flow",
                {
                    **_public_workspace_audit_ref_optional(workspace_id),
                    "flow_yaml_length": len(flow_yaml),
                    "dry_run": dry_run,
                    "env_keys": _env_keys(env_overrides),
                    "connector_id": readiness_selection["connector_id"],
                    "environment": readiness_selection["environment"],
                },
                payload,
            )
            return payload
        result = create_default_runner(
            dry_run=dry_run,
            connector_status_getter=lambda: connector_status(workspace_id),
        ).run_flow(
            parsed_flow,
            env=env_overrides,
        )
        payload = redact_json(result.as_dict())
        _audit(
            "run_flow",
            {
                **_public_workspace_audit_ref_optional(workspace_id),
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
    except (PermissionError, FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc), "dry_run": dry_run}
        safe_env_keys = _env_keys(env) if isinstance(env, dict) else []
        _audit(
            "run_flow",
            {
                **_public_workspace_audit_ref_optional(workspace_id),
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
    config_yaml: str | None = None,
    dry_run: bool = False,
    env: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    include_tags: list[str] | str | None = None,
    exclude_tags: list[str] | str | None = None,
    continue_on_failure: bool = True,
) -> dict[str, Any]:
    """Run multiple Mercury YAML flow files as an in-memory suite."""
    normalized_files: list[dict[str, str]] = []
    env_overrides: dict[str, str] = {}
    try:
        normalized_files = _flow_files_from_payload(flow_files)
        config_yaml = _validate_config_yaml(config_yaml)
        env_overrides = _env_overrides_from_payload(env)
        include = set(_string_list_from_payload(include_tags, label="include_tags"))
        exclude = set(_string_list_from_payload(exclude_tags, label="exclude_tags"))
        file_records: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        selected_paths: list[Path] = []

        with TemporaryDirectory(prefix="mercury-flow-files-") as temp_dir:
            root = Path(temp_dir).resolve()
            if config_yaml:
                (root / "config.yaml").write_text(config_yaml, encoding="utf-8")
            for item in normalized_files:
                target = root / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["flow_yaml"], encoding="utf-8")

            runner = create_default_runner(
                dry_run=dry_run,
                connector_status_getter=lambda: connector_status(workspace_id),
            )
            if config_yaml:
                workspace = discover_workspace_flows(
                    root,
                    include_tags=sorted(include),
                    exclude_tags=sorted(exclude),
                )
                file_records = [record.as_dict(root=root) for record in workspace.records]
                selected_paths = [record.path for record in workspace.ordered_selected]
            else:
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
                        if selected:
                            selected_paths.append(target)
                    except (FlowValidationError, RuntimeError, ValueError) as exc:
                        selected = not include
                        file_records.append(
                            {
                                "path": relative_path,
                                "name": None,
                                "tags": [],
                                "command_count": 0,
                                "selected": selected,
                                "status": "invalid"
                                if isinstance(exc, FlowValidationError)
                                else "error",
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

            for selected_path in selected_paths:
                parsed_flow = parse_flow_text(
                    selected_path.read_text(encoding="utf-8"),
                    path=selected_path,
                )
                block_payload, readiness_selection = _raw_mcp_connector_setup_block(
                    parsed_flow,
                    env_overrides=env_overrides,
                    workspace_id=workspace_id,
                )
                if block_payload:
                    payload = redact_json(
                        {
                            **block_payload,
                            "dry_run": dry_run,
                            "config_yaml_present": bool(config_yaml),
                            "flow_count": len(normalized_files),
                            "selected_count": len(selected_paths),
                            "skipped_count": len(
                                [
                                    item
                                    for item in file_records
                                    if not item.get("selected")
                                ]
                            ),
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
                            **_public_workspace_audit_ref_optional(workspace_id),
                            "flow_count": len(normalized_files),
                            "selected_count": len(selected_paths),
                            "dry_run": dry_run,
                            "config_yaml_present": bool(config_yaml),
                            "env_keys": _env_keys(env_overrides),
                            "connector_id": readiness_selection["connector_id"],
                            "environment": readiness_selection["environment"],
                        },
                        {
                            "status": payload["status"],
                            "result_count": len(payload["results"]),
                            "dry_run": dry_run,
                        },
                    )
                    return payload

            for selected_path in selected_paths:
                relative_path = selected_path.relative_to(root).as_posix()
                try:
                    result = runner.run_path(selected_path, env=env_overrides).as_dict()
                    results.append(_relativize_temp_paths(result, root=root))
                except (FlowValidationError, RuntimeError, ValueError) as exc:
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
                "config_yaml_present": bool(config_yaml),
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
                **_public_workspace_audit_ref_optional(workspace_id),
                "flow_count": len(normalized_files),
                "selected_count": payload["selected_count"],
                "dry_run": dry_run,
                "config_yaml_present": bool(config_yaml),
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
    except (PermissionError, FlowValidationError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc), "dry_run": dry_run}
        _audit(
            "run_flow_files",
            {
                **_public_workspace_audit_ref_optional(workspace_id),
                "flow_count": len(normalized_files),
                "dry_run": dry_run,
                "config_yaml_present": bool(config_yaml),
                "env_keys": _env_keys(env_overrides),
            },
            payload,
        )
        return payload


@mcp.tool()
def run_mercury_flow(
    flow_yaml: str | None = None,
    flow_files: dict[str, str] | list[dict[str, Any]] | None = None,
    workspace_flow_id: str | None = None,
    workspace_id: str | None = None,
    config_yaml: str | None = None,
    dry_run: bool = True,
    env: dict[str, Any] | None = None,
    include_tags: list[str] | str | None = None,
    exclude_tags: list[str] | str | None = None,
    continue_on_failure: bool = True,
) -> dict[str, Any]:
    """Run Mercury flows through one Maestro-style MCP entrypoint."""
    modes = _selected_flow_input_modes(
        flow_yaml=flow_yaml,
        flow_files=flow_files,
        workspace_flow_id=workspace_flow_id,
    )
    if len(modes) != 1:
        payload = {
            "status": "error",
            "message": (
                "Pass exactly one of flow_yaml, flow_files, or workspace_flow_id."
            ),
            "selected_modes": modes,
        }
        _audit("run_mercury_flow", {"selected_modes": modes, "dry_run": dry_run}, payload)
        return payload

    if modes[0] == "flow_yaml":
        if config_yaml is not None or include_tags is not None or exclude_tags is not None:
            payload = {
                "status": "error",
                "message": (
                    "config_yaml, include_tags, and exclude_tags are valid only "
                    "with flow_files."
                ),
                "selected_modes": modes,
            }
            _audit("run_mercury_flow", {"selected_modes": modes, "dry_run": dry_run}, payload)
            return payload
        payload = run_flow(
            flow_yaml or "",
            dry_run=dry_run,
            env=env,
            workspace_id=workspace_id,
        )
        payload["entrypoint"] = "run_mercury_flow"
        payload["input_mode"] = "flow_yaml"
        return payload

    if modes[0] == "flow_files":
        payload = run_flow_files(
            flow_files or {},
            config_yaml=config_yaml,
            dry_run=dry_run,
            env=env,
            workspace_id=workspace_id,
            include_tags=include_tags,
            exclude_tags=exclude_tags,
            continue_on_failure=continue_on_failure,
        )
        payload["entrypoint"] = "run_mercury_flow"
        payload["input_mode"] = "flow_files"
        return payload

    if not workspace_id:
        payload = {
            **_public_workspace_required_payload(),
            "message": "workspace_id is required with workspace_flow_id.",
            "selected_modes": modes,
        }
        _audit("run_mercury_flow", {"selected_modes": modes, "dry_run": dry_run}, payload)
        return payload
    payload = run_workspace_flow_tool(
        workspace_id=workspace_id,
        flow_id=workspace_flow_id or "",
        dry_run=dry_run,
        env=env,
    )
    payload["entrypoint"] = "run_mercury_flow"
    payload["input_mode"] = "workspace_flow_id"
    return payload


@mcp.tool()
def list_workspace_flows(workspace_id: str) -> dict[str, Any]:
    """List saved Mercury flows for a public workspace."""
    try:
        settings = load_settings()
        if not settings.supabase_configured:
            raise RuntimeError("Supabase is required to list saved workspace flows.")
        dashboard_payload = _product_store(settings).public_dashboard(workspace_id)
        flows = [_public_flow_summary(flow) for flow in dashboard_payload.get("flows", [])]
        payload = redact_json(
            {
                "status": "ok",
                "workspace": dashboard_payload.get("workspace") or {},
                "flow_count": len(flows),
                "flows": flows,
            }
        )
        _audit(
            "list_workspace_flows",
            _public_workspace_audit_ref(workspace_id),
            {"flow_count": len(flows)},
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "list_workspace_flows",
            _public_workspace_audit_ref_optional(workspace_id),
            payload,
        )
        return payload


@mcp.tool(name="run_workspace_flow")
def run_workspace_flow_tool(
    workspace_id: str,
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
        token_payload = _public_workspace_payload_from_value(workspace_id)
        store = _product_store(settings)
        dashboard_payload = store.public_dashboard(workspace_id)
        readiness_selection = _workspace_flow_readiness_selection(
            dashboard_payload,
            flow_id=flow_id,
            env_overrides=env_overrides,
        )
        for capability in readiness_selection["required_capabilities"]:
            blocked = public_capability_gate(capability)
            if blocked is not None:
                _audit(
                    "run_workspace_flow",
                    {
                        **_public_workspace_audit_ref(workspace_id),
                        "flow_id": flow_id,
                        "dry_run": dry_run,
                        "capability": capability,
                    },
                    blocked,
                )
                return blocked
        if not readiness_selection["connector_id"] or not workspace_connector_ready(
            dashboard_payload,
            connector_id=readiness_selection["connector_id"],
            environment=readiness_selection["environment"],
            required_capabilities=readiness_selection["required_capabilities"],
        ):
            payload = _connector_setup_block_payload()
            _audit(
                "run_workspace_flow",
                {
                    **_public_workspace_audit_ref(workspace_id),
                    "flow_id": flow_id,
                    "dry_run": dry_run,
                    "env_keys": _env_keys(env_overrides),
                    "connector_id": readiness_selection["connector_id"],
                    "environment": readiness_selection["environment"],
                },
                payload,
            )
            return payload
        flow = store.get_flow(token_payload=token_payload, flow_id=flow_id)
        if not flow:
            return {"status": "not_found", "message": f"Workspace flow not found: {flow_id}"}
        result = create_default_runner(
            dry_run=dry_run,
            connector_status_getter=lambda: connector_status(workspace_id),
        ).run_text(
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
                **_public_workspace_audit_ref(workspace_id),
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
                **_public_workspace_audit_ref_optional(workspace_id),
                "flow_id": flow_id,
                "dry_run": dry_run,
                "env_keys": safe_env_keys,
            },
            payload,
        )
        return payload


@mcp.tool(name="save_workspace_flow")
def save_workspace_flow_tool(
    workspace_id: str,
    title: str,
    flow_yaml: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save one Mercury flow into the connected workspace."""
    try:
        settings = load_settings()
        if not settings.supabase_configured:
            raise RuntimeError("Supabase is required to save workspace flows.")
        token_payload = _public_workspace_payload_from_value(workspace_id)
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
                **_public_workspace_audit_ref(workspace_id),
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
                **_public_workspace_audit_ref_optional(workspace_id),
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
    settings = load_settings()
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Mercury Tools MCP</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #111923;
      color: #f5f8fb;
      font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(720px, calc(100vw - 32px));
      border: 1px solid #314455;
      border-radius: 8px;
      background: #182331;
      padding: 22px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; color: #42c6bb; }}
    p {{ margin: 0 0 14px; color: #c6d0d9; }}
    code {{
      display: block;
      overflow-wrap: anywhere;
      border: 1px solid #314455;
      border-radius: 8px;
      background: #0b1119;
      color: #f5bf45;
      padding: 12px;
    }}
    .muted {{ color: #93a1ad; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <h1>Mercury Tools MCP Server</h1>
    <p>Mercury is an MCP/plugin tool layer for accounting agents. This service is not a web app and does not provide a browser setup workflow.</p>
    <p>MCP endpoint:</p>
    <code>{settings.mcp_endpoint}</code>
    <p class="muted">Use a host AI client such as Codex, Cursor, Claude, or another MCP client to connect and run Mercury tools.</p>
  </main>
</body>
</html>"""
    return HTMLResponse(body)


async def status(_: Request) -> Response:
    settings = load_settings()
    deployment_commit = os.environ.get("MERCURY_DEPLOYMENT_COMMIT", "")
    if len(deployment_commit) != 40 or not set(deployment_commit) <= _LOWER_HEX:
        deployment_commit = None
    payload = {
        "name": "Mercury Tools MCP",
        "version": __version__,
        "deployment_commit": deployment_commit,
        "status": "ok",
        "supabase": settings.supabase_configured,
        "openai": settings.openai_configured,
        "embedding_provider": settings.embedding_provider,
        "embedding_configured": settings.embedding_configured,
        "transport": "streamable-http",
        "mcp_path": settings.mcp_path,
        "mcp_endpoint": settings.mcp_endpoint,
        "health": "/healthz",
        "surface": "mcp-plugin-first",
        "browser_ui": "disabled",
        "legacy_http_api": (
            "enabled" if settings.enable_legacy_http_api else "disabled"
        ),
        "note": "Mercury is not a web app. Host AI clients call the MCP endpoint.",
        "flow_tools": [
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
        "http_auth_configured": settings.http_auth_configured,
    }
    if settings.enable_legacy_http_api:
        payload.update(
            {
                "connect": "/api/connect",
                "dashboard": "/api/dashboard",
                "connector_setup": "/api/connectors/setup",
                "team_invite": "/api/team/invite",
                "skill_enable": "/api/skills/enable",
                "skill_upload": "/api/skills/upload",
                "flow_validate": "/api/flows/validate",
                "flow_save": "/api/flows/save",
                "flow_import": "/api/flows/import",
                "flow_run": "/api/flows/run",
                "invite_required": bool(settings.connect_invite_code),
            }
        )
    return JSONResponse(payload)


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
        store: SupabaseProductStore | None = None
        parsed_raw_flow = None
        if flow_id and not flow_yaml:
            if not settings.supabase_configured:
                return _json_error(
                    "service_unavailable",
                    "Supabase is required to load saved workspace flows.",
                    status_code=503,
                )
            store = _product_store(settings)
            dashboard_payload = store.dashboard(token_payload)
            readiness_selection = _workspace_flow_readiness_selection(
                dashboard_payload,
                flow_id=flow_id,
                env_overrides=env_overrides,
            )
            if not readiness_selection["connector_id"] or not workspace_connector_ready(
                dashboard_payload,
                connector_id=readiness_selection["connector_id"],
                environment=readiness_selection["environment"],
                required_capabilities=readiness_selection["required_capabilities"],
            ):
                return JSONResponse(redact_json(_connector_setup_block_payload()))
            flow = store.get_flow(token_payload=token_payload, flow_id=flow_id)
            if not flow:
                return _json_error("not_found", f"Workspace flow not found: {flow_id}", status_code=404)
            flow_yaml = str(flow.get("yaml") or "")
            flow_title = str(flow.get("title") or flow.get("name") or flow_title)
        elif flow_yaml:
            parsed_raw_flow = parse_flow_text(flow_yaml)
            readiness_selection = _raw_flow_readiness_selection(
                parsed_raw_flow,
                env_overrides=env_overrides,
            )
            if readiness_selection["connector_backed"]:
                if not settings.supabase_configured:
                    return JSONResponse(redact_json(_connector_setup_block_payload()))
                store = store or _product_store(settings)
                dashboard_payload = store.dashboard(token_payload)
                if not readiness_selection["connector_id"] or not workspace_connector_ready(
                    dashboard_payload,
                    connector_id=readiness_selection["connector_id"],
                    environment=readiness_selection["environment"],
                    required_capabilities=readiness_selection["required_capabilities"],
                ):
                    return JSONResponse(redact_json(_connector_setup_block_payload()))
        runner = create_default_runner(dry_run=dry_run)
        result = (
            runner.run_flow(parsed_raw_flow, env=env_overrides)
            if parsed_raw_flow is not None
            else runner.run_text(flow_yaml, env=env_overrides)
        )
        payload = redact_json(result.as_dict())
        if flow:
            payload["workspace_flow"] = _public_flow_summary(flow)
        if settings.supabase_configured:
            try:
                payload["run_record"] = (store or _product_store(settings)).record_flow_run(
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
        return JSONResponse(
            redact_json({"status": "ok", "profile": public_connector_profile(profile)})
        )
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


async def healthz(request: Request) -> Response:
    settings = load_settings()
    http_auth_required = getattr(
        request.app.state,
        "mercury_http_require_auth",
        settings.http_require_auth,
    )
    return JSONResponse(
        {
            "status": "ok",
            "supabase": settings.supabase_configured,
            "openai": settings.openai_configured,
            "embedding_provider": settings.embedding_provider,
            "embedding_configured": settings.embedding_configured,
            "mcp_path": settings.mcp_path,
            "http_auth_required": http_auth_required,
            "http_auth_configured": settings.http_auth_configured,
            "legacy_http_api": (
                "enabled" if settings.enable_legacy_http_api else "disabled"
            ),
        }
    )


def create_http_app(
    *,
    require_auth: bool | None = None,
    cloud_dependencies: CloudDependencies | None = None,
):
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
    public_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app):
        async with public_app.router.lifespan_context(public_app):
            yield

    routes = [
        *public_app.routes,
        *cloud_routes(cloud_dependencies or CloudDependencies(settings=settings)),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_route("/", root, methods=["GET"])
    app.add_route("/api/status", status, methods=["GET"])
    app.add_route("/healthz", healthz, methods=["GET"])
    if settings.enable_legacy_http_api:
        for page_path in (
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
        app.add_route("/api/connect", connect, methods=["POST"])
        app.add_route("/api/dashboard", dashboard, methods=["GET"])
        app.add_route("/api/connectors/setup", setup_connector, methods=["POST"])
        app.add_route("/api/team/invite", invite_member, methods=["POST"])
        app.add_route("/api/skills/enable", enable_skill, methods=["POST"])
        app.add_route("/api/skills/upload", upload_skill, methods=["POST"])
        app.add_route("/api/flows/validate", validate_workspace_flow, methods=["POST"])
        app.add_route("/api/flows/save", save_workspace_flow, methods=["POST"])
        app.add_route("/api/flows/import", import_workspace_flows, methods=["POST"])
        app.add_route("/api/flows/run", run_workspace_flow, methods=["POST"])

    should_require_auth = settings.http_require_auth if require_auth is None else require_auth
    app.state.mercury_http_require_auth = should_require_auth
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
