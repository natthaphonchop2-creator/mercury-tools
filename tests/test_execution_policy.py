from dataclasses import FrozenInstanceError

import pytest

from mercury_tools.catalog.models import HttpMethod, RiskTier
from mercury_tools.execution.policy import RiskDecision, effective_risk


@pytest.mark.parametrize(
    ("method", "side_effects", "confidence", "observed", "declared", "expected"),
    [
        (HttpMethod.GET, (), "exact", "success", RiskTier.SAFE_READ, RiskTier.SAFE_READ),
        (
            HttpMethod.POST,
            ("creates_document",),
            "exact",
            "success",
            RiskTier.STANDARD_WRITE,
            RiskTier.STANDARD_WRITE,
        ),
        (
            HttpMethod.PUT,
            ("updates_document",),
            "exact",
            "success",
            RiskTier.STANDARD_WRITE,
            RiskTier.STANDARD_WRITE,
        ),
        (
            HttpMethod.PATCH,
            ("updates_document",),
            "exact",
            "success",
            RiskTier.STANDARD_WRITE,
            RiskTier.STANDARD_WRITE,
        ),
        (
            HttpMethod.DELETE,
            ("deletes_document",),
            "exact",
            "success",
            RiskTier.HIGH_RISK,
            RiskTier.HIGH_RISK,
        ),
        (
            HttpMethod.POST,
            ("payment_processed",),
            "exact",
            "success",
            RiskTier.STANDARD_WRITE,
            RiskTier.HIGH_RISK,
        ),
        (
            HttpMethod.POST,
            ("APPROVE_DOCUMENT",),
            "exact",
            "success",
            RiskTier.STANDARD_WRITE,
            RiskTier.HIGH_RISK,
        ),
        (
            HttpMethod.POST,
            ("creates_document",),
            "inferred",
            "untested",
            RiskTier.STANDARD_WRITE,
            RiskTier.HIGH_RISK,
        ),
        (
            HttpMethod.PATCH,
            ("updates_document",),
            "inferred",
            "success",
            RiskTier.STANDARD_WRITE,
            RiskTier.STANDARD_WRITE,
        ),
    ],
)
def test_effective_risk_enforces_method_effect_and_observation_floors(
    action_factory,
    method: HttpMethod,
    side_effects: tuple[str, ...],
    confidence: str,
    observed: str,
    declared: RiskTier,
    expected: RiskTier,
) -> None:
    action = action_factory(
        method=method,
        side_effects=side_effects,
        confidence=confidence,
        observed_state=observed,
        risk_tier=declared,
        required_confirmations=int(declared),
    )

    decision = effective_risk(action)

    assert decision.tier is expected
    assert decision.required_confirmations == int(expected)
    assert decision.tier >= action.risk_tier
    assert decision.required_confirmations >= action.required_confirmations


def test_effective_risk_preserves_declared_high_risk_floor(action_factory) -> None:
    action = action_factory(
        method=HttpMethod.POST,
        side_effects=("creates_document",),
        risk_tier=RiskTier.HIGH_RISK,
        required_confirmations=2,
    )

    decision = effective_risk(action)

    assert decision == RiskDecision(
        tier=RiskTier.HIGH_RISK,
        required_confirmations=2,
        reasons=("declared_risk_floor",),
    )


def test_effective_risk_uses_stable_reason_identifiers(action_factory) -> None:
    action = action_factory(
        method=HttpMethod.POST,
        side_effects=("send_email", "payment_processed"),
        confidence="inferred",
        observed_state="untested",
    )

    decision = effective_risk(action)

    assert decision.reasons == (
        "high_risk_side_effect",
        "inferred_unobserved_mutation",
    )
    assert all("send_email" not in reason for reason in decision.reasons)


def test_delete_reason_is_stable_even_without_matching_effect_name(action_factory) -> None:
    action = action_factory(
        method=HttpMethod.DELETE,
        side_effects=("removes_document",),
        risk_tier=RiskTier.HIGH_RISK,
        required_confirmations=2,
    )

    assert effective_risk(action).reasons == ("delete_method",)


def test_risk_decision_is_frozen(action_factory) -> None:
    decision = effective_risk(action_factory())

    with pytest.raises(FrozenInstanceError):
        decision.tier = RiskTier.HIGH_RISK  # type: ignore[misc]
