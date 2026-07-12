"""Ordinary-user, read-only HTTP surface for Mercury Cloud Brain."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mercury_tools.catalog.identity import build_version_id, sanitize_document
from mercury_tools.catalog.models import (
    CatalogAction,
    HttpMethod,
    revalidate_catalog_action,
)
from mercury_tools.config import Settings, load_settings
from mercury_tools.db.catalog import SupabaseCatalogStore
from mercury_tools.db.product import SKILL_CATALOG_SEED
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.mercury_runtime import skill_markdown
from mercury_tools.rag.models import SearchFilters, SearchResult
from mercury_tools.safety.redaction import redact_json, redact_text

_FILTER_FIELDS = {
    "jurisdiction",
    "connector",
    "doc_type",
    "review_status",
    "effective_date",
}
_SELECTOR_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{24}$")
_PUBLIC_DOCUMENT_URI_RE = re.compile(
    r"^mercury://wiki/[A-Za-z0-9][A-Za-z0-9._~/-]{0,480}$"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:(?:/Users|/home|/app|/var|/tmp|/private)/[^\s\"'<>]+|"
    r"[A-Za-z]:\\[^\s\"'<>]+)"
)
_PRIVATE_KEY_RE = re.compile(
    r"(?i)(?:repository|source)?_?path|credential|secret|token|api[_-]?key|authorization"
)
_PUBLIC_SKILL_FIELDS = (
    "skill_id",
    "title",
    "category",
    "summary",
    "status",
    "version",
    "required_connectors",
    "tags",
)
_SEEDED_SKILL_IDS = frozenset(str(item["skill_id"]) for item in SKILL_CATALOG_SEED)


@dataclass
class CloudDependencies:
    settings: Settings | None = None
    catalog_store: Any | None = None
    rag_store: Any | None = None
    skills: Sequence[Mapping[str, Any]] | None = None
    skill_loader: Callable[[str], str | None] = skill_markdown

    def _catalog_store(self) -> Any:
        if self.catalog_store is None:
            self.catalog_store = SupabaseCatalogStore(self.settings or load_settings())
        return self.catalog_store

    def _rag_store(self) -> Any:
        if self.rag_store is None:
            self.rag_store = SupabaseRagStore(self.settings or load_settings())
        return self.rag_store

    def _skills(self) -> Sequence[Mapping[str, Any]]:
        return SKILL_CATALOG_SEED if self.skills is None else self.skills

    async def list_actions(self, request: Request) -> Response:
        query = list(request.query_params.multi_items())
        keys = [key for key, _ in query]
        if (
            any(key not in {"connector", "method"} for key in keys)
            or len(keys) != len(set(keys))
        ):
            return _bad_request()
        connector = request.query_params.get("connector")
        method = request.query_params.get("method")
        if (
            not _valid_selector(connector)
            or (method is not None and method not in {item.value for item in HttpMethod})
        ):
            return _bad_request()
        try:
            all_actions = await run_in_threadpool(self._catalog_actions)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return _service_unavailable()
        actions = _filter_actions(all_actions, connector=connector, method=method)
        payload = [action.model_dump(mode="json") for action in actions]
        etag = _etag(
            [action.model_dump(mode="json") for action in all_actions]
        )
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse({"actions": payload}, headers={"ETag": etag})

    async def get_action(self, request: Request) -> Response:
        action_id = request.path_params["action_id"]
        if not _ACTION_ID_RE.fullmatch(action_id):
            return _bad_request()
        try:
            action = next(
                (
                    item
                    for item in await run_in_threadpool(self._catalog_actions)
                    if item.action_id == action_id
                ),
                None,
            )
        except (httpx.HTTPError, RuntimeError, ValueError):
            return _service_unavailable()
        if action is None:
            return _not_found()
        return JSONResponse({"action": action.model_dump(mode="json")})

    async def list_connectors(self, request: Request) -> Response:
        try:
            actions = await run_in_threadpool(self._catalog_actions)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return _service_unavailable()
        grouped: dict[str, dict[str, set[str]]] = {}
        for action in actions:
            connector = grouped.setdefault(
                action.connector_id,
                {"capabilities": set(), "environments": set()},
            )
            connector["capabilities"].add(action.capability)
            connector["environments"].update(action.environments)
        return JSONResponse(
            {
                "connectors": [
                    {
                        "connector_id": connector_id,
                        "capabilities": sorted(values["capabilities"]),
                        "environments": sorted(values["environments"]),
                    }
                    for connector_id, values in sorted(grouped.items())
                ]
            }
        )

    async def list_skills(self, request: Request) -> Response:
        return JSONResponse({"skills": self._public_skills()})

    async def get_skill(self, request: Request) -> Response:
        skill_id = request.path_params["skill_id"]
        if not _valid_selector(skill_id, required=True):
            return _bad_request()
        skill = next(
            (item for item in self._public_skills() if item["skill_id"] == skill_id),
            None,
        )
        if skill is None:
            return _not_found()
        try:
            markdown = await run_in_threadpool(self.skill_loader, skill_id)
        except OSError:
            return _service_unavailable()
        if markdown is None:
            return _not_found()
        return JSONResponse({**skill, "markdown": sanitize_public_text(markdown)})

    async def search_knowledge(self, request: Request) -> Response:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _bad_request()
        if not isinstance(data, Mapping):
            return _bad_request()
        if set(data) - {"query", "filters", "top_k"}:
            return _bad_request()
        query = data.get("query")
        top_k = data.get("top_k", 8)
        filters = data.get("filters", {})
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 2_000
            or not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or not 1 <= top_k <= 20
            or not _valid_filters(filters)
        ):
            return _bad_request()
        sanitized_query = sanitize_search_query(query)
        public_filters = dict(filters)
        public_filters["review_status"] = "reviewed"
        try:
            results = await run_in_threadpool(
                self._search_knowledge,
                sanitized_query,
                SearchFilters(**public_filters),
                top_k,
            )
        except (httpx.HTTPError, RuntimeError, ValueError):
            return _service_unavailable()
        public_results = [
            _public_search_result(item)
            for item in results
            if _is_public_search_result(item)
        ][:top_k]
        return JSONResponse({"results": public_results})

    async def get_document(self, request: Request) -> Response:
        document_id = request.path_params["document_id"]
        if not _valid_document_identifier(document_id):
            return _bad_request()
        try:
            document = await run_in_threadpool(self._get_document, document_id)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return _service_unavailable()
        if (
            not isinstance(document, Mapping)
            or not _document_identity_matches(document_id, document)
            or not _is_public_document(document)
        ):
            return _not_found()
        return JSONResponse(_public_document(document))

    def _catalog_actions(self) -> list[CatalogAction]:
        rows = self._catalog_store().list_active_actions()
        actions: list[CatalogAction] = []
        for row in rows:
            action = row if isinstance(row, CatalogAction) else CatalogAction.model_validate(row)
            actions.append(_public_catalog_action(action))
        return sorted(actions, key=lambda item: item.action_id)

    def _search_knowledge(
        self,
        query: str,
        filters: SearchFilters,
        top_k: int,
    ) -> list[SearchResult]:
        return self._rag_store().search_knowledge(
            query=query,
            query_embedding=None,
            filters=filters,
            top_k=top_k,
            mode="keyword",
        )

    def _get_document(self, document_id: str) -> dict[str, Any] | None:
        return self._rag_store().get_document(document_id)

    def _public_skills(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for skill in self._skills():
            if skill.get("skill_id") not in _SEEDED_SKILL_IDS:
                continue
            public = {
                field: _clean_public_value(skill[field])
                for field in _PUBLIC_SKILL_FIELDS
                if field in skill
            }
            if isinstance(public.get("skill_id"), str):
                result.append(public)
        return sorted(result, key=lambda item: item["skill_id"])


def cloud_routes(dependencies: CloudDependencies) -> list[Route]:
    return [
        Route("/api/cloud/v1/catalog/actions", dependencies.list_actions, methods=["GET"]),
        Route(
            "/api/cloud/v1/catalog/actions/{action_id}",
            dependencies.get_action,
            methods=["GET"],
        ),
        Route("/api/cloud/v1/connectors", dependencies.list_connectors, methods=["GET"]),
        Route("/api/cloud/v1/skills", dependencies.list_skills, methods=["GET"]),
        Route(
            "/api/cloud/v1/skills/{skill_id}",
            dependencies.get_skill,
            methods=["GET"],
        ),
        Route(
            "/api/cloud/v1/knowledge/search",
            dependencies.search_knowledge,
            methods=["POST"],
        ),
        Route(
            "/api/cloud/v1/documents/{document_id:path}",
            dependencies.get_document,
            methods=["GET"],
        ),
    ]


def sanitize_search_query(value: str) -> str:
    return sanitize_public_text(value)


def sanitize_public_text(value: str) -> str:
    text = str(sanitize_document(value))
    text = _BEARER_RE.sub("[REDACTED_TOKEN]", text)
    text = redact_text(text)
    return _ABSOLUTE_PATH_RE.sub("[REDACTED_PATH]", text)


def _public_search_result(result: SearchResult) -> dict[str, Any]:
    return {
        "chunk_id": str(result.chunk_id),
        "document_id": str(result.document_id),
        "document_uri": sanitize_public_text(result.document_uri),
        "chunk_uri": sanitize_public_text(result.chunk_uri),
        "text": sanitize_public_text(result.text),
        "score": result.score,
        "source_title": sanitize_public_text(result.source_title),
        "source_uri": sanitize_public_text(result.source_uri),
        "source_url": sanitize_public_text(result.source_url) if result.source_url else None,
        "citation": _public_citation(result.citation),
    }


def _public_document(document: Mapping[str, Any]) -> dict[str, Any]:
    source = _document_source(document) or {}
    return {
        "id": _clean_public_value(document.get("id")),
        "document_uri": _clean_public_value(document.get("document_uri")),
        "title": _clean_public_value(document.get("title")),
        "body": _clean_public_value(document.get("body")),
        "sha256": _clean_public_value(document.get("sha256")),
        "source": {
            "title": _clean_public_value(source.get("title")),
            "source_uri": _clean_public_value(source.get("source_uri")),
            "source_url": _clean_public_value(source.get("source_url")),
        },
    }


def _clean_public_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_public_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _clean_public_value(item)
            for key, item in value.items()
            if not _PRIVATE_KEY_RE.search(str(key))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean_public_value(item) for item in value]
    return redact_json(value)


def _public_citation(citation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _clean_public_value(citation[key])
        for key in (
            "chunk_id",
            "source_title",
            "source_uri",
            "source_url",
            "heading",
            "chunk_index",
            "page",
            "section",
        )
        if key in citation
    }


def _valid_filters(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) - _FILTER_FIELDS:
        return False
    return all(
        item is None or (isinstance(item, str) and bool(item) and len(item) <= 200)
        for item in value.values()
    )


def _valid_selector(value: str | None, *, required: bool = False) -> bool:
    if value is None:
        return not required
    return bool(_SELECTOR_RE.fullmatch(value))


def _public_source_uri(value: str, action: CatalogAction) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return sanitize_public_text(value)
    return f"mercury://catalog/{action.connector_id}/{action.action_id}"


def _public_catalog_action(action: CatalogAction) -> CatalogAction:
    validated = revalidate_catalog_action(action)
    _validate_public_action_identity(validated)
    projected = validated.model_copy(
        update={
            "version_id": "",
            "input_schema": {
                "path": {},
                "query": {},
                "headers": {},
                "body": {},
                "files": {},
            },
            "examples": (),
            "idempotency": {},
            "success_rules": {},
            "error_rules": {},
            "response_redaction": (),
            "source_uri": _public_source_uri(validated.source_uri, validated),
            "source_hash": (
                validated.source_hash
                if re.fullmatch(r"[0-9a-f]{64}", validated.source_hash)
                else hashlib.sha256(validated.source_hash.encode("utf-8")).hexdigest()
            ),
            "environments": tuple(
                sanitize_public_text(item) for item in validated.environments
            ),
            "content_type": sanitize_public_text(validated.content_type),
            "aliases_th": tuple(sanitize_public_text(item) for item in validated.aliases_th),
            "aliases_en": tuple(sanitize_public_text(item) for item in validated.aliases_en),
            "capability": sanitize_public_text(validated.capability),
            "side_effects": tuple(
                sanitize_public_text(item) for item in validated.side_effects
            ),
            "preflight_action_ids": tuple(
                item
                for item in validated.preflight_action_ids
                if _ACTION_ID_RE.fullmatch(item)
            ),
            "description": sanitize_public_text(validated.description),
        }
    )
    projected = CatalogAction.model_validate(
        {name: getattr(projected, name) for name in CatalogAction.model_fields}
    )
    projected = projected.model_copy(update={"version_id": build_version_id(projected)})
    return revalidate_catalog_action(projected)


def _validate_public_action_identity(action: CatalogAction) -> None:
    identity_values = (
        action.connector_id,
        action.path_template,
        action.operation_id,
        action.variant_id,
    )
    if (
        not _SELECTOR_RE.fullmatch(action.connector_id)
        or any(sanitize_public_text(value) != value for value in identity_values)
        or any(_ABSOLUTE_PATH_RE.search(value) for value in identity_values)
    ):
        raise ValueError("cloud_catalog_invalid")


def _filter_actions(
    actions: Sequence[CatalogAction],
    *,
    connector: str | None,
    method: str | None,
) -> list[CatalogAction]:
    return [
        action
        for action in actions
        if (connector is None or action.connector_id == connector)
        and (method is None or action.method.value == method)
    ]


def _is_public_search_result(result: Any) -> bool:
    return (
        isinstance(result, SearchResult)
        and result.document_uri.startswith("mercury://wiki/")
        and result.source_uri.startswith("mercury://wiki/")
        and result.metadata.get("review_status") == "reviewed"
    )


def _valid_document_identifier(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        pass
    if not _PUBLIC_DOCUMENT_URI_RE.fullmatch(value):
        return False
    suffix = value.removeprefix("mercury://wiki/")
    return ".." not in suffix and "//" not in suffix


def _document_source(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    source = document.get("knowledge_sources")
    if isinstance(source, Mapping):
        return source
    if (
        isinstance(source, list)
        and len(source) == 1
        and isinstance(source[0], Mapping)
    ):
        return source[0]
    return None


def _is_public_document(document: Mapping[str, Any]) -> bool:
    source = _document_source(document)
    return bool(
        str(document.get("document_uri") or "").startswith("mercury://wiki/")
        and source
        and source.get("review_status") == "reviewed"
        and str(source.get("source_uri") or "").startswith("mercury://wiki/")
    )


def _document_identity_matches(
    requested: str,
    document: Mapping[str, Any],
) -> bool:
    if requested.startswith("mercury://wiki/"):
        return document.get("document_uri") == requested
    return str(document.get("id") or "") == requested


def _etag(payload: list[dict[str, Any]]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f'"{hashlib.sha256(encoded).hexdigest()}"'


def _bad_request() -> JSONResponse:
    return JSONResponse({"error": "bad_request"}, status_code=400)


def _not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404)


def _service_unavailable() -> JSONResponse:
    return JSONResponse({"error": "service_unavailable"}, status_code=503)
