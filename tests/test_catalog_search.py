from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from mercury_tools.catalog.models import HttpMethod, RiskTier
from mercury_tools.catalog.search import (
    CatalogMatch,
    CatalogSearchResponse,
    search_actions,
)


def test_exact_action_id_and_capability_beat_aliases_and_keywords(action_factory) -> None:
    exact = action_factory(
        operation_id="exact",
        capability="documents.invoice.create",
        aliases_en=("issue invoice",),
    )
    alias = action_factory(
        operation_id="alias",
        capability="documents.receipt.create",
        aliases_en=("documents.invoice.create",),
    )

    by_capability = search_actions([alias, exact], "  DOCUMENTS.INVOICE.CREATE  ")
    by_action_id = search_actions([alias, exact], exact.action_id.upper())

    assert by_capability.matches[0].action == exact
    assert by_capability.matches[0].rank_bucket == 1
    assert by_capability.matches[0].reasons == ("exact_capability",)
    assert by_capability.ambiguous is False
    assert by_action_id.matches[0].action == exact
    assert by_action_id.matches[0].reasons == ("exact_action_id",)


@pytest.mark.parametrize("query", ["บันทึกชำระเงิน", "RECORD PAYMENT"])
def test_exact_thai_and_english_aliases_use_bucket_two(action_factory, query: str) -> None:
    action = action_factory(
        aliases_th=("บันทึกชำระเงิน",),
        aliases_en=("record payment",),
    )

    result = search_actions([action], query)

    assert result.matches == (
        CatalogMatch(action=action, rank_bucket=2, score=1.0, reasons=("exact_alias",)),
    )
    assert result.ambiguous is False


def test_keyword_ranking_precedes_normalized_token_overlap(action_factory) -> None:
    keyword = action_factory(
        connector_id="peak",
        operation_id="keyword",
        capability="billing.supplier.create",
        aliases_en=("create payment",),
    )
    overlap = action_factory(
        connector_id="flowaccount",
        operation_id="overlap",
        capability="documents.invoice.create",
        aliases_en=("supplier bill payment",),
    )

    result = search_actions([overlap, keyword], "supplier bill")

    assert [match.action for match in result.matches] == [keyword, overlap]
    assert [match.rank_bucket for match in result.matches] == [3, 4]
    assert result.matches[0].reasons == ("connector_or_capability_keyword",)
    assert result.matches[1].reasons == ("token_overlap",)


def test_semantic_scores_only_break_a_tie_inside_one_rank_bucket(action_factory) -> None:
    first = action_factory(
        operation_id="first",
        capability="documents.receipt.create",
        aliases_en=("record document",),
    )
    second = action_factory(operation_id="second", aliases_en=("record document",))
    exact = action_factory(
        operation_id="exact",
        capability="documents.invoice.create",
    )

    tied = search_actions(
        [first, second],
        "book supplier bill",
        semantic_scores={first.action_id: 0.20, second.action_id: 0.91},
    )
    protected_exact = search_actions(
        [first, exact],
        "documents.invoice.create",
        semantic_scores={first.action_id: 1.0, exact.action_id: 0.0},
    )

    assert [match.action for match in tied.matches] == [second, first]
    assert [match.rank_bucket for match in tied.matches] == [4, 4]
    assert protected_exact.matches[0].action == exact
    assert protected_exact.matches[0].rank_bucket == 1


def test_tie_order_uses_action_id_after_rank_and_scores(action_factory) -> None:
    first = action_factory(operation_id="first", aliases_en=("record document",))
    second = action_factory(operation_id="second", aliases_en=("record document",))

    result = search_actions([first, second], "record document")

    assert [match.action.action_id for match in result.matches] == sorted(
        [first.action_id, second.action_id]
    )
    assert result.ambiguous is True


def test_filters_apply_before_ranking_and_top_k_has_exact_boundaries(action_factory) -> None:
    matching_get = action_factory(
        connector_id="peak",
        method=HttpMethod.GET,
        operation_id="one",
        capability="documents.invoice.list",
        risk_tier=RiskTier.SAFE_READ,
        required_confirmations=0,
        aliases_en=("list invoices",),
    )
    matching_post = action_factory(
        connector_id="peak",
        method=HttpMethod.POST,
        operation_id="two",
        capability="documents.invoice.create",
        aliases_en=("create invoices",),
    )
    different_connector = action_factory(
        connector_id="flowaccount",
        operation_id="three",
        aliases_en=("create invoices",),
    )

    filtered = search_actions(
        [matching_post, different_connector, matching_get],
        "invoice",
        connector="PEAK",
        method=HttpMethod.GET,
        risk_tier=RiskTier.SAFE_READ,
        top_k=1,
    )
    all_matching = search_actions(
        [matching_post, different_connector, matching_get],
        "invoice",
        connector="peak",
        top_k=8,
    )

    assert filtered.matches == (
        CatalogMatch(
            action=matching_get,
            rank_bucket=3,
            score=1.0,
            reasons=("connector_or_capability_keyword",),
        ),
    )
    assert len(all_matching.matches) == 2
    assert {match.action.action_id for match in all_matching.matches} == {
        matching_get.action_id,
        matching_post.action_id,
    }


def test_empty_and_no_match_queries_return_no_candidates(action_factory) -> None:
    action = action_factory(aliases_en=("create invoice",))

    assert search_actions([action], "").matches == ()
    assert search_actions([action], "unrelated quantum weather").matches == ()
    assert search_actions([], "create invoice").matches == ()


def test_ambiguity_follows_bucket_and_bucket_four_score_rules(action_factory) -> None:
    first_alias = action_factory(operation_id="alias-one", aliases_en=("record payment",))
    second_alias = action_factory(operation_id="alias-two", aliases_en=("record payment",))
    near_first = action_factory(operation_id="near-one", aliases_en=("supplier bill record",))
    near_second = action_factory(operation_id="near-two", aliases_en=("supplier bill payment",))
    clear_second = action_factory(operation_id="clear-two", aliases_en=("supplier",))

    alias_result = search_actions([first_alias, second_alias], "record payment")
    near_result = search_actions([near_first, near_second], "supplier bill")
    clear_result = search_actions([near_first, clear_second], "supplier bill")

    assert alias_result.ambiguous is True
    assert near_result.matches[0].rank_bucket == near_result.matches[1].rank_bucket == 4
    assert near_result.matches[0].score - near_result.matches[1].score < 0.05
    assert near_result.ambiguous is True
    assert clear_result.matches[0].score - clear_result.matches[1].score >= 0.05
    assert clear_result.ambiguous is False


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"connector": ""}, "invalid_connector_filter"),
        ({"connector": 3}, "invalid_connector_filter"),
        ({"method": "GET"}, "invalid_method_filter"),
        ({"risk_tier": 1}, "invalid_risk_tier_filter"),
        ({"top_k": 0}, "invalid_top_k"),
        ({"top_k": 101}, "invalid_top_k"),
        ({"top_k": True}, "invalid_top_k"),
        ({"semantic_scores": {"known": nan}}, "invalid_semantic_score"),
        ({"semantic_scores": {"known": inf}}, "invalid_semantic_score"),
        ({"semantic_scores": {"known": -0.01}}, "invalid_semantic_score"),
        ({"semantic_scores": {"known": 1.01}}, "invalid_semantic_score"),
        ({"semantic_scores": {"known": True}}, "invalid_semantic_score"),
        ({"semantic_scores": []}, "invalid_semantic_scores"),
    ],
)
def test_invalid_filters_and_scores_fail_with_stable_identifiers(
    action_factory,
    kwargs: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{error}$"):
        search_actions([action_factory()], "invoice", **kwargs)


def test_search_result_models_are_frozen(action_factory) -> None:
    action = action_factory()
    match = CatalogMatch(action=action, rank_bucket=1, score=1.0, reasons=("exact_action_id",))
    response = CatalogSearchResponse(matches=(match,), ambiguous=False)

    with pytest.raises(FrozenInstanceError):
        match.score = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        response.ambiguous = True  # type: ignore[misc]
