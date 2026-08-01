"""Immutable action catalog contracts and repository-backed stores."""

from __future__ import annotations

from typing import Any

__all__ = [
    "ActionConfidence",
    "CatalogAction",
    "CatalogCache",
    "CatalogSource",
    "HttpMethod",
    "LocalCatalogStore",
    "ObservedState",
    "RiskTier",
    "build_action_id",
    "build_source_id",
    "build_version_id",
    "merge_actions",
]


def __getattr__(name: str) -> Any:
    if name == "CatalogCache":
        from mercury_tools.catalog.cache import CatalogCache

        return CatalogCache
    if name in {"build_action_id", "build_source_id", "build_version_id"}:
        from mercury_tools.catalog.identity import (
            build_action_id,
            build_source_id,
            build_version_id,
        )

        return {
            "build_action_id": build_action_id,
            "build_source_id": build_source_id,
            "build_version_id": build_version_id,
        }[name]
    if name in {"LocalCatalogStore", "merge_actions"}:
        from mercury_tools.catalog.local_store import LocalCatalogStore, merge_actions

        return {"LocalCatalogStore": LocalCatalogStore, "merge_actions": merge_actions}[name]
    if name in {
        "ActionConfidence",
        "CatalogAction",
        "CatalogSource",
        "HttpMethod",
        "ObservedState",
        "RiskTier",
    }:
        from mercury_tools.catalog.models import (
            ActionConfidence,
            CatalogAction,
            CatalogSource,
            HttpMethod,
            ObservedState,
            RiskTier,
        )

        return {
            "ActionConfidence": ActionConfidence,
            "CatalogAction": CatalogAction,
            "CatalogSource": CatalogSource,
            "HttpMethod": HttpMethod,
            "ObservedState": ObservedState,
            "RiskTier": RiskTier,
        }[name]
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
