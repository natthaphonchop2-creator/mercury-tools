from mercury_tools.connectors.catalog import (
    connector_by_id,
    list_connector_public_summaries,
)


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
