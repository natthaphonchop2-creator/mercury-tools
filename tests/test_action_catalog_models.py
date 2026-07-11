from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mercury_tools.catalog.identity import (
    build_action_id,
    build_source_id,
    build_version_id,
    canonical_json,
)
from mercury_tools.catalog.models import CatalogSource, HttpMethod, RiskTier


def test_action_id_is_stable_but_version_changes_with_content(action_factory) -> None:
    first = action_factory(
        source_uri="global://flow",
        description="Create invoice",
    )
    second = action_factory(
        source_uri="local://flow",
        description="Create invoice with project",
    )

    assert first.action_id == second.action_id
    assert first.version_id != second.version_id


def test_catalog_action_is_frozen(catalog_action) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        catalog_action.description = "changed"


def test_catalog_action_nested_structures_are_deeply_frozen(action_factory) -> None:
    action = action_factory(
        input_schema={"body": {"enum": ["draft", "sent"]}},
        idempotency={"statuses": {"pending", "complete"}},
    )
    version_id = action.version_id

    with pytest.raises(TypeError):
        action.input_schema["body"]["enum"][0] = "changed"
    with pytest.raises(TypeError):
        action.input_schema["body"] = {"type": "array"}
    with pytest.raises(TypeError):
        action.input_schema.__init__({"body": {"type": "array"}})

    dumped = action.model_dump(mode="json")
    assert dumped["input_schema"]["body"]["enum"] == ["draft", "sent"]
    assert set(dumped["idempotency"]["statuses"]) == {"pending", "complete"}
    assert isinstance(action.environments, tuple)
    assert build_version_id(action) == version_id


def test_catalog_source_nested_structures_are_deeply_frozen(catalog_source) -> None:
    source_hash = catalog_source.source_hash

    with pytest.raises(TypeError):
        catalog_source.sanitization["report"]["status"] = "changed"

    assert catalog_source.source_hash == source_hash


@pytest.mark.parametrize(
    ("method", "risk_tier", "required_confirmations"),
    [
        ("GET", RiskTier.SAFE_READ, 0),
        ("POST", RiskTier.STANDARD_WRITE, 1),
        ("POST", RiskTier.HIGH_RISK, 2),
        ("PUT", RiskTier.STANDARD_WRITE, 1),
        ("PATCH", RiskTier.HIGH_RISK, 2),
        ("DELETE", RiskTier.HIGH_RISK, 2),
    ],
)
def test_method_risk_tier_and_confirmation_matrix_accepts_valid_combinations(
    action_factory,
    method: str,
    risk_tier: RiskTier,
    required_confirmations: int,
) -> None:
    action = action_factory(
        method=method,
        risk_tier=risk_tier,
        required_confirmations=required_confirmations,
    )

    assert action.required_confirmations == int(action.risk_tier)


def test_risk_tier_rejects_mismatched_confirmation_count(action_factory) -> None:
    with pytest.raises(ValidationError, match="required_confirmations"):
        action_factory(risk_tier=RiskTier.HIGH_RISK, required_confirmations=1)


@pytest.mark.parametrize(
    ("method", "risk_tier", "required_confirmations"),
    [
        ("GET", RiskTier.STANDARD_WRITE, 1),
        ("GET", RiskTier.HIGH_RISK, 2),
        ("POST", RiskTier.SAFE_READ, 0),
        ("PUT", RiskTier.SAFE_READ, 0),
        ("PATCH", RiskTier.SAFE_READ, 0),
        ("DELETE", RiskTier.SAFE_READ, 0),
        ("DELETE", RiskTier.STANDARD_WRITE, 1),
    ],
)
def test_method_risk_tier_and_confirmation_matrix_rejects_invalid_combinations(
    action_factory,
    method: str,
    risk_tier: RiskTier,
    required_confirmations: int,
) -> None:
    with pytest.raises(ValidationError, match="method_risk_tier_invalid"):
        action_factory(
            method=method,
            risk_tier=risk_tier,
            required_confirmations=required_confirmations,
        )


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS", "post"])
def test_action_rejects_http_methods_outside_the_catalog_contract(
    action_factory,
    method: str,
) -> None:
    with pytest.raises(ValidationError):
        action_factory(method=method)


def test_http_method_exposes_only_contract_methods() -> None:
    assert {item.value for item in HttpMethod} == {"GET", "POST", "PUT", "PATCH", "DELETE"}


def test_canonical_json_rejects_non_finite_and_unsupported_values() -> None:
    with pytest.raises(ValueError, match="non_finite"):
        canonical_json({"value": float("nan")})
    with pytest.raises(ValueError, match="unsupported"):
        canonical_json({"value": object()})


def test_structured_identity_hashing_does_not_collide_on_delimiters() -> None:
    first = SimpleNamespace(
        connector_id="flow|account",
        method="POST",
        path_template="/invoices",
        operation_id="create",
        variant_id="simple",
    )
    second = SimpleNamespace(
        connector_id="flow",
        method="account|POST",
        path_template="/invoices",
        operation_id="create",
        variant_id="simple",
    )

    assert build_action_id(first) != build_action_id(second)
    assert build_source_id("flow|account", "https://example.test", "a" * 64) != (
        build_source_id("flow", "account|https://example.test", "a" * 64)
    )


def test_source_identity_and_hash_use_sanitized_document_and_report() -> None:
    source = CatalogSource.from_document(
        uri="https://example.test/openapi.json",
        connector_id="flowaccount",
        document={"openapi": "3.0.0", "authorization": "Bearer hidden-document-token"},
        report={"api_key": "hidden-report-key", "status": "imported"},
    )
    same_sanitized = CatalogSource.from_document(
        uri="https://example.test/openapi.json",
        connector_id="flowaccount",
        document={"openapi": "3.0.0", "authorization": "Bearer another-token"},
        report={"api_key": "another-key", "status": "imported"},
    )

    serialized = source.model_dump_json().lower()
    assert source.source_hash == same_sanitized.source_hash
    assert source.source_id == same_sanitized.source_id
    assert "hidden" not in serialized
    assert source.imported_at.tzinfo is UTC


def test_source_ingestion_recursively_sanitizes_credentials_and_uris() -> None:
    rejected_value = "synthetic-value-for-redaction"
    source = CatalogSource.from_document(
        uri=f"https://user:{rejected_value}@example.test/openapi.json?api_key={rejected_value}",
        connector_id="flowaccount",
        document={
            "openapi": "3.0.0",
            "servers": [
                {
                    "url": (
                        "https://example.test/v1?access_token="
                        f"{rejected_value}&scope=documents.read"
                    )
                }
            ],
            "headers": [{"key": "X-API-Key", "value": rejected_value}],
            "nested": {"client_secret": rejected_value},
            "metadata": {
                "key_name": "X-API-Key",
                "scope": "documents.read",
                "grant_type": "client_credentials",
            },
        },
        report={
            "request_headers": [
                {"name": "Authorization", "value": f"Bearer {rejected_value}"}
            ]
        },
    )

    serialized = source.model_dump_json()
    metadata = source.sanitization["document"]["metadata"]
    assert rejected_value not in serialized
    assert source.source_uri.startswith("https://[REDACTED]@example.test/")
    assert metadata == {
        "key_name": "X-API-Key",
        "scope": "documents.read",
        "grant_type": "client_credentials",
    }


def test_source_ingestion_sanitizes_key_variants_and_known_token_prefixes() -> None:
    rejected_value = "synthetic-value-for-redaction"
    source = CatalogSource.from_document(
        uri="https://example.test/openapi.json",
        connector_id="flowaccount",
        document={
            "openapi": "3.0.0",
            "consumerSecret": rejected_value,
            "oauth_token": rejected_value,
            "refresh_tokens": [rejected_value, f"Bearer {rejected_value}"],
            "credentials": {
                "type": "oauth2",
                "scope": "documents.read",
                "primary": rejected_value,
            },
            "sample": f"AKIA{rejected_value}",
            "oauth_metadata": {
                "token_url": "https://example.test/oauth/token",
                "credential_name": "primary",
                "key_name": "X-API-Key",
            },
        },
        report={"status": "imported"},
    )

    serialized = source.model_dump_json()
    metadata = source.sanitization["document"]["oauth_metadata"]
    assert rejected_value not in serialized
    assert metadata == {
        "token_url": "https://example.test/oauth/token",
        "credential_name": "primary",
        "key_name": "X-API-Key",
    }


@pytest.mark.parametrize(
    "override",
    [
        {"examples": ({"headers": [{"key": "X-API-Key", "value": "raw-value"}]},)},
        {"source_uri": "https://user:raw-value@example.test/openapi.json"},
        {"description": "Bearer raw-value"},
    ],
)
def test_direct_catalog_actions_reject_credentials_without_echoing_them(
    action_factory,
    override: dict[str, object],
) -> None:
    rejected_value = "raw-value"

    with pytest.raises(ValidationError, match="catalog_credentials_unsafe") as raised:
        action_factory(**override)

    assert rejected_value not in str(raised.value)


def test_direct_catalog_source_rejects_credentials_without_echoing_them(
    catalog_source,
) -> None:
    rejected_value = "raw-value"
    data = catalog_source.model_dump(mode="json")
    data["driver_suggestion"] = {"client_secret": rejected_value}

    with pytest.raises(ValidationError, match="catalog_credentials_unsafe") as raised:
        CatalogSource.model_validate(data)

    assert rejected_value not in str(raised.value)


def test_catalog_source_recomputes_hash_and_identity_from_sanitization(catalog_source) -> None:
    data = catalog_source.model_dump(mode="json")
    data["sanitization"]["report"]["status"] = "changed"

    with pytest.raises(ValidationError, match="catalog_source_hash_invalid"):
        CatalogSource.model_validate(data)

    data = catalog_source.model_dump(mode="json")
    data["source_id"] = build_source_id(
        catalog_source.connector_id,
        catalog_source.source_uri,
        "b" * 64,
    )
    with pytest.raises(ValidationError, match="catalog_source_id_invalid"):
        CatalogSource.model_validate(data)


@pytest.mark.parametrize(
    "sanitization",
    [
        {"document": {}},
        {"document": {}, "report": {}, "extra": {}},
    ],
)
def test_catalog_source_requires_exact_self_contained_sanitization_payload(
    catalog_source,
    sanitization: dict[str, object],
) -> None:
    data = catalog_source.model_dump(mode="json")
    data["sanitization"] = sanitization

    with pytest.raises(ValidationError, match="catalog_source_sanitization_invalid"):
        CatalogSource.model_validate(data)


def test_catalog_source_rejects_naive_import_time(catalog_source) -> None:
    data = catalog_source.model_dump(mode="json")
    data["imported_at"] = datetime(2026, 7, 11, 12, 0)

    with pytest.raises(ValidationError, match="catalog_source_imported_at_naive"):
        CatalogSource.model_validate(data)


def test_catalog_source_normalizes_aware_import_time_to_utc(catalog_source) -> None:
    data = catalog_source.model_dump(mode="json")
    local_time = datetime(2026, 7, 11, 12, 0, tzinfo=timezone(timedelta(hours=7)))
    data["imported_at"] = local_time

    source = CatalogSource.model_validate(data)

    assert source.imported_at == datetime(2026, 7, 11, 5, 0, tzinfo=UTC)
    assert source.imported_at.tzinfo is UTC
