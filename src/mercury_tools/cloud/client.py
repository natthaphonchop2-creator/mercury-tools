"""Typed local client for the read-only Mercury Cloud Brain API."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
    PublicEvidenceRequest,
    PublicEvidenceSelection,
    PublicEvidenceSelectionsEnvelope,
    PublicSearchEnvelope,
    PublicSkillDetail,
    PublicSkillsEnvelope,
    PublicValidationResolveRequest,
    is_canonical_document_identifier,
    is_canonical_skill_id,
    validate_document_identity,
    validate_raw_catalog_action_payload,
    validate_skill_identity,
)
from mercury_tools.cloud.models import (
    validate_public_catalog_action as _validate_shared_public_catalog_action,
)
from mercury_tools.config import load_settings

_SELECTOR_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{24}$")
_ETAG_RE = re.compile(r'^(?:W/)?"[A-Za-z0-9._:-]{1,128}"$')


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
        clock: Callable[[], datetime] | None = None,
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
        self._clock = clock or _utc_now

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
            validate_raw_catalog_action_payload(item)
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
        validate_raw_catalog_action_payload(payload["action"])
        action = revalidate_catalog_action(CatalogAction.model_validate(payload["action"]))
        if action.action_id != action_id:
            raise ValueError("cloud_catalog_invalid")
        _validate_public_catalog_action(action)
        return action

    async def resolve_validations(
        self,
        requests: Sequence[Any],
    ) -> tuple[PublicEvidenceSelection, ...]:
        batch = _validation_request_batch(requests)
        try:
            response = await self.client.post(
                "/api/cloud/v1/catalog/validation/resolve",
                json=batch.model_dump(mode="json"),
            )
        except httpx.TransportError:
            return _unavailable_selections(len(batch.requests))
        if response.status_code >= 500:
            return _unavailable_selections(len(batch.requests))
        response.raise_for_status()
        envelope = _validate_public_response(
            response,
            PublicEvidenceSelectionsEnvelope,
        )
        if len(envelope.selections) != len(batch.requests):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        admitted: list[PublicEvidenceSelection] = []
        now = self._clock()
        for request, selection in zip(
            batch.requests,
            envelope.selections,
            strict=True,
        ):
            if selection.selected is not None and request.scope_key != (
                selection.selected.connector_id,
                selection.selected.action_id,
                selection.selected.version_id,
                selection.selected.environment,
            ):
                raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
            if selection.selected is not None and not selection.selected.is_admissible_at(now):
                admitted.append(_unavailable_selection())
            else:
                admitted.append(selection)
        return tuple(admitted)

    async def resolve_validation(
        self,
        *,
        connector_id: str,
        action_id: str,
        version_id: str,
        environment: str,
    ) -> PublicEvidenceSelection:
        request = PublicEvidenceRequest.model_validate(
            {
                "connector_id": connector_id,
                "action_id": action_id,
                "version_id": version_id,
                "environment": environment,
            }
        )
        return (await self.resolve_validations((request,)))[0]

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
    _validate_shared_public_catalog_action(action)


def _validate_public_response(response: httpx.Response, model_type: Any) -> Any:
    try:
        payload = response.json()
        return model_type.model_validate_json(
            json.dumps(_default_legacy_skill_capabilities(payload, model_type))
        )
    except (KeyError, OverflowError, RecursionError, TypeError, ValueError):
        raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR) from None


def _default_legacy_skill_capabilities(payload: Any, model_type: Any) -> Any:
    """Accept only the one omitted field returned by pre-capability cloud servers."""

    if model_type is PublicSkillsEnvelope:
        if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
            return payload
        return {
            **payload,
            "skills": [
                {**skill, "required_capabilities": []}
                if isinstance(skill, dict) and "required_capabilities" not in skill
                else skill
                for skill in payload["skills"]
            ],
        }
    if (
        model_type is PublicSkillDetail
        and isinstance(payload, dict)
        and "required_capabilities" not in payload
    ):
        return {**payload, "required_capabilities": []}
    return payload


def _validation_request_batch(
    requests: Sequence[Any],
) -> PublicValidationResolveRequest:
    if isinstance(requests, str | bytes | bytearray):
        raise ValueError("cloud_validation_request_invalid")
    try:
        values = tuple(requests)
        public_requests = tuple(
            PublicEvidenceRequest.model_validate(request.model_dump(mode="python"))
            if hasattr(request, "model_dump")
            else PublicEvidenceRequest.model_validate(request)
            for request in values
        )
        return PublicValidationResolveRequest.model_validate(
            {"requests": public_requests}
        )
    except (TypeError, ValueError):
        raise ValueError("cloud_validation_request_invalid") from None


def _unavailable_selections(count: int) -> tuple[PublicEvidenceSelection, ...]:
    return tuple(_unavailable_selection() for _ in range(count))


def _unavailable_selection() -> PublicEvidenceSelection:
    return PublicEvidenceSelection.model_validate(
        {
            "selected": None,
            "blocking_conditions": ("validation_unavailable",),
        }
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
