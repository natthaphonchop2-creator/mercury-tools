"""Supabase PostgREST adapter for immutable ERP action catalog publication."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from mercury_tools.catalog.identity import validate_credential_safe
from mercury_tools.catalog.models import (
    CatalogAction,
    CatalogSource,
    HttpMethod,
    revalidate_catalog_action,
    revalidate_catalog_source,
)
from mercury_tools.config import Settings, require_supabase

_FILTER_COLUMNS = {
    "capability": "capability",
    "connector_id": "connector_id",
    "method": "erp_action_versions.method",
}
_FILTER_VALUE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ACTIVE_SELECT = (
    "action_id,active_version_id,"
    "erp_action_versions!erp_action_catalog_action_id_active_version_id_fkey(definition)"
)


@dataclass(frozen=True)
class PublishResult:
    created_versions: int
    activated_actions: int


class SupabaseCatalogStore:
    """Publish sanitized catalog definitions through the service-role Data API."""

    def __init__(self, settings: Settings):
        require_supabase(settings)
        self.settings = settings
        self.base_url = f"{settings.supabase_url}/rest/v1"
        self.headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }

    def publish(self, source: CatalogSource, actions: Sequence[CatalogAction]) -> PublishResult:
        validated_source = _validated_source(source)
        validated_actions = _validated_actions(actions)

        self._request(
            "POST",
            "erp_spec_sources",
            params={"on_conflict": "source_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=[_source_payload(validated_source)],
        )
        versions = self._request(
            "POST",
            "erp_action_versions",
            params={"on_conflict": "action_id,version_id"},
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            json=[
                _version_payload(action, validated_source.source_id)
                for action in validated_actions
            ],
        )
        self._request(
            "POST",
            "erp_action_catalog",
            params={"on_conflict": "action_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=[_catalog_payload(action) for action in validated_actions],
        )
        if versions is not None and not isinstance(versions, list):
            raise RuntimeError("supabase_catalog_response_invalid")
        return PublishResult(
            created_versions=len(versions or []),
            activated_actions=len(validated_actions),
        )

    def list_active_actions(self, filters: Mapping[str, str] | None = None) -> list[CatalogAction]:
        params = _filter_params(filters)
        params["select"] = _ACTIVE_SELECT
        rows = self._request("GET", "erp_action_catalog", params=params)
        if not isinstance(rows, list):
            raise RuntimeError("supabase_catalog_response_invalid")

        actions: list[CatalogAction] = []
        for row in rows:
            try:
                definition = _definition_from_catalog_row(row)
                actions.append(revalidate_catalog_action(CatalogAction.model_validate(definition)))
            except (KeyError, TypeError, ValueError):
                raise ValueError("catalog_active_action_invalid") from None
        return actions

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {**self.headers, **kwargs.pop("headers", {})}
        try:
            response = httpx.request(method, url, headers=headers, timeout=60, **kwargs)
        except httpx.HTTPError:
            raise RuntimeError("supabase_catalog_request_failed") from None
        if response.status_code >= 300:
            raise RuntimeError(f"supabase_catalog_request_failed: HTTP {response.status_code}")
        if not response.text:
            return None
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("supabase_catalog_response_invalid") from None


def _validated_source(source: CatalogSource) -> CatalogSource:
    try:
        return revalidate_catalog_source(source)
    except (TypeError, ValueError):
        raise ValueError("catalog_source_invalid") from None


def _validated_actions(actions: Sequence[CatalogAction]) -> list[CatalogAction]:
    validated: list[CatalogAction] = []
    action_ids: set[str] = set()
    for action in actions:
        try:
            item = revalidate_catalog_action(action)
        except (TypeError, ValueError):
            raise ValueError("catalog_action_invalid") from None
        if item.action_id in action_ids:
            raise ValueError("catalog_action_duplicate")
        action_ids.add(item.action_id)
        validated.append(item)
    return validated


def _source_payload(source: CatalogSource) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "connector_id": source.connector_id,
        "source_type": source.source_type,
        "source_uri": source.source_uri,
        "source_hash": source.source_hash,
        "imported_version": source.imported_version,
        "sanitization": source.sanitization,
        "metadata": {"driver_suggestion": source.driver_suggestion},
        "imported_at": source.imported_at.isoformat(),
    }


def _version_payload(action: CatalogAction, source_id: str) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "version_id": action.version_id,
        "connector_id": action.connector_id,
        "method": action.method.value,
        "path_template": action.path_template,
        "definition": action.model_dump(mode="json"),
        "source_id": source_id,
    }


def _catalog_payload(action: CatalogAction) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "connector_id": action.connector_id,
        "capability": action.capability,
        "active_version_id": action.version_id,
    }


def _filter_params(filters: Mapping[str, str] | None) -> dict[str, str]:
    if filters is None:
        return {}
    if not isinstance(filters, Mapping):
        raise ValueError("catalog_filter_invalid")

    params: dict[str, str] = {}
    for key in sorted(filters):
        value = filters[key]
        if key not in _FILTER_COLUMNS or not isinstance(value, str):
            raise ValueError("catalog_filter_invalid")
        try:
            validate_credential_safe({key: value})
        except ValueError:
            raise ValueError("catalog_filter_invalid") from None
        if not _FILTER_VALUE.fullmatch(value):
            raise ValueError("catalog_filter_invalid")
        if key == "method" and value not in {item.value for item in HttpMethod}:
            raise ValueError("catalog_filter_invalid")
        params[_FILTER_COLUMNS[key]] = f"eq.{value}"
    return params


def _definition_from_catalog_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise TypeError("catalog row must be a mapping")
    relation = row["erp_action_versions"]
    if isinstance(relation, list):
        if len(relation) != 1:
            raise ValueError("active version relation must be singular")
        relation = relation[0]
    if not isinstance(relation, Mapping):
        raise TypeError("active version relation must be a mapping")
    definition = relation["definition"]
    if not isinstance(definition, dict):
        raise TypeError("definition must be an object")
    if (
        definition.get("action_id") != row["action_id"]
        or definition.get("version_id") != row["active_version_id"]
    ):
        raise ValueError("active definition identity mismatch")
    return definition
