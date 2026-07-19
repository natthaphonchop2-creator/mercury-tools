from typing import get_args

import pytest

from mercury_tools.connectors.catalog import (
    CapabilityState,
    ConnectionMode,
    ConnectorModeManifest,
    connector_by_id,
    list_connector_public_summaries,
)
from mercury_tools.mcp.schemas import ConnectorEnvironment


def test_connector_catalog_is_mode_aware_and_connector_neutral() -> None:
    flow = connector_by_id("flowaccount")
    peak = connector_by_id("peak")
    express = connector_by_id("express")
    custom = connector_by_id("custom")
    generic = connector_by_id("generic_mcp")

    assert flow is not None
    assert peak is not None
    assert express is not None
    assert custom is not None
    assert generic is not None
    assert set(flow.connection_mode_ids) == {"native_mcp", "api_driver"}
    assert peak.connection_mode_ids == ["api_driver"]
    assert express.connection_mode_ids == ["local_bridge"]
    assert custom.connection_mode_ids == ["api_driver"]
    assert generic.connection_mode_ids == ["native_mcp"]


def test_public_connector_environment_covers_every_catalog_environment() -> None:
    public_environments = set(get_args(ConnectorEnvironment))
    catalog_environments = {
        environment
        for summary in list_connector_public_summaries()
        for mode in summary["connection_modes"]
        for environment in mode["supported_environments"]
    }

    assert catalog_environments <= public_environments
    assert "user_supplied" in public_environments


def test_flowaccount_native_mcp_read_only_does_not_block_api_driver_writes() -> None:
    flow = connector_by_id("flowaccount")

    assert flow is not None
    assert flow.capability_state("native_mcp", "documents.invoice.list") == "declared"
    assert (
        flow.capability_state("native_mcp", "documents.invoice.create")
        == "provider_unavailable"
    )
    assert (
        flow.capability_state("api_driver", "documents.invoice.create")
        == "not_validated"
    )


def test_flowaccount_native_mcp_uses_official_mcp_url() -> None:
    flow = connector_by_id("flowaccount")

    assert flow is not None
    native_mcp = flow.connection_mode("native_mcp")
    assert native_mcp is not None
    assert native_mcp.official_mcp_url == "https://mcp.flowaccount.com/mcp"


def test_public_catalog_rows_expose_mode_specific_readiness_without_defaults() -> None:
    required_keys = {
        "display_name",
        "connection_modes",
        "auth_modes",
        "supported_environments",
        "capability_source",
        "provider_capability_status",
        "setup_defaults",
        "local_bridge_requirement",
        "last_reviewed_at",
    }

    summaries = list_connector_public_summaries()

    assert {item["connector_id"] for item in summaries} == {
        "flowaccount",
        "peak",
        "express",
        "custom",
        "generic_mcp",
    }
    for summary in summaries:
        assert required_keys <= summary.keys()
        assert "blocked_capabilities" not in summary
        assert "default_connection_mode" not in summary
        assert "is_default" not in summary

    flow = next(item for item in summaries if item["connector_id"] == "flowaccount")
    assert flow["provider_capability_status"]["native_mcp"][
        "documents.invoice.create"
    ] == "provider_unavailable"
    assert flow["provider_capability_status"]["api_driver"][
        "documents.invoice.create"
    ] == "not_validated"
    assert flow["connection_modes"][0]["mode"] in {"native_mcp", "api_driver"}


def test_connector_compatibility_properties_remain_available_in_v0_3() -> None:
    flow = connector_by_id("flowaccount")

    assert flow is not None
    assert flow.name == "FlowAccount"
    assert flow.environments == ["production", "sandbox"]
    assert flow.preset["grant_type"] == "client_credentials"
    assert flow.required_secret_fields == ["client_id", "client_secret"]
    assert flow.preset_for_environment("sandbox")["api_base_url"] == (
        "https://openapi.flowaccount.com/test"
    )


def test_connector_mode_manifest_mappings_are_immutable() -> None:
    manifest = ConnectorModeManifest(
        mode=ConnectionMode.API_DRIVER,
        status="reviewed",
        auth_modes=("api_key",),
        supported_environments=("sandbox",),
        capability_source="test",
        provider_capability_status={
            "documents.invoice.list": CapabilityState.DECLARED,
        },
        capability_aliases={
            "documents.invoice.read": ["documents.invoice.list"],
        },
        setup_defaults={"api_base_url": "https://example.test"},
    )

    with pytest.raises(TypeError):
        manifest.provider_capability_status["documents.invoice.create"] = (
            CapabilityState.NOT_VALIDATED
        )
    with pytest.raises(TypeError):
        manifest.capability_aliases["documents.invoice.write"] = (
            "documents.invoice.create",
        )
    with pytest.raises(TypeError):
        manifest.setup_defaults["api_base_url"] = "https://changed.test"


def test_connector_mode_manifest_defensively_copies_source_mappings() -> None:
    provider_status = {
        "documents.invoice.list": CapabilityState.DECLARED,
    }
    capability_aliases = {
        "documents.invoice.read": ["documents.invoice.list"],
    }
    setup_defaults = {"api_base_url": "https://example.test"}

    manifest = ConnectorModeManifest(
        mode=ConnectionMode.API_DRIVER,
        status="reviewed",
        auth_modes=("api_key",),
        supported_environments=("sandbox",),
        capability_source="test",
        provider_capability_status=provider_status,
        capability_aliases=capability_aliases,
        setup_defaults=setup_defaults,
    )

    provider_status["documents.invoice.create"] = CapabilityState.NOT_VALIDATED
    capability_aliases["documents.invoice.read"].append("documents.invoice.get")
    setup_defaults["api_base_url"] = "https://changed.test"

    assert manifest.provider_capability_status == {
        "documents.invoice.list": CapabilityState.DECLARED,
    }
    assert manifest.capability_aliases == {
        "documents.invoice.read": ("documents.invoice.list",),
    }
    assert manifest.setup_defaults == {"api_base_url": "https://example.test"}
    assert isinstance(manifest.capability_aliases["documents.invoice.read"], tuple)
