"""Runtime risk floors for immutable catalog actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from mercury_tools.catalog.models import (
    ActionConfidence,
    CatalogAction,
    HttpMethod,
    ObservedState,
    RiskTier,
)

SENSITIVE_EFFECT_ALIASES = {
    "payment": "payment",
    "payments": "payment",
    "payment_processed": "payment",
    "payments_processed": "payment",
    "approve": "approve",
    "approves": "approve",
    "approve_document": "approve",
    "approves_document": "approve",
    "void": "void",
    "voids": "void",
    "void_document": "void",
    "voids_document": "void",
    "post": "post",
    "posts": "post",
    "post_journal": "post",
    "posts_journal": "post",
    "finalize": "finalize",
    "finalizes": "finalize",
    "finalize_document": "finalize",
    "finalizes_document": "finalize",
    "email": "email",
    "emails": "email",
    "send_email": "email",
    "send_emails": "email",
    "email_customer": "email",
    "emails_customer": "email",
    "share": "share",
    "shares": "share",
    "share_document": "share",
    "shares_document": "share",
    "invite": "invite",
    "invites": "invite",
    "invite_user": "invite",
    "invites_user": "invite",
    "delete": "delete",
    "deletes": "delete",
    "delete_document": "delete",
    "deletes_document": "delete",
}
_EFFECT_TOKEN_PATTERN = re.compile(r"[A-Z]+(?=[A-Z][a-z]|[^A-Za-z]|$)|[A-Z]?[a-z]+|\d+")


class ApprovalLevel(StrEnum):
    STANDARD = "standard"
    ELEVATED = "elevated"


class MutationClass(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    tier: RiskTier
    approval_level: ApprovalLevel
    mutation_class: MutationClass
    reasons: tuple[str, ...]


def effective_risk(action: CatalogAction) -> RiskDecision:
    """Classify one mutation without treating legacy prompt counts as policy."""
    if action.method is HttpMethod.GET:
        raise ValueError("read_action_has_no_mutation_class")

    mutation_class = (
        MutationClass.UPDATE
        if action.method in {HttpMethod.PUT, HttpMethod.PATCH}
        else MutationClass.CREATE
    )
    runtime_tier = RiskTier.STANDARD_WRITE
    reasons: list[str] = []

    if action.method is HttpMethod.DELETE:
        mutation_class = MutationClass.SENSITIVE
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("delete_method")
    elif _has_sensitive_effect(action):
        mutation_class = MutationClass.SENSITIVE
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("sensitive_side_effect")

    if action.confidence is ActionConfidence.INFERRED:
        mutation_class = MutationClass.SENSITIVE
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("inferred_mutation")
    if action.observed_state is ObservedState.UNTESTED:
        mutation_class = MutationClass.SENSITIVE
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("unobserved_mutation")

    tier = max(action.risk_tier, runtime_tier)
    if action.risk_tier > runtime_tier:
        reasons.insert(0, "declared_risk_floor")
    approval_level = (
        ApprovalLevel.ELEVATED
        if mutation_class is MutationClass.SENSITIVE
        else ApprovalLevel.STANDARD
    )
    return RiskDecision(
        tier=tier,
        approval_level=approval_level,
        mutation_class=mutation_class,
        reasons=tuple(reasons),
    )


def _has_sensitive_effect(action: CatalogAction) -> bool:
    return any(_canonical_sensitive_effect(effect) is not None for effect in action.side_effects)


def _canonical_sensitive_effect(effect: str) -> str | None:
    normalized_effect = "_".join(
        token.casefold() for token in _EFFECT_TOKEN_PATTERN.findall(effect)
    )
    return SENSITIVE_EFFECT_ALIASES.get(normalized_effect)
