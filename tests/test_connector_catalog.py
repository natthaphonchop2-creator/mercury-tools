from mercury_tools.connectors.catalog import (
    connector_by_id,
    is_public_capability_allowed,
    list_connector_summaries,
)


def test_public_policy_allows_reads_and_blocks_mutations() -> None:
    assert is_public_capability_allowed("company.info.read") is True
    assert is_public_capability_allowed("documents.invoice.list") is True
    assert is_public_capability_allowed("documents.invoice.get") is True
    assert is_public_capability_allowed("auth.token.create") is True
    assert is_public_capability_allowed("auth.client_token.create") is True
    assert is_public_capability_allowed("documents.invoice.create") is False
    assert is_public_capability_allowed("documents.invoice.payment.create") is False
    assert is_public_capability_allowed("documents.email.send") is False
    assert is_public_capability_allowed("journal.approve.create") is False


def test_connector_manifest_classifies_public_capabilities() -> None:
    manifest = connector_by_id("flowaccount")

    assert manifest is not None
    assert "documents.invoice.list" in manifest.read_capabilities
    assert "documents.invoice.create" in manifest.blocked_capabilities
    assert set(manifest.read_capabilities).isdisjoint(manifest.blocked_capabilities)


def test_flowaccount_manifest_has_presets_and_capabilities() -> None:
    manifest = connector_by_id("flowaccount")

    assert manifest is not None
    assert manifest.connector_id == "flowaccount"
    assert manifest.status == "available"
    assert manifest.required_secret_fields == ["client_id", "client_secret"]
    assert manifest.preset["grant_type"] == "client_credentials"
    assert manifest.preset["scope"] == "flowaccount-api"
    assert manifest.preset["api_base_url"] == "https://openapi.flowaccount.com/v1"
    assert manifest.preset["token_url"] == "https://openapi.flowaccount.com/v1/token"
    assert "company.info.read" in manifest.capabilities
    assert "documents.invoice.list" in manifest.capabilities
    assert "documents.invoice.create" in manifest.capabilities
    assert "documents.expense.create" in manifest.capabilities
    assert "journal.draft.create" in manifest.capabilities
    assert "documents.email.send" in manifest.capabilities
    assert manifest.validation.safe_probe is True
    assert manifest.validation.healthcheck_endpoint == "/company/info"


def test_peak_manifest_uses_real_open_api_setup_fields() -> None:
    peak = connector_by_id("peak")
    express = connector_by_id("express")
    custom = connector_by_id("custom")

    assert peak is not None
    assert peak.status == "available"
    assert peak.required_secret_fields == [
        "connect_id",
        "connect_key",
        "application_code",
        "user_token",
    ]
    assert peak.preset["auth_method"] == "hmac_sha1_client_token"
    assert peak.preset["token_path"] == "/clienttoken"
    assert peak.environment_presets["uat"]["api_base_url"] == (
        "https://peakengineapidev.azurewebsites.net/api/v1"
    )
    assert "user.info.read" in peak.capabilities
    assert "documents.invoice.list" in peak.capabilities
    assert "documents.invoice.create" in peak.capabilities
    assert "daily_journal.create" in peak.capabilities
    assert "contacts.create" in peak.capabilities
    assert express is not None
    assert express.status == "setup_target"
    assert custom is not None
    assert custom.name == "Custom ERP"
    assert custom.status == "setup_target"
    assert custom.environments == ["production", "sandbox", "gateway"]


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
        "custom",
    }
    assert "client_secret" in serialized
    assert "super-secret" not in serialized
    assert "bearer" not in serialized


def test_flowaccount_public_summary_keeps_setup_field_names_and_urls() -> None:
    manifest = connector_by_id("flowaccount")

    assert manifest is not None
    summary = manifest.public_summary()

    assert summary["required_secret_fields"] == ["client_id", "client_secret"]
    assert summary["preset"]["token_url"] == "https://openapi.flowaccount.com/v1/token"
    assert summary["validation"]["token_url"] == "https://openapi.flowaccount.com/v1/token"
    assert "documents.invoice.list" in summary["read_capabilities"]
    assert "documents.invoice.create" in summary["blocked_capabilities"]
    assert "credential_values" not in summary
