"""Typed local client for the read-only Mercury Cloud Brain API."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx

from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.catalog.models import (
    CatalogAction,
    HttpMethod,
    revalidate_catalog_action,
)
from mercury_tools.cloud.api import sanitize_search_query
from mercury_tools.config import load_settings

_SELECTOR_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{24}$")
_PUBLIC_DOCUMENT_URI_RE = re.compile(
    r"^mercury://wiki/[A-Za-z0-9][A-Za-z0-9._~/-]{0,480}$"
)
_SEARCH_FILTERS = {
    "jurisdiction",
    "connector",
    "doc_type",
    "review_status",
    "effective_date",
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
        try:
            response = await self.client.get(
                "/api/cloud/v1/catalog/actions",
                headers=self.cache.conditional_headers(),
            )
        except httpx.TransportError:
            return CatalogFetchResult(
                actions=self._cached_actions(connector=connector, method=method),
                source="cache",
            )
        if response.status_code == 304 or response.status_code >= 500:
            return CatalogFetchResult(
                actions=self._cached_actions(connector=connector, method=method),
                source="cache",
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or set(payload) != {"actions"}:
            raise ValueError("cloud_catalog_invalid")
        rows = payload["actions"]
        if not isinstance(rows, list):
            raise ValueError("cloud_catalog_invalid")
        actions = tuple(
            revalidate_catalog_action(CatalogAction.model_validate(item))
            for item in rows
        )
        self.cache.replace_global(list(actions), response.headers.get("etag"))
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
            return self._cached_action(action_id)
        if response.status_code >= 500:
            return self._cached_action(action_id)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or set(payload) != {"action"}:
            raise ValueError("cloud_catalog_invalid")
        action = revalidate_catalog_action(CatalogAction.model_validate(payload["action"]))
        if action.action_id != action_id:
            raise ValueError("cloud_catalog_invalid")
        return action

    async def list_connectors(self) -> tuple[dict[str, Any], ...]:
        response = await self.client.get("/api/cloud/v1/connectors")
        response.raise_for_status()
        return tuple(response.json()["connectors"])

    async def list_skills(self) -> tuple[dict[str, Any], ...]:
        response = await self.client.get("/api/cloud/v1/skills")
        response.raise_for_status()
        return tuple(response.json()["skills"])

    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        _require_selector(skill_id)
        response = await self.client.get(f"/api/cloud/v1/skills/{quote(skill_id, safe='')}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

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
            or not _valid_search_filters(filters)
        ):
            raise ValueError("cloud_search_invalid")
        response = await self.client.post(
            "/api/cloud/v1/knowledge/search",
            json={
                "query": sanitize_search_query(query),
                "filters": {
                    key: sanitize_search_query(value)
                    for key, value in (filters or {}).items()
                },
                "top_k": top_k,
            },
        )
        response.raise_for_status()
        return tuple(response.json()["results"])

    async def get_document(self, document_id: str) -> dict[str, Any] | None:
        if not _valid_document_identifier(document_id):
            raise ValueError("cloud_identifier_invalid")
        response = await self.client.get(
            f"/api/cloud/v1/documents/{quote(document_id, safe='')}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def _cached_actions(
        self,
        *,
        connector: str | None,
        method: str | None,
    ) -> tuple[CatalogAction, ...]:
        return _filter_actions(
            tuple(self.cache.list_global()),
            connector=connector,
            method=method,
        )

    def _cached_action(self, action_id: str) -> CatalogAction | None:
        return next(
            (item for item in self.cache.list_global() if item.action_id == action_id),
            None,
        )


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


def _valid_search_filters(filters: dict[str, str] | None) -> bool:
    if filters is None:
        return True
    if not isinstance(filters, dict) or set(filters) - _SEARCH_FILTERS:
        return False
    return all(
        isinstance(value, str)
        and bool(value)
        and len(value) <= 200
        and bool(_SELECTOR_RE.fullmatch(value))
        for value in filters.values()
    )


def _valid_document_identifier(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        pass
    if not _PUBLIC_DOCUMENT_URI_RE.fullmatch(value):
        return False
    suffix = value.removeprefix("mercury://wiki/")
    return ".." not in suffix and "//" not in suffix
