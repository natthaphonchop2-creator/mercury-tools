from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest

from mercury_tools.catalog.models import CatalogAction
from mercury_tools.qualification.manifest import (
    SandboxActionPolicy,
    SandboxDisposition,
    SandboxExecutionManifest,
)
from mercury_tools.qualification.models import SemanticContract
from mercury_tools.qualification.planner import (
    plan_fixture_dependencies,
    stable_topological_sort,
)


def _actions(action_factory: Callable[..., CatalogAction]) -> tuple[CatalogAction, ...]:
    return (
        action_factory(
            path_template="/contacts",
            operation_id="createContact",
            capability="contacts.create",
            description="Create contact",
        ),
        action_factory(
            path_template="/products",
            operation_id="createProduct",
            capability="product_masters.create",
            description="Create product",
        ),
        action_factory(
            path_template="/invoices",
            operation_id="createInvoice",
            capability="documents.invoice.create",
            description="Create invoice",
        ),
    )


def _manifest(
    actions: Sequence[CatalogAction],
    *,
    prerequisites: dict[tuple[str, str], tuple[str, ...]] | None = None,
    executable: frozenset[tuple[str, str]] = frozenset(),
) -> SandboxExecutionManifest:
    prerequisites = prerequisites or {}
    policies: list[SandboxActionPolicy] = []
    for action in actions:
        identity = (action.action_id, action.version_id)
        is_executable = identity in executable
        policies.append(
            SandboxActionPolicy(
                action_id=action.action_id,
                version_id=action.version_id,
                disposition=(
                    SandboxDisposition.SANDBOX_EXECUTABLE
                    if is_executable
                    else SandboxDisposition.CONTRACT_ONLY
                ),
                prerequisites=prerequisites.get(identity, ()),
                fixture_builder="build_fixture" if is_executable else None,
                ownership_predicate="fixture_owned" if is_executable else None,
                cleanup_action_id=action.action_id if is_executable else None,
                external_effects=action.side_effects if is_executable else (),
                controlled_destination=is_executable,
                max_attempts=1 if is_executable else 0,
                request_budget=1 if is_executable else 0,
            )
        )
    return SandboxExecutionManifest(
        environment="sandbox",
        catalog_sha256="a" * 64,
        actions=tuple(reversed(policies)),
    )


def _semantics(
    actions: Sequence[CatalogAction],
    *,
    next_actions: dict[tuple[str, str], tuple[str, ...]] | None = None,
) -> dict[tuple[str, str], SemanticContract]:
    next_actions = next_actions or {}
    return {
        (action.action_id, action.version_id): SemanticContract(
            business_object=action.capability.split(".", 1)[0],
            operation=action.capability.rsplit(".", 1)[-1],
            next_action_ids=next_actions.get((action.action_id, action.version_id), ()),
        )
        for action in actions
    }


def test_only_reviewed_manifest_edges_authorize_execution(
    action_factory: Callable[..., CatalogAction],
) -> None:
    actions = _actions(action_factory)
    prerequisite, dependent, semantic_target = actions
    manifest = _manifest(
        actions,
        prerequisites={(dependent.action_id, dependent.version_id): (prerequisite.action_id,)},
    )
    semantics = _semantics(
        actions,
        next_actions={(dependent.action_id, dependent.version_id): (semantic_target.action_id,)},
    )

    plan = plan_fixture_dependencies(actions, manifest, semantics)

    assert tuple(
        (edge.prerequisite.identity, edge.dependent.identity, edge.authorizes_execution)
        for edge in plan.reviewed_edges
    ) == (
        (
            (prerequisite.action_id, prerequisite.version_id),
            (dependent.action_id, dependent.version_id),
            True,
        ),
    )
    assert tuple(
        (
            recommendation.prerequisite.identity,
            recommendation.dependent.identity,
            recommendation.authorizes_execution,
        )
        for recommendation in plan.recommendations
    ) == (
        (
            (dependent.action_id, dependent.version_id),
            (semantic_target.action_id, semantic_target.version_id),
            False,
        ),
    )


def test_semantic_recommendations_do_not_change_execution_order(
    action_factory: Callable[..., CatalogAction],
) -> None:
    actions = _actions(action_factory)[:2]
    lower, higher = sorted(actions, key=lambda action: (action.action_id, action.version_id))
    semantics = _semantics(
        actions,
        next_actions={(higher.action_id, higher.version_id): (lower.action_id,)},
    )

    plan = plan_fixture_dependencies(actions, _manifest(actions), semantics)

    assert tuple(reference.identity for reference in plan.execution_order) == (
        (lower.action_id, lower.version_id),
        (higher.action_id, higher.version_id),
    )
    assert plan.recommendations[0].authorizes_execution is False


def test_planner_cannot_widen_task6_executable_policy(
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = _actions(action_factory)[0]
    manifest = _manifest(
        (action,),
        executable=frozenset({(action.action_id, action.version_id)}),
    )

    plan = plan_fixture_dependencies((action,), manifest, _semantics((action,)))

    assert plan.executable_actions == ()


@pytest.mark.parametrize(
    ("nodes", "edges", "error"),
    [
        ((("act_a", "av_1"), ("act_a", "av_1")), (), "fixture_plan_node_duplicate"),
        (
            (("act_a", "av_1"),),
            ((("act_a", "av_1"), ("act_b", "av_2")),),
            "fixture_plan_node_missing",
        ),
        (
            (("act_a", "av_1"), ("act_b", "av_2")),
            (
                (("act_a", "av_1"), ("act_b", "av_2")),
                (("act_b", "av_2"), ("act_a", "av_1")),
            ),
            "fixture_plan_cycle",
        ),
    ],
)
def test_topological_sort_has_exact_payload_free_errors(
    nodes: tuple[tuple[str, str], ...],
    edges: tuple[tuple[tuple[str, str], tuple[str, str]], ...],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{error}$"):
        stable_topological_sort(nodes, edges)


def test_topological_sort_uses_stable_exact_identity_ties() -> None:
    nodes = (("act_b", "av_2"), ("act_a", "av_2"), ("act_a", "av_1"))

    assert stable_topological_sort(nodes, ()) == tuple(sorted(nodes))


def test_action_id_only_prerequisite_cannot_select_between_versions(
    action_factory: Callable[..., CatalogAction],
) -> None:
    first = action_factory(description="First reviewed version")
    second = action_factory(description="Second reviewed version")
    dependent = action_factory(
        path_template="/expenses",
        operation_id="createExpense",
        capability="documents.expense.create",
    )
    actions = (first, second, dependent)
    manifest = _manifest(
        actions,
        prerequisites={(dependent.action_id, dependent.version_id): (first.action_id,)},
    )

    with pytest.raises(ValueError, match="^fixture_plan_node_ambiguous$"):
        plan_fixture_dependencies(actions, manifest, _semantics(actions))


@pytest.mark.parametrize("source", ["catalog", "manifest", "semantics"])
def test_planner_rejects_duplicate_or_misaligned_exact_identities(
    action_factory: Callable[..., CatalogAction],
    source: str,
) -> None:
    actions = _actions(action_factory)
    manifest = _manifest(actions)
    semantics: Any = _semantics(actions)
    planned_actions: Sequence[CatalogAction] = actions
    expected = "fixture_plan_node_duplicate"

    if source == "catalog":
        planned_actions = (*actions, actions[0])
    elif source == "manifest":
        manifest = _manifest(actions[:-1])
        expected = "fixture_plan_manifest_identity_mismatch"
    else:
        semantics = {
            key: value
            for key, value in semantics.items()
            if key
            != (
                actions[-1].action_id,
                actions[-1].version_id,
            )
        }
        expected = "fixture_plan_semantics_identity_mismatch"

    with pytest.raises(ValueError, match=f"^{expected}$"):
        plan_fixture_dependencies(planned_actions, manifest, semantics)
