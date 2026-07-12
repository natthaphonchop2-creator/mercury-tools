"""Per-request repository runtime for the local Mercury Finance MCP."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx
from anyio import to_thread

from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.catalog.importers.service import ImportResult, import_spec
from mercury_tools.catalog.local_store import LocalCatalogStore, merge_actions
from mercury_tools.catalog.models import CatalogAction, HttpMethod, RiskTier
from mercury_tools.catalog.search import CatalogSearchResponse, search_actions
from mercury_tools.cloud.client import CloudBrainClient
from mercury_tools.drivers.registry import DriverRegistry
from mercury_tools.execution.executor import ERPExecutor, ExecutionPolicyError
from mercury_tools.execution.policy import effective_risk
from mercury_tools.execution.store import LocalRequestStore
from mercury_tools.local.audit import AuditLedger
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import (
    RepositoryConfig,
    RepositoryContext,
    load_repository_config,
)
from mercury_tools.safety.redaction import redact_json


class LocalActionCatalog:
    """Mutable in-memory view over one global snapshot and one local overlay."""

    def __init__(self, actions: Iterable[CatalogAction] = ()) -> None:
        self.replace(actions)

    def replace(self, actions: Iterable[CatalogAction]) -> None:
        indexed: dict[str, CatalogAction] = {}
        versions: dict[tuple[str, str], CatalogAction] = {}
        ordered = tuple(actions)
        for action in ordered:
            if action.action_id in indexed:
                raise ValueError("catalog_action_duplicate")
            indexed[action.action_id] = action
            versions[(action.action_id, action.version_id)] = action
        self._actions = ordered
        self._by_id = indexed
        self._by_version = versions

    def list(self) -> tuple[CatalogAction, ...]:
        return self._actions

    def require(self, action_id: str) -> CatalogAction:
        try:
            return self._by_id[action_id]
        except KeyError:
            raise LookupError("catalog_action_not_found") from None

    def require_version(self, action_id: str, version_id: str) -> CatalogAction:
        try:
            return self._by_version[(action_id, version_id)]
        except KeyError:
            raise LookupError("catalog_action_version_not_found") from None


class LocalMercuryRuntime:
    """All local dependencies bound to one canonical repository context."""

    def __init__(
        self,
        *,
        repository: RepositoryContext,
        repository_config: RepositoryConfig,
        cache: CatalogCache,
        local_store: LocalCatalogStore,
        cloud: CloudBrainClient,
        catalog: LocalActionCatalog,
        drivers: DriverRegistry,
        credentials: CredentialStore,
        request_store: LocalRequestStore,
        audit: AuditLedger,
        executor: ERPExecutor,
    ) -> None:
        self.repository = repository
        self.repository_config = repository_config
        self.cache = cache
        self.local_store = local_store
        self.cloud = cloud
        self.catalog = catalog
        self.drivers = drivers
        self.credentials = credentials
        self.request_store = request_store
        self.audit = audit
        self.executor = executor

    @classmethod
    def for_repository(cls, context: RepositoryContext) -> LocalMercuryRuntime:
        """Build a fresh runtime without loading or retaining raw credentials."""

        if not isinstance(context, RepositoryContext):
            raise ValueError("invalid_repository_context")
        repository_config = load_repository_config(context)
        cache = CatalogCache(context)
        local_store = LocalCatalogStore(context)
        catalog = LocalActionCatalog(
            merge_actions(cache.list_global(), local_store.list_actions())
        )
        cloud = CloudBrainClient(cache=cache)
        drivers = DriverRegistry.for_repository(repository_config)
        credentials = CredentialStore(context)
        request_store = LocalRequestStore(context)
        audit = AuditLedger(context.audit_dir / "audit.jsonl")
        executor = ERPExecutor(
            context=context,
            repository_config=repository_config,
            catalog=catalog,
            drivers=drivers,
            credentials=credentials,
            request_store=request_store,
            audit_ledger=audit,
            roots=(context.root,),
        )
        return cls(
            repository=context,
            repository_config=repository_config,
            cache=cache,
            local_store=local_store,
            cloud=cloud,
            catalog=catalog,
            drivers=drivers,
            credentials=credentials,
            request_store=request_store,
            audit=audit,
            executor=executor,
        )

    async def aclose(self) -> None:
        await self.cloud.aclose()

    async def refresh_catalog(self) -> None:
        """Fetch one unfiltered global snapshot, then merge the local overlay."""

        fetched = await self.cloud.list_actions()
        local_actions = await to_thread.run_sync(self.local_store.list_actions)
        self.catalog.replace(merge_actions(fetched.actions, local_actions))

    async def refresh_overlay(self) -> None:
        global_actions = await to_thread.run_sync(self.cache.list_global)
        local_actions = await to_thread.run_sync(self.local_store.list_actions)
        self.catalog.replace(merge_actions(global_actions, local_actions))

    async def search_actions(
        self,
        query: str,
        *,
        connector: str | None = None,
        method: HttpMethod | None = None,
        risk_tier: RiskTier | None = None,
        top_k: int = 8,
    ) -> CatalogSearchResponse:
        await self.refresh_catalog()
        semantic_scores = await self._semantic_action_scores(query, connector=connector)
        return search_actions(
            self.catalog.list(),
            query,
            connector=connector,
            method=method,
            risk_tier=risk_tier,
            top_k=top_k,
            semantic_scores=semantic_scores,
        )

    async def _semantic_action_scores(
        self,
        query: str,
        *,
        connector: str | None,
    ) -> dict[str, float]:
        filters = {"doc_type": "endpoint_dictionary"}
        if connector:
            filters["connector"] = connector
        try:
            results = await self.cloud.search_knowledge(query, filters=filters, top_k=20)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError):
            return {}

        scores: dict[str, float] = {}
        for result in results:
            action_id = _semantic_action_id(result)
            score = result.get("score")
            if action_id is None or not isinstance(score, int | float):
                continue
            scores[action_id] = max(scores.get(action_id, 0.0), float(score))
        return scores

    async def search_knowledge(
        self,
        query: str,
        *,
        filters: dict[str, str] | None = None,
        top_k: int = 8,
    ) -> tuple[dict[str, Any], ...]:
        return await self.cloud.search_knowledge(query, filters=filters, top_k=top_k)

    async def get_document(self, document_id: str) -> dict[str, Any] | None:
        return await self.cloud.get_document(document_id)

    async def run_accounting_skill(
        self,
        skill_id: str,
        *,
        inputs: Mapping[str, Any] | None = None,
        evidence_mode: bool = True,
    ) -> dict[str, Any]:
        skill = await self.cloud.get_skill(skill_id)
        if skill is None:
            return {"status": "skill_not_found", "skill_id": skill_id}
        query = " ".join(
            value
            for value in (
                str(skill.get("title") or "").strip(),
                str(skill.get("summary") or "").strip(),
            )
            if value
        )
        context = await self.cloud.search_knowledge(query, top_k=8) if evidence_mode else ()
        return redact_json(
            {
                "status": "ok",
                "skill": skill,
                "inputs": dict(inputs or {}),
                "context": list(context),
                "tool_plan": [
                    "connector_status",
                    "retrieve_context_pack",
                    "search_erp_actions",
                    "get_erp_action_schema",
                    "run_erp_read",
                    "preview_erp_write",
                    "confirm_erp_write",
                    "execute_erp_write",
                    "get_erp_request_status",
                ],
                "llm_called": False,
            }
        )

    async def run_read(
        self,
        action_id: str,
        inputs: Mapping[str, Any],
        environment: str,
    ) -> dict[str, Any]:
        action = self.catalog.require(action_id)
        if effective_risk(action).tier is not RiskTier.SAFE_READ:
            raise ExecutionPolicyError("erp_read_requires_effective_tier_zero")
        result = await self.executor.run_read(
            repository=self.repository,
            action=action,
            environment=environment,
            inputs=inputs,
        )
        return redact_json(result.public_dict())

    async def preview_write(
        self,
        action_id: str,
        inputs: Mapping[str, Any],
        environment: str,
    ) -> dict[str, Any]:
        action = self.catalog.require(action_id)
        prepared = await self.executor.preview_write(
            repository=self.repository,
            action=action,
            environment=environment,
            inputs=inputs,
        )
        return redact_json({"status": "confirmation_required", **prepared.public_dict()})

    async def import_catalog_spec(
        self,
        *,
        connector_id: str,
        source_path: str | Path | None,
        source_url: str | None,
    ) -> ImportResult:
        result = await to_thread.run_sync(
            lambda: import_spec(
                self.repository,
                connector_id=connector_id,
                source_path=source_path,
                source_url=source_url,
            )
        )
        await self.refresh_overlay()
        return result

    def credential_summary(self, connector_id: str, environment: str) -> dict[str, Any]:
        driver = self.drivers.get(connector_id)
        return self.credentials.status(
            connector_id,
            environment,
            driver.credential_fields(environment),
        ).public_dict()

    def connector_summaries(
        self,
        *,
        connector: str | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        action_environments: dict[str, set[str]] = {}
        capabilities: dict[str, set[str]] = {}
        for action in self.catalog.list():
            action_environments.setdefault(action.connector_id, set()).update(action.environments)
            capabilities.setdefault(action.connector_id, set()).add(action.capability)
        for connector_id, environments in self.repository_config.connectors.items():
            action_environments.setdefault(connector_id, set()).update(environments)
        for connector_id, environments in self.repository_config.validations.items():
            action_environments.setdefault(connector_id, set()).update(environments)
        if connector and environment:
            action_environments.setdefault(connector, set()).add(environment)

        driver_ids = {
            item["connector_id"]: item["driver_id"]
            for item in self.drivers.public_summaries()
            if item.get("entry_type") == "connector"
        }
        rows: list[dict[str, Any]] = []
        selected_connectors = [connector] if connector else sorted(action_environments)
        for connector_id in selected_connectors:
            if connector_id not in driver_ids:
                continue
            environments = (
                [environment]
                if environment
                else sorted(action_environments.get(connector_id, ()))
            )
            for selected_environment in environments:
                credential = self.credential_summary(connector_id, selected_environment)
                validation = self.repository_config.validations.get(connector_id, {}).get(
                    selected_environment
                )
                rows.append(
                    {
                        **credential,
                        "driver_id": driver_ids[connector_id],
                        "capabilities": sorted(capabilities.get(connector_id, ())),
                        "validation": dict(validation) if validation else None,
                        "requires_safe_probe": validation is None,
                    }
                )
        return rows


def _semantic_action_id(result: Mapping[str, Any]) -> str | None:
    citation = result.get("citation")
    if not isinstance(citation, Mapping):
        return None
    section = citation.get("section")
    if isinstance(section, Mapping):
        action_id = section.get("action_id")
        return action_id if isinstance(action_id, str) else None
    if isinstance(section, str) and section.startswith("act_"):
        return section
    return None
