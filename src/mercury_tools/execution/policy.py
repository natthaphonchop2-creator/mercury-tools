"""Runtime risk floors for immutable catalog actions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mercury_tools.catalog.models import (
    ActionConfidence,
    CatalogAction,
    HttpMethod,
    ObservedState,
    RiskTier,
)

HIGH_RISK_EFFECTS = frozenset({"payment", "approve", "void", "delete", "email", "share", "invite"})
_EFFECT_TOKEN_PATTERN = re.compile(r"[A-Z]+(?=[A-Z][a-z]|[^A-Za-z]|$)|[A-Z]?[a-z]+|\d+")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    tier: RiskTier
    required_confirmations: int
    reasons: tuple[str, ...]


def effective_risk(action: CatalogAction) -> RiskDecision:
    """Apply non-decreasing runtime risk and confirmation requirements."""
    runtime_tier = (
        RiskTier.SAFE_READ if action.method is HttpMethod.GET else RiskTier.STANDARD_WRITE
    )
    reasons: list[str] = []

    if action.method is HttpMethod.DELETE:
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("delete_method")
    elif _has_high_risk_effect(action):
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("high_risk_side_effect")

    if (
        action.method is not HttpMethod.GET
        and action.confidence is ActionConfidence.INFERRED
        and action.observed_state is ObservedState.UNTESTED
    ):
        runtime_tier = RiskTier.HIGH_RISK
        reasons.append("inferred_unobserved_mutation")

    tier = max(action.risk_tier, runtime_tier)
    if action.risk_tier > runtime_tier:
        reasons.insert(0, "declared_risk_floor")
    confirmations = max(action.required_confirmations, int(tier))
    return RiskDecision(tier=tier, required_confirmations=confirmations, reasons=tuple(reasons))


def _has_high_risk_effect(action: CatalogAction) -> bool:
    return any(HIGH_RISK_EFFECTS & _effect_tokens(effect) for effect in action.side_effects)


def _effect_tokens(effect: str) -> frozenset[str]:
    return frozenset(token.casefold() for token in _EFFECT_TOKEN_PATTERN.findall(effect))
