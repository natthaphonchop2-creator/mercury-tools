from __future__ import annotations

import asyncio
import base64
import threading
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError


def _require_attribute(module_name: str, attribute: str):
    module = __import__(module_name, fromlist=[attribute])
    assert hasattr(module, attribute), f"{module_name}.{attribute} is not implemented"
    return getattr(module, attribute)


def _published_company_health():
    lookup = _require_attribute(
        "mercury_tools.skills.catalog",
        "published_accounting_skill",
    )
    skill = lookup("company-health-check-th", "0.1.0")
    assert skill is not None
    return skill


async def _execute_read_backed_skill(
    monkeypatch: pytest.MonkeyPatch,
    *,
    skill_id: str,
    expected_capabilities: tuple[str, ...],
    observed: dict[str, object],
    failed_capability: str | None = None,
    omitted_capability: str | None = None,
    schema_drift_capability: str | None = None,
    schema_drift_once: bool = False,
    schema_persistence_started: threading.Event | None = None,
    release_schema_persistence: threading.Event | None = None,
    schema_persistence_failures: int = 0,
    retry_once_task_name: str | None = None,
    route_through_server: bool = False,
    defer_server_call: bool = False,
):
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from mercury_tools.auth.models import MercuryPrincipal
    from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
    from mercury_tools.mcp import v1_tools
    from mercury_tools.mcp.v1_schemas import RunAccountingSkillArguments
    from mercury_tools.providers.base import (
        DispatchCertainty,
        ProviderCallResult,
        ProviderOperationClass,
        ProviderSchemaChanged,
        ProviderStatusClass,
        ProviderUnavailable,
        QualifiedCapabilityBinding,
    )
    from mercury_tools.providers.manifest import load_provider_manifest
    from mercury_tools.providers.models import (
        AuthorizationMethod,
        ConnectionReadiness,
        ProviderConnection,
        ProviderId,
    )
    from mercury_tools.qualification.provider_mcp import (
        CapabilityResolution,
        QualificationGateError,
    )
    from mercury_tools.rag.models import SearchResult
    from mercury_tools.skills.catalog import published_accounting_skill
    from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    workspace_id = UUID("22222222-2222-4222-8222-222222222222")
    connection_id = UUID("55555555-5555-4555-8555-555555555555")
    tenant_id = UUID("11111111-1111-4111-8111-111111111111")
    user_id = UUID("33333333-3333-4333-8333-333333333333")
    skill = published_accounting_skill(skill_id, "0.1.0")
    assert skill is not None

    schemas = {
        "provider_profile.get": (
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"profile_status": {"const": "ready"}},
                "required": ["profile_status"],
                "additionalProperties": False,
            },
            ("/profile_status",),
        ),
        "documents.invoice.list": (
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"document_count": {"type": "integer", "minimum": 0}},
                "required": ["document_count"],
                "additionalProperties": False,
            },
            ("/document_count",),
        ),
        "documents.invoice.get": (
            {
                "type": "object",
                "properties": {"document_id": {"type": "string", "minLength": 1}},
                "required": ["document_id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"document_number": {"type": "string", "minLength": 1}},
                "required": ["document_number"],
                "additionalProperties": False,
            },
            ("/document_number",),
        ),
    }

    def qualification(capability_id: str) -> ProviderMCPQualification:
        input_schema, output_schema, public_paths = schemas[capability_id]
        definition = ProviderMCPQualification.discovered(
            provider="flowaccount",
            environment="sandbox",
            provider_tool_name=f"PRIVATE_{capability_id.replace('.', '_').upper()}",
            normalized_capability=capability_id,
            input_schema=input_schema,
            output_schema=output_schema,
            public_output_field_paths=public_paths,
            response_shape_hash="a" * 64,
            required_permissions=("documents.read",),
        )
        return definition.model_copy(
            update={
                "qualification_state": QualificationState.ENABLED,
                "company_sha256": "b" * 64,
                "evidence_revision_sha256": "c" * 64,
                "qualification_evidence_uri": (
                    "catalog://global/flowaccount/qualifications/"
                    f"{definition.capability_version_sha256}-{'c' * 64}.json"
                ),
                "evidence_evaluated_at": now,
                "evidence_expires_at": now + timedelta(days=1),
            }
        )

    qualification_capabilities = list(dict.fromkeys(expected_capabilities))
    if route_through_server and "documents.invoice.list" not in qualification_capabilities:
        qualification_capabilities.append("documents.invoice.list")
    qualifications = tuple(
        qualification(capability_id)
        for capability_id in qualification_capabilities
        if capability_id != omitted_capability
    )
    by_capability = {item.normalized_capability: item for item in qualifications}
    principal = MercuryPrincipal(
        subject=user_id,
        client_id="test-client",
        scopes=frozenset(),
    )
    membership = WorkspaceMembership(
        tenant_id=tenant_id,
        tenant_display_name="Example Tenant",
        workspace_id=workspace_id,
        workspace_display_name="Example Workspace",
        role=WorkspaceRole.MEMBER,
    )
    connection = ProviderConnection(
        id=connection_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        auth_user_id=user_id,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        provider_account_id="private-company-id",
        account_display_name="Example Company",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read",),
        readiness=ConnectionReadiness.READY,
        revision=1,
        last_validated_at=now,
        credential_envelope_ids=(UUID("66666666-6666-4666-8666-666666666666"),),
        created_at=now,
        updated_at=now,
    )

    class ConnectionStore:
        def load_connection(self, **kwargs: object):
            assert kwargs == {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "auth_user_id": user_id,
                "connection_id": connection_id,
            }
            return connection

    class QualificationCatalog:
        def __init__(self) -> None:
            self.items = list(qualifications)
            self.transitions: list[str] = []
            self.transition_attempts = 0

        def list_provider_mcp_qualifications(self):
            return tuple(self.items)

        def disable_provider_mcp_capability_version(
            self,
            qualification_value: ProviderMCPQualification,
        ) -> ProviderMCPQualification:
            self.transition_attempts += 1
            if schema_persistence_started is not None:
                schema_persistence_started.set()
            if release_schema_persistence is not None:
                assert release_schema_persistence.wait(timeout=2)
            if self.transition_attempts <= schema_persistence_failures:
                raise RuntimeError("simulated_catalog_transition_failure")
            self.transitions.append(qualification_value.capability_version_sha256)
            disabled = qualification_value.model_copy(
                update={
                    "qualification_state": QualificationState.DISABLED,
                    "disable_reason": "schema_changed",
                }
            )
            self.items = [
                disabled
                if (
                    item.provider == qualification_value.provider
                    and item.environment == qualification_value.environment
                    and item.normalized_capability == qualification_value.normalized_capability
                    and item.capability_version_sha256
                    == qualification_value.capability_version_sha256
                )
                else item
                for item in self.items
            ]
            return disabled

    qualification_catalog = QualificationCatalog()

    class Resolver:
        async def resolve_for_connection(
            self,
            _connection: ProviderConnection,
            *,
            selection: object,
            deadline: object,
        ):
            del deadline
            selected = next(
                (
                    item
                    for item in qualification_catalog.items
                    if item.normalized_capability == selection.normalized_capability
                    and item.capability_version_sha256 == selection.capability_version_sha256
                    and item.qualification_state is QualificationState.ENABLED
                ),
                None,
            )
            if selected is None:
                return CapabilityResolution(status="capability_unavailable")
            return CapabilityResolution(status="enabled", qualification=selected)

        async def bind_exact_for_connection(
            self,
            _connection: ProviderConnection,
            *,
            capability_id: str,
            capability_version: str,
            deadline: object,
        ):
            del deadline
            selected = next(
                (
                    item
                    for item in qualification_catalog.items
                    if item.normalized_capability == capability_id
                    and item.capability_version_sha256 == capability_version
                    and item.qualification_state is QualificationState.ENABLED
                ),
                None,
            )
            if selected is None:
                raise QualificationGateError("capability_unavailable")
            return selected, QualifiedCapabilityBinding(
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                normalized_capability=capability_id,
                provider_tool=selected.provider_tool_name,
                operation_class=ProviderOperationClass.READ,
                qualification_hash="c" * 64,
            )

    class Driver:
        _manifest = load_provider_manifest(
            Path(__file__).resolve().parents[1]
            / "catalog"
            / "global"
            / "flowaccount"
            / "driver.json"
        )

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []
            self.schema_drift_calls = 0
            self.retry_calls = 0

        async def call(
            self,
            _connection: ProviderConnection,
            binding: QualifiedCapabilityBinding,
            inputs: object,
            _operation_id: object,
            *,
            deadline: object,
        ) -> ProviderCallResult:
            assert binding.operation_class is ProviderOperationClass.READ
            self.calls.append(
                (
                    binding.normalized_capability,
                    inputs.model_dump(mode="json"),
                )
            )
            task = asyncio.current_task()
            if (
                retry_once_task_name is not None
                and task is not None
                and task.get_name() == retry_once_task_name
            ):
                self.retry_calls += 1
                if self.retry_calls == 1:
                    raise ProviderUnavailable(
                        ProviderId.FLOWACCOUNT,
                        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
                    )
            if binding.normalized_capability == failed_capability:
                raise ProviderUnavailable(
                    ProviderId.FLOWACCOUNT,
                    dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
                )
            if binding.normalized_capability == schema_drift_capability:
                self.schema_drift_calls += 1
                if not schema_drift_once or self.schema_drift_calls == 1:
                    raise ProviderSchemaChanged(
                        ProviderId.FLOWACCOUNT,
                        dispatch_certainty=DispatchCertainty.DISPATCHED,
                    )
            data = {
                "provider_profile.get": {"profile_status": "ready"},
                "documents.invoice.list": {"document_count": 2},
                "documents.invoice.get": {"document_number": "INV-001"},
            }[binding.normalized_capability]
            return ProviderCallResult(
                provider=ProviderId.FLOWACCOUNT,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data=data,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )

    driver = Driver()

    class Runtime:
        connection_store = ConnectionStore()
        qualification_resolver = Resolver()
        registry = SimpleNamespace(get=lambda _provider: driver)

        def __init__(self) -> None:
            self.qualification_catalog = qualification_catalog

        async def aclose(self) -> None:
            return None

    class Store:
        def get_published_skill_projection(self, **kwargs: object):
            requested = published_accounting_skill(
                str(kwargs["skill_id"]),
                str(kwargs["skill_version"]),
            )
            assert requested is not None
            return requested.published_projection()

        def search_workspace_knowledge(self, **_kwargs: object):
            return [
                SearchResult(
                    chunk_id="77777777-7777-4777-8777-777777777777",
                    document_id="88888888-8888-4888-8888-888888888888",
                    document_uri="catalog://wiki/document",
                    chunk_uri="catalog://wiki/document#chunk-1",
                    text="Reviewed accounting guidance.",
                    score=0.9,
                    source_title="Reviewed source",
                    source_uri="catalog://wiki/source",
                    source_url=None,
                    source_path=None,
                    citation={"heading": "Reviewed section"},
                    metadata={
                        "review_status": "reviewed",
                        "jurisdiction": "TH",
                        "doc_type": "wiki",
                        "source_id": "99999999-9999-4999-8999-999999999999",
                    },
                )
            ]

    request_fastmcp: list[object | None] = []

    async def require_workspace(*args: object, **_kwargs: object):
        context = args[0] if args else None
        try:
            request_fastmcp.append(context.fastmcp)
        except (AttributeError, ValueError):
            request_fastmcp.append(None)
        return principal, membership

    monkeypatch.setattr(v1_tools, "_require_workspace", require_workspace)
    payload: dict[str, object] = {
        "workspace_id": workspace_id,
        "connection_id": connection_id,
        "skill_id": skill.skill_id,
        "skill_version": skill.skill_version,
        "query": "Review the accounting evidence",
        "host_evidence": [
            {
                "source": "google_sheets",
                "evidence_type": "business_record",
                "source_reference": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "facts": [{"field": "invoice_total", "value": "1250.00"}],
            }
        ],
    }
    if "documents.invoice.get" in expected_capabilities:
        payload["document_ids"] = ["INV-001"]
    arguments = RunAccountingSkillArguments.model_validate(payload)
    audits: list[dict[str, object]] = []
    observed.update(
        skill=skill,
        driver=driver,
        audits=audits,
        qualifications=by_capability,
        qualification_catalog=qualification_catalog,
        request_fastmcp=request_fastmcp,
        runtime_factory=Runtime,
        store_factory=Store,
        wire_arguments=arguments.model_dump(
            mode="json",
            exclude_none=True,
            exclude_unset=True,
        ),
    )
    if defer_server_call and not route_through_server:
        return None
    if route_through_server:
        from mercury_tools.mcp.server import StrictInputFastMCP

        notifications: list[str] = []

        async def capture_terminal_audit(
            _recorder: object,
            event: dict[str, object],
        ) -> None:
            audits.append(event)

        async def capture_runtime_audit(event: dict[str, object]) -> None:
            audits.append(event)

        monkeypatch.setattr(v1_tools, "_write_audit", capture_terminal_audit)
        monkeypatch.setattr(v1_tools, "_record_connector_status_audit", capture_runtime_audit)
        server = StrictInputFastMCP("Skill-observed schema drift")
        v1_tools.configure_v1_tools(
            server,
            enabled=True,
            service_factory=lambda: object(),
            runtime_factory=Runtime,
            store_factory=Store,
        )
        await v1_tools.refresh_generated_provider_tools(
            server,
            runtime_factory=Runtime,
        )
        context = SimpleNamespace(
            session=SimpleNamespace(
                send_tool_list_changed=lambda: notifications.append("tools/list_changed")
            )
        )
        server.get_context = lambda: context
        wire_arguments = observed["wire_arguments"]
        observed.update(
            server=server,
            notifications=notifications,
        )
        if defer_server_call:
            return None
        _content, structured = await server.call_tool(
            "run_accounting_skill",
            wire_arguments,
        )
        return structured
    return await v1_tools.run_accounting_skill(
        SimpleNamespace(),
        arguments=arguments,
        service_factory=lambda: object(),
        store_factory=Store,
        runtime_factory=Runtime,
        audit_recorder=audits.append,
    )


def test_git_catalog_resolves_only_the_exact_immutable_published_version() -> None:
    skill = _published_company_health()
    lookup = _require_attribute(
        "mercury_tools.skills.catalog",
        "published_accounting_skill",
    )

    assert skill.skill_id == "company-health-check-th"
    assert skill.skill_version == "0.1.0"
    assert lookup(skill.skill_id, skill.skill_version) is skill
    assert lookup(skill.skill_id, "0.1.1") is None
    assert skill.published_projection()["skill_id"] == skill.skill_id
    assert skill.published_projection()["skill_version"] == skill.skill_version
    assert len(skill.projection_sha256) == 64


def test_git_read_mapping_rejects_noncanonical_request_or_result_name() -> None:
    SkillReadMapping = _require_attribute(
        "mercury_tools.skills.catalog",
        "SkillReadMapping",
    )

    with pytest.raises(ValueError, match="^skill_read_mapping_invalid$"):
        SkillReadMapping(
            skill_capability="documents.invoice.list",
            capability_id="documents.invoice.list",
            request_kind="invoice_get",
            result_fact_name="invoice_list",
        )
    with pytest.raises(ValueError, match="^skill_read_mapping_invalid$"):
        SkillReadMapping(
            skill_capability="documents.invoice.list",
            capability_id="documents.invoice.list",
            request_kind="invoice_list",
            result_fact_name="Invoice List",
        )


def test_skill_read_input_mapping_serializes_dates_for_exact_catalog_validation() -> None:
    from mercury_tools.catalog.models import ProviderMCPQualification
    from mercury_tools.mcp import v1_tools
    from mercury_tools.mcp.v1_schemas import RunAccountingSkillArguments
    from mercury_tools.skills.catalog import published_accounting_skill

    skill = published_accounting_skill("vat-summary-th", "0.1.0")
    assert skill is not None
    qualification = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="PRIVATE_INVOICE_LIST",
        normalized_capability="documents.invoice.list",
        input_schema={
            "type": "object",
            "properties": {
                "period_start": {"type": "string", "format": "date"},
                "period_end": {"type": "string", "format": "date"},
            },
            "required": ["period_start", "period_end"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 0}},
            "required": ["count"],
            "additionalProperties": False,
        },
        public_output_field_paths=("/count",),
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    arguments = RunAccountingSkillArguments.model_validate(
        {
            "workspace_id": "22222222-2222-4222-8222-222222222222",
            "connection_id": "55555555-5555-4555-8555-555555555555",
            "skill_id": skill.skill_id,
            "skill_version": skill.skill_version,
            "query": "Summarize VAT",
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
        }
    )

    inputs = v1_tools._skill_read_inputs(
        skill.read_mappings[0],
        qualification,
        arguments,
    )

    assert inputs.model_dump(mode="json") == {
        "period_start": "2026-07-01",
        "period_end": "2026-07-31",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill_id", "required_reads"),
    [
        ("company-health-check-th", ("provider_profile.get",)),
        ("vat-summary-th", ("documents.invoice.list",)),
        (
            "invoice-review-th",
            ("documents.invoice.list", "documents.invoice.get"),
        ),
        (
            "management-report-th",
            ("provider_profile.get", "documents.invoice.list"),
        ),
        ("accounts-receivable-reconciliation-th", ("documents.invoice.list",)),
    ],
)
async def test_each_published_read_backed_skill_executes_exact_required_reads(
    monkeypatch: pytest.MonkeyPatch,
    skill_id: str,
    required_reads: tuple[str, ...],
) -> None:
    from jsonschema import Draft202012Validator

    observed: dict[str, object] = {}
    result = await _execute_read_backed_skill(
        monkeypatch,
        skill_id=skill_id,
        expected_capabilities=required_reads,
        observed=observed,
    )
    skill = observed["skill"]
    driver = observed["driver"]
    audits = observed["audits"]
    qualifications = observed["qualifications"]
    data = result.data.model_dump(mode="json")

    Draft202012Validator(skill.published_projection()["output_schema"]).validate(data)
    assert result.skill_id == skill.skill_id
    assert result.skill_version == skill.skill_version
    assert data["output_schema_name"] == skill.output_schema_name
    assert data["facts"]
    assert data["citations"] == ["catalog://wiki/source"]
    assert [capability for capability, _inputs in driver.calls] == list(required_reads)
    assert all(
        mapping["capability_id"] in required_reads
        for mapping in skill.published_projection()["read_mappings"]
    )

    terminal = [event for event in audits if event["tool_name"] == "run_accounting_skill"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == "ok"
    assert terminal[0]["output_summary"]["read_outcomes"] == [
        {
            "capability_id": capability_id,
            "capability_version": qualifications[capability_id].capability_version_sha256,
            "status": "ok",
        }
        for capability_id in required_reads
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_capability", "omitted_capability", "error_code"),
    [
        ("provider_profile.get", None, "capability_unavailable"),
        (None, "provider_profile.get", "insufficient_evidence"),
    ],
)
async def test_read_backed_skill_missing_or_failed_read_returns_closed_error(
    monkeypatch: pytest.MonkeyPatch,
    failed_capability: str | None,
    omitted_capability: str | None,
    error_code: str,
) -> None:
    from mercury_tools.mcp.v1_errors import MercuryV1ToolError

    observed: dict[str, object] = {}
    with pytest.raises(MercuryV1ToolError, match=f"^{error_code}$"):
        await _execute_read_backed_skill(
            monkeypatch,
            skill_id="company-health-check-th",
            expected_capabilities=("provider_profile.get",),
            observed=observed,
            failed_capability=failed_capability,
            omitted_capability=omitted_capability,
        )

    driver = observed["driver"]
    audits = observed["audits"]
    assert all(capability != "documents.invoice.create" for capability, _inputs in driver.calls)
    terminal = [event for event in audits if event["tool_name"] == "run_accounting_skill"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == "error"


@pytest.mark.asyncio
async def test_production_http_lowlevel_skill_drift_is_owned_by_isolated_serving_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect

    from mcp.server.lowlevel.server import request_ctx
    from mcp.shared.context import RequestContext
    from mcp.types import CallToolRequest, CallToolRequestParams

    from mercury_tools.config import Settings
    from mercury_tools.mcp import server as server_module
    from mercury_tools.mcp import v1_tools
    from mercury_tools.qualification.provider_mcp import QualificationGateError
    from mercury_tools.v1.constants import CANONICAL_MCP_RESOURCE

    observed: dict[str, object] = {}
    await _execute_read_backed_skill(
        monkeypatch,
        skill_id="company-health-check-th",
        expected_capabilities=("provider_profile.get", "documents.invoice.list"),
        observed=observed,
        schema_drift_capability="provider_profile.get",
        schema_drift_once=True,
        schema_persistence_failures=1,
        defer_server_call=True,
    )
    runtime = observed["runtime_factory"]()

    async def unused_service_method(*_args: object, **_kwargs: object) -> None:
        return None

    async def startup() -> None:
        return None

    async def capture_terminal_audit(
        _recorder: object,
        event: dict[str, object],
    ) -> None:
        observed["audits"].append(event)

    async def capture_runtime_audit(event: dict[str, object]) -> None:
        observed["audits"].append(event)

    provider_oauth_service = SimpleNamespace(
        complete_callback=unused_service_method,
        disconnect=unused_service_method,
    )
    peak_setup_service = SimpleNamespace(
        start=unused_service_method,
        exchange=unused_service_method,
        complete=unused_service_method,
        disconnect=unused_service_method,
    )
    runtime.principal_resolver = SimpleNamespace(resolve=unused_service_method)
    runtime.provider_oauth_service = provider_oauth_service
    runtime.peak_setup_service = peak_setup_service
    runtime.validate_for_runtime = lambda _settings: None
    runtime.startup = startup

    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-test",
        openai_api_key="",
        supabase_publishable_key="sb_publishable_test",
        v1_enabled=True,
        canonical_mcp_resource=CANONICAL_MCP_RESOURCE,
        supabase_auth_issuer="https://example.supabase.co/auth/v1",
        supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        supabase_jwt_audience=CANONICAL_MCP_RESOURCE,
        vault_active_key=base64.b64encode(b"a" * 32).decode("ascii"),
        vault_active_key_version="v1",
        flowaccount_mcp_sandbox_url="https://flowaccount-sandbox.example/mcp",
        flowaccount_mcp_production_url="https://flowaccount.example/mcp",
        flowaccount_oauth_sandbox_authorization_server_origin=(
            "https://identity-sandbox.flowaccount.example"
        ),
        flowaccount_oauth_production_authorization_server_origin=(
            "https://identity.flowaccount.example"
        ),
        peak_mcp_uat_url="https://peak-uat.example/mcp",
        peak_mcp_production_url="https://peak.example/mcp",
        provider_callback_base_url="https://mercury-tools-mcp.example",
    )
    source = server_module.mcp
    original_tools = dict(source._tool_manager._tools)
    lifecycle_attributes = (
        "_mercury_v1_generated_provider_projection",
        "_mercury_v1_generated_provider_tools",
        "_mercury_v1_legacy_tools",
    )
    missing = object()
    original_lifecycle_attributes = {
        name: getattr(source, name, missing) for name in lifecycle_attributes
    }
    serving = None

    async def registry_contract(instance: object) -> dict[str, object]:
        return {
            "tools": {
                tool.name: tool.model_dump(mode="json", by_alias=True)
                for tool in await instance.list_tools()
            },
            "resources": sorted(
                resource.model_dump_json(by_alias=True)
                for resource in await instance.list_resources()
            ),
            "resource_templates": sorted(
                template.model_dump_json(by_alias=True)
                for template in await instance.list_resource_templates()
            ),
            "prompts": sorted(
                prompt.model_dump_json(by_alias=True) for prompt in await instance.list_prompts()
            ),
        }

    def lowlevel_call_tool_owner(instance: object) -> object:
        handler = instance._mcp_server.request_handlers[CallToolRequest]
        bindings = [
            cell.cell_contents
            for cell in (handler.__closure__ or ())
            if inspect.ismethod(cell.cell_contents) and cell.cell_contents.__name__ == "call_tool"
        ]
        assert len(bindings) == 1
        return bindings[0].__self__

    notifications: list[str] = []

    class RequestSession:
        async def send_tool_list_changed(self) -> None:
            notifications.append("tools/list_changed")

    session = RequestSession()

    async def call_lowlevel(handler: object, request_id: int) -> dict[str, object]:
        token = request_ctx.set(
            RequestContext(
                request_id=request_id,
                meta=None,
                session=session,
                lifespan_context=None,
            )
        )
        try:
            result = await handler(
                CallToolRequest(
                    params=CallToolRequestParams(
                        name="run_accounting_skill",
                        arguments=observed["wire_arguments"],
                    )
                )
            )
        finally:
            request_ctx.reset(token)
        assert result.root.structuredContent is not None
        return result.root.structuredContent

    try:
        v1_tools.configure_v1_tools(source, enabled=False)
        for name in lifecycle_attributes:
            if hasattr(source, name):
                delattr(source, name)
        v1_tools.configure_v1_tools(
            source,
            enabled=True,
            service_factory=lambda: object(),
            runtime_factory=lambda: runtime,
            store_factory=observed["store_factory"],
        )
        monkeypatch.setattr(v1_tools, "_write_audit", capture_terminal_audit)
        monkeypatch.setattr(v1_tools, "_record_connector_status_audit", capture_runtime_audit)
        monkeypatch.setattr(server_module, "_PROCESS_V1_ENABLED", True)
        monkeypatch.setattr(server_module, "load_settings", lambda: settings)
        monkeypatch.setattr(
            server_module,
            "build_provider_oauth_production_composition",
            lambda *, settings: runtime,
        )

        app = server_module.create_http_app(require_auth=False)
        serving = app.state.mercury_mcp
        source_contract = await registry_contract(source)
        source_settings = source.settings.model_dump(mode="python")

        assert serving is not source
        assert serving._mcp_server is not source._mcp_server
        assert serving._tool_manager is not source._tool_manager
        assert serving._resource_manager is not source._resource_manager
        assert serving._prompt_manager is not source._prompt_manager
        assert serving._tool_manager._tools is not source._tool_manager._tools
        assert serving._resource_manager._resources is not source._resource_manager._resources
        assert serving._resource_manager._templates is not source._resource_manager._templates
        assert serving._prompt_manager._prompts is not source._prompt_manager._prompts
        assert serving._session_manager is not source._session_manager
        assert lowlevel_call_tool_owner(serving) is serving
        assert await registry_contract(serving) == source_contract
        assert serving._custom_starlette_routes == source._custom_starlette_routes
        assert serving._custom_starlette_routes is not source._custom_starlette_routes

        async with app.router.lifespan_context(app):
            projection = serving._mercury_v1_generated_provider_projection
            assert projection._server is serving
            assert getattr(source, "_mercury_v1_generated_provider_projection", None) is None
            assert "mercury_flowaccount_provider_profile_get" not in source._tool_manager._tools
            assert "mercury_flowaccount_provider_profile_get" in serving._tool_manager._tools

            handler = serving._mcp_server.request_handlers[CallToolRequest]
            first = await call_lowlevel(handler, 1)
            qualification = observed["qualifications"]["provider_profile.get"]
            with pytest.raises(QualificationGateError, match="^capability_unavailable$"):
                projection.ensure_dispatch_allowed(qualification)
            repeated = await call_lowlevel(handler, 2)
            changed = await app.state.refresh_generated_provider_tools(
                SimpleNamespace(session=session)
            )
            published_tools = {tool.name for tool in await serving.list_tools()}

        qualification = observed["qualifications"]["provider_profile.get"]
        catalog = observed["qualification_catalog"]
        alerts = [
            event
            for event in observed["audits"]
            if event.get("output_summary", {}).get("alert") == "catalog_transition_unavailable"
        ]
        assert first["error"]["code"] == "capability_unavailable"
        assert repeated["error"]["code"] == "capability_unavailable"
        assert changed is True
        assert catalog.transition_attempts == 2
        assert catalog.transitions == [qualification.capability_version_sha256]
        assert "mercury_flowaccount_provider_profile_get" not in published_tools
        assert "mercury_flowaccount_invoice_list" in published_tools
        assert notifications == ["tools/list_changed"]
        assert len(observed["driver"].calls) == 1
        assert observed["request_fastmcp"] == [serving, serving]
        assert len(alerts) == 1
        assert alerts[0]["output_summary"]["dispatch_certainty"] == "dispatched"
        assert await registry_contract(source) == source_contract
        assert source.settings.model_dump(mode="python") == source_settings
        assert projection._publisher._published == {}
        assert projection._publisher._refresh_sessions == {}
        assert projection._publisher._notification_retention_closed is True
        assert await registry_contract(serving) == source_contract
    finally:
        source._tool_manager._tools.clear()
        source._tool_manager._tools.update(original_tools)
        for name, value in original_lifecycle_attributes.items():
            if hasattr(source, name):
                delattr(source, name)
            if value is not missing:
                setattr(source, name, value)


@pytest.mark.asyncio
async def test_skill_observed_schema_drift_demotes_exact_version_and_blocks_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.catalog.models import QualificationState

    observed: dict[str, object] = {}
    first = await _execute_read_backed_skill(
        monkeypatch,
        skill_id="company-health-check-th",
        expected_capabilities=("provider_profile.get",),
        observed=observed,
        schema_drift_capability="provider_profile.get",
        route_through_server=True,
    )

    server = observed["server"]
    _content, repeated = await server.call_tool(
        "run_accounting_skill",
        observed["wire_arguments"],
    )
    qualification = observed["qualifications"]["provider_profile.get"]
    catalog = observed["qualification_catalog"]
    terminal = [
        event for event in observed["audits"] if event["tool_name"] == "run_accounting_skill"
    ]

    assert first["error"]["code"] == "capability_version_changed"
    assert repeated["error"]["code"] == "insufficient_evidence"
    assert catalog.transitions == [qualification.capability_version_sha256]
    assert [
        (item.normalized_capability, item.qualification_state, item.disable_reason)
        for item in catalog.items
    ] == [
        ("provider_profile.get", QualificationState.DISABLED, "schema_changed"),
        ("documents.invoice.list", QualificationState.ENABLED, None),
    ]
    assert {tool.name for tool in await server.list_tools()} >= {
        "run_accounting_skill",
        "mercury_flowaccount_invoice_list",
    }
    assert "mercury_flowaccount_provider_profile_get" not in {
        tool.name for tool in await server.list_tools()
    }
    assert observed["notifications"] == ["tools/list_changed"]
    assert len(observed["driver"].calls) == 1
    assert terminal[0]["output_summary"]["read_outcomes"] == [
        {
            "capability_id": "provider_profile.get",
            "capability_version": qualification.capability_version_sha256,
            "status": "error",
            "error_code": "capability_version_changed",
            "dispatch_certainty": "dispatched",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ("skill", "generated"))
@pytest.mark.parametrize("dispatch_boundary", ("initial", "retry"))
async def test_tombstone_blocks_stale_resolved_dispatch_after_durable_demotion(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    dispatch_boundary: str,
) -> None:
    from mercury_tools.catalog.models import QualificationState
    from mercury_tools.execution.hosted.read_service import HostedReadService

    persistence_started = threading.Event()
    release_persistence = threading.Event()
    stale_at_dispatch_boundary = asyncio.Event()
    release_stale = asyncio.Event()
    stale_task_name = f"{surface}-{dispatch_boundary}-stale"
    observed: dict[str, object] = {}
    await _execute_read_backed_skill(
        monkeypatch,
        skill_id="company-health-check-th",
        expected_capabilities=("provider_profile.get",),
        observed=observed,
        schema_drift_capability="provider_profile.get",
        schema_drift_once=True,
        schema_persistence_started=persistence_started,
        release_schema_persistence=release_persistence,
        retry_once_task_name=stale_task_name if dispatch_boundary == "retry" else None,
        route_through_server=True,
        defer_server_call=True,
    )
    server = observed["server"]
    qualification = observed["qualifications"]["provider_profile.get"]
    original_call_read = HostedReadService._call_read
    original_init = HostedReadService.__init__

    async def gated_call_read(self, *args: object, **kwargs: object):
        binding = kwargs["binding"]
        task = asyncio.current_task()
        if (
            task is not None
            and task.get_name() == stale_task_name
            and binding.normalized_capability == "provider_profile.get"
        ):
            stale_at_dispatch_boundary.set()
            await release_stale.wait()
        return await original_call_read(self, *args, **kwargs)

    async def gated_retry_pause(seconds: float) -> None:
        task = asyncio.current_task()
        assert seconds == 0.05
        assert task is not None and task.get_name() == stale_task_name
        stale_at_dispatch_boundary.set()
        await release_stale.wait()

    def init_with_gated_retry(self, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)
        self._sleep = gated_retry_pause

    if dispatch_boundary == "initial":
        monkeypatch.setattr(HostedReadService, "_call_read", gated_call_read)
    else:
        monkeypatch.setattr(HostedReadService, "__init__", init_with_gated_retry)

    async def call_profile() -> dict[str, object]:
        if surface == "skill":
            _content, structured = await server.call_tool(
                "run_accounting_skill",
                observed["wire_arguments"],
            )
            return structured
        _content, structured = await server.call_tool(
            "mercury_flowaccount_provider_profile_get",
            {
                "workspace_id": observed["wire_arguments"]["workspace_id"],
                "connection_id": observed["wire_arguments"]["connection_id"],
                "capability_version": qualification.capability_version_sha256,
            },
        )
        return structured

    async def call_unrelated() -> dict[str, object]:
        if surface == "skill":
            arguments = dict(observed["wire_arguments"])
            arguments.update(
                {
                    "skill_id": "vat-summary-th",
                    "query": "Summarize VAT from the unrelated exact version",
                }
            )
            _content, structured = await server.call_tool(
                "run_accounting_skill",
                arguments,
            )
            return structured
        unrelated = observed["qualifications"]["documents.invoice.list"]
        _content, structured = await server.call_tool(
            "mercury_flowaccount_invoice_list",
            {
                "workspace_id": observed["wire_arguments"]["workspace_id"],
                "connection_id": observed["wire_arguments"]["connection_id"],
                "capability_version": unrelated.capability_version_sha256,
            },
        )
        return structured

    stale_task = asyncio.create_task(call_profile(), name=stale_task_name)
    drift_task: asyncio.Task[dict[str, object]] | None = None
    try:
        await asyncio.wait_for(stale_at_dispatch_boundary.wait(), timeout=1)
        drift_task = asyncio.create_task(call_profile(), name=f"{surface}-drift")
        assert await asyncio.to_thread(persistence_started.wait, 1)
        projection = server._mercury_v1_generated_provider_projection
        quarantine_active = bool(projection._publisher._quarantined_versions)
        release_persistence.set()
        drift = await asyncio.wait_for(drift_task, timeout=1)
        transient_quarantine_released = not projection._publisher._quarantined_versions
        published_tools_after_demotion = {tool.name for tool in await server.list_tools()}
        unrelated = await asyncio.wait_for(call_unrelated(), timeout=1)
        profile_dispatches_before_stale_release = len(
            [call for call in observed["driver"].calls if call[0] == "provider_profile.get"]
        )
        release_stale.set()
        stale = await asyncio.wait_for(stale_task, timeout=1)
    finally:
        release_stale.set()
        release_persistence.set()

    catalog = observed["qualification_catalog"]
    profile_dispatches = [
        call for call in observed["driver"].calls if call[0] == "provider_profile.get"
    ]
    expected_profile_dispatches = 1 if dispatch_boundary == "initial" else 2

    assert quarantine_active is True
    assert drift["error"]["code"] == "capability_version_changed"
    assert transient_quarantine_released is True
    assert stale.get("error", {}).get("code") == "capability_unavailable"
    if surface == "skill":
        assert unrelated["skill_id"] == "vat-summary-th"
    else:
        assert unrelated["capability_id"] == "documents.invoice.list"
    assert profile_dispatches_before_stale_release == expected_profile_dispatches
    assert len(profile_dispatches) == expected_profile_dispatches
    assert catalog.transitions == [qualification.capability_version_sha256]
    assert catalog.items[0].qualification_state is QualificationState.DISABLED
    assert "mercury_flowaccount_provider_profile_get" not in published_tools_after_demotion
    assert "mercury_flowaccount_invoice_list" in published_tools_after_demotion


def test_git_projection_routes_skill_requirements_to_exact_v1_read_capabilities() -> None:
    skill = _published_company_health()
    projection = skill.published_projection()

    assert projection["v1_capability_routes"] == {
        "company.read": ["provider_profile.get"],
        "documents.invoice.list": ["documents.invoice.list"],
        "tax.vat.summary.read": [],
    }
    assert "documents.invoice.create" not in str(projection["v1_capability_routes"])


@pytest.mark.parametrize(
    ("mutation", "missing"),
    [
        ({"projection": None}, "skill_schema"),
        ({"enabled_capabilities": ()}, "capability:company.read"),
        ({"business_fact_count": 0}, "business_fact"),
        ({"knowledge_source_count": 0}, "knowledge_source"),
        ({"citation_count": 0}, "citation"),
    ],
)
def test_published_skill_route_returns_insufficient_evidence_for_every_missing_authority(
    mutation: dict[str, object],
    missing: str,
) -> None:
    resolve = _require_attribute(
        "mercury_tools.skills.routing",
        "resolve_published_skill_route",
    )
    skill = _published_company_health()
    arguments: dict[str, object] = {
        "projection": skill.published_projection(),
        "enabled_capabilities": ("company.read",),
        "business_fact_count": 1,
        "knowledge_source_count": 1,
        "citation_count": 1,
    }
    arguments.update(mutation)

    route = resolve(skill, **arguments)

    assert route["status"] == "insufficient_evidence"
    assert missing in route["missing_evidence"]
    assert route["skill_id"] == skill.skill_id
    assert route["skill_version"] == skill.skill_version


def test_skill_route_consumes_enabled_capabilities_without_broadening_authority() -> None:
    resolve = _require_attribute(
        "mercury_tools.skills.routing",
        "resolve_published_skill_route",
    )
    skill = _published_company_health()

    observed_only = resolve(
        skill,
        projection=skill.published_projection(),
        enabled_capabilities=(),
        business_fact_count=1,
        knowledge_source_count=1,
        citation_count=1,
    )
    ready = resolve(
        skill,
        projection=skill.published_projection(),
        enabled_capabilities=("company.read",),
        business_fact_count=1,
        knowledge_source_count=1,
        citation_count=1,
    )

    assert observed_only["status"] == "insufficient_evidence"
    assert ready["status"] == "ready"
    assert ready["required_capabilities"] == ["company.read"]
    assert ready["allowed_action_classes"] == ["provider_read"]
    assert "provider_create" in ready["blocked_action_classes"]
    assert "enable" not in str(ready).lower()
    assert "discover" not in str(ready).lower()
    assert "qualify" not in str(ready).lower()


@pytest.mark.asyncio
async def test_runtime_binds_skill_requirements_to_exact_enabled_catalog_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.mcp import v1_tools

    skill = _published_company_health()
    tenant_id = UUID("11111111-1111-4111-8111-111111111111")
    workspace_id = UUID("22222222-2222-4222-8222-222222222222")
    connection_id = UUID("33333333-3333-4333-8333-333333333333")
    user_id = UUID("44444444-4444-4444-8444-444444444444")
    connection = SimpleNamespace(
        id=connection_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        auth_user_id=user_id,
        provider=SimpleNamespace(value="flowaccount"),
        environment="sandbox",
    )
    qualifications = (
        SimpleNamespace(
            provider="flowaccount",
            environment="sandbox",
            normalized_capability="provider_profile.get",
        ),
        SimpleNamespace(
            provider="flowaccount",
            environment="sandbox",
            normalized_capability="documents.invoice.list",
        ),
        SimpleNamespace(
            provider="flowaccount",
            environment="sandbox",
            normalized_capability="documents.invoice.create",
        ),
    )

    async def load_connection(*_args: object, **_kwargs: object):
        return connection

    async def list_qualifications(_runtime: object):
        return qualifications

    async def resolve_qualification(
        _runtime: object,
        *,
        connection: object,
        qualification: object,
    ):
        assert connection is not None
        version = (
            "a" * 64 if qualification.normalized_capability == "provider_profile.get" else "b" * 64
        )
        return SimpleNamespace(
            status="enabled",
            qualification=SimpleNamespace(
                normalized_capability=qualification.normalized_capability,
                capability_version_sha256=version,
                public_output_field_paths=(),
            ),
        )

    monkeypatch.setattr(v1_tools, "_store_load_connection", load_connection)
    monkeypatch.setattr(v1_tools, "_catalog_qualifications", list_qualifications)
    monkeypatch.setattr(v1_tools, "_resolve_qualification", resolve_qualification)

    bindings, enabled = await v1_tools._enabled_skill_capability_bindings(
        object(),
        skill=skill,
        membership=SimpleNamespace(tenant_id=tenant_id),
        workspace_id=workspace_id,
        principal=SimpleNamespace(subject=user_id),
        connection_id=connection_id,
    )

    assert enabled == ("company.read", "documents.invoice.list")
    assert [binding.model_dump(mode="json") for binding in bindings] == [
        {
            "skill_capability": "company.read",
            "capability_id": "provider_profile.get",
            "capability_version": "a" * 64,
        },
        {
            "skill_capability": "documents.invoice.list",
            "capability_id": "documents.invoice.list",
            "capability_version": "b" * 64,
        },
    ]
    assert all(binding.capability_id != "documents.invoice.create" for binding in bindings)


def test_projection_hash_mismatch_is_not_executable() -> None:
    resolve = _require_attribute(
        "mercury_tools.skills.routing",
        "resolve_published_skill_route",
    )
    skill = _published_company_health()
    projection = deepcopy(skill.published_projection())
    projection["summary"] = "Supabase must not redefine a Git Skill"

    route = resolve(
        skill,
        projection=projection,
        enabled_capabilities=("company.read",),
        business_fact_count=1,
        knowledge_source_count=1,
        citation_count=1,
    )

    assert route["status"] == "insufficient_evidence"
    assert route["missing_evidence"] == ["skill_schema"]


def test_host_connected_evidence_is_typed_and_rejects_credentials() -> None:
    HostConnectedEvidenceInput = _require_attribute(
        "mercury_tools.mcp.v1_schemas",
        "HostConnectedEvidenceInput",
    )
    valid = HostConnectedEvidenceInput.model_validate(
        {
            "source": "google_sheets",
            "evidence_type": "business_record",
            "source_reference": "44444444-4444-4444-8444-444444444444",
            "facts": [
                {
                    "field": "invoice_total",
                    "value": "1250.00",
                }
            ],
        }
    )

    assert valid.facts[0].field == "invoice_total"
    assert valid.facts[0].value == Decimal("1250.00")
    for field, value in (
        ("oauth_token", "private-value"),
        ("api_key", "private-value"),
        ("access_key", "private-value"),
        ("authorization", "Bearer private-value"),
        ("raw_authorization_header", "private-value"),
        ("connect_id", "private-value"),
        ("connect_key", "private-value"),
        ("credential_envelope", "private-value"),
        ("service_role_key", "private-value"),
    ):
        with pytest.raises(ValidationError):
            HostConnectedEvidenceInput.model_validate(
                {
                    "source": "google_sheets",
                    "evidence_type": "business_record",
                    "source_reference": "44444444-4444-4444-8444-444444444444",
                    "facts": [{"field": field, "value": value}],
                }
            )
    for value in (
        "Bearer private-value",
        "copied key: sk-private-value",
        '{"credential_envelope":{"api_key":"private-value"}}',
        "abcdefgh.ijklmnop.qrstuvwx",
        "4/P7q7W91a-oMsCeLvIaQm6bTrgtp7",
        "0123456789abcdef0123456789abcdef",
    ):
        with pytest.raises(ValidationError):
            HostConnectedEvidenceInput.model_validate(
                {
                    "source": "gmail",
                    "evidence_type": "message_fact",
                    "source_reference": "44444444-4444-4444-8444-444444444444",
                    "facts": [{"field": "invoice_total", "value": value}],
                }
            )
    with pytest.raises(ValidationError):
        HostConnectedEvidenceInput.model_validate(
            {
                "source": "host_mcp",
                "evidence_type": "business_record",
                "source_reference": "oauth-token-envelope",
                "facts": [{"field": "invoice_total", "value": "1250.00"}],
            }
        )


@pytest.mark.asyncio
async def test_run_skill_rejects_unpublished_projection_before_runtime_or_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.mcp import v1_tools
    from mercury_tools.mcp.v1_errors import MercuryV1ToolError
    from mercury_tools.mcp.v1_schemas import RunAccountingSkillArguments

    calls: list[str] = []

    async def require_workspace(*_args: object, **_kwargs: object):
        return (
            SimpleNamespace(subject=UUID("33333333-3333-4333-8333-333333333333")),
            SimpleNamespace(tenant_id=UUID("11111111-1111-4111-8111-111111111111")),
        )

    class Store:
        def get_published_skill_projection(self, **_kwargs: object):
            calls.append("projection")
            return None

        def search_workspace_knowledge(self, **_kwargs: object):
            raise AssertionError("knowledge search must follow exact Skill publication")

    async def runtime_factory():
        raise AssertionError("provider runtime must follow exact Skill publication")

    monkeypatch.setattr(v1_tools, "_require_workspace", require_workspace)
    arguments = RunAccountingSkillArguments.model_validate(
        {
            "workspace_id": "22222222-2222-4222-8222-222222222222",
            "connection_id": "55555555-5555-4555-8555-555555555555",
            "skill_id": "company-health-check-th",
            "skill_version": "0.1.0",
            "query": "Review the company",
        }
    )

    with pytest.raises(MercuryV1ToolError, match="^insufficient_evidence$"):
        await v1_tools.run_accounting_skill(
            SimpleNamespace(),
            arguments=arguments,
            service_factory=lambda: object(),
            store_factory=Store,
            runtime_factory=runtime_factory,
        )

    assert calls == ["projection"]


@pytest.mark.asyncio
async def test_run_accounting_skill_is_an_exact_generated_union_without_generic_inputs() -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Task 11 Skills")
    configure_v1_tools(server, enabled=True)
    tools = {tool.name: tool for tool in await server.list_tools()}
    schema = tools["run_accounting_skill"].inputSchema
    output_schema = tools["run_accounting_skill"].outputSchema

    assert "oneOf" in schema
    assert schema["discriminator"]["propertyName"] == "skill_id"
    assert len(schema["oneOf"]) >= 15
    branches = [schema["$defs"][branch["$ref"].rsplit("/", 1)[-1]] for branch in schema["oneOf"]]
    by_skill = {branch["properties"]["skill_id"]["const"]: branch for branch in branches}
    company_health = by_skill["company-health-check-th"]
    assert company_health["properties"]["skill_version"]["const"] == "0.1.0"
    assert {"workspace_id", "connection_id", "skill_id", "skill_version", "query"} <= set(
        company_health["required"]
    )
    projected_input = _published_company_health().published_projection()["input_schema"]
    assert all(
        company_health["properties"][field] == field_schema
        for field, field_schema in projected_input["properties"].items()
    )
    assert all(
        schema["$defs"][name] == definition for name, definition in projected_input["$defs"].items()
    )
    assert "inputs" not in str(schema)
    assert company_health["additionalProperties"] is False
    assert output_schema is not None
    output_data = output_schema["$defs"]["RunAccountingSkillData"]
    assert output_data["properties"]["facts"]["items"]["maxLength"] == 2_000
    assert output_data["properties"]["citations"]["items"]["maxLength"] == 2_000

    schema_text = str(schema).lower()
    for forbidden in (
        "oauth_token",
        "api_key",
        "authorization_header",
        "credential_envelope",
        "provider_create",
    ):
        assert forbidden not in schema_text


def test_supabase_projection_lookup_carries_exact_authenticated_identity(monkeypatch) -> None:
    from mercury_tools.db.supabase import SupabaseRagStore

    method = getattr(SupabaseRagStore, "get_published_skill_projection", None)
    assert callable(method), "published Skill projection lookup is not implemented"
    captured: dict[str, object] = {}
    store = object.__new__(SupabaseRagStore)

    def request(http_method: str, path: str, **kwargs: object) -> list[dict[str, object]]:
        captured.update(http_method=http_method, path=path, payload=kwargs["json"])
        return []

    monkeypatch.setattr(store, "_request", request)

    result = method(
        store,
        tenant_id="11111111-1111-4111-8111-111111111111",
        workspace_id="22222222-2222-4222-8222-222222222222",
        auth_user_id="33333333-3333-4333-8333-333333333333",
        skill_id="company-health-check-th",
        skill_version="0.1.0",
    )

    assert result is None
    assert captured == {
        "http_method": "POST",
        "path": "rpc/resolve_mercury_v1_published_skill",
        "payload": {
            "p_tenant_id": "11111111-1111-4111-8111-111111111111",
            "p_workspace_id": "22222222-2222-4222-8222-222222222222",
            "p_auth_user_id": "33333333-3333-4333-8333-333333333333",
            "p_skill_id": "company-health-check-th",
            "p_skill_version": "0.1.0",
        },
    }
