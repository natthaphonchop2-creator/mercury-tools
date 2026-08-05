"""Shared parent/item operation state contracts."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class ParentOperationState(StrEnum):
    PREPARED = "prepared"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED_PRE_DISPATCH = "failed_pre_dispatch"
    PROVIDER_REJECTED = "provider_rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class OperationItemState(StrEnum):
    PREPARED = "prepared"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED_PRE_DISPATCH = "failed_pre_dispatch"
    PROVIDER_REJECTED = "provider_rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    NOT_DISPATCHED = "not_dispatched"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_PARENT_TRANSITIONS: dict[ParentOperationState, frozenset[ParentOperationState]] = {
    ParentOperationState.PREPARED: frozenset(
        {
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.EXPIRED,
            ParentOperationState.CANCELLED,
        }
    ),
    ParentOperationState.AWAITING_CONFIRMATION: frozenset(
        {
            ParentOperationState.DISPATCHING,
            ParentOperationState.FAILED_PRE_DISPATCH,
            ParentOperationState.EXPIRED,
            ParentOperationState.CANCELLED,
        }
    ),
    ParentOperationState.FAILED_PRE_DISPATCH: frozenset(
        {ParentOperationState.DISPATCHING, ParentOperationState.CANCELLED}
    ),
    ParentOperationState.DISPATCHING: frozenset(
        {
            ParentOperationState.SUCCEEDED,
            ParentOperationState.FAILED_PRE_DISPATCH,
            ParentOperationState.PROVIDER_REJECTED,
            ParentOperationState.OUTCOME_UNKNOWN,
            ParentOperationState.NEEDS_MANUAL_REVIEW,
        }
    ),
    ParentOperationState.OUTCOME_UNKNOWN: frozenset(
        {ParentOperationState.SUCCEEDED, ParentOperationState.NEEDS_MANUAL_REVIEW}
    ),
    ParentOperationState.SUCCEEDED: frozenset(),
    ParentOperationState.PROVIDER_REJECTED: frozenset(),
    ParentOperationState.NEEDS_MANUAL_REVIEW: frozenset(),
    ParentOperationState.EXPIRED: frozenset(),
    ParentOperationState.CANCELLED: frozenset(),
}

_ITEM_TRANSITIONS: dict[OperationItemState, frozenset[OperationItemState]] = {
    OperationItemState.PREPARED: frozenset(
        {
            OperationItemState.AWAITING_CONFIRMATION,
            OperationItemState.EXPIRED,
            OperationItemState.CANCELLED,
        }
    ),
    OperationItemState.AWAITING_CONFIRMATION: frozenset(
        {
            OperationItemState.DISPATCHING,
            OperationItemState.FAILED_PRE_DISPATCH,
            OperationItemState.NOT_DISPATCHED,
            OperationItemState.CANCELLED,
            OperationItemState.EXPIRED,
        }
    ),
    OperationItemState.FAILED_PRE_DISPATCH: frozenset(
        {OperationItemState.DISPATCHING, OperationItemState.CANCELLED}
    ),
    OperationItemState.DISPATCHING: frozenset(
        {
            OperationItemState.SUCCEEDED,
            OperationItemState.FAILED_PRE_DISPATCH,
            OperationItemState.PROVIDER_REJECTED,
            OperationItemState.OUTCOME_UNKNOWN,
        }
    ),
    OperationItemState.OUTCOME_UNKNOWN: frozenset(
        {OperationItemState.SUCCEEDED, OperationItemState.NEEDS_MANUAL_REVIEW}
    ),
    OperationItemState.SUCCEEDED: frozenset(),
    OperationItemState.PROVIDER_REJECTED: frozenset(),
    OperationItemState.NEEDS_MANUAL_REVIEW: frozenset(),
    OperationItemState.NOT_DISPATCHED: frozenset(),
    OperationItemState.EXPIRED: frozenset(),
    OperationItemState.CANCELLED: frozenset(),
}

_ITEM_TERMINAL = frozenset(
    {
        OperationItemState.SUCCEEDED,
        OperationItemState.PROVIDER_REJECTED,
        OperationItemState.OUTCOME_UNKNOWN,
        OperationItemState.NEEDS_MANUAL_REVIEW,
        OperationItemState.NOT_DISPATCHED,
        OperationItemState.EXPIRED,
        OperationItemState.CANCELLED,
    }
)

_DISPATCH_RECOVERY_STATES = frozenset(
    {
        OperationItemState.SUCCEEDED,
        OperationItemState.PROVIDER_REJECTED,
        OperationItemState.OUTCOME_UNKNOWN,
        OperationItemState.NEEDS_MANUAL_REVIEW,
        OperationItemState.NOT_DISPATCHED,
    }
)


def _coerce_parent(value: ParentOperationState | str) -> ParentOperationState | None:
    try:
        return ParentOperationState(value)
    except (TypeError, ValueError):
        return None


def _coerce_item(value: OperationItemState | str) -> OperationItemState | None:
    try:
        return OperationItemState(value)
    except (TypeError, ValueError):
        return None


def parent_operation_transition_allowed(
    current: ParentOperationState | str,
    target: ParentOperationState | str,
    *,
    child_states: Iterable[OperationItemState | str],
) -> bool:
    """Return whether an aggregate parent transition preserves child coherence."""

    source = _coerce_parent(current)
    destination = _coerce_parent(target)
    children = tuple(_coerce_item(item) for item in child_states)
    if source is None or destination is None or any(item is None for item in children):
        return False
    checked_children = tuple(item for item in children if item is not None)
    if destination not in _PARENT_TRANSITIONS[source]:
        return False
    if destination is ParentOperationState.AWAITING_CONFIRMATION:
        return all(item is OperationItemState.PREPARED for item in checked_children)
    if destination is ParentOperationState.DISPATCHING:
        return all(
            item
            in {OperationItemState.AWAITING_CONFIRMATION, OperationItemState.FAILED_PRE_DISPATCH}
            for item in checked_children
        )
    if destination is ParentOperationState.FAILED_PRE_DISPATCH:
        return bool(checked_children) and all(
            item in {OperationItemState.FAILED_PRE_DISPATCH, OperationItemState.NOT_DISPATCHED}
            for item in checked_children
        )
    if destination is ParentOperationState.SUCCEEDED:
        return bool(checked_children) and all(
            item is OperationItemState.SUCCEEDED for item in checked_children
        )
    if destination is ParentOperationState.PROVIDER_REJECTED:
        return (
            bool(checked_children)
            and all(item in _ITEM_TERMINAL for item in checked_children)
            and any(item is OperationItemState.PROVIDER_REJECTED for item in checked_children)
        )
    if destination is ParentOperationState.OUTCOME_UNKNOWN:
        return (
            bool(checked_children)
            and all(item in _ITEM_TERMINAL for item in checked_children)
            and any(item is OperationItemState.OUTCOME_UNKNOWN for item in checked_children)
        )
    if destination is ParentOperationState.NEEDS_MANUAL_REVIEW:
        return (
            bool(checked_children)
            and all(item in _ITEM_TERMINAL for item in checked_children)
            and any(
                item in {OperationItemState.NEEDS_MANUAL_REVIEW, OperationItemState.OUTCOME_UNKNOWN}
                for item in checked_children
            )
        )
    if destination is ParentOperationState.EXPIRED:
        return bool(checked_children) and all(
            item is OperationItemState.EXPIRED for item in checked_children
        )
    if destination is ParentOperationState.CANCELLED:
        return bool(checked_children) and all(
            item in {OperationItemState.CANCELLED, OperationItemState.NOT_DISPATCHED}
            for item in checked_children
        )
    return True


def parent_operation_children_compatible(
    parent_state: ParentOperationState | str,
    child_states: Iterable[OperationItemState | str],
) -> bool:
    """Return whether persisted child states are coherent with their parent."""

    parent = _coerce_parent(parent_state)
    children = tuple(_coerce_item(item) for item in child_states)
    if parent is None or not children or any(item is None for item in children):
        return False
    checked = tuple(item for item in children if item is not None)
    if parent is ParentOperationState.PREPARED:
        return all(
            item
            in {
                OperationItemState.PREPARED,
                OperationItemState.CANCELLED,
                OperationItemState.EXPIRED,
            }
            for item in checked
        )
    if parent is ParentOperationState.AWAITING_CONFIRMATION:
        return all(
            item
            in {
                OperationItemState.PREPARED,
                OperationItemState.AWAITING_CONFIRMATION,
                OperationItemState.FAILED_PRE_DISPATCH,
                OperationItemState.NOT_DISPATCHED,
                OperationItemState.EXPIRED,
                OperationItemState.CANCELLED,
            }
            for item in checked
        )
    if parent is ParentOperationState.FAILED_PRE_DISPATCH:
        return all(
            item
            in {
                OperationItemState.FAILED_PRE_DISPATCH,
                OperationItemState.NOT_DISPATCHED,
                OperationItemState.CANCELLED,
            }
            for item in checked
        )
    if parent is ParentOperationState.DISPATCHING:
        return all(
            item
            in {
                OperationItemState.AWAITING_CONFIRMATION,
                OperationItemState.FAILED_PRE_DISPATCH,
                OperationItemState.DISPATCHING,
                *_DISPATCH_RECOVERY_STATES,
            }
            for item in checked
        )
    if parent is ParentOperationState.SUCCEEDED:
        return all(item is OperationItemState.SUCCEEDED for item in checked)
    if parent is ParentOperationState.PROVIDER_REJECTED:
        return all(item in _ITEM_TERMINAL for item in checked) and any(
            item is OperationItemState.PROVIDER_REJECTED for item in checked
        )
    if parent is ParentOperationState.OUTCOME_UNKNOWN:
        return all(item in _ITEM_TERMINAL for item in checked) and any(
            item
            in {
                OperationItemState.SUCCEEDED,
                OperationItemState.OUTCOME_UNKNOWN,
                OperationItemState.NEEDS_MANUAL_REVIEW,
            }
            for item in checked
        )
    if parent is ParentOperationState.NEEDS_MANUAL_REVIEW:
        return all(item in _ITEM_TERMINAL for item in checked) and any(
            item in {OperationItemState.NEEDS_MANUAL_REVIEW, OperationItemState.OUTCOME_UNKNOWN}
            for item in checked
        )
    if parent is ParentOperationState.EXPIRED:
        return all(item is OperationItemState.EXPIRED for item in checked)
    if parent is ParentOperationState.CANCELLED:
        return all(
            item in {OperationItemState.CANCELLED, OperationItemState.NOT_DISPATCHED}
            for item in checked
        )
    return False


def item_operation_transition_allowed(
    current: OperationItemState | str,
    target: OperationItemState | str,
    *,
    parent_state: ParentOperationState | str,
) -> bool:
    """Return whether a child transition is valid under its parent state."""

    source = _coerce_item(current)
    destination = _coerce_item(target)
    parent = _coerce_parent(parent_state)
    if source is None or destination is None or parent is None:
        return False
    if destination not in _ITEM_TRANSITIONS[source]:
        return False
    if source is OperationItemState.PREPARED:
        if destination is OperationItemState.AWAITING_CONFIRMATION:
            return parent is ParentOperationState.AWAITING_CONFIRMATION
        return parent is ParentOperationState.PREPARED
    if source is OperationItemState.AWAITING_CONFIRMATION:
        if destination is OperationItemState.DISPATCHING:
            return parent is ParentOperationState.DISPATCHING
        if destination is OperationItemState.NOT_DISPATCHED:
            return parent in {
                ParentOperationState.AWAITING_CONFIRMATION,
                ParentOperationState.DISPATCHING,
            }
        return parent is ParentOperationState.AWAITING_CONFIRMATION
    if source is OperationItemState.FAILED_PRE_DISPATCH:
        if destination is OperationItemState.DISPATCHING:
            return parent is ParentOperationState.DISPATCHING
        return parent is ParentOperationState.FAILED_PRE_DISPATCH
    if source is OperationItemState.DISPATCHING:
        return parent is ParentOperationState.DISPATCHING
    if source is OperationItemState.OUTCOME_UNKNOWN:
        return parent is ParentOperationState.OUTCOME_UNKNOWN
    return False


__all__ = [
    "OperationItemState",
    "ParentOperationState",
    "item_operation_transition_allowed",
    "parent_operation_children_compatible",
    "parent_operation_transition_allowed",
]
