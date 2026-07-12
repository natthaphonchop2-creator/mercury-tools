"""Typed local client for the read-only Mercury Cloud Brain API."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from anyio import to_thread

from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.catalog.models import (
    CatalogAction,
    HttpMethod,
    revalidate_catalog_action,
)
from mercury_tools.cloud.api import (
    sanitize_search_filters,
    sanitize_search_query,
)
from mercury_tools.cloud.models import (
    PUBLIC_RESPONSE_VALIDATION_ERROR,
    PublicConnectorsEnvelope,
    PublicDocument,
    PublicSearchEnvelope,
    PublicSkillDetail,
    PublicSkillsEnvelope,
    is_canonical_document_identifier,
    is_canonical_skill_id,
    sanitize_public_text,
    validate_document_identity,
    validate_public_api_path_template,
    validate_skill_identity,
)
from mercury_tools.config import load_settings

_SELECTOR_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{24}$")
_ETAG_RE = re.compile(r'^(?:W/)?"[A-Za-z0-9._:-]{1,128}"$')
_PUBLIC_INPUT_SCHEMA = {
    "path": {},
    "query": {},
    "headers": {},
    "body": {},
    "files": {},
}


@dataclass(frozen=True)
class CatalogFetchResult:
    actions: tuple[CatalogAction, ...]
    source: Literal["cloud", "cache"]


class CloudBrainClient:
    def __init__(
        self,
        *,
        cache: CatalogCache,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cache = cache
        resolved_base_url = base_url or load_settings().cloud_base_url
        parsed_base_url = urlparse(resolved_base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.netloc
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("cloud_base_url_invalid")
        self.client = httpx.AsyncClient(
            base_url=resolved_base_url.rstrip("/"),
            transport=transport,
            timeout=30,
        )

    async def __aenter__(self) -> CloudBrainClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    async def list_actions(
        self,
        *,
        connector: str | None = None,
        method: str | None = None,
    ) -> CatalogFetchResult:
        _validate_catalog_filter(connector=connector, method=method)
        conditional_headers = await to_thread.run_sync(self.cache.conditional_headers)
        try:
            response = await self.client.get(
                "/api/cloud/v1/catalog/actions",
                headers=conditional_headers,
            )
        except httpx.TransportError:
            return CatalogFetchResult(
                actions=await self._cached_actions(connector=connector, method=method),
                source="cache",
            )
        if response.status_code == 304 or response.status_code >= 500:
            return CatalogFetchResult(
                actions=await self._cached_actions(connector=connector, method=method),
                source="cache",
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or set(payload) != {"actions"}:
            raise ValueError("cloud_catalog_invalid")
        rows = payload["actions"]
        if not isinstance(rows, list):
            raise ValueError("cloud_catalog_invalid")
        for item in rows:
            _validate_public_action_payload_path(item)
        etag = response.headers.get("etag")
        if not isinstance(etag, str) or not _ETAG_RE.fullmatch(etag):
            raise ValueError("cloud_catalog_etag_invalid")
        actions = tuple(
            revalidate_catalog_action(CatalogAction.model_validate(item))
            for item in rows
        )
        for action in actions:
            _validate_public_catalog_action(action)
        await to_thread.run_sync(self.cache.replace_global, list(actions), etag)
        return CatalogFetchResult(
            actions=_filter_actions(actions, connector=connector, method=method),
            source="cloud",
        )

    async def get_action(self, action_id: str) -> CatalogAction | None:
        if not _ACTION_ID_RE.fullmatch(action_id):
            raise ValueError("cloud_identifier_invalid")
        try:
            response = await self.client.get(
                f"/api/cloud/v1/catalog/actions/{quote(action_id, safe='')}"
            )
        except httpx.TransportError:
            return await self._cached_action(action_id)
        if response.status_code >= 500:
            return await self._cached_action(action_id)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or set(payload) != {"action"}:
            raise ValueError("cloud_catalog_invalid")
        _validate_public_action_payload_path(payload["action"])
        action = revalidate_catalog_action(CatalogAction.model_validate(payload["action"]))
        if action.action_id != action_id:
            raise ValueError("cloud_catalog_invalid")
        _validate_public_catalog_action(action)
        return action

    async def list_connectors(self) -> tuple[dict[str, Any], ...]:
        response = await self.client.get("/api/cloud/v1/connectors")
        response.raise_for_status()
        envelope = _validate_public_response(response, PublicConnectorsEnvelope)
        return tuple(item.model_dump(mode="json") for item in envelope.connectors)

    async def list_skills(self) -> tuple[dict[str, Any], ...]:
        response = await self.client.get("/api/cloud/v1/skills")
        response.raise_for_status()
        envelope = _validate_public_response(response, PublicSkillsEnvelope)
        return tuple(item.model_dump(mode="json") for item in envelope.skills)

    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        _require_skill_identifier(skill_id)
        response = await self.client.get(f"/api/cloud/v1/skills/{quote(skill_id, safe='')}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        skill = _validate_public_response(response, PublicSkillDetail)
        try:
            validate_skill_identity(skill_id, skill)
        except ValueError:
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR) from None
        return skill.model_dump(mode="json")

    async def search_knowledge(
        self,
        query: str,
        *,
        filters: dict[str, str] | None = None,
        top_k: int = 8,
    ) -> tuple[dict[str, Any], ...]:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 2_000
            or not isinstance(top_k, int)
            or isinstance(top_k, bool)
            or not 1 <= top_k <= 20
        ):
            raise ValueError("cloud_search_invalid")
        try:
            public_filters = sanitize_search_filters(filters or {})
        except ValueError:
            raise ValueError("cloud_search_invalid") from None
        response = await self.client.post(
            "/api/cloud/v1/knowledge/search",
            json={
                "query": sanitize_search_query(query),
                "filters": public_filters,
                "top_k": top_k,
            },
        )
        response.raise_for_status()
        envelope = _validate_public_response(response, PublicSearchEnvelope)
        return tuple(item.model_dump(mode="json") for item in envelope.results)

    async def get_document(self, document_id: str) -> dict[str, Any] | None:
        if not _valid_document_identifier(document_id):
            raise ValueError("cloud_identifier_invalid")
        response = await self.client.get(
            f"/api/cloud/v1/documents/{quote(document_id, safe='')}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        document = _validate_public_response(response, PublicDocument)
        try:
            validate_document_identity(document_id, document)
        except ValueError:
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR) from None
        return document.model_dump(mode="json")

    async def _cached_actions(
        self,
        *,
        connector: str | None,
        method: str | None,
    ) -> tuple[CatalogAction, ...]:
        actions = tuple(await to_thread.run_sync(self.cache.list_global))
        for action in actions:
            _validate_public_catalog_action(action)
        return _filter_actions(
            actions,
            connector=connector,
            method=method,
        )

    async def _cached_action(self, action_id: str) -> CatalogAction | None:
        actions = await to_thread.run_sync(self.cache.list_global)
        action = next(
            (item for item in actions if item.action_id == action_id),
            None,
        )
        if action is not None:
            _validate_public_catalog_action(action)
        return action


def _filter_actions(
    actions: tuple[CatalogAction, ...],
    *,
    connector: str | None,
    method: str | None,
) -> tuple[CatalogAction, ...]:
    return tuple(
        action
        for action in actions
        if (connector is None or action.connector_id == connector)
        and (method is None or action.method.value == method)
    )


def _validate_catalog_filter(*, connector: str | None, method: str | None) -> None:
    if connector is not None:
        _require_selector(connector)
    if method is not None and method not in {item.value for item in HttpMethod}:
        raise ValueError("cloud_catalog_filter_invalid")


def _require_selector(value: str) -> None:
    if not isinstance(value, str) or not _SELECTOR_RE.fullmatch(value):
        raise ValueError("cloud_identifier_invalid")


def _require_skill_identifier(value: str) -> None:
    if not is_canonical_skill_id(value):
        raise ValueError("cloud_identifier_invalid")


def _valid_document_identifier(value: str) -> bool:
    return is_canonical_document_identifier(value)


def _validate_public_catalog_action(action: CatalogAction) -> None:
    payload = action.model_dump(mode="json")
    if (
        payload["input_schema"] != _PUBLIC_INPUT_SCHEMA
        or payload["examples"] != []
        or payload["idempotency"] != {}
        or payload["success_rules"] != {}
        or payload["error_rules"] != {}
        or payload["response_redaction"] != []
        or not re.fullmatch(r"[0-9a-f]{64}", action.source_hash)
        or any(not _ACTION_ID_RE.fullmatch(item) for item in action.preflight_action_ids)
        or not _is_public_catalog_source(action)
    ):
        raise ValueError("cloud_catalog_projection_invalid")
    if (
        sanitize_public_text(action.path_template, redact_paths=False) != action.path_template
    ):
        raise ValueError("cloud_catalog_projection_invalid")
    try:
        validate_public_api_path_template(action.path_template)
    except ValueError:
        raise ValueError("cloud_catalog_projection_invalid") from None
    public_text_payload = {**payload, "path_template": ""}
    serialized = json.dumps(public_text_payload, ensure_ascii=False, sort_keys=True)
    if sanitize_public_text(serialized) != serialized:
        raise ValueError("cloud_catalog_projection_invalid")


def _validate_public_action_payload_path(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("cloud_catalog_invalid")
    try:
        validate_public_api_path_template(value.get("path_template"))
    except ValueError:
        raise ValueError("cloud_catalog_projection_invalid") from None


def _is_public_catalog_source(action: CatalogAction) -> bool:
    source_uri = action.source_uri
    if source_uri == f"mercury://catalog/{action.connector_id}/{action.action_id}":
        return True
    parsed = urlparse(source_uri)
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and sanitize_public_text(source_uri) == source_uri
    )


def _validate_public_response(response: httpx.Response, model_type: Any) -> Any:
    try:
        return model_type.model_validate(response.json())
    except (KeyError, OverflowError, RecursionError, TypeError, ValueError):
        raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR) from None
