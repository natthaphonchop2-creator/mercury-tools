from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest


def ready_profile(
    connector_id: str,
    *,
    connection_mode: str = "api_driver",
    environment: str = "production",
    capability_states: dict[str, str],
    external_server_name: str | None = None,
) -> dict[str, object]:
    return {
        "connector_id": connector_id,
        "connection_mode": connection_mode,
        "environment": environment,
        "status": "ready_read_only",
        "capability_states": capability_states,
        "evidence_source": {
            "native_mcp": "native_mcp_safe_read",
            "api_driver": "api_driver_safe_probe",
            "local_bridge": "local_bridge_safe_probe",
        }[connection_mode],
        "validated_at": "2026-07-19T12:00:00+00:00",
        "external_server_name": external_server_name or f"{connector_id}-accounting-mcp",
        "metadata": {"client_secret": "must-not-leak"},
    }


def test_accounting_skill_catalog_is_the_immutable_source_for_seed_and_schema() -> None:
    from mercury_tools.db.product import SKILL_CATALOG_SEED
    from mercury_tools.skills.catalog import (
        ACCOUNTING_SKILL_CATALOG,
        accounting_skill_by_id,
        accounting_skill_input_schema,
    )

    assert isinstance(ACCOUNTING_SKILL_CATALOG, tuple)
    assert [item.skill_id for item in ACCOUNTING_SKILL_CATALOG] == [
        row["skill_id"] for row in SKILL_CATALOG_SEED
    ]
    assert len({item.skill_id for item in ACCOUNTING_SKILL_CATALOG}) == len(
        ACCOUNTING_SKILL_CATALOG
    )

    company_health = accounting_skill_by_id("company-health-check-th")
    assert company_health is not None
    assert company_health.required_capabilities == ("company.read",)
    assert company_health.optional_capabilities == (
        "documents.invoice.list",
        "tax.vat.summary.read",
    )
    assert accounting_skill_input_schema(company_health.skill_id) == (
        company_health.input_schema.model_json_schema()
    )
    for definition in ACCOUNTING_SKILL_CATALOG:
        schema = accounting_skill_input_schema(definition.skill_id)
        assert schema is not None
        assert "connection_mode" in schema["properties"]
        validated = definition.input_schema.model_validate(
            {"query": "Review", "connection_mode": "native_mcp"}
        )
        assert validated.connection_mode == "native_mcp"
    with pytest.raises(FrozenInstanceError):
        company_health.title = "mutable"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("connector_id", "provider_capability"),
    [
        ("flowaccount", "company.info.read"),
        ("peak", "user.info.read"),
    ],
)
def test_company_health_skill_routes_over_two_provider_profiles(
    connector_id: str,
    provider_capability: str,
) -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    route = resolve_skill_route(
        skill,
        [
            ready_profile(
                connector_id,
                capability_states={provider_capability: "observed"},
            )
        ],
    )

    assert route["status"] == "ready"
    assert route["skill_id"] == "company-health-check-th"
    assert route["selected_profile"] == {
        "connector_id": connector_id,
        "connection_mode": "api_driver",
        "environment": "production",
    }
    assert route["capability_resolution"][0] == {
        "capability": "company.read",
        "provider_capabilities": [provider_capability],
        "required": True,
        "state": "observed",
    }
    assert route["ordered_steps"][0]["action"] == "invoke_connected_provider_capability"
    assert route["host_tool_requirements"][0]["connection_mode"] == "api_driver"
    assert "must-not-leak" not in str(route)


def test_skill_route_honors_explicit_connector_then_requires_selection_when_ambiguous() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    profiles = [
        ready_profile(
            "peak",
            capability_states={"user.info.read": "observed"},
        ),
        ready_profile(
            "flowaccount",
            capability_states={"company.info.read": "observed"},
        ),
    ]

    ambiguous = resolve_skill_route(skill, profiles)
    selected = resolve_skill_route(
        skill,
        profiles,
        requested_connector_id="peak",
    )

    assert ambiguous["status"] == "connector_selection_required"
    assert ambiguous["choices"] == [
        {
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
        },
        {
            "connector_id": "peak",
            "connection_mode": "api_driver",
            "environment": "production",
        },
    ]
    assert selected["status"] == "ready"
    assert selected["selected_profile"]["connector_id"] == "peak"


def test_skill_route_selects_connection_mode_for_same_connector_profiles() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    profiles = [
        ready_profile(
            "flowaccount",
            connection_mode="native_mcp",
            capability_states={"company.info.read": "observed"},
        ),
        ready_profile(
            "flowaccount",
            connection_mode="api_driver",
            capability_states={"company.info.read": "observed"},
        ),
    ]

    ambiguous = resolve_skill_route(
        skill,
        profiles,
        requested_connector_id="flowaccount",
    )
    selected = resolve_skill_route(
        skill,
        profiles,
        requested_connector_id=" FLOWACCOUNT ",
        requested_connection_mode=" NATIVE_MCP ",
    )

    assert ambiguous["status"] == "connector_selection_required"
    assert ambiguous["choices"] == [
        {
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
        },
        {
            "connector_id": "flowaccount",
            "connection_mode": "native_mcp",
            "environment": "production",
        },
    ]
    assert selected["status"] == "ready"
    assert selected["selected_profile"] == {
        "connector_id": "flowaccount",
        "connection_mode": "native_mcp",
        "environment": "production",
    }


def test_skill_route_keeps_environment_ambiguity_after_mode_selection() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    profiles = [
        ready_profile(
            "flowaccount",
            environment=environment,
            capability_states={"company.info.read": "observed"},
        )
        for environment in ("production", "sandbox")
    ]

    route = resolve_skill_route(
        skill,
        profiles,
        requested_connector_id="flowaccount",
        requested_connection_mode="api_driver",
    )

    assert route["status"] == "connector_selection_required"
    assert [choice["environment"] for choice in route["choices"]] == [
        "production",
        "sandbox",
    ]


def test_native_provider_unavailable_write_does_not_block_unrelated_read_skill() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    company_health = accounting_skill_by_id("company-health-check-th")
    assert company_health is not None
    create_skill = replace(
        company_health,
        skill_id="invoice-create-test",
        required_capabilities=("documents.invoice.create",),
        optional_capabilities=(),
        read_mappings=(),
    )
    native_profile = ready_profile(
        "flowaccount",
        connection_mode="native_mcp",
        capability_states={
            "company.info.read": "observed",
            "documents.invoice.create": "provider_unavailable",
        },
    )

    unavailable = resolve_skill_route(create_skill, [native_profile])
    readable = resolve_skill_route(company_health, [native_profile])

    assert unavailable["status"] == "unavailable"
    assert unavailable["reason"] == "provider_capability_unavailable"
    assert readable["status"] == "ready"
    assert readable["ordered_steps"][0]["action"] == "invoke_connected_provider_capability"
    assert readable["host_tool_requirements"] == [
        {
            "connector_id": "flowaccount",
            "connection_mode": "native_mcp",
            "environment": "production",
            "external_server_name": "flowaccount-accounting-mcp",
            "provider_capabilities": ["company.info.read"],
        }
    ]


def test_native_route_preserves_server_case_and_includes_only_observed_optional_steps() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    route = resolve_skill_route(
        skill,
        [
            ready_profile(
                "flowaccount",
                connection_mode="native_mcp",
                capability_states={
                    "company.info.read": "observed",
                    "documents.invoice.list": "observed",
                },
                external_server_name=" FlowAccount-Prod-MCP ",
            )
        ],
    )

    assert route["status"] == "ready"
    assert route["ordered_steps"] == [
        {
            "step": 1,
            "action": "invoke_connected_provider_capability",
            "capability": "company.read",
            "provider_capabilities": ["company.info.read"],
            "required": True,
        },
        {
            "step": 2,
            "action": "invoke_connected_provider_capability",
            "capability": "documents.invoice.list",
            "provider_capabilities": ["documents.invoice.list"],
            "required": False,
        },
    ]
    assert route["host_tool_requirements"] == [
        {
            "connector_id": "flowaccount",
            "connection_mode": "native_mcp",
            "environment": "production",
            "external_server_name": "FlowAccount-Prod-MCP",
            "provider_capabilities": [
                "company.info.read",
                "documents.invoice.list",
            ],
        }
    ]
    unavailable_optional = next(
        item
        for item in route["capability_resolution"]
        if item["capability"] == "tax.vat.summary.read"
    )
    assert unavailable_optional["required"] is False
    assert unavailable_optional["state"] == "provider_capability_unavailable"


@pytest.mark.parametrize(
    (
        "skill_id",
        "connector_id",
        "capability_states",
        "optional_capability",
        "provider_action",
    ),
    [
        (
            "vat-summary-th",
            "flowaccount",
            {
                "documents.invoice.list": "observed",
                "tax.vat_summary.read": "observed",
            },
            "tax.vat.summary.read",
            "tax.vat_summary.read",
        ),
        (
            "management-report-th",
            "peak",
            {
                "user.info.read": "observed",
                "documents.invoice.list": "observed",
                "daily_journal.get": "observed",
            },
            "journal.read",
            "daily_journal.get",
        ),
    ],
)
def test_observed_provider_alias_resolves_observed_optional_canonical_capability(
    skill_id: str,
    connector_id: str,
    capability_states: dict[str, str],
    optional_capability: str,
    provider_action: str,
) -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id(skill_id)
    assert skill is not None
    route = resolve_skill_route(
        skill,
        [ready_profile(connector_id, capability_states=capability_states)],
    )

    assert route["status"] == "ready"
    optional_resolution = next(
        item for item in route["capability_resolution"] if item["capability"] == optional_capability
    )
    assert optional_resolution == {
        "capability": optional_capability,
        "provider_capabilities": [provider_action],
        "required": False,
        "state": "observed",
    }


def test_local_bridge_skill_route_returns_exact_setup_handoff() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    route = resolve_skill_route(
        skill,
        [
            ready_profile(
                "express",
                connection_mode="local_bridge",
                environment="local",
                capability_states={"company.read": "observed"},
            )
        ],
        requested_connector_id="express",
    )

    assert route["status"] == "local_bridge_required"
    assert route["reason"] == "local_bridge_required"
    assert route["ordered_steps"] == [
        {
            "step": 1,
            "action": "configure_local_bridge",
            "connector_id": "express",
            "environment": "local",
        }
    ]


def test_multiple_local_bridge_profiles_require_sorted_sanitized_tuple_selection() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    profiles = [
        ready_profile(
            "express",
            connection_mode="local_bridge",
            environment=environment,
            capability_states={},
        )
        for environment in ("local", "gateway")
    ]
    for profile in profiles:
        profile["status"] = "requires_local_setup"
        profile["external_server_name"] = "should-not-leak"

    route = resolve_skill_route(
        skill,
        profiles,
        requested_connector_id=" EXPRESS ",
        requested_connection_mode=" LOCAL_BRIDGE ",
    )

    assert route == {
        "status": "connector_selection_required",
        "skill_id": "company-health-check-th",
        "choices": [
            {
                "connector_id": "express",
                "connection_mode": "local_bridge",
                "environment": "gateway",
            },
            {
                "connector_id": "express",
                "connection_mode": "local_bridge",
                "environment": "local",
            },
        ],
        "selected_profile": None,
        "capability_resolution": [],
        "ordered_steps": [],
        "host_tool_requirements": [],
    }


@pytest.mark.parametrize(
    "external_server_name",
    [None, "https://flowaccount-mcp", "bad server"],
)
def test_malformed_ready_native_profile_is_not_validated(
    external_server_name: str | None,
) -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    profile = ready_profile(
        "flowaccount",
        connection_mode="native_mcp",
        capability_states={"company.info.read": "observed"},
    )
    profile["external_server_name"] = external_server_name

    route = resolve_skill_route(skill, [profile])

    assert route["status"] == "unavailable"
    assert route["reason"] == "not_validated"
    assert route["selected_profile"] == {
        "connector_id": "flowaccount",
        "connection_mode": "native_mcp",
        "environment": "production",
    }
    assert route["ordered_steps"] == []
    assert route["host_tool_requirements"] == []


def test_discovered_mcp_profile_uses_observed_normalized_capability() -> None:
    from mercury_tools.skills.catalog import accounting_skill_by_id
    from mercury_tools.skills.routing import resolve_skill_route

    skill = accounting_skill_by_id("company-health-check-th")
    assert skill is not None
    route = resolve_skill_route(
        skill,
        [
            ready_profile(
                "generic_mcp",
                connection_mode="native_mcp",
                environment="user_supplied",
                capability_states={"company.read": "observed"},
            )
        ],
    )

    assert route["status"] == "ready"
    assert route["capability_resolution"][0]["provider_capabilities"] == ["company.read"]
