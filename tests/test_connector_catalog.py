from mercury_tools.connectors.catalog import connector_by_id, list_connector_summaries


def test_flowaccount_manifest_has_presets_and_capabilities() -> None:
    manifest = connector_by_id("flowaccount")

    assert manifest is not None
    assert manifest.connector_id == "flowaccount"
    assert manifest.status == "available"
    assert manifest.required_secret_fields == ["client_id", "client_secret"]
    assert manifest.preset["grant_type"] == "client_credentials"
    assert manifest.preset["scope"] == "flowaccount-api"
    assert manifest.preset["api_base_url"] == "https://openapi.flowaccount.com/v1"
    assert manifest.preset["token_url"] == "https://openapi.flowaccount.com/token"
    assert "company.info.read" in manifest.capabilities
    assert "documents.invoice.list" in manifest.capabilities
    assert manifest.validation.read_only is True


def test_setup_target_manifests_are_visible_but_not_live() -> None:
    peak = connector_by_id("peak")
    express = connector_by_id("express")

    assert peak is not None
    assert peak.status == "setup_target"
    assert express is not None
    assert express.status == "setup_target"


def test_connector_by_id_is_case_and_whitespace_normalized() -> None:
    spaced = connector_by_id(" FlowAccount ")
    upper = connector_by_id("FLOWACCOUNT")

    assert spaced is not None
    assert upper is not None
    assert spaced.connector_id == "flowaccount"
    assert upper.connector_id == "flowaccount"
    assert spaced.connector_id == upper.connector_id


def test_connector_summaries_do_not_include_secrets() -> None:
    summaries = list_connector_summaries()
    serialized = str(summaries).lower()

    assert {item["connector_id"] for item in summaries} >= {
        "flowaccount",
        "peak",
        "express",
    }
    assert "client_secret" in serialized
    assert "super-secret" not in serialized
    assert "bearer" not in serialized
