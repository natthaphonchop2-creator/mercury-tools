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


def test_source_ingestion_redacts_credential_containers_headers_and_uri_variants() -> None:
    rejected_value = "task-3-credential-raw-value"
    source = CatalogSource.from_document(
        uri=(
            f"//user:{rejected_value}@example.test/openapi.json?"
            f"access_token={rejected_value}&scope=documents.read"
        ),
        connector_id="flowaccount",
        document={
            "openapi": "3.0.0",
            "authentication": {
                "type": "basic",
                "scheme": "Bearer",
                "scope": "documents.read",
                "username": rejected_value,
                "user": rejected_value,
                "login": rejected_value,
                "password": rejected_value,
                "secret": rejected_value,
                "token": rejected_value,
                "value": rejected_value,
                "field_name": rejected_value,
                "credential_name": rejected_value,
                "key_name": "X-API-Key",
                "header_name": "Authorization",
                "parameter_name": "access_token",
                "client_id_name": "client_id",
                "client_secret_name": "client_secret",
            },
            "headers": [
                {"name": header_name, "value": rejected_value}
                for header_name in (
                    "Authorization",
                    "Proxy-Authorization",
                    "Cookie",
                    "Set-Cookie",
                    "API-Key",
                    "X-API-Key",
                    "X-Auth-Token",
                    "X-Access-Token",
                    "X-Client-Secret",
                    "X-Amz-Security-Token",
                )
            ],
            "relative_uri": f"/v1/items?token={rejected_value}&page=1",
            "templated_uri": (
                f"{{{{baseUrl}}}}/v1/items?X-Auth-Token={rejected_value}"
                "&page=1"
            ),
            "ordinary_relative_uri": "/v1/items?page=1",
        },
        report={"status": "imported"},
    )

    dumped = source.model_dump(mode="json")
    serialized = source.model_dump_json()
    canonical = canonical_json(dumped)

    assert rejected_value not in str(dumped)
    assert rejected_value not in serialized
    assert rejected_value not in canonical
    assert source.source_uri.startswith("//[REDACTED]@example.test/")
    assert dumped["sanitization"]["document"]["ordinary_relative_uri"] == (
        "/v1/items?page=1"
    )
    assert dumped["sanitization"]["document"]["authentication"]["key_name"] == (
        "X-API-Key"
    )
    assert dumped["sanitization"]["document"]["authentication"]["header_name"] == (
        "Authorization"
    )
    authentication = dumped["sanitization"]["document"]["authentication"]
    for key in (
        "username",
        "user",
        "login",
        "password",
        "secret",
        "token",
        "value",
        "field_name",
        "credential_name",
    ):
        assert authentication[key] == "[REDACTED]"
    assert {
        key: authentication[key]
        for key in (
            "key_name",
            "header_name",
            "parameter_name",
            "client_id_name",
            "client_secret_name",
        )
    } == {
        "key_name": "X-API-Key",
        "header_name": "Authorization",
        "parameter_name": "access_token",
        "client_id_name": "client_id",
        "client_secret_name": "client_secret",
    }


@pytest.mark.parametrize(
    ("uri", "expected_uri"),
    [
        (
            "https://user:task-3-credential-raw-value@example.test/v1?"
            "access_token=task-3-credential-raw-value&page=1",
            "https://[REDACTED]@example.test/v1?access_token=%5BREDACTED%5D&page=1",
        ),
        (
            "//user:task-3-credential-raw-value@example.test/v1?"
            "access_token=task-3-credential-raw-value&page=1",
            "//[REDACTED]@example.test/v1?access_token=%5BREDACTED%5D&page=1",
        ),
        (
            "/v1?access_token=task-3-credential-raw-value&page=1",
            "/v1?access_token=%5BREDACTED%5D&page=1",
        ),
        (
            "{{baseUrl}}/v1?access_token=task-3-credential-raw-value&page=1",
            "{{baseUrl}}/v1?access_token=%5BREDACTED%5D&page=1",
        ),
        (
            "user:task-3-credential-raw-value@example.test/v1?"
            "access_token=task-3-credential-raw-value&page=1",
            "[REDACTED]@example.test/v1?access_token=%5BREDACTED%5D&page=1",
        ),
    ],
)
def test_source_ingestion_sanitizes_sensitive_uri_forms(
    uri: str,
    expected_uri: str,
) -> None:
    source = CatalogSource.from_document(
        uri=uri,
        connector_id="flowaccount",
        document={"openapi": "3.0.0", "server_uri": uri},
        report={"status": "imported"},
    )

    dumped = source.model_dump(mode="json")
    canonical = canonical_json(dumped)

    assert source.source_uri == expected_uri
    assert dumped["sanitization"]["document"]["server_uri"] == expected_uri
    assert "task-3-credential-raw-value" not in canonical


def test_source_ingestion_sanitizes_parser_rejected_uri_query_without_losing_fragment() -> None:
    rejected_value = "task-3-malformed-uri-raw-value"
    uri = (
        "https://[invalid/v1?%61ccess%5Ftoken="
        f"{rejected_value}&&scope=documents.read&flag#docs-section"
    )
    expected_uri = (
        "https://[invalid/v1?%61ccess%5Ftoken=[REDACTED]"
        "&&scope=documents.read&flag#docs-section"
    )

    source = CatalogSource.from_document(
        uri=uri,
        connector_id="flowaccount",
        document={"openapi": "3.0.0", "server_uri": uri},
        report={"status": "imported"},
    )

    dumped = source.model_dump(mode="json")
    assert source.source_uri == expected_uri
    assert dumped["sanitization"]["document"]["server_uri"] == expected_uri
    assert rejected_value not in source.model_dump_json()


def test_direct_catalog_action_rejects_parser_rejected_uri_without_echoing_value(
    action_factory,
) -> None:
    rejected_value = "task-3-malformed-uri-raw-value"
    source_uri = (
        "https://[invalid/v1?%61ccess%5Ftoken="
        f"{rejected_value}&&scope=documents.read#docs-section"
    )

    with pytest.raises(ValidationError, match="catalog_credentials_unsafe") as raised:
        action_factory(source_uri=source_uri)

    assert rejected_value not in str(raised.value)


@pytest.mark.parametrize(
    "examples",
    [
        ({"name": "Cookie", "value": "task-3-credential-raw-value"},),
        ({"key": "Proxy-Authorization", "value": "task-3-credential-raw-value"},),
        ({"header": "X-Auth-Token", "value": "task-3-credential-raw-value"},),
        ({"name": "X-Access-Token", "value": "task-3-credential-raw-value"},),
        ({"key": "X-Client-Secret", "value": "task-3-credential-raw-value"},),
        ({"header": "X-Amz-Security-Token", "value": "task-3-credential-raw-value"},),
        (
            {
                "authentication": {
                    key: "task-3-credential-raw-value"
                    for key in (
                        "username",
                        "user",
                        "login",
                        "password",
                        "secret",
                        "token",
                        "value",
                    )
                }
            },
        ),
    ],
)
def test_direct_catalog_actions_reject_new_credential_shapes_without_echoing_them(
    action_factory,
    examples: tuple[dict[str, object], ...],
) -> None:
    rejected_value = "task-3-credential-raw-value"

    with pytest.raises(ValidationError, match="catalog_credentials_unsafe") as raised:
        action_factory(examples=examples)

    assert rejected_value not in str(raised.value)


def test_direct_catalog_actions_allow_safe_parameter_metadata_and_relative_urls(
    action_factory,
) -> None:
    action = action_factory(
        source_uri="/v1/items?page=1",
        examples=(
            {
                "authentication": {
                    "key_name": "X-API-Key",
                    "header_name": "Authorization",
                    "parameter_name": "access_token",
                    "client_id_name": "client_id",
                    "client_secret_name": "client_secret",
                }
            },
        ),
    )

    assert action.source_uri == "/v1/items?page=1"
    assert action.examples[0]["authentication"]["key_name"] == "X-API-Key"


@pytest.mark.parametrize(
    "source_uri",
    [
        "https://user:task-3-credential-raw-value@example.test/v1?"
        "access_token=task-3-credential-raw-value",
        "//user:task-3-credential-raw-value@example.test/v1?"
        "access_token=task-3-credential-raw-value",
        "/v1?access_token=task-3-credential-raw-value",
        "{{baseUrl}}/v1?access_token=task-3-credential-raw-value",
        "user:task-3-credential-raw-value@example.test/v1?"
        "access_token=task-3-credential-raw-value",
    ],
)
def test_direct_catalog_actions_reject_sensitive_uri_forms_without_echoing_them(
    action_factory,
    source_uri: str,
) -> None:
    rejected_value = "task-3-credential-raw-value"

    with pytest.raises(ValidationError, match="catalog_credentials_unsafe") as raised:
        action_factory(source_uri=source_uri)

    assert rejected_value not in str(raised.value)


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
