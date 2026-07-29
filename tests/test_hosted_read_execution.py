from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderOperationClass,
    ProviderSchemaChanged,
    ProviderStatusClass,
    ProviderUnavailable,
    QualifiedCapabilityBinding,
)
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

WORKSPACE_ID = UUID("12345678-1234-5678-9234-567812345678")
CONNECTION_ID = UUID("87654321-4321-8765-4321-876543218765")
TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


class InvoiceReadInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    invoice_reference: str


def _qualification(
    *,
    capability_id: str = "documents.invoice.get",
    capability_version: str | None = None,
) -> ProviderMCPQualification:
    definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="PRIVATE_DOWNSTREAM_GET_INVOICE",
        normalized_capability=capability_id,
        input_schema={
            "type": "object",
            "properties": {"invoice_reference": {"type": "string", "minLength": 1}},
            "required": ["invoice_reference"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "document_number": {"type": "string", "minLength": 1},
                "invoice_id": {"type": "string", "minLength": 1},
                "contact_email": {"type": "string", "minLength": 1},
                "tax_id": {"type": "string", "minLength": 1},
            },
            "required": ["document_number", "invoice_id", "contact_email", "tax_id"],
            "additionalProperties": False,
        },
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    return definition.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "capability_version_sha256": capability_version or definition.capability_version_sha256,
            "company_sha256": "b" * 64,
            "evidence_revision_sha256": "c" * 64,
            "qualification_evidence_uri": (
                "catalog://global/flowaccount/qualifications/"
                f"{capability_version or definition.capability_version_sha256}-{'c' * 64}.json"
            ),
            "evidence_evaluated_at": NOW,
            "evidence_expires_at": NOW + timedelta(days=1),
        }
    )


def _connection() -> ProviderConnection:
    return ProviderConnection(
        id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        provider_account_id="private-company-id",
        account_display_name="Example Company",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read",),
        readiness=ConnectionReadiness.READY,
        revision=1,
        last_validated_at=NOW,
        credential_envelope_ids=(UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),),
        created_at=NOW,
        updated_at=NOW,
    )


def _principal() -> MercuryPrincipal:
    return MercuryPrincipal(subject=USER_ID, client_id="test-client", scopes=frozenset())


class FakeConnectionStore:
    def __init__(self, connection: ProviderConnection) -> None:
        self.connection = connection
        self.calls: list[dict[str, object]] = []

    def load_connection(self, **kwargs: object) -> ProviderConnection:
        self.calls.append(kwargs)
        return self.connection


class FakeResolver:
    def __init__(self, qualification: ProviderMCPQualification) -> None:
        self.qualification = qualification
        self.calls: list[tuple[UUID, str, str]] = []

    async def bind_exact_for_connection(
        self,
        connection: ProviderConnection,
        *,
        capability_id: str,
        capability_version: str,
        deadline: object,
    ) -> tuple[ProviderMCPQualification, QualifiedCapabilityBinding]:
        self.calls.append((connection.id, capability_id, capability_version))
        if capability_version != self.qualification.capability_version_sha256:
            from mercury_tools.qualification.provider_mcp import QualificationGateError

            raise QualificationGateError("capability_unavailable")
        return (
            self.qualification,
            QualifiedCapabilityBinding(
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                normalized_capability=capability_id,
                provider_tool="PRIVATE_DOWNSTREAM_GET_INVOICE",
                operation_class=ProviderOperationClass.READ,
                qualification_hash="c" * 64,
            ),
        )


class FakeDriver:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[
            tuple[ProviderConnection, QualifiedCapabilityBinding, BaseModel, object]
        ] = []

    async def call(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        inputs: BaseModel,
        operation_id: object,
        *,
        deadline: object | None = None,
    ) -> ProviderCallResult:
        self.calls.append((connection, binding, inputs, deadline))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, ProviderCallResult)
        return outcome


class FakeRegistry:
    def __init__(self, driver: FakeDriver) -> None:
        self.driver = driver

    def get(self, provider: ProviderId) -> FakeDriver:
        assert provider is ProviderId.FLOWACCOUNT
        return self.driver


class HostedReadOnlyDriver(FakeDriver):
    async def call(self, *_args: object, **_kwargs: object) -> ProviderCallResult:
        raise AssertionError("PEAK V1 reads must use the hosted-read entrypoint")

    async def call_hosted_read(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        inputs: BaseModel,
        operation_id: object,
        *,
        deadline: object | None = None,
    ) -> ProviderCallResult:
        return await super().call(
            connection,
            binding,
            inputs,
            operation_id,
            deadline=deadline,
        )


def _runtime(qualification: ProviderMCPQualification, driver: FakeDriver):
    connection_store = FakeConnectionStore(_connection())
    return SimpleNamespace(
        connection_store=connection_store,
        qualification_resolver=FakeResolver(qualification),
        registry=FakeRegistry(driver),
    )


def _membership(_principal_value: MercuryPrincipal, workspace_id: UUID) -> WorkspaceMembership:
    assert workspace_id == WORKSPACE_ID
    return WorkspaceMembership(
        tenant_id=TENANT_ID,
        tenant_display_name="Example Tenant",
        workspace_id=WORKSPACE_ID,
        workspace_display_name="Example Workspace",
        role=WorkspaceRole.MEMBER,
    )


@pytest.mark.asyncio
async def test_hosted_read_requires_the_exact_enabled_capability_version() -> None:
    from mercury_tools.execution.hosted.read_service import HostedReadService
    from mercury_tools.qualification.provider_mcp import QualificationGateError

    qualification = _qualification()
    driver = FakeDriver([])
    runtime = _runtime(qualification, driver)
    service = HostedReadService(
        runtime_factory=lambda: runtime,
        membership_resolver=_membership,
    )

    with pytest.raises(QualificationGateError, match="^capability_unavailable$"):
        await service.execute(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            "f" * 64,
            InvoiceReadInputs(invoice_reference="INV-001"),
        )

    assert runtime.connection_store.calls == [
        {
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "auth_user_id": USER_ID,
            "connection_id": CONNECTION_ID,
        }
    ]
    assert driver.calls == []


@pytest.mark.asyncio
async def test_hosted_read_retries_only_pre_dispatch_safe_read_failures_within_deadline() -> None:
    from mercury_tools.execution.hosted.read_service import HostedReadService

    qualification = _qualification()
    driver = FakeDriver(
        [
            ProviderUnavailable(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ),
            ProviderCallResult(
                provider=ProviderId.FLOWACCOUNT,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data={
                    "document_number": "INV-001",
                    "invoice_id": "provider-invoice-id",
                    "contact_email": "person@example.com",
                    "tax_id": "1234567890123",
                },
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            ),
        ]
    )
    runtime = _runtime(qualification, driver)
    pauses: list[float] = []
    audits: list[dict[str, object]] = []
    service = HostedReadService(
        runtime_factory=lambda: runtime,
        membership_resolver=_membership,
        sleep=lambda seconds: pauses.append(seconds),
        audit_recorder=audits.append,
    )

    envelope = await service.execute(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        InvoiceReadInputs(invoice_reference="INV-001"),
    )

    assert len(driver.calls) == 2
    assert pauses == [0.05]
    assert envelope.capability_id == qualification.normalized_capability
    assert envelope.capability_version == qualification.capability_version_sha256
    assert envelope.data == {
        "document_number": "INV-001",
        "invoice_id": "provider-invoice-id",
    }
    assert len(audits) == 1
    audit_text = str(audits[0])
    assert "private-company-id" not in audit_text
    assert "provider-invoice-id" not in audit_text
    assert "person@example.com" not in audit_text
    assert "1234567890123" not in audit_text
    assert (
        audits[0]["input"]["workspace_id_sha256"]
        == hashlib.sha256(str(WORKSPACE_ID).encode("utf-8")).hexdigest()
    )


@pytest.mark.asyncio
async def test_hosted_read_never_retries_a_possibly_dispatched_failure() -> None:
    from mercury_tools.execution.hosted.read_service import HostedReadService

    qualification = _qualification()
    driver = FakeDriver(
        [
            ProviderUnavailable(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        ]
    )
    runtime = _runtime(qualification, driver)
    service = HostedReadService(
        runtime_factory=lambda: runtime,
        membership_resolver=_membership,
        sleep=lambda _seconds: pytest.fail("possibly dispatched reads must not retry"),
    )

    with pytest.raises(ProviderUnavailable):
        await service.execute(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            InvoiceReadInputs(invoice_reference="INV-001"),
        )

    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_hosted_read_uses_the_peak_v1_entrypoint_when_available() -> None:
    from mercury_tools.execution.hosted.read_service import HostedReadService

    qualification = _qualification()
    driver = HostedReadOnlyDriver(
        [
            ProviderCallResult(
                provider=ProviderId.FLOWACCOUNT,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data={
                    "document_number": "INV-001",
                    "invoice_id": "provider-invoice-id",
                    "contact_email": "person@example.com",
                    "tax_id": "1234567890123",
                },
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        ]
    )
    runtime = _runtime(qualification, driver)
    service = HostedReadService(runtime_factory=lambda: runtime, membership_resolver=_membership)

    await service.execute(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        InvoiceReadInputs(invoice_reference="INV-001"),
    )

    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_hosted_read_surfaces_schema_drift_as_version_change_without_dispatch_retry() -> None:
    from mercury_tools.execution.hosted.read_service import HostedReadService

    qualification = _qualification()
    driver = FakeDriver(
        [
            ProviderSchemaChanged(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        ]
    )
    runtime = _runtime(qualification, driver)
    service = HostedReadService(runtime_factory=lambda: runtime, membership_resolver=_membership)

    with pytest.raises(ProviderSchemaChanged):
        await service.execute(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            InvoiceReadInputs(invoice_reference="INV-001"),
        )

    assert len(driver.calls) == 1


@pytest.mark.asyncio
async def test_hosted_read_rejects_a_connection_environment_mismatch() -> None:
    from mercury_tools.execution.hosted.read_service import HostedReadService
    from mercury_tools.qualification.provider_mcp import QualificationGateError

    qualification = _qualification()
    driver = FakeDriver([])
    runtime = _runtime(qualification, driver)
    runtime.connection_store.connection = _connection().model_copy(
        update={"environment": "production"}
    )
    service = HostedReadService(runtime_factory=lambda: runtime, membership_resolver=_membership)

    with pytest.raises(QualificationGateError, match="^capability_unavailable$"):
        await service.execute(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            InvoiceReadInputs(invoice_reference="INV-001"),
        )

    assert driver.calls == []


@pytest.mark.asyncio
async def test_hosted_read_records_a_sanitized_terminal_audit_for_failure_and_cancellation() -> (
    None
):
    from mercury_tools.execution.hosted.read_service import HostedReadService

    qualification = _qualification()
    failure_audits: list[dict[str, object]] = []
    failed_runtime = _runtime(
        qualification,
        FakeDriver(
            [
                ProviderUnavailable(
                    ProviderId.FLOWACCOUNT,
                    dispatch_certainty=DispatchCertainty.DISPATCHED,
                )
            ]
        ),
    )
    failed_service = HostedReadService(
        runtime_factory=lambda: failed_runtime,
        membership_resolver=_membership,
        audit_recorder=failure_audits.append,
    )
    with pytest.raises(ProviderUnavailable):
        await failed_service.execute(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            InvoiceReadInputs(invoice_reference="INV-001"),
        )
    assert len(failure_audits) == 1
    assert failure_audits[0]["status"] == "error"
    assert failure_audits[0]["output_summary"]["status_class"] == "unavailable"

    started = asyncio.Event()

    class BlockingDriver(FakeDriver):
        async def call(self, *_args: object, **_kwargs: object) -> ProviderCallResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("cancellation must interrupt the provider wait")

    cancellation_audits: list[dict[str, object]] = []
    cancelled_runtime = _runtime(qualification, BlockingDriver([]))
    cancelled_service = HostedReadService(
        runtime_factory=lambda: cancelled_runtime,
        membership_resolver=_membership,
        audit_recorder=cancellation_audits.append,
    )
    task = asyncio.create_task(
        cancelled_service.execute(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            InvoiceReadInputs(invoice_reference="INV-001"),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(cancellation_audits) == 1
    assert cancellation_audits[0]["status"] == "cancelled"
    assert "INV-001" not in str(cancellation_audits[0])
