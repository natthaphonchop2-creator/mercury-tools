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
        "payment",
        "payments",
        "pAyMeNt",
        "pAyMeNtS",
        "PaymentProcessed",
        "payments-processed",
        "approve",
        "approves",
        "ApproveDocument",
        "approves-document",
        "void",
        "voids",
        "VoidDocument",
        "voids-document",
        "post",
        "posts",
        "PostJournal",
        "posts-journal",
        "finalize",
        "finalizes",
        "FinalizeDocument",
        "finalizes-document",
        "email",
        "emails",
        "SendEmail",
        "sEnD_eMaIl",
        "sEnD-eMaIl",
        "sEnD eMaIl",
        "send-emails",
        "EmailCustomer",
        "emails-customer",
        "share",
        "shares",
        "ShareDocument",
        "shares-document",
        "invite",
        "invites",
        "InviteUser",
        "invites-user",
        "delete",
        "deletes",
        "DeleteDocument",
        "deletes-document",
    ],
)
def test_sensitive_effect_aliases_are_elevated(
    action_factory,
    side_effect: str,
) -> None:
    action = action_factory(side_effects=(side_effect,))

    decision = effective_risk(action)

    assert decision.approval_level is ApprovalLevel.ELEVATED
    assert decision.mutation_class is MutationClass.SENSITIVE
    assert decision.tier is RiskTier.HIGH_RISK
    assert decision.reasons == ("sensitive_side_effect",)


@pytest.mark.parametrize(
    "side_effect",
    [
        "ſhare",
        "ſHARE",
        "ſhares",
        "ſhare_document",
        "ſend_email",
        "ſend-emails",
    ],
)
def test_casefold_confusables_do_not_elevate_sensitive_aliases(
    action_factory,
    side_effect: str,
) -> None:
    action = action_factory(side_effects=(side_effect,))

    decision = effective_risk(action)

    assert decision == RiskDecision(
        tier=RiskTier.STANDARD_WRITE,
        approval_level=ApprovalLevel.STANDARD,
        mutation_class=MutationClass.CREATE,
        reasons=(),
    )


@pytest.mark.parametrize(
    "side_effect",
    [
        "postpone",
        "shared_cache",
        "delete_preview",
        "paymentบัญชี",
        "บัญชีpayment",
        "email通知",
        "通知email",
    ],
)
def test_non_sensitive_effects_with_sensitive_substrings_remain_standard(
    action_factory,
    side_effect: str,
) -> None:
    action = action_factory(side_effects=(side_effect,))

    decision = effective_risk(action)

    assert decision == RiskDecision(
        tier=RiskTier.STANDARD_WRITE,
        approval_level=ApprovalLevel.STANDARD,
        mutation_class=MutationClass.CREATE,
        reasons=(),
    )


@pytest.mark.parametrize(
    ("confidence", "observed_state", "expected_class", "expected_reasons"),
    [
        (
            "exact",
            "untested",
            MutationClass.SENSITIVE,
            ("unobserved_mutation",),
        ),
        (
            "inferred",
            "success",
            MutationClass.SENSITIVE,
            ("inferred_mutation",),
        ),
        (
            "inferred",
            "untested",
            MutationClass.SENSITIVE,
            ("inferred_mutation", "unobserved_mutation"),
        ),
        ("exact", "success", MutationClass.CREATE, ()),
    ],
)
def test_mutation_confidence_and_observation_matrix(
    action_factory,
    confidence: str,
    observed_state: str,
    expected_class: MutationClass,
    expected_reasons: tuple[str, ...],
) -> None:
    action = action_factory(
        confidence=confidence,
        observed_state=observed_state,
        side_effects=("creates_document",),
    )

    decision = effective_risk(action)

    expected_elevated = expected_class is MutationClass.SENSITIVE
    assert decision.approval_level is (
        ApprovalLevel.ELEVATED if expected_elevated else ApprovalLevel.STANDARD
    )
    assert decision.mutation_class is expected_class
    assert decision.tier is (
        RiskTier.HIGH_RISK if expected_elevated else RiskTier.STANDARD_WRITE
    )
    assert decision.reasons == expected_reasons


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
