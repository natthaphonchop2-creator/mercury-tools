"""Ordinary-user, read-only HTTP surface for Mercury Cloud Brain."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mercury_tools.auth.consent import IDENTITY_SCOPES
from mercury_tools.catalog.models import (
    CatalogAction,
    HttpMethod,
    revalidate_catalog_action,
)
from mercury_tools.cloud.models import (
    PublicConnectorsEnvelope,
    PublicDocument,
    PublicEvidenceRequest,
    PublicEvidenceSelection,
    PublicEvidenceSelectionsEnvelope,
    PublicSearchEnvelope,
    PublicSkill,
    PublicSkillDetail,
    PublicSkillsEnvelope,
    PublicValidationEvidence,
    PublicValidationResolveRequest,
    is_canonical_document_identifier,
    is_canonical_public_wiki_uri,
    is_canonical_skill_id,
    sanitize_public_text,
    validate_public_catalog_action,
    validate_raw_catalog_action_payload,
)
from mercury_tools.config import Settings, V1ConfigurationError, load_settings
from mercury_tools.db.catalog import SupabaseCatalogStore
from mercury_tools.db.product import SKILL_CATALOG_SEED
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.db.validation import ResolveResult, SupabaseValidationStore
from mercury_tools.mercury_runtime import skill_markdown
from mercury_tools.providers.oauth import (
    FLOWACCOUNT_CALLBACK_PATH,
    OAuthCallback,
    ProviderOAuthError,
)
from mercury_tools.qualification.selection import (
    EvidenceRequest,
    EvidenceSelection,
    select_evidence,
)
from mercury_tools.rag.models import (
    SearchFilters,
    SearchResult,
    is_validation_knowledge,
    project_public_knowledge_metadata,
)
from mercury_tools.safety.redaction import redact_json

_FILTER_FIELDS = {
    "jurisdiction",
    "connector",
    "doc_type",
    "review_status",
    "effective_date",
    "action_id",
    "version_id",
    "environment",
    "capability",
    "accounting_use",
}
_SELECTOR_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{24}$")
_VERSION_ID_RE = re.compile(r"^av_[0-9a-f]{64}$")
_DOTTED_TERM_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_ENVIRONMENTS = frozenset({"sandbox", "test", "uat", "production"})
_PUBLIC_RESULT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_PRIVATE_KEY_RE = re.compile(r"(?i)(?:repository|source)?_?path|credential")
_PUBLIC_SKILL_FIELDS = (
    "skill_id",
    "title",
    "category",
    "summary",
    "status",
    "version",
    "required_capabilities",
    "required_connectors",
    "tags",
)
_CANONICAL_SKILLS = tuple(deepcopy(SKILL_CATALOG_SEED))
_CANONICAL_SKILL_IDS = frozenset(str(item["skill_id"]) for item in _CANONICAL_SKILLS)
_ORDINARY_DEPENDENCY_ERRORS = (
    httpx.HTTPError,
    KeyError,
    TypeError,
    ValueError,
    OSError,
    RuntimeError,
    OverflowError,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CloudDependencies:
    settings: Settings | None = None
    catalog_store: Any | None = None
    rag_store: Any | None = None
    validation_store: Any | None = None
    skills: Sequence[Mapping[str, Any]] | None = None
    skill_loader: Callable[[str], str | None] = skill_markdown
    clock: Callable[[], datetime] | None = None
    provider_oauth_service: Any | None = None

    def __post_init__(self) -> None:
        if self.provider_oauth_service is not None and not callable(
            getattr(self.provider_oauth_service, "complete_callback", None)
        ):
            raise V1ConfigurationError("v1_provider_oauth_service_invalid")
        if (
            self.settings is not None
            and self.settings.v1_enabled
            and self.provider_oauth_service is None
        ):
            raise V1ConfigurationError("v1_provider_oauth_service_missing")

    def _catalog_store(self) -> Any:
        if self.catalog_store is None:
            self.catalog_store = SupabaseCatalogStore(self.settings or load_settings())
        return self.catalog_store

    def _rag_store(self) -> Any:
        if self.rag_store is None:
            self.rag_store = SupabaseRagStore(self.settings or load_settings())
        return self.rag_store

    def _validation_store(self) -> Any:
        if self.validation_store is None:
            self.validation_store = SupabaseValidationStore(self.settings or load_settings())
        return self.validation_store

    def _skills(self) -> Sequence[Mapping[str, Any]]:
        return _CANONICAL_SKILLS

    async def list_actions(self, request: Request) -> Response:
        query = list(request.query_params.multi_items())
        keys = [key for key, _ in query]
        if any(key not in {"connector", "method"} for key in keys) or len(keys) != len(set(keys)):
            return _bad_request()
        connector = request.query_params.get("connector")
        method = request.query_params.get("method")
        if not _valid_selector(connector) or (
            method is not None and method not in {item.value for item in HttpMethod}
        ):
            return _bad_request()
        try:
            all_actions = await run_in_threadpool(self._catalog_actions)
            actions = _filter_actions(all_actions, connector=connector, method=method)
            payload = [action.model_dump(mode="json") for action in actions]
            etag = _etag(payload)
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
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
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        if action is None:
            return _not_found()
        try:
            payload = action.model_dump(mode="json")
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        return JSONResponse({"action": payload})

    async def resolve_validation(self, request: Request) -> Response:
        try:
            batch = PublicValidationResolveRequest.model_validate_json(await request.body())
            evidence_requests = tuple(
                EvidenceRequest.model_validate(item.model_dump(mode="python"))
                for item in batch.requests
            )
        except (TypeError, ValueError, UnicodeDecodeError):
            return _bad_request()

        try:
            resolved_at = (self.clock or _utc_now)()
            resolved = await run_in_threadpool(
                self._validation_store().resolve,
                evidence_requests,
                resolved_at,
            )
            validated = ResolveResult.model_validate(resolved)
            selections = _ordered_public_evidence_selections(
                batch,
                validated,
                now=resolved_at,
            )
            envelope = PublicEvidenceSelectionsEnvelope.model_validate({"selections": selections})
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        return JSONResponse(envelope.model_dump(mode="json"))

    async def list_connectors(self, request: Request) -> Response:
        try:
            actions = await run_in_threadpool(self._catalog_actions)
            grouped: dict[str, dict[str, set[str]]] = {}
            for action in actions:
                connector = grouped.setdefault(
                    action.connector_id,
                    {"capabilities": set(), "environments": set()},
                )
                connector["capabilities"].add(action.capability)
                connector["environments"].update(action.environments)
            payload = [
                {
                    "connector_id": connector_id,
                    "capabilities": sorted(values["capabilities"]),
                    "environments": sorted(values["environments"]),
                }
                for connector_id, values in sorted(grouped.items())
            ]
            envelope = PublicConnectorsEnvelope.model_validate({"connectors": payload})
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        return JSONResponse(envelope.model_dump(mode="json"))

    async def list_skills(self, request: Request) -> Response:
        try:
            skills = self._public_skills()
            envelope = PublicSkillsEnvelope.model_validate({"skills": skills})
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        return JSONResponse(envelope.model_dump(mode="json"))

    async def get_skill(self, request: Request) -> Response:
        skill_id = request.path_params["skill_id"]
        if not is_canonical_skill_id(skill_id):
            return _bad_request()
        try:
            skill = next(
                (item for item in self._public_skills() if item["skill_id"] == skill_id),
                None,
            )
            if skill is None:
                return _not_found()
            markdown = await run_in_threadpool(self.skill_loader, skill_id)
            if markdown is None:
                return _not_found()
            if not isinstance(markdown, str):
                raise ValueError("cloud_skill_markdown_invalid")
            payload = PublicSkillDetail.model_validate(
                {**skill, "markdown": sanitize_public_text(markdown)}
            )
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        return JSONResponse(payload.model_dump(mode="json"))

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
        ):
            return _bad_request()
        sanitized_query = sanitize_search_query(query)
        try:
            public_filters = sanitize_search_filters(filters)
        except ValueError:
            return _bad_request()
        try:
            results = await run_in_threadpool(
                self._search_knowledge,
                sanitized_query,
                SearchFilters(**public_filters),
                top_k,
            )
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        try:
            public_results = _project_public_search_results(results, top_k=top_k)
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        return JSONResponse({"results": public_results})

    async def get_document(self, request: Request) -> Response:
        document_id = request.path_params["document_id"]
        if not _valid_document_identifier(document_id):
            return _bad_request()
        try:
            document = await run_in_threadpool(self._get_document, document_id)
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        try:
            public_document = _project_public_document(document_id, document)
        except _ORDINARY_DEPENDENCY_ERRORS:
            return _service_unavailable()
        if public_document is None:
            return _not_found()
        return JSONResponse(public_document)

    async def flowaccount_oauth_callback(self, request: Request) -> Response:
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        }
        query = list(request.query_params.multi_items())
        keys = [key for key, _value in query]
        key_set = set(keys)
        if key_set not in ({"code", "state"}, {"error", "state"}) or len(keys) != len(key_set):
            return JSONResponse(
                {"error": "provider_oauth_callback_invalid"},
                status_code=400,
                headers=headers,
            )
        if self.provider_oauth_service is None:
            return JSONResponse(
                {"error": "provider_oauth_callback_unavailable"},
                status_code=503,
                headers=headers,
            )
        try:
            callback = OAuthCallback.model_validate(
                {key: request.query_params[key] for key in key_set}
            )
            summary = await self.provider_oauth_service.complete_callback(callback)
        except ProviderOAuthError as exc:
            return JSONResponse(
                {"error": exc.code},
                status_code=400,
                headers=headers,
            )
        except (KeyError, TypeError, ValueError):
            return JSONResponse(
                {"error": "provider_oauth_callback_invalid"},
                status_code=400,
                headers=headers,
            )
        except Exception:
            return JSONResponse(
                {"error": "provider_oauth_callback_unavailable"},
                status_code=503,
                headers=headers,
            )
        return JSONResponse(
            {
                "provider": summary.provider.value,
                "company_display_name": summary.account_display_name,
                "environment": summary.environment,
                "readiness": summary.readiness.value,
                "instruction": "Return to the Mercury host to continue.",
            },
            headers=headers,
        )

    def _catalog_actions(self) -> list[CatalogAction]:
        rows = self._catalog_store().list_active_actions()
        actions: list[CatalogAction] = []
        action_ids: set[str] = set()
        version_ids: set[str] = set()
        for row in rows:
            if isinstance(row, CatalogAction):
                action = revalidate_catalog_action(row)
            else:
                validate_raw_catalog_action_payload(row)
                action = CatalogAction.model_validate(row)
            public_action = _public_catalog_action(action)
            if public_action.action_id in action_ids or public_action.version_id in version_ids:
                raise ValueError("cloud_catalog_duplicate")
            action_ids.add(public_action.action_id)
            version_ids.add(public_action.version_id)
            actions.append(public_action)
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
            if skill.get("skill_id") not in _CANONICAL_SKILL_IDS:
                continue
            public = {
                field: _clean_public_value(skill[field])
                for field in _PUBLIC_SKILL_FIELDS
                if field in skill
            }
            if isinstance(public.get("skill_id"), str):
                result.append(PublicSkill.model_validate(public).model_dump(mode="json"))
        return sorted(result, key=lambda item: item["skill_id"])


def cloud_routes(dependencies: CloudDependencies) -> list[Route]:
    routes = [
        Route("/api/cloud/v1/catalog/actions", dependencies.list_actions, methods=["GET"]),
        Route(
            "/api/cloud/v1/catalog/actions/{action_id}",
            dependencies.get_action,
            methods=["GET"],
        ),
        Route(
            "/api/cloud/v1/catalog/validation/resolve",
            dependencies.resolve_validation,
            methods=["POST"],
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
    provider_oauth_service = getattr(
        dependencies,
        "provider_oauth_service",
        None,
    )
    if provider_oauth_service is not None:
        routes.append(
            Route(
                FLOWACCOUNT_CALLBACK_PATH,
                dependencies.flowaccount_oauth_callback,
                methods=["GET"],
            )
        )
    return routes


def protected_resource_routes(settings: Settings) -> list[Route]:
    async def metadata(_request: Request) -> Response:
        return JSONResponse(
            {
                "resource": settings.canonical_mcp_resource,
                "authorization_servers": [settings.supabase_auth_issuer],
                "scopes_supported": [
                    scope for scope in ("openid", "email", "profile") if scope in IDENTITY_SCOPES
                ],
                "bearer_methods_supported": ["header"],
            },
            headers={"Cache-Control": "public, max-age=300"},
        )

    return [
        Route(
            "/.well-known/oauth-protected-resource",
            metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource/mcp",
            metadata,
            methods=["GET"],
        ),
    ]


def _ordered_public_evidence_selections(
    batch: PublicValidationResolveRequest,
    resolved: ResolveResult,
    *,
    now: datetime,
) -> tuple[PublicEvidenceSelection, ...]:
    by_scope: dict[tuple[str, str, str, str], EvidenceSelection] = {}
    for entry in resolved.entries:
        scope = entry.request.scope_key
        if scope in by_scope:
            raise ValueError("cloud_validation_response_invalid")
        selected_again = select_evidence(
            entry.selection.records,
            request=entry.request,
            now=now,
        )
        if selected_again != entry.selection:
            raise ValueError("cloud_validation_response_invalid")
        by_scope[scope] = entry.selection

    expected = {request.scope_key for request in batch.requests}
    if set(by_scope) != expected:
        raise ValueError("cloud_validation_response_invalid")

    return tuple(
        _public_evidence_selection(request, by_scope[request.scope_key], now=now)
        for request in batch.requests
    )


def _public_evidence_selection(
    request: PublicEvidenceRequest,
    selection: EvidenceSelection,
    *,
    now: datetime,
) -> PublicEvidenceSelection:
    selected = selection.selected
    if selected is None:
        blockers = selection.blocking_conditions or ("validation_unavailable",)
        return PublicEvidenceSelection.model_validate(
            {"selected": None, "blocking_conditions": blockers}
        )
    if not selected.approved_public or request.scope_key != (
        selected.connector_id,
        selected.action_id,
        selected.version_id,
        selected.environment,
    ):
        raise ValueError("cloud_validation_response_invalid")

    evidence = PublicValidationEvidence.model_validate(
        {field: getattr(selected, field) for field in PublicValidationEvidence.model_fields}
    )
    if not evidence.is_admissible_at(now):
        return PublicEvidenceSelection.model_validate(
            {
                "selected": None,
                "blocking_conditions": ("validation_unavailable",),
            }
        )
    return PublicEvidenceSelection.model_validate({"selected": evidence, "blocking_conditions": ()})


def sanitize_search_query(value: str) -> str:
    return sanitize_public_text(value)


def sanitize_search_filters(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) - _FILTER_FIELDS:
        raise ValueError("cloud_search_filters_invalid")
    sanitized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not item or len(item) > 200:
            raise ValueError("cloud_search_filters_invalid")
        if key == "effective_date":
            try:
                if date.fromisoformat(item).isoformat() != item:
                    raise ValueError
            except ValueError:
                raise ValueError("cloud_search_filters_invalid") from None
        elif (
            (key == "action_id" and _ACTION_ID_RE.fullmatch(item) is None)
            or (key == "version_id" and _VERSION_ID_RE.fullmatch(item) is None)
            or (key == "environment" and item not in _ENVIRONMENTS)
            or (key in {"capability", "accounting_use"} and _DOTTED_TERM_RE.fullmatch(item) is None)
            or (
                key
                not in {
                    "effective_date",
                    "action_id",
                    "version_id",
                    "environment",
                    "capability",
                    "accounting_use",
                }
                and not _SELECTOR_RE.fullmatch(item)
            )
        ):
            raise ValueError("cloud_search_filters_invalid")
        clean = sanitize_public_text(item)
        if clean != item:
            raise ValueError("cloud_search_filters_invalid")
        sanitized[key] = clean
    sanitized["review_status"] = "reviewed"
    return sanitized


def _public_search_result(result: SearchResult) -> dict[str, Any]:
    validation = is_validation_knowledge(
        result.metadata,
        document_uri=result.document_uri,
        source_uri=result.source_uri,
    )
    metadata = project_public_knowledge_metadata(
        result.metadata,
        document_uri=result.document_uri,
        source_uri=result.source_uri,
    )
    payload = {
        "chunk_id": result.chunk_id,
        "document_id": result.document_id,
        "document_uri": sanitize_public_text(result.document_uri),
        "chunk_uri": sanitize_public_text(result.chunk_uri),
        "text": sanitize_public_text(result.text),
        "score": result.score,
        "source_title": sanitize_public_text(result.source_title),
        "source_uri": sanitize_public_text(result.source_uri),
        "source_url": (
            None if validation or not result.source_url else sanitize_public_text(result.source_url)
        ),
        "citation": _public_citation(
            result.citation,
            include_source_url=not validation,
        ),
    }
    if validation:
        payload["metadata"] = metadata
    return payload


def _project_public_search_results(
    results: Any,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes, bytearray)):
        raise ValueError("cloud_search_results_invalid")
    public: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, SearchResult):
            raise ValueError("cloud_search_result_invalid")
        _validate_search_result_shape(result)
        if _is_public_search_result(result):
            projected = _public_search_result(result)
            json.dumps(projected, allow_nan=False)
            public.append(projected)
            if len(public) == top_k:
                break
    return PublicSearchEnvelope.model_validate({"results": public}).model_dump(mode="json")[
        "results"
    ]


def _validate_search_result_shape(result: SearchResult) -> None:
    string_fields = (
        result.chunk_id,
        result.document_id,
        result.document_uri,
        result.chunk_uri,
        result.text,
        result.source_title,
        result.source_uri,
    )
    if (
        any(not isinstance(item, str) for item in string_fields)
        or any(
            not _PUBLIC_RESULT_ID_RE.fullmatch(item) or sanitize_public_text(item) != item
            for item in (result.chunk_id, result.document_id)
        )
        or not isinstance(result.citation, Mapping)
        or not isinstance(result.metadata, Mapping)
        or (result.source_url is not None and not isinstance(result.source_url, str))
        or isinstance(result.score, bool)
        or not isinstance(result.score, (int, float))
        or not math.isfinite(result.score)
        or not isinstance(result.metadata.get("review_status"), str)
    ):
        raise ValueError("cloud_search_result_invalid")


def _public_document(document: Mapping[str, Any]) -> dict[str, Any]:
    source = _document_source(document) or {}
    validation = is_validation_knowledge(
        document.get("metadata"),
        document_uri=document.get("document_uri"),
        source_uri=source.get("source_uri"),
        doc_type=source.get("doc_type"),
    )
    metadata = project_public_knowledge_metadata(
        document.get("metadata"),
        document_uri=document.get("document_uri"),
        source_uri=source.get("source_uri"),
        doc_type=source.get("doc_type"),
    )
    payload = {
        "id": _clean_public_value(document.get("id")),
        "document_uri": _clean_public_value(document.get("document_uri")),
        "title": _clean_public_value(document.get("title")),
        "body": _clean_public_value(document.get("body")),
        "sha256": _clean_public_value(document.get("sha256")),
        "source": {
            "title": _clean_public_value(source.get("title")),
            "source_uri": _clean_public_value(source.get("source_uri")),
            "source_url": (None if validation else _clean_public_value(source.get("source_url"))),
        },
    }
    if validation:
        payload["metadata"] = metadata
    return payload


def _project_public_document(
    requested: str,
    document: Any,
) -> dict[str, Any] | None:
    if document is None:
        return None
    if not isinstance(document, Mapping):
        raise ValueError("cloud_document_invalid")
    if not _document_identity_matches(requested, document):
        return None
    document_uri = document.get("document_uri")
    if not isinstance(document_uri, str):
        raise ValueError("cloud_document_invalid")
    source = _document_source(document)
    if source is None:
        if _looks_like_wiki_uri(document_uri):
            raise ValueError("cloud_document_invalid")
        return None
    source_uri = source.get("source_uri")
    if not isinstance(source_uri, str):
        if _looks_like_wiki_uri(document_uri):
            raise ValueError("cloud_document_invalid")
        return None
    document_is_wiki = _looks_like_wiki_uri(document_uri)
    source_is_wiki = _looks_like_wiki_uri(source_uri)
    if not document_is_wiki and not source_is_wiki:
        return None
    if not is_canonical_public_wiki_uri(document_uri) or not is_canonical_public_wiki_uri(
        source_uri
    ):
        raise ValueError("cloud_document_invalid")
    review_status = source.get("review_status")
    if not isinstance(review_status, str):
        raise ValueError("cloud_document_invalid")
    if review_status != "reviewed":
        return None
    try:
        project_public_knowledge_metadata(
            document.get("metadata"),
            document_uri=document_uri,
            source_uri=source_uri,
            doc_type=source.get("doc_type"),
        )
    except ValueError:
        return None
    for field in ("id", "title", "body", "sha256"):
        if not isinstance(document.get(field), str):
            raise ValueError("cloud_document_invalid")
    if not isinstance(source.get("title"), str) or (
        source.get("source_url") is not None and not isinstance(source.get("source_url"), str)
    ):
        raise ValueError("cloud_document_invalid")
    return PublicDocument.model_validate(_public_document(document)).model_dump(mode="json")


def _clean_public_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_public_text(value)
    if isinstance(value, Mapping):
        redacted = redact_json({str(key): item for key, item in value.items()})
        return {
            str(key): _clean_public_value(item)
            for key, item in redacted.items()
            if not _PRIVATE_KEY_RE.search(str(key))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_clean_public_value(item) for item in value]
    return redact_json(value)


def _public_citation(
    citation: Mapping[str, Any],
    *,
    include_source_url: bool,
) -> dict[str, Any]:
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
        if key in citation and (include_source_url or key != "source_url")
    }


def _valid_selector(value: str | None, *, required: bool = False) -> bool:
    if value is None:
        return not required
    return bool(_SELECTOR_RE.fullmatch(value))


def _public_catalog_action(action: CatalogAction) -> CatalogAction:
    validated = revalidate_catalog_action(action)
    validate_public_catalog_action(validated)
    return validated


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
    document_is_wiki = _looks_like_wiki_uri(result.document_uri)
    source_is_wiki = _looks_like_wiki_uri(result.source_uri)
    chunk_is_wiki = _looks_like_wiki_uri(result.chunk_uri)
    if not document_is_wiki and not source_is_wiki and not chunk_is_wiki:
        return False
    if (
        not is_canonical_public_wiki_uri(result.document_uri)
        or not is_canonical_public_wiki_uri(result.source_uri)
        or not is_canonical_public_wiki_uri(result.chunk_uri, allow_chunk=True)
        or not result.chunk_uri.startswith(f"{result.document_uri}#")
    ):
        raise ValueError("cloud_search_result_invalid")
    if result.metadata.get("review_status") != "reviewed":
        return False
    try:
        project_public_knowledge_metadata(
            result.metadata,
            document_uri=result.document_uri,
            source_uri=result.source_uri,
        )
    except ValueError:
        return False
    return True


def _looks_like_wiki_uri(value: Any) -> bool:
    return isinstance(value, str) and value.casefold().startswith("mercury://wiki")


def _valid_document_identifier(value: str) -> bool:
    return is_canonical_document_identifier(value)


def _document_source(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    source = document.get("knowledge_sources")
    if isinstance(source, Mapping):
        return source
    if isinstance(source, list) and len(source) == 1 and isinstance(source[0], Mapping):
        return source[0]
    return None


def _document_identity_matches(
    requested: str,
    document: Mapping[str, Any],
) -> bool:
    if is_canonical_public_wiki_uri(requested):
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
