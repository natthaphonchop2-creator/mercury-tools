from __future__ import annotations

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

    qualifications = tuple(
        qualification(capability_id)
        for capability_id in dict.fromkeys(expected_capabilities)
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
        def list_provider_mcp_qualifications(self):
            return qualifications

    class Resolver:
        async def resolve_for_connection(
            self,
            _connection: ProviderConnection,
            *,
            selection: object,
            deadline: object,
        ):
            del deadline
            selected = by_capability.get(selection.normalized_capability)
            if (
                selected is None
                or selected.capability_version_sha256 != selection.capability_version_sha256
            ):
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
            selected = by_capability.get(capability_id)
            if selected is None or selected.capability_version_sha256 != capability_version:
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
            if binding.normalized_capability == failed_capability:
                raise ProviderUnavailable(
                    ProviderId.FLOWACCOUNT,
                    dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
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
        qualification_catalog = QualificationCatalog()
        qualification_resolver = Resolver()
        registry = SimpleNamespace(get=lambda _provider: driver)

        async def aclose(self) -> None:
            return None

    class Store:
        def get_published_skill_projection(self, **_kwargs: object):
            return skill.published_projection()

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

    async def require_workspace(*_args: object, **_kwargs: object):
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
    )
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
