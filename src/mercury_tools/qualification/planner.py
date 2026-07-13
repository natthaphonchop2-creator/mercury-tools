"""Deterministic fixture planning bounded by reviewed sandbox policy."""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal, TypeAlias

from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.qualification.manifest import (
    SandboxExecutionManifest,
    reviewed_policy_for,
)
from mercury_tools.qualification.models import SemanticContract, StrictSafeModel

ActionIdentity: TypeAlias = tuple[str, str]
DependencyIdentity: TypeAlias = tuple[ActionIdentity, ActionIdentity]

_ACTION_ID = re.compile(r"^act_[0-9a-f]{24}$")
_VERSION_ID = re.compile(r"^av_[0-9a-f]{64}$")


class ActionReference(StrictSafeModel):
    action_id: str
    version_id: str

    @property
    def identity(self) -> ActionIdentity:
        return (self.action_id, self.version_id)


class ReviewedDependency(StrictSafeModel):
    prerequisite: ActionReference
    dependent: ActionReference
    authorizes_execution: Literal[True] = True


class DependencyRecommendation(StrictSafeModel):
    prerequisite: ActionReference
    dependent: ActionReference
    reason: Literal["semantic_next_action"] = "semantic_next_action"
    authorizes_execution: Literal[False] = False


class FixturePlan(StrictSafeModel):
    execution_order: tuple[ActionReference, ...]
    reviewed_edges: tuple[ReviewedDependency, ...]
    recommendations: tuple[DependencyRecommendation, ...]
    executable_actions: tuple[ActionReference, ...]


def stable_topological_sort(
    nodes: Sequence[ActionIdentity],
    edges: Sequence[DependencyIdentity],
) -> tuple[ActionIdentity, ...]:
    """Sort exact action versions with stable ties and payload-free errors."""
    normalized_nodes = tuple(_node_identity(node) for node in nodes)
    if len(normalized_nodes) != len(set(normalized_nodes)):
        raise ValueError("fixture_plan_node_duplicate")

    node_set = set(normalized_nodes)
    successors: dict[ActionIdentity, set[ActionIdentity]] = {
        node: set() for node in normalized_nodes
    }
    indegree = {node: 0 for node in normalized_nodes}
    for raw_edge in edges:
        try:
            prerequisite = _node_identity(raw_edge[0])
            dependent = _node_identity(raw_edge[1])
        except (IndexError, TypeError):
            raise ValueError("fixture_plan_node_missing") from None
        if prerequisite not in node_set or dependent not in node_set:
            raise ValueError("fixture_plan_node_missing")
        if dependent not in successors[prerequisite]:
            successors[prerequisite].add(dependent)
            indegree[dependent] += 1

    ready = [node for node, count in indegree.items() if count == 0]
    heapq.heapify(ready)
    ordered: list[ActionIdentity] = []
    while ready:
        node = heapq.heappop(ready)
        ordered.append(node)
        for dependent in sorted(successors[node]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)

    if len(ordered) != len(normalized_nodes):
        raise ValueError("fixture_plan_cycle")
    return tuple(ordered)


def plan_fixture_dependencies(
    actions: Sequence[CatalogAction],
    manifest: SandboxExecutionManifest,
    semantics: Mapping[ActionIdentity, SemanticContract],
) -> FixturePlan:
    """Build a plan without promoting inferred dependencies into policy."""
    indexed_actions = _validated_actions(actions)
    identities = tuple(indexed_actions)
    checked_manifest = _validated_manifest(manifest, indexed_actions)
    checked_semantics = _validated_semantics(semantics, set(identities))
    versions_by_action = _versions_by_action(identities)

    reviewed_identities: set[DependencyIdentity] = set()
    for policy in checked_manifest.actions:
        dependent = (policy.action_id, policy.version_id)
        for prerequisite in policy.prerequisites:
            reviewed_identities.add(
                (_resolve_reference(prerequisite, versions_by_action), dependent)
            )

    execution_order = stable_topological_sort(
        identities,
        tuple(sorted(reviewed_identities)),
    )
    recommendations = _semantic_recommendations(
        checked_semantics,
        versions_by_action,
    )

    executable: list[ActionReference] = []
    for identity in sorted(identities):
        action = indexed_actions[identity]
        try:
            checked_manifest.require_executable(action)
        except (LookupError, PermissionError):
            continue
        except (TypeError, ValueError):
            raise ValueError("fixture_plan_manifest_invalid") from None
        executable.append(_reference(identity))

    return FixturePlan(
        execution_order=tuple(_reference(identity) for identity in execution_order),
        reviewed_edges=tuple(
            ReviewedDependency(
                prerequisite=_reference(prerequisite),
                dependent=_reference(dependent),
            )
            for prerequisite, dependent in sorted(reviewed_identities)
        ),
        recommendations=recommendations,
        executable_actions=tuple(executable),
    )


def _validated_actions(
    actions: Sequence[CatalogAction],
) -> dict[ActionIdentity, CatalogAction]:
    if isinstance(actions, (str, bytes, bytearray)) or not isinstance(actions, Sequence):
        raise ValueError("fixture_plan_catalog_invalid")

    indexed: dict[ActionIdentity, CatalogAction] = {}
    try:
        for raw_action in actions:
            action = revalidate_catalog_action(raw_action)
            identity = (action.action_id, action.version_id)
            if identity in indexed:
                raise ValueError("fixture_plan_node_duplicate")
            indexed[identity] = action
    except ValueError as exc:
        if str(exc) == "fixture_plan_node_duplicate":
            raise
        raise ValueError("fixture_plan_catalog_invalid") from None
    except (AttributeError, TypeError):
        raise ValueError("fixture_plan_catalog_invalid") from None
    return indexed


def _validated_manifest(
    manifest: SandboxExecutionManifest,
    actions: Mapping[ActionIdentity, CatalogAction],
) -> SandboxExecutionManifest:
    if not isinstance(manifest, SandboxExecutionManifest):
        raise ValueError("fixture_plan_manifest_invalid")
    try:
        checked = SandboxExecutionManifest.model_validate(
            {
                field_name: getattr(manifest, field_name)
                for field_name in SandboxExecutionManifest.model_fields
            }
        )
    except (AttributeError, TypeError, ValueError):
        raise ValueError("fixture_plan_manifest_invalid") from None

    manifest_identities = {(policy.action_id, policy.version_id) for policy in checked.actions}
    if manifest_identities != set(actions):
        raise ValueError("fixture_plan_manifest_identity_mismatch")

    if checked.catalog_sha256 != _catalog_snapshot_sha256(tuple(actions.values())):
        raise ValueError("fixture_plan_manifest_catalog_mismatch")
    try:
        expected_policies = tuple(
            reviewed_policy_for(actions[identity]) for identity in sorted(actions)
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("fixture_plan_manifest_invalid") from None
    if checked.actions != expected_policies:
        raise ValueError("fixture_plan_manifest_policy_mismatch")
    try:
        for policy in checked.actions:
            policy.validate_against(
                actions[(policy.action_id, policy.version_id)],
                environment=checked.environment,
            )
    except (KeyError, TypeError, ValueError):
        raise ValueError("fixture_plan_manifest_identity_mismatch") from None
    return checked


def _catalog_snapshot_sha256(actions: Sequence[CatalogAction]) -> str:
    payload = [action.model_dump(mode="json") for action in actions]
    try:
        serialized = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("fixture_plan_catalog_invalid") from None
    return hashlib.sha256(serialized).hexdigest()


def _validated_semantics(
    semantics: Mapping[ActionIdentity, SemanticContract],
    expected: set[ActionIdentity],
) -> dict[ActionIdentity, SemanticContract]:
    if not isinstance(semantics, Mapping):
        raise ValueError("fixture_plan_semantics_invalid")
    checked: dict[ActionIdentity, SemanticContract] = {}
    try:
        for raw_identity, raw_contract in semantics.items():
            identity = _node_identity(raw_identity)
            if identity in checked:
                raise ValueError("fixture_plan_semantics_identity_duplicate")
            checked[identity] = SemanticContract.model_validate(
                {
                    field_name: getattr(raw_contract, field_name)
                    for field_name in SemanticContract.model_fields
                }
            )
    except ValueError as exc:
        if str(exc) == "fixture_plan_semantics_identity_duplicate":
            raise
        raise ValueError("fixture_plan_semantics_invalid") from None
    except (AttributeError, TypeError):
        raise ValueError("fixture_plan_semantics_invalid") from None
    if set(checked) != expected:
        raise ValueError("fixture_plan_semantics_identity_mismatch")
    return checked


def _semantic_recommendations(
    semantics: Mapping[ActionIdentity, SemanticContract],
    versions_by_action: Mapping[str, tuple[ActionIdentity, ...]],
) -> tuple[DependencyRecommendation, ...]:
    inferred: set[DependencyIdentity] = set()
    for prerequisite, contract in semantics.items():
        for next_action_id in contract.next_action_ids:
            inferred.add(
                (
                    prerequisite,
                    _resolve_reference(next_action_id, versions_by_action),
                )
            )
    return tuple(
        DependencyRecommendation(
            prerequisite=_reference(prerequisite),
            dependent=_reference(dependent),
        )
        for prerequisite, dependent in sorted(inferred)
    )


def _versions_by_action(
    identities: Sequence[ActionIdentity],
) -> dict[str, tuple[ActionIdentity, ...]]:
    grouped: defaultdict[str, list[ActionIdentity]] = defaultdict(list)
    for identity in identities:
        grouped[identity[0]].append(identity)
    return {
        action_id: tuple(sorted(action_identities))
        for action_id, action_identities in grouped.items()
    }


def _resolve_reference(
    value: str,
    versions_by_action: Mapping[str, tuple[ActionIdentity, ...]],
) -> ActionIdentity:
    if not isinstance(value, str):
        raise ValueError("fixture_plan_node_missing")
    action_id, separator, version_id = value.partition("@")
    candidates = versions_by_action.get(action_id, ())
    if separator:
        exact = (action_id, version_id)
        if _ACTION_ID.fullmatch(action_id) is None or _VERSION_ID.fullmatch(version_id) is None:
            raise ValueError("fixture_plan_node_missing")
        if exact not in candidates:
            raise ValueError("fixture_plan_node_missing")
        return exact
    if not candidates:
        raise ValueError("fixture_plan_node_missing")
    if len(candidates) != 1:
        raise ValueError("fixture_plan_node_ambiguous")
    return candidates[0]


def _node_identity(value: object) -> ActionIdentity:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not isinstance(value[1], str)
        or not value[0]
        or not value[1]
    ):
        raise ValueError("fixture_plan_node_missing")
    return (value[0], value[1])


def _reference(identity: ActionIdentity) -> ActionReference:
    return ActionReference(action_id=identity[0], version_id=identity[1])


__all__ = [
    "ActionIdentity",
    "ActionReference",
    "DependencyRecommendation",
    "FixturePlan",
    "ReviewedDependency",
    "plan_fixture_dependencies",
    "stable_topological_sort",
]
