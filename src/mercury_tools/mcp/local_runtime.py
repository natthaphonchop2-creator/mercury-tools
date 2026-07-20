"""Per-request repository runtime for the local Mercury Finance MCP."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
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
from mercury_tools.cloud.models import (
    PublicEvidenceRequest,
    PublicEvidenceSelection,
)
from mercury_tools.drivers.registry import DriverRegistry
from mercury_tools.execution.executor import ERPExecutor, ExecutionPolicyError
from mercury_tools.execution.store import LocalRequestStore
from mercury_tools.local.audit import AuditLedger
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import (
    RepositoryConfig,
    RepositoryContext,
    load_repository_config,
)
from mercury_tools.qualification.models import SemanticContract
from mercury_tools.qualification.semantics import require_semantic_contract
from mercury_tools.rag.models import DOCUMENTED_SEARCH_FILTER_FIELDS, SearchFilters
from mercury_tools.safety.redaction import redact_json

_SEARCH_FILTER_FIELDS = DOCUMENTED_SEARCH_FILTER_FIELDS
_VALIDATION_BATCH_SIZE = 100
_UNAVAILABLE_BLOCKERS = frozenset(
    {
        "semantic_contract_unavailable",
        "validation_environment_ambiguous",
        "validation_unavailable",
    }
)
_BUILTIN_CONNECTORS = ("flowaccount", "peak")
_BUILTIN_SEMANTIC_COUNTS = {"flowaccount": 190, "peak": 64}
_SEMANTIC_ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{24}$")
_SEMANTIC_VERSION_ID_RE = re.compile(r"^av_[0-9a-f]{64}$")
_ActionContextKey = tuple[str, str, str | None]


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
        semantic_contracts: Mapping[tuple[str, str], SemanticContract],
        drivers: DriverRegistry,
        credentials: CredentialStore,
        request_store: LocalRequestStore,
        audit: AuditLedger,
        executor: ERPExecutor,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.repository_config = repository_config
        self.cache = cache
        self.local_store = local_store
        self.cloud = cloud
        self.catalog = catalog
        self.semantic_contracts = dict(semantic_contracts)
        self.drivers = drivers
        self.credentials = credentials
        self.request_store = request_store
        self.audit = audit
        self.executor = executor
        self._clock = clock or _utc_now

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
        semantic_contracts = dict(_checked_in_semantic_contracts())
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
            semantic_contracts=semantic_contracts,
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

    async def action_context(
        self,
        action: CatalogAction,
        *,
        environment: str | None,
    ) -> dict[str, Any]:
        contexts = await self.action_contexts((action,), environment=environment)
        return contexts[(action.action_id, action.version_id)]

    async def action_contexts(
        self,
        actions: Sequence[CatalogAction],
        *,
        environment: str | None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        scoped_contexts = await self._scoped_action_contexts(
            tuple((action, environment) for action in actions)
        )
        return {
            (action.action_id, action.version_id): scoped_contexts[
                _action_context_key(action, environment)
            ]
            for action in actions
        }

    async def _scoped_action_contexts(
        self,
        scopes: Sequence[tuple[CatalogAction, str | None]],
    ) -> dict[_ActionContextKey, dict[str, Any]]:
        contexts: dict[_ActionContextKey, dict[str, Any]] = {}
        pending: list[
            tuple[
                _ActionContextKey,
                CatalogAction,
                str,
                SemanticContract,
                PublicEvidenceRequest,
            ]
        ] = []
        seen: set[_ActionContextKey] = set()
        for action, requested_environment in scopes:
            key = _action_context_key(action, requested_environment)
            if key in seen:
                continue
            seen.add(key)
            try:
                semantic = require_semantic_contract(action, self.semantic_contracts)
            except (TypeError, ValueError):
                contexts[key] = _blocked_action_context(
                    environment=requested_environment,
                    semantic=None,
                    condition="semantic_contract_unavailable",
                )
                continue

            selected_environment = (
                requested_environment
                if requested_environment is not None
                else self._validated_repository_environment(action)
            )
            if selected_environment is None:
                contexts[key] = _blocked_action_context(
                    environment=None,
                    semantic=semantic,
                    condition="validation_environment_ambiguous",
                )
                continue
            try:
                request = PublicEvidenceRequest.model_validate(
                    {
                        "connector_id": action.connector_id,
                        "action_id": action.action_id,
                        "version_id": action.version_id,
                        "environment": selected_environment,
                    }
                )
            except (TypeError, ValueError):
                raise ValueError("cloud_validation_request_invalid") from None
            pending.append((key, action, selected_environment, semantic, request))

        for offset in range(0, len(pending), _VALIDATION_BATCH_SIZE):
            batch = pending[offset : offset + _VALIDATION_BATCH_SIZE]
            requests = tuple(item[4] for item in batch)
            try:
                selections = tuple(await self.cloud.resolve_validations(requests))
                if len(selections) != len(batch):
                    raise ValueError("cloud_validation_response_invalid")
            except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError):
                selections = tuple(_unavailable_selection() for _item in batch)

            validation_now = _runtime_validation_now(self)
            for item, raw_selection in zip(batch, selections, strict=True):
                key, action, selected_environment, semantic, _request = item
                try:
                    selection = PublicEvidenceSelection.model_validate(raw_selection)
                    _validate_runtime_selection(
                        action,
                        selected_environment,
                        semantic,
                        selection,
                        now=validation_now,
                    )
                except (TypeError, ValueError):
                    selection = _unavailable_selection()
                contexts[key] = {
                    "environment": selected_environment,
                    "semantic_contract": semantic.model_dump(mode="json"),
                    "validation": selection.model_dump(mode="json"),
                }
        return contexts

    def _validated_repository_environment(
        self,
        action: CatalogAction,
    ) -> str | None:
        configured = set(
            self.repository_config.connectors.get(action.connector_id, {})
        )
        validated = {
            environment
            for environment, record in self.repository_config.validations.get(
                action.connector_id,
                {},
            ).items()
            if isinstance(record, Mapping)
            and record.get("validation_state") == "connected"
        }
        candidates = configured & validated & set(action.environments)
        return next(iter(candidates)) if len(candidates) == 1 else None

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
        return await self.cloud.search_knowledge(
            query,
            filters=_knowledge_search_filters(filters),
            top_k=top_k,
        )

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
        if (
            action.method is not HttpMethod.GET
            or action.risk_tier is not RiskTier.SAFE_READ
        ):
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

    async def enriched_connector_summaries(
        self,
        *,
        connector: str | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.connector_summaries(
            connector=connector,
            environment=environment,
        )
        actions = self.catalog.list()
        scoped_rows: list[
            tuple[dict[str, Any], str, tuple[CatalogAction, ...]]
        ] = []
        scopes: list[tuple[CatalogAction, str | None]] = []
        for row in rows:
            connector_id = row.get("connector_id")
            selected_environment = row.get("environment")
            if not isinstance(connector_id, str) or not isinstance(
                selected_environment, str
            ):
                raise ValueError("connector_status_invalid")
            scoped_actions = tuple(
                action
                for action in actions
                if action.connector_id == connector_id
                and selected_environment in action.environments
            )
            scoped_rows.append((row, selected_environment, scoped_actions))
            scopes.extend((action, selected_environment) for action in scoped_actions)

        contexts = await self._scoped_action_contexts(scopes)
        enriched: list[dict[str, Any]] = []
        for row, selected_environment, scoped_actions in scoped_rows:
            selected_count = 0
            blocked_count = 0
            unavailable_count = 0
            for action in scoped_actions:
                context = contexts[
                    _action_context_key(action, selected_environment)
                ]
                validation = context["validation"]
                if validation["selected"] is not None:
                    selected_count += 1
                    continue
                blockers = set(validation["blocking_conditions"])
                if blockers.intersection(_UNAVAILABLE_BLOCKERS):
                    unavailable_count += 1
                else:
                    blocked_count += 1
            enriched.append(
                {
                    **row,
                    "catalog_action_count": len(scoped_actions),
                    "validation_coverage": {
                        "selected_count": selected_count,
                        "blocked_count": blocked_count,
                        "unavailable_count": unavailable_count,
                    },
                }
            )
        return enriched


@lru_cache(maxsize=1)
def _checked_in_semantic_contracts() -> tuple[
    tuple[tuple[str, str], SemanticContract],
    ...,
]:
    result: dict[tuple[str, str], SemanticContract] = {}
    package_root = resources.files("mercury_tools.catalog").joinpath("global")
    for connector_id in _BUILTIN_CONNECTORS:
        contracts = _load_packaged_semantic_contracts(
            package_root.joinpath(connector_id, "semantic-contracts.json"),
            expected_count=_BUILTIN_SEMANTIC_COUNTS[connector_id],
        )
        if set(result).intersection(contracts):
            raise ValueError("semantic_contract_identity_duplicate")
        result.update(contracts)
    if len(result) != sum(_BUILTIN_SEMANTIC_COUNTS.values()):
        raise ValueError("semantic_contract_coverage_incomplete")
    return tuple(sorted(result.items()))


def _load_packaged_semantic_contracts(
    sidecar: Any,
    *,
    expected_count: int,
) -> dict[tuple[str, str], SemanticContract]:
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("semantic_contracts_missing") from None
    if not isinstance(payload, Mapping) or set(payload) != {"contracts"}:
        raise ValueError("semantic_contracts_invalid")
    rows = payload["contracts"]
    if not isinstance(rows, list):
        raise ValueError("semantic_contracts_invalid")

    contracts: dict[tuple[str, str], SemanticContract] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("semantic_contracts_invalid")
        fields = dict(row)
        action_id = fields.pop("action_id", None)
        version_id = fields.pop("version_id", None)
        if (
            not isinstance(action_id, str)
            or _SEMANTIC_ACTION_ID_RE.fullmatch(action_id) is None
            or not isinstance(version_id, str)
            or _SEMANTIC_VERSION_ID_RE.fullmatch(version_id) is None
        ):
            raise ValueError("semantic_contract_identity_invalid")
        identity = (action_id, version_id)
        if identity in contracts:
            raise ValueError("semantic_contract_identity_duplicate")
        try:
            contracts[identity] = SemanticContract.model_validate(fields)
        except (TypeError, ValueError):
            raise ValueError("semantic_contracts_invalid") from None
    if len(contracts) != expected_count:
        raise ValueError("semantic_contract_coverage_incomplete")
    return contracts


def _blocked_action_context(
    *,
    environment: str | None,
    semantic: SemanticContract | None,
    condition: str,
) -> dict[str, Any]:
    selection = PublicEvidenceSelection.model_validate(
        {
            "selected": None,
            "blocking_conditions": (condition,),
        }
    )
    return {
        "environment": environment,
        "semantic_contract": (
            semantic.model_dump(mode="json") if semantic is not None else None
        ),
        "validation": selection.model_dump(mode="json"),
    }


def _unavailable_selection() -> PublicEvidenceSelection:
    return PublicEvidenceSelection.model_validate(
        {
            "selected": None,
            "blocking_conditions": ("validation_unavailable",),
        }
    )


def _validate_runtime_selection(
    action: CatalogAction,
    environment: str,
    semantic: SemanticContract,
    selection: PublicEvidenceSelection,
    *,
    now: datetime,
) -> None:
    selected = selection.selected
    if selected is None:
        return
    if (
        (
            selected.connector_id,
            selected.action_id,
            selected.version_id,
            selected.environment,
        )
        != (
            action.connector_id,
            action.action_id,
            action.version_id,
            environment,
        )
        or selected.semantic_contract != semantic
    ):
        raise ValueError("cloud_validation_response_invalid")
    if not selected.is_admissible_at(now):
        raise ValueError("cloud_validation_response_invalid")


def _runtime_validation_now(runtime: LocalMercuryRuntime) -> datetime:
    clock = getattr(runtime, "_clock", None)
    return clock() if callable(clock) else _utc_now()


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


def _action_context_key(
    action: CatalogAction,
    environment: str | None,
) -> _ActionContextKey:
    return action.action_id, action.version_id, environment


def _knowledge_search_filters(
    filters: Mapping[str, str] | None,
) -> dict[str, str] | None:
    if filters is None:
        return None
    if not isinstance(filters, Mapping) or set(filters) - _SEARCH_FILTER_FIELDS:
        raise ValueError("cloud_search_invalid")
    copied = dict(filters)
    try:
        SearchFilters(**copied)
    except (TypeError, ValueError):
        raise ValueError("cloud_search_invalid") from None
    return copied
