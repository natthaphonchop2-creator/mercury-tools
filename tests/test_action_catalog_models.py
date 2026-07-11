from datetime import UTC

import pytest
from pydantic import ValidationError

from mercury_tools.catalog.identity import canonical_json
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


@pytest.mark.parametrize(
    ("risk_tier", "required_confirmations"),
    [
        (RiskTier.SAFE_READ, 0),
        (RiskTier.STANDARD_WRITE, 1),
        (RiskTier.HIGH_RISK, 2),
    ],
)
def test_risk_tier_requires_its_matching_confirmation_count(
    action_factory,
    risk_tier: RiskTier,
    required_confirmations: int,
) -> None:
    action = action_factory(
        risk_tier=risk_tier,
        required_confirmations=required_confirmations,
    )

    assert action.required_confirmations == int(action.risk_tier)


def test_risk_tier_rejects_mismatched_confirmation_count(action_factory) -> None:
    with pytest.raises(ValidationError, match="required_confirmations"):
        action_factory(risk_tier=RiskTier.HIGH_RISK, required_confirmations=1)


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
