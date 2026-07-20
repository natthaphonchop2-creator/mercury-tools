from dataclasses import FrozenInstanceError

import pytest

from mercury_tools.catalog.models import HttpMethod, RiskTier
from mercury_tools.execution.policy import (
    ApprovalLevel,
    MutationClass,
    RiskDecision,
    effective_risk,
)


def test_standard_create_requires_one_standard_approval(action_factory) -> None:
    action = action_factory(
        method=HttpMethod.POST,
        side_effects=("creates_document",),
    )

    decision = effective_risk(action)

    assert decision == RiskDecision(
        tier=RiskTier.STANDARD_WRITE,
        approval_level=ApprovalLevel.STANDARD,
        mutation_class=MutationClass.CREATE,
        reasons=(),
    )


@pytest.mark.parametrize("method", [HttpMethod.PUT, HttpMethod.PATCH])
def test_update_requires_one_standard_approval(action_factory, method: HttpMethod) -> None:
    action = action_factory(
        method=method,
        side_effects=("updates_document",),
    )

    decision = effective_risk(action)

    assert decision.approval_level is ApprovalLevel.STANDARD
    assert decision.mutation_class is MutationClass.UPDATE
    assert decision.tier is RiskTier.STANDARD_WRITE


def test_delete_requires_one_elevated_approval(action_factory) -> None:
    action = action_factory(
        method=HttpMethod.DELETE,
        side_effects=("removes_document",),
        risk_tier=RiskTier.HIGH_RISK,
        required_confirmations=2,
    )

    decision = effective_risk(action)

    assert decision.approval_level is ApprovalLevel.ELEVATED
    assert decision.mutation_class is MutationClass.SENSITIVE
    assert decision.tier is RiskTier.HIGH_RISK
    assert decision.reasons == ("delete_method",)


@pytest.mark.parametrize(
    "side_effect",
    [
        "payment_processed",
        "approve_document",
        "void_document",
        "post_journal",
        "finalize_document",
        "send_email",
        "share_document",
        "invite_user",
        "delete_document",
    ],
)
def test_payment_post_void_email_and_share_are_sensitive(
    action_factory,
    side_effect: str,
) -> None:
    action = action_factory(side_effects=(side_effect,))

    decision = effective_risk(action)

    assert decision.approval_level is ApprovalLevel.ELEVATED
    assert decision.mutation_class is MutationClass.SENSITIVE
    assert decision.tier is RiskTier.HIGH_RISK
    assert decision.reasons == ("sensitive_side_effect",)


def test_inferred_unobserved_mutation_is_sensitive(action_factory) -> None:
    action = action_factory(
        confidence="inferred",
        observed_state="untested",
        side_effects=("creates_document",),
    )

    decision = effective_risk(action)

    assert decision.approval_level is ApprovalLevel.ELEVATED
    assert decision.mutation_class is MutationClass.SENSITIVE
    assert decision.tier is RiskTier.HIGH_RISK
    assert decision.reasons == ("inferred_unobserved_mutation",)


def test_catalog_confirmation_count_is_compatibility_data_only(action_factory) -> None:
    action = action_factory(
        method=HttpMethod.POST,
        side_effects=("creates_document",),
        risk_tier=RiskTier.HIGH_RISK,
        required_confirmations=2,
    )

    decision = effective_risk(action)

    assert decision == RiskDecision(
        tier=RiskTier.HIGH_RISK,
        approval_level=ApprovalLevel.STANDARD,
        mutation_class=MutationClass.CREATE,
        reasons=("declared_risk_floor",),
    )


def test_effective_risk_rejects_read_action_without_a_mutation_class(
    action_factory,
) -> None:
    action = action_factory(
        method=HttpMethod.GET,
        risk_tier=RiskTier.SAFE_READ,
        required_confirmations=0,
        side_effects=(),
    )

    with pytest.raises(ValueError, match="^read_action_has_no_mutation_class$"):
        effective_risk(action)


def test_risk_decision_is_frozen(action_factory) -> None:
    decision = effective_risk(action_factory())

    with pytest.raises(FrozenInstanceError):
        decision.tier = RiskTier.HIGH_RISK  # type: ignore[misc]
