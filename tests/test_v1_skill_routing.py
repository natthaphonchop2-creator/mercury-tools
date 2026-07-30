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
    connection = SimpleNamespace(
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
            ),
        )

    monkeypatch.setattr(v1_tools, "_store_load_connection", load_connection)
    monkeypatch.setattr(v1_tools, "_catalog_qualifications", list_qualifications)
    monkeypatch.setattr(v1_tools, "_resolve_qualification", resolve_qualification)

    bindings, enabled = await v1_tools._enabled_skill_capability_bindings(
        object(),
        skill=skill,
        membership=object(),
        workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
        principal=object(),
        connection_id=UUID("33333333-3333-4333-8333-333333333333"),
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
