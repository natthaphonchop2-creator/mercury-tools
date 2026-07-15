from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from mercury_tools.catalog.models import CatalogAction
from mercury_tools.qualification import planner as planner_module
from mercury_tools.qualification.manifest import (
    SandboxDisposition,
    SandboxExecutionManifest,
    load_sandbox_execution_manifest,
    reviewed_policy_for,
)
from mercury_tools.qualification.models import SemanticContract
from mercury_tools.qualification.planner import (
    plan_fixture_dependencies,
    stable_topological_sort,
)
from mercury_tools.qualification.semantics import (
    load_actions,
    load_semantic_contracts,
)

ROOT = Path(__file__).resolve().parents[1]
FLOWACCOUNT_CATALOG = ROOT / "catalog/global/flowaccount/actions.json"
FLOWACCOUNT_MANIFEST = ROOT / "catalog/global/flowaccount/sandbox-execution-manifest.json"
FLOWACCOUNT_SEMANTICS = ROOT / "catalog/global/flowaccount/semantic-contracts.json"


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
    policy_updates: dict[tuple[str, str], dict[str, Any]] | None = None,
    catalog_sha256: str | None = None,
) -> SandboxExecutionManifest:
    policy_updates = policy_updates or {}
    policies = []
    for action in sorted(actions, key=lambda item: (item.action_id, item.version_id)):
        policy = reviewed_policy_for(action)
        updates = policy_updates.get((action.action_id, action.version_id))
        policies.append(policy.model_copy(update=updates) if updates else policy)
    return SandboxExecutionManifest(
        environment="sandbox",
        catalog_sha256=catalog_sha256 or _catalog_sha256(actions),
        actions=tuple(policies),
    )


def _catalog_sha256(actions: Sequence[CatalogAction]) -> str:
    payload = [action.model_dump(mode="json") for action in actions]
    snapshot = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode()
    return hashlib.sha256(snapshot).hexdigest()


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions = _actions(action_factory)
    prerequisite, dependent, semantic_target = actions
    manifest = _manifest(
        actions,
        policy_updates={
            (dependent.action_id, dependent.version_id): {
                "prerequisites": (prerequisite.action_id,)
            }
        },
    )
    canonical_by_identity = {
        (policy.action_id, policy.version_id): policy for policy in manifest.actions
    }
    monkeypatch.setattr(
        planner_module,
        "reviewed_policy_for",
        lambda action: canonical_by_identity[(action.action_id, action.version_id)],
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
        policy_updates={
            (action.action_id, action.version_id): {
                "disposition": SandboxDisposition.SANDBOX_EXECUTABLE,
                "fixture_builder": "build_fixture",
                "ownership_predicate": "fixture_owned",
                "cleanup_action_id": action.action_id,
                "controlled_destination": True,
                "max_attempts": 1,
                "request_budget": 1,
            }
        },
    )

    with pytest.raises(ValueError) as raised:
        plan_fixture_dependencies((action,), manifest, _semantics((action,)))

    assert raised.value.args == ("fixture_plan_manifest_policy_mismatch",)


def test_forged_prerequisite_and_fixture_fields_never_create_authorizing_edge(
    action_factory: Callable[..., CatalogAction],
) -> None:
    actions = _actions(action_factory)
    prerequisite, dependent, _ = actions
    forged = _manifest(
        actions,
        policy_updates={
            (dependent.action_id, dependent.version_id): {
                "prerequisites": (prerequisite.action_id,),
                "fixture_builder": "forged_fixture_builder",
                "ownership_predicate": "forged_ownership",
                "cleanup_action_id": prerequisite.action_id,
            }
        },
    )

    with pytest.raises(ValueError) as raised:
        plan_fixture_dependencies(actions, forged, _semantics(actions))

    assert raised.value.args == ("fixture_plan_manifest_policy_mismatch",)


def test_forged_catalog_hash_is_rejected_without_echo(
    action_factory: Callable[..., CatalogAction],
) -> None:
    actions = _actions(action_factory)
    forged_hash = "f" * 64

    with pytest.raises(ValueError) as raised:
        plan_fixture_dependencies(
            actions,
            _manifest(actions, catalog_sha256=forged_hash),
            _semantics(actions),
        )

    assert raised.value.args == ("fixture_plan_manifest_catalog_mismatch",)
    assert forged_hash not in str(raised.value)


def test_catalog_hash_preserves_caller_action_order(
    action_factory: Callable[..., CatalogAction],
) -> None:
    actions = _actions(action_factory)
    manifest = _manifest(actions)

    with pytest.raises(ValueError, match="^fixture_plan_manifest_catalog_mismatch$"):
        plan_fixture_dependencies(tuple(reversed(actions)), manifest, _semantics(actions))


def test_builtin_catalog_manifest_and_semantics_are_canonical() -> None:
    actions = load_actions(FLOWACCOUNT_CATALOG)
    manifest = load_sandbox_execution_manifest(FLOWACCOUNT_MANIFEST, FLOWACCOUNT_CATALOG)
    semantics = load_semantic_contracts(FLOWACCOUNT_SEMANTICS, actions)

    plan = plan_fixture_dependencies(actions, manifest, semantics)

    assert len(plan.execution_order) == len(actions) == 190
    assert plan.reviewed_edges == ()


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
    monkeypatch: pytest.MonkeyPatch,
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
        policy_updates={
            (dependent.action_id, dependent.version_id): {"prerequisites": (first.action_id,)}
        },
    )
    canonical_by_identity = {
        (policy.action_id, policy.version_id): policy for policy in manifest.actions
    }
    monkeypatch.setattr(
        planner_module,
        "reviewed_policy_for",
        lambda action: canonical_by_identity[(action.action_id, action.version_id)],
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
