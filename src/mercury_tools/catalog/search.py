"""Deterministic ranking for immutable ERP catalog actions."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from mercury_tools.catalog.models import CatalogAction, HttpMethod, RiskTier

_MAX_TOP_K = 100
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CatalogMatch:
    action: CatalogAction
    rank_bucket: int
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogSearchResponse:
    matches: tuple[CatalogMatch, ...]
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class _RankedAction:
    match: CatalogMatch
    semantic_score: float


def search_actions(
    actions: Iterable[CatalogAction],
    query: str,
    connector: str | None = None,
    method: HttpMethod | None = None,
    risk_tier: RiskTier | None = None,
    top_k: int = 8,
    semantic_scores: Mapping[str, float] | None = None,
) -> CatalogSearchResponse:
    """Rank matching actions without allowing semantic scores to cross buckets."""
    normalized_query = _normalize_query(query)
    normalized_connector = _normalize_connector(connector)
    _validate_filters(method, risk_tier, top_k)
    scores = _validate_semantic_scores(semantic_scores)
    if not normalized_query:
        return CatalogSearchResponse(matches=(), ambiguous=False)

    query_tokens = _tokens(normalized_query)
    ranked: list[_RankedAction] = []
    for action in actions:
        if not _matches_filters(action, normalized_connector, method, risk_tier):
            continue
        candidate = _rank_action(action, normalized_query, query_tokens, scores)
        if candidate is not None:
            ranked.append(candidate)

    ranked.sort(
        key=lambda item: (
            item.match.rank_bucket,
            -item.match.score,
            -item.semantic_score,
            item.match.action.action_id,
        )
    )
    matches = tuple(item.match for item in ranked[:top_k])
    return CatalogSearchResponse(matches=matches, ambiguous=_is_ambiguous(matches))


def _normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("invalid_query")
    return " ".join(query.casefold().split())


def _normalize_connector(connector: str | None) -> str | None:
    if connector is None:
        return None
    if not isinstance(connector, str) or not connector.strip():
        raise ValueError("invalid_connector_filter")
    return connector.casefold().strip()


def _validate_filters(
    method: HttpMethod | None,
    risk_tier: RiskTier | None,
    top_k: int,
) -> None:
    if method is not None and not isinstance(method, HttpMethod):
        raise ValueError("invalid_method_filter")
    if risk_tier is not None and not isinstance(risk_tier, RiskTier):
        raise ValueError("invalid_risk_tier_filter")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= _MAX_TOP_K:
        raise ValueError("invalid_top_k")


def _validate_semantic_scores(
    semantic_scores: Mapping[str, float] | None,
) -> Mapping[str, float]:
    if semantic_scores is None:
        return {}
    if not isinstance(semantic_scores, Mapping):
        raise ValueError("invalid_semantic_scores")

    validated: dict[str, float] = {}
    for action_id, score in semantic_scores.items():
        if (
            not isinstance(action_id, str)
            or not action_id
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0.0 <= float(score) <= 1.0
        ):
            raise ValueError("invalid_semantic_score")
        validated[action_id] = float(score)
    return validated


def _matches_filters(
    action: CatalogAction,
    connector: str | None,
    method: HttpMethod | None,
    risk_tier: RiskTier | None,
) -> bool:
    return (
        (connector is None or action.connector_id.casefold() == connector)
        and (method is None or action.method is method)
        and (risk_tier is None or action.risk_tier is risk_tier)
    )


def _rank_action(
    action: CatalogAction,
    normalized_query: str,
    query_tokens: frozenset[str],
    semantic_scores: Mapping[str, float],
) -> _RankedAction | None:
    semantic_score = semantic_scores.get(action.action_id, 0.0)
    action_id = _normalize_query(action.action_id)
    capability = _normalize_query(action.capability)
    if normalized_query == action_id:
        return _ranked(action, 1, 1.0, "exact_action_id", semantic_score)
    if normalized_query == capability:
        return _ranked(action, 1, 1.0, "exact_capability", semantic_score)

    aliases = {_normalize_query(alias) for alias in (*action.aliases_th, *action.aliases_en)}
    if normalized_query in aliases:
        return _ranked(action, 2, 1.0, "exact_alias", semantic_score)

    keyword_tokens = _tokens(f"{action.connector_id} {action.capability}")
    keyword_overlap = _overlap_score(query_tokens, keyword_tokens)
    if keyword_overlap > 0.0:
        return _ranked(
            action, 3, keyword_overlap, "connector_or_capability_keyword", semantic_score
        )

    token_overlap = _overlap_score(query_tokens, _search_tokens(action))
    if token_overlap == 0.0 and semantic_score == 0.0:
        return None
    return _ranked(action, 4, token_overlap, "token_overlap", semantic_score)


def _ranked(
    action: CatalogAction,
    rank_bucket: int,
    score: float,
    reason: str,
    semantic_score: float,
) -> _RankedAction:
    return _RankedAction(
        match=CatalogMatch(
            action=action,
            rank_bucket=rank_bucket,
            score=score,
            reasons=(reason,),
        ),
        semantic_score=semantic_score,
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(_TOKEN_PATTERN.findall(value.casefold()))


def _search_tokens(action: CatalogAction) -> frozenset[str]:
    return _tokens(
        " ".join(
            (
                action.connector_id,
                action.capability,
                action.operation_id,
                action.path_template,
                action.description,
                *action.aliases_th,
                *action.aliases_en,
            )
        )
    )


def _overlap_score(query_tokens: frozenset[str], candidate_tokens: frozenset[str]) -> float:
    if not query_tokens:
        return 0.0
    return len(query_tokens & candidate_tokens) / len(query_tokens)


def _is_ambiguous(matches: tuple[CatalogMatch, ...]) -> bool:
    if len(matches) < 2:
        return False
    first, second = matches[:2]
    if first.rank_bucket == second.rank_bucket and first.rank_bucket <= 3:
        return True
    return first.rank_bucket == second.rank_bucket == 4 and first.score - second.score < 0.05
