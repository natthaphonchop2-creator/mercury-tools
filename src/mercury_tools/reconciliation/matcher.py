"""Stable one-to-one matching for canonical accounting transactions."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from heapq import heappop, heappush
from typing import Any, Literal

from mercury_tools.reconciliation.models import (
    MONEY_QUANTUM,
    CanonicalTransaction,
    DuplicateEvidence,
    PairEvidence,
    ReconciliationPolicy,
    ReconciliationResult,
    UnmatchedEvidence,
)


@dataclass(frozen=True)
class _Candidate:
    left: CanonicalTransaction
    right: CanonicalTransaction
    classification: Literal["matched", "difference"]
    amount_difference: Decimal
    date_difference_days: int
    matched_fields: tuple[str, ...]
    quality: tuple[int, int, int, Decimal, int]

    @property
    def stable_rank(self) -> tuple[Any, ...]:
        return (*self.quality, self.left.transaction_id, self.right.transaction_id)


@dataclass
class _FlowEdge:
    to: int
    reverse: int
    capacity: int
    cost: int


def _text_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _transaction_sort_key(transaction: CanonicalTransaction) -> tuple[str, str]:
    return (transaction.transaction_id, transaction.source)


def _duplicate_key(transaction: CanonicalTransaction) -> tuple[Any, ...]:
    return (
        transaction.source,
        transaction.amount,
        transaction.currency,
        transaction.transaction_date,
        _text_key(transaction.reference),
        _text_key(transaction.counterparty_key),
        transaction.document_state,
    )


def _normalize_transactions(
    rows: Sequence[CanonicalTransaction | Mapping[str, Any]],
) -> tuple[CanonicalTransaction, ...]:
    normalized = tuple(
        row
        if isinstance(row, CanonicalTransaction)
        else CanonicalTransaction.model_validate(row)
        for row in rows
    )
    ids = [row.transaction_id for row in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("reconciliation_transaction_id_duplicate")
    return tuple(sorted(normalized, key=_transaction_sort_key))


def _partition_duplicates(
    rows: tuple[CanonicalTransaction, ...],
    *,
    side: Literal["left", "right"],
) -> tuple[tuple[CanonicalTransaction, ...], tuple[DuplicateEvidence, ...]]:
    grouped: dict[tuple[Any, ...], list[CanonicalTransaction]] = defaultdict(list)
    for row in rows:
        grouped[_duplicate_key(row)].append(row)

    canonical: list[CanonicalTransaction] = []
    evidence: list[DuplicateEvidence] = []
    for group in grouped.values():
        ordered = sorted(group, key=_transaction_sort_key)
        canonical.append(ordered[0])
        if len(ordered) > 1:
            evidence.append(
                DuplicateEvidence(
                    side=side,
                    canonical_transaction_id=ordered[0].transaction_id,
                    transaction_ids=tuple(row.transaction_id for row in ordered),
                    evidence_refs=_ordered_unique(
                        ref for row in ordered for ref in row.evidence_refs
                    ),
                )
            )
    return (
        tuple(sorted(canonical, key=_transaction_sort_key)),
        tuple(sorted(evidence, key=lambda item: item.transaction_ids)),
    )


def _candidate_for(
    left: CanonicalTransaction,
    right: CanonicalTransaction,
    policy: ReconciliationPolicy,
) -> _Candidate | None:
    if left.currency != right.currency:
        return None
    date_difference = abs((left.transaction_date - right.transaction_date).days)
    if date_difference > policy.date_tolerance_days:
        return None
    if policy.require_document_state_match and left.document_state != right.document_state:
        return None

    reference_match = bool(
        _text_key(left.reference)
        and _text_key(left.reference) == _text_key(right.reference)
    )
    counterparty_match = bool(
        _text_key(left.counterparty_key)
        and _text_key(left.counterparty_key) == _text_key(right.counterparty_key)
    )
    if policy.require_identity_match and not (reference_match or counterparty_match):
        return None

    amount_difference = abs(left.amount - right.amount)
    if amount_difference <= policy.amount_tolerance:
        classification: Literal["matched", "difference"] = "matched"
        classification_rank = 0
    elif amount_difference <= policy.difference_tolerance:
        classification = "difference"
        classification_rank = 1
    else:
        return None

    matched_fields = ["currency"]
    if reference_match:
        matched_fields.append("reference")
    if counterparty_match:
        matched_fields.append("counterparty_key")
    if left.document_state == right.document_state:
        matched_fields.append("document_state")
    if amount_difference <= policy.amount_tolerance:
        matched_fields.append("amount")
    if date_difference == 0:
        matched_fields.append("transaction_date")

    quality = (
        classification_rank,
        0 if reference_match else 1,
        0 if counterparty_match else 1,
        amount_difference,
        date_difference,
    )
    return _Candidate(
        left=left,
        right=right,
        classification=classification,
        amount_difference=amount_difference,
        date_difference_days=date_difference,
        matched_fields=tuple(matched_fields),
        quality=quality,
    )


def _score_candidates(
    left_rows: tuple[CanonicalTransaction, ...],
    right_rows: tuple[CanonicalTransaction, ...],
    policy: ReconciliationPolicy,
) -> tuple[_Candidate, ...]:
    candidates = (
        candidate
        for left in left_rows
        for right in right_rows
        if (candidate := _candidate_for(left, right, policy)) is not None
    )
    return tuple(sorted(candidates, key=lambda item: item.stable_rank))


def _add_flow_edge(
    graph: list[list[_FlowEdge]],
    source: int,
    destination: int,
    *,
    capacity: int,
    cost: int,
) -> _FlowEdge:
    forward = _FlowEdge(
        to=destination,
        reverse=len(graph[destination]),
        capacity=capacity,
        cost=cost,
    )
    reverse = _FlowEdge(
        to=source,
        reverse=len(graph[source]),
        capacity=0,
        cost=-cost,
    )
    graph[source].append(forward)
    graph[destination].append(reverse)
    return forward


def _candidate_costs(candidates: tuple[_Candidate, ...]) -> dict[tuple[str, str], int]:
    pair_order = sorted(
        candidates,
        key=lambda item: (item.left.transaction_id, item.right.transaction_id),
    )
    left_ids = sorted({item.left.transaction_id for item in pair_order})
    right_ids = sorted({item.right.transaction_id for item in pair_order})
    max_flow = min(
        len(left_ids),
        len(right_ids),
    )
    # Positional base-N digits make the final equal-cost assignment prefer
    # earlier left IDs and then earlier right IDs without input-order dependence.
    stable_base = len(right_ids) + 1
    left_stable_weights = {
        transaction_id: stable_base ** (len(left_ids) - index - 1)
        for index, transaction_id in enumerate(left_ids)
    }
    right_stable_ranks = {
        transaction_id: index for index, transaction_id in enumerate(right_ids)
    }
    stable_shift = len(right_ids) * left_stable_weights[left_ids[0]]
    max_stable_sum = max_flow * stable_shift
    max_date_sum = max_flow * max(item.date_difference_days for item in pair_order)
    amount_costs = [int(item.amount_difference / MONEY_QUANTUM) for item in pair_order]
    max_amount_sum = max_flow * max(amount_costs)

    date_weight = max_stable_sum + 1
    amount_weight = (max_date_sum + 1) * date_weight
    counterparty_weight = (max_amount_sum + 1) * amount_weight
    reference_weight = (max_flow + 1) * counterparty_weight
    classification_weight = (max_flow + 1) * reference_weight

    costs: dict[tuple[str, str], int] = {}
    for candidate, amount_cost in zip(pair_order, amount_costs, strict=True):
        classification, reference, counterparty, _, date_difference = candidate.quality
        left_weight = left_stable_weights[candidate.left.transaction_id]
        stable_cost = stable_shift + (
            right_stable_ranks[candidate.right.transaction_id] - len(right_ids)
        ) * left_weight
        costs[(candidate.left.transaction_id, candidate.right.transaction_id)] = (
            classification * classification_weight
            + reference * reference_weight
            + counterparty * counterparty_weight
            + amount_cost * amount_weight
            + date_difference * date_weight
            + stable_cost
        )
    return costs


def _optimal_one_to_one_match(
    candidates: tuple[_Candidate, ...],
) -> tuple[_Candidate, ...]:
    """Maximize assignments, then minimize aggregate quality and stable pair IDs."""

    if not candidates:
        return ()

    left_ids = sorted({item.left.transaction_id for item in candidates})
    right_ids = sorted({item.right.transaction_id for item in candidates})
    source = 0
    left_offset = 1
    right_offset = left_offset + len(left_ids)
    sink = right_offset + len(right_ids)
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]
    left_nodes = {
        transaction_id: left_offset + index
        for index, transaction_id in enumerate(left_ids)
    }
    right_nodes = {
        transaction_id: right_offset + index for index, transaction_id in enumerate(right_ids)
    }

    for transaction_id in left_ids:
        _add_flow_edge(
            graph,
            source,
            left_nodes[transaction_id],
            capacity=1,
            cost=0,
        )
    for transaction_id in right_ids:
        _add_flow_edge(
            graph,
            right_nodes[transaction_id],
            sink,
            capacity=1,
            cost=0,
        )

    candidate_costs = _candidate_costs(candidates)
    candidate_edges: list[tuple[_Candidate, _FlowEdge]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (item.left.transaction_id, item.right.transaction_id),
    ):
        pair = (candidate.left.transaction_id, candidate.right.transaction_id)
        edge = _add_flow_edge(
            graph,
            left_nodes[pair[0]],
            right_nodes[pair[1]],
            capacity=1,
            cost=candidate_costs[pair],
        )
        candidate_edges.append((candidate, edge))

    potentials = [0] * len(graph)
    while True:
        distances: list[int | None] = [None] * len(graph)
        previous: list[tuple[int, int] | None] = [None] * len(graph)
        distances[source] = 0
        queue: list[tuple[int, int]] = [(0, source)]
        while queue:
            distance, node = heappop(queue)
            if distances[node] != distance:
                continue
            for edge_index, edge in enumerate(graph[node]):
                if edge.capacity == 0:
                    continue
                next_distance = distance + edge.cost + potentials[node] - potentials[edge.to]
                if distances[edge.to] is not None and next_distance >= distances[edge.to]:
                    continue
                distances[edge.to] = next_distance
                previous[edge.to] = (node, edge_index)
                heappush(queue, (next_distance, edge.to))

        if distances[sink] is None:
            break
        for node, distance in enumerate(distances):
            if distance is not None:
                potentials[node] += distance

        node = sink
        while node != source:
            previous_step = previous[node]
            if previous_step is None:
                raise RuntimeError("reconciliation_assignment_path_invalid")
            previous_node, edge_index = previous_step
            edge = graph[previous_node][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse].capacity += 1
            node = previous_node

    return tuple(
        sorted(
            (candidate for candidate, edge in candidate_edges if edge.capacity == 0),
            key=lambda item: (item.left.transaction_id, item.right.transaction_id),
        )
    )


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def match_transactions(
    left: Sequence[CanonicalTransaction | Mapping[str, Any]],
    right: Sequence[CanonicalTransaction | Mapping[str, Any]],
    *,
    policy: ReconciliationPolicy,
) -> ReconciliationResult:
    """Return deterministic one-to-one reconciliation groups with source evidence."""

    left_rows = _normalize_transactions(left)
    right_rows = _normalize_transactions(right)
    left_unique, left_duplicates = _partition_duplicates(left_rows, side="left")
    right_unique, right_duplicates = _partition_duplicates(right_rows, side="right")
    candidates = _score_candidates(left_unique, right_unique, policy)
    assignments = _optimal_one_to_one_match(candidates)

    candidates_by_left: dict[str, list[_Candidate]] = defaultdict(list)
    candidates_by_right: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_left[candidate.left.transaction_id].append(candidate)
        candidates_by_right[candidate.right.transaction_id].append(candidate)

    matched: list[PairEvidence] = []
    differences: list[PairEvidence] = []
    assigned_left: set[str] = set()
    assigned_right: set[str] = set()
    for assignment in assignments:
        assigned_left.add(assignment.left.transaction_id)
        assigned_right.add(assignment.right.transaction_id)
        row_candidates = candidates_by_left[assignment.left.transaction_id]
        tied = sum(
            candidate.quality == assignment.quality for candidate in row_candidates
        )
        item = PairEvidence(
            classification=assignment.classification,
            left_transaction_id=assignment.left.transaction_id,
            right_transaction_id=assignment.right.transaction_id,
            amount_difference=assignment.amount_difference,
            date_difference_days=assignment.date_difference_days,
            matched_fields=assignment.matched_fields,
            evidence_refs=_ordered_unique(
                (*assignment.left.evidence_refs, *assignment.right.evidence_refs)
            ),
            candidate_count=len(row_candidates),
            tie_breaker="stable_transaction_id" if tied > 1 else "none",
        )
        if assignment.classification == "matched":
            matched.append(item)
        else:
            differences.append(item)

    unmatched = [
        UnmatchedEvidence(
            side="left",
            transaction_id=row.transaction_id,
            evidence_refs=row.evidence_refs,
            reason=(
                "candidate_contention"
                if candidates_by_left[row.transaction_id]
                else "no_eligible_candidate"
            ),
        )
        for row in left_unique
        if row.transaction_id not in assigned_left
    ]
    unmatched.extend(
        UnmatchedEvidence(
            side="right",
            transaction_id=row.transaction_id,
            evidence_refs=row.evidence_refs,
            reason=(
                "candidate_contention"
                if candidates_by_right[row.transaction_id]
                else "no_eligible_candidate"
            ),
        )
        for row in right_unique
        if row.transaction_id not in assigned_right
    )
    return ReconciliationResult(
        matched=tuple(
            sorted(
                matched,
                key=lambda item: (item.left_transaction_id, item.right_transaction_id),
            )
        ),
        differences=tuple(
            sorted(
                differences,
                key=lambda item: (item.left_transaction_id, item.right_transaction_id),
            )
        ),
        duplicates=tuple((*left_duplicates, *right_duplicates)),
        unmatched=tuple(
            sorted(unmatched, key=lambda item: (item.side, item.transaction_id))
        ),
    )
