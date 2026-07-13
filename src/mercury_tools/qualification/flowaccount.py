"""Bounded, local-only FlowAccount sandbox qualification runner."""

from __future__ import annotations

import asyncio
import secrets
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import Field, model_validator

from mercury_tools.catalog.models import CatalogAction, HttpMethod, revalidate_catalog_action
from mercury_tools.drivers.flowaccount import FlowAccountDriver
from mercury_tools.drivers.models import AuthContext, ConnectionProbe, ConnectorResult
from mercury_tools.execution.request_builder import RequestBuildError, build_request
from mercury_tools.local.repository import ensure_repository_state, normalize_repository_config
from mercury_tools.mcp.local_runtime import LocalMercuryRuntime
from mercury_tools.qualification.coverage import (
    FLOWACCOUNT_CANONICAL_IDENTITIES,
    QualificationCoverageReport,
    build_coverage_report,
    build_terminal_record,
    build_unvalidated_terminal_record,
    require_canonical_flowaccount_actions,
    safe_response_shape,
)
from mercury_tools.qualification.fixtures import (
    CleanupAdapter,
    CleanupCoordinator,
    CleanupOutcome,
    FixtureCleanupTarget,
    FixtureRegistry,
)
from mercury_tools.qualification.manifest import (
    FLOWACCOUNT_ACTION_COUNT,
    SandboxActionPolicy,
    SandboxDisposition,
    SandboxExecutionManifest,
    load_sandbox_execution_manifest,
)
from mercury_tools.qualification.models import (
    ExecutionEligibility,
    QualificationRunState,
    SemanticContract,
    StrictSafeModel,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.network import (
    SandboxOrigins,
    SandboxTenantBinding,
    require_verified_sandbox_tenant,
    sandbox_http_client,
    sandbox_request_transport_eligible,
    validate_flowaccount_sandbox_origins,
)
from mercury_tools.qualification.planner import FixturePlan, plan_fixture_dependencies
from mercury_tools.qualification.response_shape import extract_response_shape
from mercury_tools.qualification.run_store import QualificationRunStore
from mercury_tools.qualification.semantics import (
    load_actions,
    load_semantic_contracts,
)
from mercury_tools.safety.network import NetworkPolicyError

_OPAQUE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_EARLY_FAILURE_EVALUATED_AT = datetime(1970, 1, 1, tzinfo=UTC)


class SandboxRunApproval(StrictSafeModel):
    reads: bool
    writes: bool = False
    dry_run: bool = False


class QualificationLimits(StrictSafeModel):
    requests_per_second: float = Field(default=2.0, gt=0, le=2.0)
    max_read_pages: int = Field(default=3, ge=1, le=3)
    max_read_attempts: int = Field(default=2, ge=1, le=2)
    max_mutation_attempts: Literal[1] = 1
    max_total_requests: int = Field(default=40, ge=1, le=40)

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_limits(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and any(
            type(value.get(field_name)) is bool
            for field_name in cls.model_fields
            if field_name in value
        ):
            raise ValueError("qualification_limits_invalid")
        return value


class _NoMutationCleanupAdapter:
    async def cleanup(self, _fixture: FixtureCleanupTarget) -> CleanupOutcome:
        return CleanupOutcome.FAILED


class _QualificationBudgetExceeded(RuntimeError):
    pass


class _EphemeralRunStore:
    """In-memory run state used when any caller requests dry-run behavior."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._state = QualificationRunState.FAILED

    @property
    def state(self) -> QualificationRunState:
        return self._state

    @property
    def publication_allowed(self) -> bool:
        return self._state is QualificationRunState.COMPLETED

    def complete(self) -> None:
        if self._state is QualificationRunState.QUARANTINED:
            raise ValueError("qualification_run_quarantined")
        self._state = QualificationRunState.COMPLETED

    def quarantine(self, _reason: str) -> None:
        self._state = QualificationRunState.QUARANTINED


class _EarlyFailureQualificationRunner:
    """Dependency-free runner returned when repository setup cannot start."""

    def __init__(self) -> None:
        self.run_id = _new_run_id()

    @property
    def request_count(self) -> int:
        return 0

    @property
    def request_methods(self) -> tuple[str, ...]:
        return ()

    async def qualify_all(
        self,
        *,
        approval: SandboxRunApproval,
        dry_run: bool | None = None,
    ) -> QualificationCoverageReport:
        SandboxRunApproval.model_validate(approval)
        del dry_run
        return build_flowaccount_preflight_failure_report(run_id=self.run_id)


class _BoundedTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        delegate: httpx.AsyncBaseTransport,
        *,
        limits: QualificationLimits,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], Awaitable[None]],
    ) -> None:
        self._delegate = delegate
        self._limits = limits
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._last_started: float | None = None
        self.request_count = 0
        self.mutation_attempts = 0
        self.budget_exhausted = False
        self.mutation_budget_exhausted = False
        self.rate_limited = False
        self.requests: list[tuple[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        await self._account_attempt(
            method=method,
            path=request.url.path,
            is_token=method == "POST" and request.url.path == "/test/token",
        )
        response = await self._delegate.handle_async_request(request)
        if response.status_code == 429:
            self.rate_limited = True
        return response

    async def account_cleanup_attempt(self, *, method: str, action_id: str) -> None:
        checked_method = method.upper()
        if checked_method not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise _QualificationBudgetExceeded("qualification_cleanup_method_invalid")
        await self._account_attempt(
            method=checked_method,
            path=f"cleanup:{action_id}",
            is_token=False,
        )

    async def _account_attempt(self, *, method: str, path: str, is_token: bool) -> None:
        if self.request_count >= self._limits.max_total_requests:
            self.budget_exhausted = True
            raise _QualificationBudgetExceeded("qualification_request_budget_exhausted")
        is_mutation = method in {"POST", "PUT", "PATCH", "DELETE"} and not is_token
        if is_mutation and self.rate_limited:
            raise _QualificationBudgetExceeded("qualification_rate_limited")
        if is_mutation and self.mutation_attempts >= self._limits.max_mutation_attempts:
            self.mutation_budget_exhausted = True
            raise _QualificationBudgetExceeded("qualification_mutation_budget_exhausted")

        now = self._monotonic()
        if self._last_started is not None:
            minimum_interval = 1.0 / self._limits.requests_per_second
            delay = minimum_interval - (now - self._last_started)
            if delay > 0:
                await self._sleeper(delay)
                now = self._monotonic()
        self._last_started = now
        if is_mutation:
            self.mutation_attempts += 1
        self.request_count += 1
        self.requests.append((method, path))

    async def aclose(self) -> None:
        await self._delegate.aclose()


class _BoundedCleanupAdapter:
    def __init__(
        self,
        delegate: CleanupAdapter,
        transport: _BoundedTransport,
        catalog: Any,
    ) -> None:
        self._delegate = delegate
        self._transport = transport
        self._catalog = catalog

    async def cleanup(self, fixture: FixtureCleanupTarget) -> CleanupOutcome:
        action = revalidate_catalog_action(
            self._catalog.require_version(*fixture.cleanup_action_ref)
        )
        await self._transport.account_cleanup_attempt(
            method=action.method.value,
            action_id=action.action_id,
        )
        return await self._delegate.cleanup(fixture)


@dataclass(frozen=True)
class _Contracts:
    actions: tuple[CatalogAction, ...]
    manifest: SandboxExecutionManifest
    semantics: dict[tuple[str, str], SemanticContract]
    plan: FixturePlan


class FlowAccountQualificationRunner:
    """Qualify all canonical FlowAccount actions with one bounded sandbox session."""

    def __init__(
        self,
        runtime: Any,
        manifest: SandboxExecutionManifest | None = None,
        *,
        actions: Sequence[CatalogAction] | None = None,
        semantics: Mapping[tuple[str, str], SemanticContract] | None = None,
        catalog_path: Path | None = None,
        manifest_path: Path | None = None,
        semantics_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limits: QualificationLimits | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        run_id: str | None = None,
        run_store: QualificationRunStore | None = None,
        fixture_registry: FixtureRegistry | None = None,
        cleanup_adapter: CleanupAdapter | None = None,
        dry_run: bool = False,
        runtime_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.runtime = runtime
        self._runtime_factory = runtime_factory
        self._constructor_dry_run = bool(dry_run)
        self._provided_manifest = manifest
        self._provided_actions = tuple(actions) if actions is not None else None
        self._provided_semantics = dict(semantics) if semantics is not None else None
        self.catalog_path = Path(catalog_path) if catalog_path is not None else None
        self.manifest_path = Path(manifest_path) if manifest_path is not None else None
        self.semantics_path = Path(semantics_path) if semantics_path is not None else None
        self.limits = QualificationLimits.model_validate(limits or QualificationLimits())
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or asyncio.sleep
        self.run_id = run_id or _new_run_id()
        self._provided_run_store = run_store
        self._run_store: QualificationRunStore | _EphemeralRunStore | None = None
        self.fixture_registry = fixture_registry or FixtureRegistry(run_id=self.run_id)
        self.cleanup_adapter = cleanup_adapter or _NoMutationCleanupAdapter()
        if self.fixture_registry.run_id != self.run_id:
            raise ValueError("qualification_run_store_mismatch")
        delegate = transport if transport is not None else getattr(runtime, "transport", None)
        if delegate is None:
            delegate = httpx.AsyncHTTPTransport()
        if not isinstance(delegate, httpx.AsyncBaseTransport):
            raise ValueError("qualification_transport_invalid")
        self._transport = _BoundedTransport(
            delegate,
            limits=self.limits,
            monotonic=self._monotonic,
            sleeper=self._sleeper,
        )
        self._dispatched_action_identities: set[tuple[str, str]] = set()
        self._used = False

    @property
    def request_count(self) -> int:
        return self._transport.request_count

    @property
    def request_methods(self) -> tuple[str, ...]:
        return tuple(method for method, _path in self._transport.requests)

    @property
    def run_store(self) -> QualificationRunStore | _EphemeralRunStore:
        if self._run_store is None:
            raise RuntimeError("qualification_run_store_not_selected")
        return self._run_store

    async def qualify_all(
        self,
        *,
        approval: SandboxRunApproval,
        dry_run: bool | None = None,
    ) -> QualificationCoverageReport:
        if self._used:
            raise ValueError("qualification_runner_already_used")
        self._used = True
        try:
            checked_approval = SandboxRunApproval.model_validate(approval)
            is_dry_run = self._constructor_dry_run or checked_approval.dry_run or bool(dry_run)
            if not is_dry_run and self._runtime_factory is not None:
                self.runtime = self._runtime_factory()
            self._select_run_store(is_dry_run=is_dry_run)
            evaluated_at = self._timestamp()
        except Exception:
            return self._terminalize_runner_failure(
                evaluated_at=_EARLY_FAILURE_EVALUATED_AT,
                use_validated_contracts=False,
            )
        try:
            return await self._qualify_selected_run(
                approval=checked_approval,
                is_dry_run=is_dry_run,
                evaluated_at=evaluated_at,
            )
        except Exception:
            return self._terminalize_runner_failure(
                evaluated_at=evaluated_at,
                use_validated_contracts=True,
            )

    async def _qualify_selected_run(
        self,
        *,
        approval: SandboxRunApproval,
        is_dry_run: bool,
        evaluated_at: datetime,
    ) -> QualificationCoverageReport:
        records: dict[tuple[str, str], ValidationKnowledge] = {}
        contracts: _Contracts | None = None
        preflight_failure: str | None = None

        if is_dry_run:
            try:
                contracts = self._load_contracts()
            except Exception:
                preflight_failure = "contract_invalid"
        else:
            contracts, preflight_failure, records = await self._qualify_live(
                approval,
                evaluated_at,
            )

        if contracts is None:
            try:
                contracts = self._load_contracts()
            except Exception:
                preflight_failure = preflight_failure or "contract_invalid"

        if contracts is None:
            self._fill_unvalidated_records(records, evaluated_at=evaluated_at)
        elif is_dry_run or preflight_failure is not None:
            self._fill_terminal_records(
                contracts,
                records,
                evaluated_at=evaluated_at,
                executable_status=(
                    ValidationStatus.CONTRACT_VALIDATED
                    if is_dry_run and preflight_failure is None
                    else self._preflight_status(preflight_failure)
                ),
            )

        if is_dry_run and preflight_failure is None:
            self.run_store.complete()
            final_state = self.run_store.state
        elif not is_dry_run and preflight_failure is None:
            final_state = await self._cleanup_and_complete()
        else:
            final_state = self.run_store.state

        if len(records) != FLOWACCOUNT_ACTION_COUNT:
            if contracts is None:
                self._fill_unvalidated_records(records, evaluated_at=evaluated_at)
            else:
                self._fill_terminal_records(
                    contracts,
                    records,
                    evaluated_at=evaluated_at,
                    executable_status=ValidationStatus.BLOCKED_MISSING_PREREQUISITE,
                )
        return build_coverage_report(
            contracts.actions if contracts is not None else None,
            tuple(records.values()),
            final_state,
            run_id=self.run_id,
            http_attempts=self._transport.request_count,
            mutation_attempts=self._transport.mutation_attempts,
        )

    def _select_run_store(self, *, is_dry_run: bool) -> None:
        if self._run_store is not None:
            raise ValueError("qualification_run_store_already_selected")
        if is_dry_run:
            selected: QualificationRunStore | _EphemeralRunStore = _EphemeralRunStore(
                self.run_id
            )
        else:
            selected = self._provided_run_store or QualificationRunStore(
                self.runtime.repository.root,
                self.run_id,
                clock=self._clock,
            )
        if self.fixture_registry.run_id != selected.run_id:
            raise ValueError("qualification_run_store_mismatch")
        self._run_store = selected

    async def _qualify_live(
        self,
        approval: SandboxRunApproval,
        evaluated_at: datetime,
    ) -> tuple[
        _Contracts | None,
        str | None,
        dict[tuple[str, str], ValidationKnowledge],
    ]:
        records: dict[tuple[str, str], ValidationKnowledge] = {}
        try:
            driver = self._driver()
            origins = validate_flowaccount_sandbox_origins(driver)
        except Exception:
            return None, "origin_invalid", records

        snapshot = None
        credentials: dict[str, str] = {}
        try:
            fields = driver.credential_fields("sandbox")
            snapshot = self.runtime.credentials.snapshot("flowaccount", "sandbox", fields)
            credentials = snapshot.credentials
            if not snapshot.status.configured:
                return None, "missing_credentials", records
        except Exception:
            credentials.clear()
            return None, "missing_credentials", records

        try:
            async with sandbox_http_client(
                transport=self._transport,
                network=getattr(self.runtime.executor, "network", None),
            ) as client:
                try:
                    auth, probe = await driver.prepare_sandbox_auth_and_probe(
                        environment="sandbox",
                        credentials=credentials,
                        client=client,
                        origins=origins,
                    )
                    expected = self._expected_tenant(driver)
                    require_verified_sandbox_tenant(probe, expected=expected)
                except Exception:
                    return None, "tenant_or_auth_failed", records

                try:
                    contracts = self._load_contracts()
                except Exception:
                    return None, "contract_invalid", records
                failure = await self._execute_actions(
                    contracts,
                    approval,
                    client=client,
                    auth=auth,
                    origins=origins,
                    evaluated_at=evaluated_at,
                    records=records,
                )
                return contracts, failure, records
        finally:
            credentials.clear()

    async def _execute_actions(
        self,
        contracts: _Contracts,
        approval: SandboxRunApproval,
        *,
        client: httpx.AsyncClient,
        auth: AuthContext,
        origins: SandboxOrigins,
        evaluated_at: datetime,
        records: dict[tuple[str, str], ValidationKnowledge],
    ) -> str | None:
        actions = {(action.action_id, action.version_id): action for action in contracts.actions}
        policies = {
            (policy.action_id, policy.version_id): policy for policy in contracts.manifest.actions
        }
        halted: str | None = None
        for reference in contracts.plan.execution_order:
            identity = (reference.action_id, reference.version_id)
            action = actions[identity]
            policy = policies[identity]
            if policy.disposition is not SandboxDisposition.SANDBOX_EXECUTABLE:
                records[identity] = self._policy_record(
                    action,
                    policy,
                    contracts.semantics[identity],
                    evaluated_at,
                )
                continue
            if halted is not None or not approval.reads or action.method is not HttpMethod.GET:
                records[identity] = self._record(
                    action,
                    contracts.semantics[identity],
                    evaluated_at,
                    policy=policy,
                    status=ValidationStatus.BLOCKED_MISSING_PREREQUISITE,
                    eligibility=ExecutionEligibility.BLOCKED,
                )
                continue

            started = self._monotonic()
            attempts_before = self._transport.request_count
            try:
                result = await self._dispatch_safe_read(
                    action,
                    policy,
                    client=client,
                    auth=auth,
                    origins=origins,
                )
            finally:
                if self._transport.request_count > attempts_before:
                    self._dispatched_action_identities.add(identity)
            latency_ms = _latency_ms(started, self._monotonic())
            if self._transport.budget_exhausted:
                records[identity] = self._record(
                    action,
                    contracts.semantics[identity],
                    evaluated_at,
                    policy=policy,
                    status=ValidationStatus.BLOCKED_MISSING_PREREQUISITE,
                    eligibility=ExecutionEligibility.BLOCKED,
                    latency_ms=latency_ms,
                )
                halted = "request_budget_exhausted"
                continue
            status = (
                ValidationStatus.LIVE_SUCCESS
                if result.status == "succeeded"
                else ValidationStatus.OUTCOME_UNKNOWN
                if result.status == "outcome_unknown"
                else ValidationStatus.LIVE_FAILED
            )
            sanitized = self._driver().sanitize_response(action, result.data)
            shape = safe_response_shape(extract_response_shape(sanitized))
            records[identity] = self._record(
                action,
                contracts.semantics[identity],
                evaluated_at,
                policy=policy,
                status=status,
                eligibility=ExecutionEligibility.SANDBOX_READ,
                response_shape=shape,
                status_class=_status_class(result),
                latency_ms=latency_ms,
            )
            try:
                self.runtime.executor._audit_result(
                    action=action,
                    environment="sandbox",
                    event="completed",
                    state=result.status,
                    result=_audit_safe_result(result),
                    latency_ms=latency_ms,
                )
            except Exception:
                self.run_store.quarantine("outcome_unknown")
                halted = "outcome_unknown"
                records[identity] = self._record(
                    action,
                    contracts.semantics[identity],
                    evaluated_at,
                    policy=policy,
                    status=ValidationStatus.OUTCOME_UNKNOWN,
                    eligibility=ExecutionEligibility.BLOCKED,
                    status_class="unknown",
                    latency_ms=latency_ms,
                )
                continue

            if status is ValidationStatus.OUTCOME_UNKNOWN:
                self.run_store.quarantine("outcome_unknown")
                halted = "outcome_unknown"
            elif result.http_status == 429 or self._transport.rate_limited:
                halted = "rate_limited"
        return halted

    async def _dispatch_safe_read(
        self,
        action: CatalogAction,
        policy: SandboxActionPolicy,
        *,
        client: httpx.AsyncClient,
        auth: AuthContext,
        origins: SandboxOrigins,
    ) -> ConnectorResult:
        result = ConnectorResult("failed", 0, None, "request_failed", False)
        for _attempt in range(self.limits.max_read_attempts):
            request: httpx.Request | None = None
            try:
                active = revalidate_catalog_action(
                    self.runtime.catalog.require_version(action.action_id, action.version_id)
                )
                policy.validate_against(active, environment="sandbox")
                template = build_request(
                    active,
                    origins.api_url,
                    {},
                    tuple(self.runtime.executor.roots),
                    repository_id=self.runtime.repository.repository_id,
                    environment="sandbox",
                )
                request = template.to_httpx_request(auth)
                response = await client.send(request)
                return self._driver().interpret_response(
                    action=active,
                    response=response,
                    dispatched=True,
                )
            except _QualificationBudgetExceeded:
                return ConnectorResult(
                    status="failed",
                    http_status=0,
                    data=None,
                    summary="request_failed",
                    dispatched=False,
                )
            except (
                RequestBuildError,
                NetworkPolicyError,
                httpx.HTTPError,
                TypeError,
                ValueError,
            ):
                dispatched = request is not None and sandbox_request_transport_eligible(request)
                result = ConnectorResult(
                    status="failed",
                    http_status=0,
                    data=None,
                    summary="request_failed",
                    dispatched=dispatched,
                )
                if dispatched:
                    return result
        return result

    def _load_contracts(self) -> _Contracts:
        loaded_actions = (
            tuple(revalidate_catalog_action(action) for action in self._provided_actions)
            if self._provided_actions is not None
            else tuple(load_actions(self._required_path(self.catalog_path)))
        )
        actions = require_canonical_flowaccount_actions(loaded_actions)
        for action in actions:
            active = revalidate_catalog_action(
                self.runtime.catalog.require_version(action.action_id, action.version_id)
            )
            if active != action:
                raise ValueError("qualification_catalog_mismatch")

        manifest = (
            SandboxExecutionManifest.model_validate(self._provided_manifest)
            if self._provided_manifest is not None
            else load_sandbox_execution_manifest(
                self._required_path(self.manifest_path),
                self._required_path(self.catalog_path),
            )
        )
        semantics = (
            {
                identity: SemanticContract.model_validate(contract)
                for identity, contract in self._provided_semantics.items()
            }
            if self._provided_semantics is not None
            else load_semantic_contracts(
                self._required_path(self.semantics_path),
                actions,
            )
        )
        plan = plan_fixture_dependencies(actions, manifest, semantics)
        return _Contracts(actions=actions, manifest=manifest, semantics=semantics, plan=plan)

    def _fill_unvalidated_records(
        self,
        records: dict[tuple[str, str], ValidationKnowledge],
        *,
        evaluated_at: datetime,
    ) -> None:
        for identity in FLOWACCOUNT_CANONICAL_IDENTITIES:
            records.setdefault(
                identity,
                build_unvalidated_terminal_record(
                    identity=identity,
                    run_id=self.run_id,
                    run_state=self.run_store.state,
                    evaluated_at=evaluated_at,
                ),
            )

    def _fill_terminal_records(
        self,
        contracts: _Contracts,
        records: dict[tuple[str, str], ValidationKnowledge],
        *,
        evaluated_at: datetime,
        executable_status: ValidationStatus,
    ) -> None:
        policies = {
            (policy.action_id, policy.version_id): policy for policy in contracts.manifest.actions
        }
        for action in contracts.actions:
            identity = (action.action_id, action.version_id)
            if identity in records:
                continue
            policy = policies[identity]
            if policy.disposition is SandboxDisposition.SANDBOX_EXECUTABLE:
                records[identity] = self._record(
                    action,
                    contracts.semantics[identity],
                    evaluated_at,
                    policy=policy,
                    status=executable_status,
                    eligibility=(
                        ExecutionEligibility.DISCOVERY_ONLY
                        if executable_status is ValidationStatus.CONTRACT_VALIDATED
                        else ExecutionEligibility.BLOCKED
                    ),
                )
            else:
                records[identity] = self._policy_record(
                    action,
                    policy,
                    contracts.semantics[identity],
                    evaluated_at,
                )

    def _terminalize_runner_failure(
        self,
        *,
        evaluated_at: datetime,
        use_validated_contracts: bool,
    ) -> QualificationCoverageReport:
        dispatched = self._transport.request_count > 0 or self._transport.mutation_attempts > 0
        run_state = (
            QualificationRunState.QUARANTINED
            if dispatched
            else QualificationRunState.FAILED
        )
        if dispatched and self._run_store is not None:
            with suppress(Exception):
                self.run_store.quarantine("outcome_unknown")

        records = self._runner_failure_records(
            evaluated_at=evaluated_at,
            run_state=run_state,
            use_validated_contracts=use_validated_contracts,
        )
        return QualificationCoverageReport(
            connector_id="flowaccount",
            environment="sandbox",
            run_id=self.run_id,
            run_state=run_state,
            http_attempts=self._transport.request_count,
            mutation_attempts=self._transport.mutation_attempts,
            records=records,
        )

    def _runner_failure_records(
        self,
        *,
        evaluated_at: datetime,
        run_state: QualificationRunState,
        use_validated_contracts: bool,
    ) -> tuple[ValidationKnowledge, ...]:
        if use_validated_contracts and self._run_store is not None:
            try:
                contracts = self._load_contracts()
                policies = {
                    (policy.action_id, policy.version_id): policy
                    for policy in contracts.manifest.actions
                }
                records: list[ValidationKnowledge] = []
                for action in contracts.actions:
                    identity = (action.action_id, action.version_id)
                    policy = policies[identity]
                    if identity in self._dispatched_action_identities:
                        record = self._record(
                            action,
                            contracts.semantics[identity],
                            evaluated_at,
                            policy=policy,
                            status=ValidationStatus.OUTCOME_UNKNOWN,
                            eligibility=ExecutionEligibility.BLOCKED,
                            status_class="unknown",
                        )
                    elif policy.disposition is SandboxDisposition.SANDBOX_EXECUTABLE:
                        record = self._record(
                            action,
                            contracts.semantics[identity],
                            evaluated_at,
                            policy=policy,
                            status=ValidationStatus.BLOCKED_MISSING_PREREQUISITE,
                            eligibility=ExecutionEligibility.BLOCKED,
                        )
                    else:
                        record = self._policy_record(
                            action,
                            policy,
                            contracts.semantics[identity],
                            evaluated_at,
                        )
                    records.append(record.model_copy(update={"run_state": run_state}))
                if len(records) == FLOWACCOUNT_ACTION_COUNT:
                    return tuple(records)
            except Exception:
                pass

        return tuple(
            build_unvalidated_terminal_record(
                identity=identity,
                run_id=self.run_id,
                run_state=run_state,
                evaluated_at=evaluated_at,
            )
            for identity in FLOWACCOUNT_CANONICAL_IDENTITIES
        )

    def _policy_record(
        self,
        action: CatalogAction,
        policy: SandboxActionPolicy,
        semantic: SemanticContract,
        evaluated_at: datetime,
    ) -> ValidationKnowledge:
        status = {
            SandboxDisposition.CONTRACT_ONLY: ValidationStatus.CONTRACT_VALIDATED,
            SandboxDisposition.BLOCKED_EXTERNAL_EFFECT: (ValidationStatus.BLOCKED_EXTERNAL_EFFECT),
            SandboxDisposition.UNSUPPORTED_BY_SANDBOX: (ValidationStatus.UNSUPPORTED_BY_SANDBOX),
        }.get(policy.disposition, ValidationStatus.BLOCKED_MISSING_PREREQUISITE)
        return self._record(
            action,
            semantic,
            evaluated_at,
            policy=policy,
            status=status,
            eligibility=(
                ExecutionEligibility.DISCOVERY_ONLY
                if status is ValidationStatus.CONTRACT_VALIDATED
                else ExecutionEligibility.BLOCKED
            ),
        )

    def _record(
        self,
        action: CatalogAction,
        semantic: SemanticContract,
        evaluated_at: datetime,
        *,
        policy: SandboxActionPolicy,
        status: ValidationStatus,
        eligibility: ExecutionEligibility,
        response_shape: Mapping[str, Any] | None = None,
        status_class: str = "not_attempted",
        latency_ms: int | None = None,
    ) -> ValidationKnowledge:
        return build_terminal_record(
            action=action,
            semantic_contract=semantic,
            run_id=self.run_id,
            run_state=self.run_store.state,
            validation_status=status,
            execution_eligibility=eligibility,
            evaluated_at=evaluated_at,
            prerequisites=policy.prerequisites,
            response_shape=response_shape,
            status_class=status_class,
            latency_ms=latency_ms,
        )

    async def _cleanup_and_complete(self) -> QualificationRunState:
        try:
            coordinator = CleanupCoordinator(
                self.fixture_registry,
                _BoundedCleanupAdapter(
                    self.cleanup_adapter,
                    self._transport,
                    self.runtime.catalog,
                ),
                self.run_store,
            )
            await coordinator.cleanup()
        except Exception:
            if self.run_store.state is not QualificationRunState.QUARANTINED:
                self.run_store.quarantine("cleanup_failed")
        return self.run_store.state

    def _expected_tenant(self, driver: FlowAccountDriver) -> SandboxTenantBinding:
        config = normalize_repository_config(self.runtime.repository_config)
        try:
            record = config.validations["flowaccount"]["sandbox"]
            company_name = record["company_name"]
            if (
                not isinstance(company_name, str)
                or not company_name.strip()
                or record["probe_action"] != driver.safe_probe_action("sandbox")
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError("flowaccount_sandbox_expected_tenant_invalid") from None
        return require_verified_sandbox_tenant(
            ConnectionProbe(
                status="connected",
                connector_id="flowaccount",
                environment="sandbox",
                company_name=company_name,
                details={},
            )
        )

    def _driver(self) -> FlowAccountDriver:
        try:
            driver = self.runtime.drivers.get("flowaccount")
        except Exception:
            raise ValueError("flowaccount_sandbox_driver_invalid") from None
        if not isinstance(driver, FlowAccountDriver):
            raise ValueError("flowaccount_sandbox_driver_invalid")
        return driver

    def _timestamp(self) -> datetime:
        try:
            value = self._clock()
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError
            return value.astimezone(UTC)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("qualification_clock_invalid") from None

    @staticmethod
    def _required_path(value: Path | None) -> Path:
        if value is None:
            raise ValueError("qualification_contract_path_missing")
        return value

    @staticmethod
    def _preflight_status(reason: str | None) -> ValidationStatus:
        return (
            ValidationStatus.BLOCKED_MISSING_CREDENTIALS
            if reason == "missing_credentials"
            else ValidationStatus.BLOCKED_MISSING_PREREQUISITE
        )


def _status_class(result: ConnectorResult) -> str:
    if result.http_status:
        return f"{result.http_status // 100}xx"
    return "network_error" if result.dispatched else "not_attempted"


def _audit_safe_result(result: ConnectorResult) -> ConnectorResult:
    return ConnectorResult(
        status=result.status,
        http_status=result.http_status,
        data=None,
        summary="response_sanitized",
        dispatched=result.dispatched,
    )


def _latency_ms(started: float, finished: float) -> int:
    return max(0, min(int((finished - started) * 1000), 86_400_000))


def _new_run_id() -> str:
    return "run_" + "".join(secrets.choice(_OPAQUE_ALPHABET) for _ in range(26))


def _load_requested_canonical_actions(path: Path) -> tuple[CatalogAction, ...]:
    try:
        return require_canonical_flowaccount_actions(tuple(load_actions(path)))
    except Exception:
        return ()


def build_flowaccount_preflight_failure_report(
    *,
    run_id: str | None = None,
) -> QualificationCoverageReport:
    """Terminalize frozen coverage without relying on setup-time dependencies."""
    selected_run_id = run_id or _new_run_id()
    records = tuple(
        build_unvalidated_terminal_record(
            identity=identity,
            run_id=selected_run_id,
            run_state=QualificationRunState.FAILED,
            evaluated_at=_EARLY_FAILURE_EVALUATED_AT,
        )
        for identity in FLOWACCOUNT_CANONICAL_IDENTITIES
    )
    return build_coverage_report(
        None,
        records,
        QualificationRunState.FAILED,
        run_id=selected_run_id,
        http_attempts=0,
        mutation_attempts=0,
    )


def create_flowaccount_qualification_runner(
    repository_root: Path,
    *,
    dry_run: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    limits: QualificationLimits | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> FlowAccountQualificationRunner | _EarlyFailureQualificationRunner:
    """Bind a runner, or return frozen terminal coverage when setup cannot start."""
    try:
        return _create_flowaccount_qualification_runner(
            repository_root,
            dry_run=dry_run,
            transport=transport,
            limits=limits,
            clock=clock,
            monotonic=monotonic,
            sleeper=sleeper,
        )
    except Exception:
        return _EarlyFailureQualificationRunner()


def _create_flowaccount_qualification_runner(
    repository_root: Path,
    *,
    dry_run: bool,
    transport: httpx.AsyncBaseTransport | None,
    limits: QualificationLimits | None,
    clock: Callable[[], datetime] | None,
    monotonic: Callable[[], float] | None,
    sleeper: Callable[[float], Awaitable[None]] | None,
) -> FlowAccountQualificationRunner:
    """Bind a runner to one repository and the checked-in canonical sidecars."""
    root = Path(repository_root).expanduser().resolve(strict=True)
    catalog_path = root / "catalog" / "global" / "flowaccount" / "actions.json"
    manifest_path = root / "catalog" / "global" / "flowaccount" / "sandbox-execution-manifest.json"
    semantics_path = root / "catalog" / "global" / "flowaccount" / "semantic-contracts.json"
    actions = _load_requested_canonical_actions(catalog_path)
    temporary_repository = tempfile.TemporaryDirectory(prefix="mercury-qualification-")
    context = ensure_repository_state(Path(temporary_repository.name))
    runtime = LocalMercuryRuntime.for_repository(context)
    runtime.catalog.replace(actions)

    def persistent_runtime() -> LocalMercuryRuntime:
        persistent = LocalMercuryRuntime.for_repository(ensure_repository_state(root))
        persistent.catalog.replace(actions)
        return persistent

    runner = FlowAccountQualificationRunner(
        runtime,
        actions=actions,
        catalog_path=catalog_path,
        manifest_path=manifest_path,
        semantics_path=semantics_path,
        transport=transport,
        limits=limits,
        clock=clock,
        monotonic=monotonic,
        sleeper=sleeper,
        dry_run=dry_run,
        runtime_factory=persistent_runtime,
    )
    runner._temporary_repository = temporary_repository
    return runner


__all__ = [
    "FlowAccountQualificationRunner",
    "QualificationLimits",
    "SandboxRunApproval",
    "build_flowaccount_preflight_failure_report",
    "create_flowaccount_qualification_runner",
]
