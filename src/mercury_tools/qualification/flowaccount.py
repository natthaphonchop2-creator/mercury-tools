"""Bounded, local-only FlowAccount sandbox qualification runner."""

from __future__ import annotations

import asyncio
import secrets
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    QualificationCoverageReport,
    build_coverage_report,
    build_terminal_record,
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
    reviewed_policy_for,
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
    contract_for,
    load_actions,
    load_semantic_contracts,
)
from mercury_tools.safety.network import NetworkPolicyError

_OPAQUE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


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
        if self.request_count >= self._limits.max_total_requests:
            self.budget_exhausted = True
            raise _QualificationBudgetExceeded("qualification_request_budget_exhausted")
        now = self._monotonic()
        if self._last_started is not None:
            minimum_interval = 1.0 / self._limits.requests_per_second
            delay = minimum_interval - (now - self._last_started)
            if delay > 0:
                await self._sleeper(delay)
                now = self._monotonic()
        self._last_started = now

        method = request.method.upper()
        is_token = request.url.path == "/test/token"
        if method in {"POST", "PUT", "PATCH", "DELETE"} and not is_token:
            if self.mutation_attempts >= self._limits.max_mutation_attempts:
                self.mutation_budget_exhausted = True
                raise _QualificationBudgetExceeded("qualification_mutation_budget_exhausted")
            self.mutation_attempts += 1
        self.request_count += 1
        self.requests.append((method, request.url.path))
        response = await self._delegate.handle_async_request(request)
        if response.status_code == 429:
            self.rate_limited = True
        return response

    async def aclose(self) -> None:
        await self._delegate.aclose()


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
    ) -> None:
        self.runtime = runtime
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
        repository_root = self.runtime.repository.root
        self.run_store = run_store or QualificationRunStore(
            repository_root,
            self.run_id,
            clock=self._clock,
        )
        self.fixture_registry = fixture_registry or FixtureRegistry(run_id=self.run_id)
        self.cleanup_adapter = cleanup_adapter or _NoMutationCleanupAdapter()
        if self.fixture_registry.run_id != self.run_store.run_id:
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
        self._used = False

    @property
    def request_count(self) -> int:
        return self._transport.request_count

    @property
    def request_methods(self) -> tuple[str, ...]:
        return tuple(method for method, _path in self._transport.requests)

    async def qualify_all(
        self,
        *,
        approval: SandboxRunApproval,
        dry_run: bool | None = None,
    ) -> QualificationCoverageReport:
        if self._used:
            raise ValueError("qualification_runner_already_used")
        self._used = True
        checked_approval = SandboxRunApproval.model_validate(approval)
        is_dry_run = checked_approval.dry_run if dry_run is None else bool(dry_run)
        evaluated_at = self._timestamp()
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
                checked_approval,
                evaluated_at,
            )

        if contracts is None:
            try:
                contracts = self._load_contracts()
            except Exception:
                contracts = self._fallback_contracts()
                preflight_failure = preflight_failure or "contract_invalid"

        if is_dry_run or preflight_failure is not None:
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

        final_state = self.run_store.state
        if preflight_failure is None:
            final_state = await self._cleanup_and_complete()

        if len(records) != FLOWACCOUNT_ACTION_COUNT:
            self._fill_terminal_records(
                contracts,
                records,
                evaluated_at=evaluated_at,
                executable_status=ValidationStatus.BLOCKED_MISSING_PREREQUISITE,
            )
        return build_coverage_report(
            contracts.actions,
            tuple(records.values()),
            final_state,
            run_id=self.run_id,
            http_attempts=self._transport.request_count,
            mutation_attempts=self._transport.mutation_attempts,
        )

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
            result = await self._dispatch_safe_read(
                action,
                policy,
                client=client,
                auth=auth,
                origins=origins,
            )
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
                    result=result,
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
        actions = (
            tuple(revalidate_catalog_action(action) for action in self._provided_actions)
            if self._provided_actions is not None
            else tuple(load_actions(self._required_path(self.catalog_path)))
        )
        if len(actions) != FLOWACCOUNT_ACTION_COUNT:
            raise ValueError("qualification_catalog_coverage_invalid")
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

    def _fallback_contracts(self) -> _Contracts:
        if self._provided_actions is not None:
            actions = tuple(revalidate_catalog_action(action) for action in self._provided_actions)
        elif self.catalog_path is not None:
            actions = tuple(load_actions(self.catalog_path))
        else:
            actions = tuple(
                revalidate_catalog_action(action)
                for action in self.runtime.catalog.list()
                if action.connector_id == "flowaccount"
            )
        if len(actions) != FLOWACCOUNT_ACTION_COUNT:
            raise ValueError("qualification_catalog_coverage_invalid")
        manifest = SandboxExecutionManifest(
            environment="sandbox",
            catalog_sha256="0" * 64,
            actions=tuple(
                reviewed_policy_for(action)
                for action in sorted(
                    actions,
                    key=lambda item: (item.action_id, item.version_id),
                )
            ),
        )
        semantics = {
            (action.action_id, action.version_id): (
                self._provided_semantics.get((action.action_id, action.version_id))
                if self._provided_semantics is not None
                else contract_for(action)
            )
            for action in actions
        }
        checked_semantics = {
            identity: SemanticContract.model_validate(contract or contract_for(action))
            for identity, action in {
                (action.action_id, action.version_id): action for action in actions
            }.items()
            for contract in (semantics[identity],)
        }
        execution_order = tuple(sorted((action.action_id, action.version_id) for action in actions))
        from mercury_tools.qualification.planner import ActionReference

        plan = FixturePlan(
            execution_order=tuple(
                ActionReference(action_id=action_id, version_id=version_id)
                for action_id, version_id in execution_order
            ),
            reviewed_edges=(),
            recommendations=(),
            executable_actions=tuple(
                ActionReference(action_id=action_id, version_id=version_id)
                for action_id, version_id in execution_order
                if (action_id, version_id)
                in {
                    (policy.action_id, policy.version_id)
                    for policy in manifest.actions
                    if policy.disposition is SandboxDisposition.SANDBOX_EXECUTABLE
                }
            ),
        )
        return _Contracts(
            actions=actions,
            manifest=manifest,
            semantics=checked_semantics,
            plan=plan,
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
                self.cleanup_adapter,
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


def _latency_ms(started: float, finished: float) -> int:
    return max(0, min(int((finished - started) * 1000), 86_400_000))


def _new_run_id() -> str:
    return "run_" + "".join(secrets.choice(_OPAQUE_ALPHABET) for _ in range(26))


def create_flowaccount_qualification_runner(
    repository_root: Path,
    *,
    dry_run: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    limits: QualificationLimits | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> FlowAccountQualificationRunner:
    """Bind a runner to one repository and the checked-in canonical sidecars."""
    root = Path(repository_root).expanduser().resolve(strict=True)
    catalog_path = root / "catalog" / "global" / "flowaccount" / "actions.json"
    manifest_path = root / "catalog" / "global" / "flowaccount" / "sandbox-execution-manifest.json"
    semantics_path = root / "catalog" / "global" / "flowaccount" / "semantic-contracts.json"
    actions = tuple(load_actions(catalog_path))
    temporary_repository: tempfile.TemporaryDirectory[str] | None = None
    state_root = root
    if dry_run:
        temporary_repository = tempfile.TemporaryDirectory(prefix="mercury-qualification-")
        state_root = Path(temporary_repository.name)
    context = ensure_repository_state(state_root)
    runtime = LocalMercuryRuntime.for_repository(context)
    runtime.catalog.replace(actions)
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
    )
    runner._temporary_repository = temporary_repository
    return runner


__all__ = [
    "FlowAccountQualificationRunner",
    "QualificationLimits",
    "SandboxRunApproval",
    "create_flowaccount_qualification_runner",
]
