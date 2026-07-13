from __future__ import annotations

import json
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from mercury_tools.catalog.identity import build_action_id, build_version_id
from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.drivers.flowaccount import FlowAccountDriver
from mercury_tools.drivers.models import ConnectorResult, CredentialStatus
from mercury_tools.drivers.registry import DriverRegistry
from mercury_tools.execution.executor import ERPExecutor
from mercury_tools.execution.store import LocalRequestStore
from mercury_tools.local.audit import AuditLedger
from mercury_tools.local.credentials import CredentialSnapshot
from mercury_tools.local.repository import RepositoryConfig, RepositoryContext
from mercury_tools.mcp.local_runtime import LocalActionCatalog
from mercury_tools.qualification.fixtures import (
    CleanupOutcome,
    FixtureCleanupTarget,
    FixtureRegistry,
)
from mercury_tools.qualification.flowaccount import (
    FlowAccountQualificationRunner,
    QualificationLimits,
    SandboxRunApproval,
    create_flowaccount_qualification_runner,
)
from mercury_tools.qualification.manifest import (
    LIVE_READS,
    SandboxExecutionManifest,
    load_sandbox_execution_manifest,
)
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationRunState,
    ValidationStatus,
)
from mercury_tools.qualification.semantics import load_actions, load_semantic_contracts

ROOT = Path(__file__).resolve().parents[1]
FLOWACCOUNT_ACTIONS = ROOT / "catalog/global/flowaccount/actions.json"
FLOWACCOUNT_MANIFEST = ROOT / "catalog/global/flowaccount/sandbox-execution-manifest.json"
FLOWACCOUNT_SEMANTICS = ROOT / "catalog/global/flowaccount/semantic-contracts.json"
NOW = datetime(2026, 7, 14, 9, 30, tzinfo=UTC)


class PeerStream:
    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return ("93.184.216.34", 443) if name == "server_addr" else None


def provider_response(
    request: httpx.Request,
    status: int,
    payload: object,
) -> httpx.Response:
    return httpx.Response(
        status,
        request=request,
        json=payload,
        extensions={"network_stream": PeerStream()},
    )


@pytest.fixture(autouse=True)
def flowaccount_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )


@pytest.fixture(scope="module")
def flowaccount_actions():
    return tuple(load_actions(FLOWACCOUNT_ACTIONS))


@pytest.fixture(scope="module")
def sandbox_manifest() -> SandboxExecutionManifest:
    return load_sandbox_execution_manifest(FLOWACCOUNT_MANIFEST, FLOWACCOUNT_ACTIONS)


@pytest.fixture(scope="module")
def flowaccount_semantics(flowaccount_actions):
    return load_semantic_contracts(FLOWACCOUNT_SEMANTICS, flowaccount_actions)


class CredentialSnapshotSpy:
    def __init__(self, *, configured: bool = True) -> None:
        self.configured = configured
        self.snapshot_calls = 0
        self.loaded: dict[str, str] | None = None

    def snapshot(self, connector_id: str, environment: str, fields: object) -> CredentialSnapshot:
        del fields
        self.snapshot_calls += 1
        credentials = (
            {"client_id": "sandbox-client", "client_secret": "sandbox-secret"}
            if self.configured
            else {}
        )
        self.loaded = credentials
        required = ("client_id", "client_secret")
        present = required if self.configured else ()
        return CredentialSnapshot(
            credentials=credentials,
            status=CredentialStatus(
                connector_id=connector_id,
                environment=environment,
                required_fields=required,
                present_fields=present,
                missing_fields=() if self.configured else required,
                configured=self.configured,
            ),
            generation=b"g" * 32,
        )


class ForbiddenCredentialStore:
    def snapshot(self, *_args: object, **_kwargs: object) -> CredentialSnapshot:
        raise AssertionError("dry_run_read_credentials")


def validation_config(company_name: str | None = "Example Books") -> RepositoryConfig:
    return RepositoryConfig(
        validations={
            "flowaccount": {
                "sandbox": {
                    "connector_id": "flowaccount",
                    "environment": "sandbox",
                    "company_name": company_name,
                    "validation_state": "connected",
                    "probe_action": "GET /company/info",
                    "validated_at": "2026-07-14T09:00:00+07:00",
                }
            }
        }
    )


def runtime_for(
    context: RepositoryContext,
    actions: tuple[Any, ...],
    *,
    credentials: object,
    repository_config: RepositoryConfig,
) -> SimpleNamespace:
    catalog = LocalActionCatalog(actions)
    drivers = DriverRegistry()
    drivers.register(FlowAccountDriver())
    executor = ERPExecutor(
        context=context,
        repository_config=repository_config,
        catalog=catalog,
        drivers=drivers,
        credentials=credentials,
        request_store=LocalRequestStore(context),
        audit_ledger=AuditLedger(context.audit_dir / "audit.jsonl"),
        roots=(context.root,),
    )
    return SimpleNamespace(
        repository=context,
        repository_config=repository_config,
        catalog=catalog,
        drivers=drivers,
        credentials=credentials,
        executor=executor,
        audit=executor.audit_ledger,
        supabase=SimpleNamespace(
            publish=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("qualification_called_supabase")
            )
        ),
    )


async def no_sleep(_delay: float) -> None:
    return None


def make_runner(
    runtime: SimpleNamespace,
    manifest: SandboxExecutionManifest,
    actions: tuple[Any, ...],
    semantics: dict[tuple[str, str], Any],
    *,
    transport: httpx.AsyncBaseTransport,
) -> FlowAccountQualificationRunner:
    return FlowAccountQualificationRunner(
        runtime,
        manifest,
        actions=actions,
        semantics=semantics,
        transport=transport,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
    )


def alternate_identity(action: CatalogAction) -> CatalogAction:
    values = action.model_dump(mode="python")
    values["operation_id"] = f"{action.operation_id}_alternate"
    candidate = CatalogAction.model_validate(values)
    values["action_id"] = build_action_id(candidate)
    candidate = CatalogAction.model_validate(values)
    values["version_id"] = build_version_id(candidate)
    return revalidate_catalog_action(CatalogAction.model_validate(values))


def assert_early_preflight_failure(
    report: Any,
    actions: tuple[CatalogAction, ...],
    *,
    forbidden: tuple[str, ...] = (),
) -> None:
    expected = {(action.action_id, action.version_id) for action in actions}
    actual = {(record.action_id, record.version_id) for record in report.records}
    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert actual == expected
    assert {record.validation_status for record in report.records} == {
        ValidationStatus.BLOCKED_MISSING_PREREQUISITE
    }
    assert {record.evidence_level for record in report.records} == {EvidenceLevel.DOCUMENTED}
    assert {record.execution_eligibility for record in report.records} == {
        ExecutionEligibility.BLOCKED
    }
    assert report.http_attempts == 0
    assert report.mutation_attempts == 0
    serialized = json.dumps(report.public_dict(), ensure_ascii=False, sort_keys=True)
    for value in forbidden:
        assert value not in serialized


def test_qualification_limits_are_immutable_and_capped() -> None:
    limits = QualificationLimits()

    assert limits.requests_per_second == 2.0
    assert limits.max_read_pages == 3
    assert limits.max_read_attempts == 2
    assert limits.max_mutation_attempts == 1
    assert limits.max_total_requests == 40
    with pytest.raises(ValidationError):
        limits.max_total_requests = 41  # type: ignore[misc]
    with pytest.raises(ValidationError):
        QualificationLimits(requests_per_second=2.01)
    with pytest.raises(ValidationError):
        QualificationLimits(max_read_pages=4)
    with pytest.raises(ValidationError):
        QualificationLimits(max_read_attempts=3)
    with pytest.raises(ValidationError):
        QualificationLimits(max_mutation_attempts=2)
    with pytest.raises(ValidationError):
        QualificationLimits(max_total_requests=41)


@pytest.mark.asyncio
async def test_runtime_factory_failure_terminalizes_all_frozen_identities(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    calls: list[str] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=ForbiddenCredentialStore(),
        repository_config=RepositoryConfig(),
    )

    def fail_runtime_factory() -> Any:
        raise RuntimeError(
            "runtime-factory-sensitive https://provider.invalid /Users/private/runtime.json"
        )

    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(
            lambda request: calls.append(str(request.url)) or provider_response(request, 500, {})
        ),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
        runtime_factory=fail_runtime_factory,
    )

    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=False, dry_run=False)
    )

    assert_early_preflight_failure(
        report,
        flowaccount_actions,
        forbidden=("runtime-factory-sensitive", "provider.invalid", "/Users/private"),
    )
    assert calls == []


@pytest.mark.asyncio
async def test_run_store_initialization_failure_terminalizes_all_frozen_identities(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=ForbiddenCredentialStore(),
        repository_config=RepositoryConfig(),
    )

    def fail_run_store(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError(
            "run-store-sensitive https://state.invalid /Users/private/state.json"
        )

    monkeypatch.setattr(
        "mercury_tools.qualification.flowaccount.QualificationRunStore",
        fail_run_store,
    )
    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(
            lambda request: calls.append(str(request.url)) or provider_response(request, 500, {})
        ),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
    )

    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=False, dry_run=False)
    )

    assert_early_preflight_failure(
        report,
        flowaccount_actions,
        forbidden=("run-store-sensitive", "state.invalid", "/Users/private"),
    )
    assert calls == []


@pytest.mark.asyncio
async def test_invalid_clock_terminalizes_in_sticky_dry_run_without_persistent_state(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    calls: list[str] = []
    validation_root = repository_context.mercury_dir / "validation"
    state_before = tuple(validation_root.rglob("*")) if validation_root.exists() else ()
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=ForbiddenCredentialStore(),
        repository_config=RepositoryConfig(),
    )
    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(
            lambda request: calls.append(str(request.url)) or provider_response(request, 500, {})
        ),
        clock=lambda: datetime(2026, 7, 14, 9, 30),
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
    )

    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=True, dry_run=True),
        dry_run=False,
    )

    assert_early_preflight_failure(report, flowaccount_actions)
    state_after = tuple(validation_root.rglob("*")) if validation_root.exists() else ()
    assert state_after == state_before
    assert calls == []


@pytest.mark.asyncio
async def test_dry_run_validates_all_contracts_without_credentials_network_or_supabase(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    network_calls: list[str] = []
    validation_root = repository_context.mercury_dir / "validation"
    state_before = tuple(validation_root.rglob("*")) if validation_root.exists() else ()
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=ForbiddenCredentialStore(),
        repository_config=RepositoryConfig(),
    )
    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(
            lambda request: (
                network_calls.append(str(request.url)) or provider_response(request, 500, {})
            )
        ),
    )

    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=True, dry_run=True),
        dry_run=False,
    )

    identities = [(record.action_id, record.version_id) for record in report.records]
    assert report.run_state is QualificationRunState.COMPLETED
    assert len(identities) == 190
    assert len(set(identities)) == 190
    assert identities == sorted(identities)
    assert network_calls == []
    assert all(
        record.validation_status
        in {
            ValidationStatus.CONTRACT_VALIDATED,
            ValidationStatus.BLOCKED_EXTERNAL_EFFECT,
            ValidationStatus.UNSUPPORTED_BY_SANDBOX,
        }
        for record in report.records
    )
    assert all(record.latency_ms is None for record in report.records)
    assert report.public_dict()["total"] == 190
    state_after = tuple(validation_root.rglob("*")) if validation_root.exists() else ()
    assert state_after == state_before


@pytest.mark.asyncio
async def test_arbitrary_same_size_action_set_is_never_used_for_terminal_coverage(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    replacement = alternate_identity(flowaccount_actions[0])
    untrusted_actions = (replacement, *flowaccount_actions[1:])
    runtime = runtime_for(
        repository_context,
        untrusted_actions,
        credentials=ForbiddenCredentialStore(),
        repository_config=RepositoryConfig(),
    )
    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=untrusted_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError(str(request.url)))
        ),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
    )

    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=False, dry_run=True)
    )

    expected = {(action.action_id, action.version_id) for action in flowaccount_actions}
    actual = {(record.action_id, record.version_id) for record in report.records}
    assert report.run_state is QualificationRunState.FAILED
    assert actual == expected
    assert (replacement.action_id, replacement.version_id) not in actual
    assert {record.validation_status for record in report.records} == {
        ValidationStatus.BLOCKED_MISSING_PREREQUISITE
    }
    assert {record.evidence_level for record in report.records} == {EvidenceLevel.DOCUMENTED}
    assert {record.execution_eligibility for record in report.records} == {
        ExecutionEligibility.BLOCKED
    }


@pytest.mark.parametrize("broken_sidecar", ["manifest", "semantics"])
@pytest.mark.asyncio
async def test_sidecar_failure_is_blocked_without_synthetic_validated_evidence(
    broken_sidecar: str,
    tmp_path: Path,
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    broken = tmp_path / f"{broken_sidecar}.json"
    broken.write_text("{}\n", encoding="utf-8")
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=ForbiddenCredentialStore(),
        repository_config=RepositoryConfig(),
    )
    runner = FlowAccountQualificationRunner(
        runtime,
        None if broken_sidecar == "manifest" else sandbox_manifest,
        actions=flowaccount_actions,
        semantics=None if broken_sidecar == "semantics" else flowaccount_semantics,
        catalog_path=FLOWACCOUNT_ACTIONS,
        manifest_path=broken if broken_sidecar == "manifest" else FLOWACCOUNT_MANIFEST,
        semantics_path=broken if broken_sidecar == "semantics" else FLOWACCOUNT_SEMANTICS,
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError(str(request.url)))
        ),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
    )

    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=False, dry_run=True)
    )

    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert {record.validation_status for record in report.records} == {
        ValidationStatus.BLOCKED_MISSING_PREREQUISITE
    }
    assert {record.evidence_level for record in report.records} == {EvidenceLevel.DOCUMENTED}
    assert {record.execution_eligibility for record in report.records} == {
        ExecutionEligibility.BLOCKED
    }


@pytest.mark.asyncio
async def test_live_run_uses_one_snapshot_one_probe_and_four_canonical_reads(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    audited_results: list[ConnectorResult] = []
    credentials = CredentialSnapshotSpy()
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=credentials,
        repository_config=validation_config(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if len(calls) == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(
            request,
            200,
            {
                "status": True,
                "data": [
                    {
                        "providerRecordId": "provider-991234567",
                        "rawPayloadMarker": "raw-provider-payload-88224466",
                    }
                ],
            },
        )

    original_audit_result = runtime.executor._audit_result

    def capture_audit_result(**kwargs: Any) -> str:
        audited_results.append(kwargs["result"])
        return original_audit_result(**kwargs)

    monkeypatch.setattr(runtime.executor, "_audit_result", capture_audit_result)

    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(handler),
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    live = [
        record
        for record in report.records
        if record.validation_status is ValidationStatus.LIVE_SUCCESS
    ]
    assert report.run_state is QualificationRunState.COMPLETED
    assert len(report.records) == 190
    assert {(record.action_id, record.version_id) for record in live} == LIVE_READS
    assert credentials.snapshot_calls == 1
    assert credentials.loaded == {}
    assert calls[0:2] == [
        ("POST", "/test/token"),
        ("GET", "/test/company/info"),
    ]
    assert len(calls) == 2 + len(LIVE_READS)
    assert all(method == "GET" for method, _path in calls[2:])
    assert len(audited_results) == len(LIVE_READS)
    assert all(result.data is None for result in audited_results)
    public_json = json.dumps(report.public_dict(), ensure_ascii=False, sort_keys=True)
    state_json = runner.run_store.state_path.read_text(encoding="utf-8")
    audit_json = (repository_context.audit_dir / "audit.jsonl").read_text(encoding="utf-8")
    for persisted in (public_json, state_json, audit_json):
        assert "issued-sandbox-token" not in persisted
        assert "provider-991234567" not in persisted
        assert "raw-provider-payload-88224466" not in persisted
        assert "Example Books" not in persisted
        assert "company_label_sha256" not in persisted


@pytest.mark.parametrize(
    ("failure_phase", "expected_attempts"),
    [
        ("processing", 3),
        ("audit", 3),
        ("reporting", 6),
    ],
)
@pytest.mark.asyncio
async def test_post_dispatch_failure_preserves_attempts_and_quarantines_safely(
    failure_phase: str,
    expected_attempts: int,
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )
    failure_detail = (
        "post-dispatch-sensitive https://post-dispatch.invalid "
        "/Users/private/provider-result.json Secret Example Company"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if len(calls) == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(
            request,
            200,
            {
                "status": True,
                "data": [
                    {
                        "providerRecordId": "provider-991234567",
                        "rawPayloadMarker": "raw-provider-payload-88224466",
                    }
                ],
            },
        )

    def fail_post_dispatch(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError(failure_detail)

    if failure_phase == "processing":
        monkeypatch.setattr(
            "mercury_tools.qualification.flowaccount.extract_response_shape",
            fail_post_dispatch,
        )
    elif failure_phase == "audit":
        monkeypatch.setattr(runtime.executor, "_audit_result", fail_post_dispatch)
    else:
        monkeypatch.setattr(
            "mercury_tools.qualification.flowaccount.build_coverage_report",
            fail_post_dispatch,
        )

    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(handler),
    )

    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=False)
    )

    expected_identities = {
        (action.action_id, action.version_id) for action in flowaccount_actions
    }
    actual_identities = {
        (record.action_id, record.version_id) for record in report.records
    }
    unknown_records = [
        record
        for record in report.records
        if record.validation_status is ValidationStatus.OUTCOME_UNKNOWN
    ]
    assert report.run_state is QualificationRunState.QUARANTINED
    assert runner.run_store.state is QualificationRunState.QUARANTINED
    assert runner.run_store.publication_allowed is False
    assert len(report.records) == 190
    assert actual_identities == expected_identities
    assert report.http_attempts == runner.request_count == expected_attempts
    assert report.mutation_attempts == 0
    assert len(calls) == expected_attempts
    assert unknown_records
    assert all(
        record.evidence_level is EvidenceLevel.SANDBOX_OBSERVED
        and record.execution_eligibility is ExecutionEligibility.BLOCKED
        and record.response_shape == {}
        for record in unknown_records
    )

    public_json = json.dumps(report.public_dict(), ensure_ascii=False, sort_keys=True)
    state_json = runner.run_store.state_path.read_text(encoding="utf-8")
    audit_path = repository_context.audit_dir / "audit.jsonl"
    audit_json = audit_path.read_text(encoding="utf-8") if audit_path.exists() else ""
    for persisted in (public_json, state_json, audit_json):
        assert "post-dispatch-sensitive" not in persisted
        assert "post-dispatch.invalid" not in persisted
        assert "/Users/private" not in persisted
        assert "Secret Example Company" not in persisted
        assert "issued-sandbox-token" not in persisted
        assert "provider-991234567" not in persisted
        assert "raw-provider-payload-88224466" not in persisted
        assert "Example Books" not in persisted


@pytest.mark.asyncio
async def test_missing_expected_local_tenant_still_terminalizes_all_actions_without_dispatch(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    calls: list[tuple[str, str]] = []
    credentials = CredentialSnapshotSpy()
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=credentials,
        repository_config=RepositoryConfig(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        payload = (
            {"access_token": "issued-sandbox-token"}
            if request.url.path == "/test/token"
            else {"companyName": "Example Books"}
        )
        return provider_response(request, 200, payload)

    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(handler),
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert len({(record.action_id, record.version_id) for record in report.records}) == 190
    assert calls == [
        ("POST", "/test/token"),
        ("GET", "/test/company/info"),
    ]
    assert credentials.loaded == {}
    assert ValidationStatus.BLOCKED_MISSING_PREREQUISITE in {
        record.validation_status for record in report.records
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requests_per_second", True),
        ("max_read_pages", True),
        ("max_read_attempts", True),
        ("max_mutation_attempts", True),
        ("max_total_requests", True),
    ],
)
def test_qualification_limits_reject_boolean_numeric_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        QualificationLimits(**{field: value})


@pytest.mark.asyncio
async def test_invalid_origin_terminalizes_all_before_credential_snapshot_or_network(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = CredentialSnapshotSpy()
    calls: list[str] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=credentials,
        repository_config=validation_config(),
    )
    monkeypatch.setitem(
        FlowAccountDriver.BASE_URLS,
        "sandbox",
        "https://openapi.flowaccount.com/v1",
    )
    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(
            lambda request: calls.append(str(request.url)) or provider_response(request, 500, {})
        ),
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert credentials.snapshot_calls == 0
    assert calls == []


@pytest.mark.asyncio
async def test_actual_tenant_mismatch_blocks_actions_and_never_exposes_labels_or_hashes(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    calls: list[tuple[str, str]] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config("Expected Ledger"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        payload = (
            {"access_token": "issued-sandbox-token"}
            if request.url.path == "/test/token"
            else {"companyName": "Unexpected Ledger"}
        )
        return provider_response(request, 200, payload)

    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(handler),
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert calls == [
        ("POST", "/test/token"),
        ("GET", "/test/company/info"),
    ]
    public_json = json.dumps(report.public_dict(), ensure_ascii=False, sort_keys=True)
    assert "Expected Ledger" not in public_json
    assert "Unexpected Ledger" not in public_json
    assert "company_label_sha256" not in public_json


@pytest.mark.asyncio
async def test_http_429_stops_remaining_reads_and_sandbox_writes_never_widens_policy(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    calls: list[tuple[str, str]] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if len(calls) == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(request, 429, {"status": False})

    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(handler),
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=True))

    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert calls[0:2] == [
        ("POST", "/test/token"),
        ("GET", "/test/company/info"),
    ]
    assert len(calls) == 3
    assert all(method == "GET" for method, _path in calls[2:])
    assert runner.request_count == 3
    assert ValidationStatus.LIVE_FAILED in {record.validation_status for record in report.records}


class UnknownOutcomeFlowAccountDriver(FlowAccountDriver):
    def interpret_response(
        self,
        *,
        action: Any,
        response: httpx.Response,
        dispatched: bool,
    ):
        result = super().interpret_response(
            action=action,
            response=response,
            dispatched=dispatched,
        )
        if action.path_template == "/contacts":
            return type(result)(
                status="outcome_unknown",
                http_status=result.http_status,
                data=result.data,
                summary="manual_reconciliation_required",
                dispatched=result.dispatched,
            )
        return result


@pytest.mark.asyncio
async def test_unknown_outcome_quarantines_and_blocks_remaining_action_dispatch(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    calls: list[tuple[str, str]] = []
    credentials = CredentialSnapshotSpy()
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=credentials,
        repository_config=validation_config(),
    )
    runtime.drivers = DriverRegistry()
    runtime.drivers.register(UnknownOutcomeFlowAccountDriver())
    runtime.executor.drivers = runtime.drivers

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if len(calls) == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(request, 200, {"status": True, "data": []})

    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(handler),
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.QUARANTINED
    assert len(report.records) == 190
    assert ValidationStatus.OUTCOME_UNKNOWN in {
        record.validation_status for record in report.records
    }
    assert len(calls) < 2 + len(LIVE_READS)
    assert runner.run_store.publication_allowed is False


class FailingCleanupAdapter:
    def __init__(self) -> None:
        self.provider_ids: list[str] = []

    async def cleanup(self, fixture: FixtureCleanupTarget) -> CleanupOutcome:
        self.provider_ids.append(fixture.provider_id)
        return CleanupOutcome.FAILED


class SuccessfulCleanupAdapter:
    def __init__(self) -> None:
        self.provider_ids: list[str] = []

    async def cleanup(self, fixture: FixtureCleanupTarget) -> CleanupOutcome:
        self.provider_ids.append(fixture.provider_id)
        return CleanupOutcome.CLEANED


@pytest.mark.asyncio
async def test_cleanup_failure_quarantines_without_persisting_or_reporting_provider_id(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    run_id = "run_01J00000000000000000000000"
    fixture_registry = FixtureRegistry(run_id=run_id)
    fixture_registry.register(
        provider_id="provider-sensitive-991234567",
        action_ref=(flowaccount_actions[0].action_id, flowaccount_actions[0].version_id),
        cleanup_action_ref=(flowaccount_actions[1].action_id, flowaccount_actions[1].version_id),
    )
    cleanup = FailingCleanupAdapter()
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if call_count == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(request, 200, {"status": True, "data": []})

    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(handler),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
        run_id=run_id,
        fixture_registry=fixture_registry,
        cleanup_adapter=cleanup,
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert cleanup.provider_ids == ["provider-sensitive-991234567"]
    assert report.run_state is QualificationRunState.QUARANTINED
    assert runner.run_store.publication_allowed is False
    assert len(report.records) == 190
    assert runner.request_count == 2 + len(LIVE_READS) + 1
    assert report.http_attempts == runner.request_count
    assert report.mutation_attempts == 1
    public_json = json.dumps(report.public_dict(), ensure_ascii=False, sort_keys=True)
    state_json = runner.run_store.state_path.read_text(encoding="utf-8")
    audit_json = (repository_context.audit_dir / "audit.jsonl").read_text(encoding="utf-8")
    assert "provider-sensitive-991234567" not in public_json
    assert "provider-sensitive-991234567" not in state_json
    assert "provider-sensitive-991234567" not in audit_json


@pytest.mark.asyncio
async def test_cleanup_uses_shared_rate_total_and_mutation_accounting(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    run_id = "run_01J00000000000000000000001"
    fixture_registry = FixtureRegistry(run_id=run_id)
    fixture_registry.register(
        provider_id="provider-cleanup-991234567",
        action_ref=(flowaccount_actions[0].action_id, flowaccount_actions[0].version_id),
        cleanup_action_ref=(flowaccount_actions[1].action_id, flowaccount_actions[1].version_id),
    )
    cleanup = SuccessfulCleanupAdapter()
    sleeps: list[float] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if call_count == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(request, 200, {"status": True, "data": []})

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(handler),
        limits=QualificationLimits(max_total_requests=7),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=record_sleep,
        run_id=run_id,
        fixture_registry=fixture_registry,
        cleanup_adapter=cleanup,
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.COMPLETED
    assert cleanup.provider_ids == ["provider-cleanup-991234567"]
    assert runner.request_count == 7
    assert report.http_attempts == 7
    assert report.mutation_attempts == 1
    assert sleeps == [0.5] * 6


@pytest.mark.asyncio
async def test_total_request_budget_blocks_cleanup_before_provider_adapter(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    run_id = "run_01J00000000000000000000002"
    fixture_registry = FixtureRegistry(run_id=run_id)
    fixture_registry.register(
        provider_id="provider-cleanup-88224466",
        action_ref=(flowaccount_actions[0].action_id, flowaccount_actions[0].version_id),
        cleanup_action_ref=(flowaccount_actions[1].action_id, flowaccount_actions[1].version_id),
    )
    cleanup = SuccessfulCleanupAdapter()
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if call_count == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(request, 200, {"status": True, "data": []})

    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(handler),
        limits=QualificationLimits(max_total_requests=6),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
        run_id=run_id,
        fixture_registry=fixture_registry,
        cleanup_adapter=cleanup,
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.QUARANTINED
    assert cleanup.provider_ids == []
    assert runner.request_count == 6
    assert report.http_attempts == 6
    assert report.mutation_attempts == 0


@pytest.mark.asyncio
async def test_missing_runtime_driver_is_a_terminal_preflight_failure(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )
    runtime.drivers = DriverRegistry()
    runtime.executor.drivers = runtime.drivers
    runner = make_runner(
        runtime,
        sandbox_manifest,
        flowaccount_actions,
        flowaccount_semantics,
        transport=httpx.MockTransport(lambda request: provider_response(request, 500, {})),
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert len({(record.action_id, record.version_id) for record in report.records}) == 190


@pytest.mark.asyncio
async def test_runner_uses_runtime_transport_when_constructor_transport_is_omitted(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if len(calls) == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(request, 200, {"status": True, "data": []})

    runtime.transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "mercury_tools.qualification.flowaccount.httpx.AsyncHTTPTransport",
        lambda: (_ for _ in ()).throw(AssertionError("default_transport_created")),
    )
    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=no_sleep,
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.COMPLETED
    assert calls[0:2] == ["/test/token", "/test/company/info"]
    assert len(calls) == 2 + len(LIVE_READS)


@pytest.mark.asyncio
async def test_factory_approval_dry_run_is_sticky_before_source_repository_state_selection(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    catalog_target = source_root / "catalog" / "global" / "flowaccount"
    catalog_target.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "catalog" / "global" / "flowaccount", catalog_target)
    gitignore = source_root / ".gitignore"
    gitignore.write_text("keep-this-line\n", encoding="utf-8")

    runner = create_flowaccount_qualification_runner(source_root, dry_run=False)
    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=False, dry_run=True),
        dry_run=False,
    )

    assert report.run_state is QualificationRunState.COMPLETED
    assert len(report.records) == 190
    assert gitignore.read_text(encoding="utf-8") == "keep-this-line\n"
    assert not (source_root / ".mercury").exists()


@pytest.mark.asyncio
async def test_requested_catalog_failure_uses_frozen_canonical_terminal_identities(
    tmp_path: Path,
    flowaccount_actions,
) -> None:
    source_root = tmp_path / "source"
    catalog_target = source_root / "catalog" / "global" / "flowaccount"
    catalog_target.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "catalog" / "global" / "flowaccount", catalog_target)
    (catalog_target / "actions.json").write_text("[]\n", encoding="utf-8")

    runner = create_flowaccount_qualification_runner(source_root, dry_run=True)
    report = await runner.qualify_all(
        approval=SandboxRunApproval(reads=True, writes=False, dry_run=False),
        dry_run=False,
    )

    expected = {(action.action_id, action.version_id) for action in flowaccount_actions}
    actual = {(record.action_id, record.version_id) for record in report.records}
    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert actual == expected
    assert {record.validation_status for record in report.records} == {
        ValidationStatus.BLOCKED_MISSING_PREREQUISITE
    }
    assert {record.evidence_level for record in report.records} == {EvidenceLevel.DOCUMENTED}
    assert not (source_root / ".mercury").exists()


@pytest.mark.asyncio
async def test_total_request_budget_stops_dispatch_and_is_reported_without_urls(
    repository_context: RepositoryContext,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
    flowaccount_semantics,
) -> None:
    calls: list[tuple[str, str]] = []
    sleeps: list[float] = []
    runtime = runtime_for(
        repository_context,
        flowaccount_actions,
        credentials=CredentialSnapshotSpy(),
        repository_config=validation_config(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-sandbox-token"})
        if len(calls) == 2:
            return provider_response(request, 200, {"companyName": "Example Books"})
        return provider_response(request, 200, {"status": True, "data": []})

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    runner = FlowAccountQualificationRunner(
        runtime,
        sandbox_manifest,
        actions=flowaccount_actions,
        semantics=flowaccount_semantics,
        transport=httpx.MockTransport(handler),
        limits=QualificationLimits(max_total_requests=3),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        sleeper=record_sleep,
    )

    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=True))

    assert report.run_state is QualificationRunState.FAILED
    assert len(report.records) == 190
    assert len(calls) == 3
    assert runner.request_count == 3
    assert all(method == "GET" for method, _path in calls[2:])
    assert sleeps == [0.5, 0.5]
    payload = report.public_dict()
    assert payload["http_attempts"] == 3
    assert payload["mutation_attempts"] == 0
    public_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "/test/" not in public_json
    assert "openapi.flowaccount.com" not in public_json
