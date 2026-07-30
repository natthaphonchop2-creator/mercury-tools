"""Deterministic connector-profile routing for accounting Skills."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from mercury_tools.catalog.identity import canonical_json
from mercury_tools.connectors.catalog import CapabilityState, connector_by_id
from mercury_tools.skills.catalog import AccountingSkillDefinition

_READY_PROFILE_STATUSES = frozenset({"ready_read_only", "ready_read_write"})
_OBSERVED_CAPABILITY_STATES = frozenset({"observed", "enabled"})
_SAFE_PROFILE_VALUE_RE = re.compile(r"^[A-Za-z0-9._ -]{1,200}$")


def _sanitize_profile_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean if _SAFE_PROFILE_VALUE_RE.fullmatch(clean) else None


def _clean_profile_value(value: Any) -> str | None:
    clean = _sanitize_profile_value(value)
    return clean.lower() if clean is not None else None


def published_projection_matches(
    skill: AccountingSkillDefinition,
    projection: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(projection, Mapping):
        return False
    if "projection" in projection:
        if (
            projection.get("publication_status") != "published"
            or projection.get("skill_id") != skill.skill_id
            or projection.get("skill_version") != skill.skill_version
            or projection.get("projection_sha256") != skill.projection_sha256
            or projection.get("git_source_path") != skill.git_source_path
        ):
            return False
        projected_definition = projection.get("projection")
        if not isinstance(projected_definition, Mapping):
            return False
        projection = projected_definition
    try:
        return (
            projection.get("skill_id") == skill.skill_id
            and projection.get("skill_version") == skill.skill_version
            and canonical_json(dict(projection)) == canonical_json(skill.published_projection())
        )
    except (TypeError, ValueError):
        return False


def resolve_published_skill_route(
    skill: AccountingSkillDefinition,
    *,
    projection: Mapping[str, Any] | None,
    enabled_capabilities: Sequence[str],
    business_fact_count: int,
    knowledge_source_count: int,
    citation_count: int,
) -> dict[str, Any]:
    """Resolve evidence for an exact published Skill without changing authority."""

    counts = (business_fact_count, knowledge_source_count, citation_count)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise ValueError("skill_evidence_invalid")
    if any(not isinstance(value, str) for value in enabled_capabilities):
        raise ValueError("skill_capabilities_invalid")

    missing: list[str] = []
    if not published_projection_matches(skill, projection):
        missing.append("skill_schema")
    else:
        available = frozenset(enabled_capabilities)
        missing.extend(
            f"capability:{capability}"
            for capability in skill.required_capabilities
            if capability not in available
        )
        evidence_counts = {
            "business_fact": business_fact_count,
            "knowledge_source": knowledge_source_count,
            "citation": citation_count,
        }
        missing.extend(
            requirement
            for requirement in skill.evidence_requirements
            if evidence_counts.get(requirement, 0) == 0
        )

    return {
        "status": "insufficient_evidence" if missing else "ready",
        "skill_id": skill.skill_id,
        "skill_version": skill.skill_version,
        "missing_evidence": missing,
        "required_capabilities": list(skill.required_capabilities),
        "optional_capabilities": list(skill.optional_capabilities),
        "allowed_action_classes": list(skill.allowed_action_classes),
        "blocked_action_classes": list(skill.blocked_action_classes),
    }


def _safe_external_server_name(value: Any) -> str | None:
    from mercury_tools.db.product import safe_external_server_name

    return safe_external_server_name(value)


def _public_profile(profile: Mapping[str, Any]) -> dict[str, str] | None:
    connector_id = _clean_profile_value(profile.get("connector_id"))
    connection_mode = _clean_profile_value(profile.get("connection_mode"))
    environment = _clean_profile_value(profile.get("environment"))
    connector = connector_by_id(connector_id or "")
    if (
        connector is None
        or connector.connection_mode(connection_mode or "") is None
        or environment is None
    ):
        return None
    return {
        "connector_id": connector.connector_id,
        "connection_mode": connection_mode or "",
        "environment": environment,
    }


def _capability_states(profile: Mapping[str, Any]) -> dict[str, str]:
    raw = profile.get("capability_states")
    if not isinstance(raw, Mapping):
        return {}
    return {
        capability: state
        for key, value in raw.items()
        if (capability := _clean_profile_value(key)) and (state := _clean_profile_value(value))
    }


def _capability_resolution(
    profile: Mapping[str, Any],
    capability: str,
    *,
    required: bool,
) -> dict[str, Any]:
    public_profile = _public_profile(profile)
    if public_profile is None:
        return {
            "capability": capability,
            "provider_capabilities": [],
            "required": required,
            "state": "not_validated",
        }
    connector = connector_by_id(public_profile["connector_id"])
    assert connector is not None
    mode = connector.connection_mode(public_profile["connection_mode"])
    assert mode is not None
    provider_capabilities = connector.provider_capabilities(
        mode.mode.value,
        capability,
    )
    observed_states = _capability_states(profile)
    if (
        not provider_capabilities
        and mode.capability_source == "discovered_tools"
        and capability in observed_states
    ):
        provider_capabilities = (capability,)
    if not provider_capabilities or any(
        mode.provider_capability_status.get(action) is CapabilityState.PROVIDER_UNAVAILABLE
        for action in provider_capabilities
    ):
        state = "provider_capability_unavailable"
    else:
        action_states = [observed_states.get(action) for action in provider_capabilities]
        if all(item in _OBSERVED_CAPABILITY_STATES for item in action_states):
            state = "observed"
        elif "provider_unavailable" in action_states:
            state = "provider_capability_unavailable"
        else:
            state = next(
                (
                    item
                    for item in action_states
                    if item
                    in {
                        "not_authorized",
                        "validation_failed",
                        "environment_mismatch",
                        "policy_confirmation_required",
                    }
                ),
                "not_validated",
            )
    return {
        "capability": capability,
        "provider_capabilities": list(provider_capabilities),
        "required": required,
        "state": state,
    }


def _assess_profile(
    skill: AccountingSkillDefinition,
    profile: Mapping[str, Any],
) -> dict[str, Any] | None:
    public_profile = _public_profile(profile)
    if public_profile is None:
        return None
    if skill.required_connectors and public_profile["connector_id"] not in (
        skill.required_connectors
    ):
        return {
            "profile": profile,
            "public_profile": public_profile,
            "ready": False,
            "reason": "connector_not_supported",
            "capability_resolution": [],
        }
    if public_profile["connection_mode"] == "local_bridge":
        return {
            "profile": profile,
            "public_profile": public_profile,
            "ready": False,
            "reason": "local_bridge_required",
            "capability_resolution": [],
        }

    resolution = [
        *(
            _capability_resolution(profile, capability, required=True)
            for capability in skill.required_capabilities
        ),
        *(
            _capability_resolution(profile, capability, required=False)
            for capability in skill.optional_capabilities
        ),
    ]
    required_failure = next(
        (item for item in resolution if item["required"] and item["state"] != "observed"),
        None,
    )
    status = _clean_profile_value(profile.get("status"))
    reason = required_failure["state"] if required_failure else None
    if (
        reason is None
        and public_profile["connection_mode"] == "native_mcp"
        and _safe_external_server_name(profile.get("external_server_name")) is None
    ):
        reason = "not_validated"
    elif reason is None and status not in _READY_PROFILE_STATUSES:
        reason = "not_authorized" if status == "requires_authorization" else "not_validated"
    return {
        "profile": profile,
        "public_profile": public_profile,
        "ready": reason is None,
        "reason": reason,
        "capability_resolution": resolution,
    }


def _host_execution_plan(
    assessment: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profile = assessment["profile"]
    public_profile = assessment["public_profile"]
    actionable = [
        item
        for item in assessment["capability_resolution"]
        if item["required"] or item["state"] == "observed"
    ]
    steps = [
        {
            "step": index,
            "action": "invoke_connected_provider_capability",
            "capability": item["capability"],
            "provider_capabilities": item["provider_capabilities"],
            "required": item["required"],
        }
        for index, item in enumerate(actionable, start=1)
    ]
    server_name = _safe_external_server_name(profile.get("external_server_name"))
    requirement = {
        "connector_id": public_profile["connector_id"],
        "connection_mode": public_profile["connection_mode"],
        "environment": public_profile["environment"],
        "external_server_name": server_name,
        "provider_capabilities": sorted(
            {
                provider_capability
                for item in actionable
                for provider_capability in item["provider_capabilities"]
            }
        ),
    }
    return steps, [requirement]


def _ready_route(
    skill: AccountingSkillDefinition,
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    public_profile = assessment["public_profile"]
    ordered_steps, host_tool_requirements = _host_execution_plan(assessment)
    return {
        "status": "ready",
        "skill_id": skill.skill_id,
        "selected_profile": dict(public_profile),
        "capability_resolution": assessment["capability_resolution"],
        "ordered_steps": ordered_steps,
        "host_tool_requirements": host_tool_requirements,
    }


def _local_bridge_route(
    skill: AccountingSkillDefinition,
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    public_profile = assessment["public_profile"]
    return {
        "status": "local_bridge_required",
        "reason": "local_bridge_required",
        "skill_id": skill.skill_id,
        "selected_profile": dict(public_profile),
        "capability_resolution": assessment["capability_resolution"],
        "ordered_steps": [
            {
                "step": 1,
                "action": "configure_local_bridge",
                "connector_id": public_profile["connector_id"],
                "environment": public_profile["environment"],
            }
        ],
        "host_tool_requirements": [],
    }


def _connector_selection_route(
    skill: AccountingSkillDefinition,
    assessments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "connector_selection_required",
        "skill_id": skill.skill_id,
        "choices": [dict(item["public_profile"]) for item in assessments],
        "selected_profile": None,
        "capability_resolution": [],
        "ordered_steps": [],
        "host_tool_requirements": [],
    }


def resolve_skill_route(
    skill: AccountingSkillDefinition,
    profiles: Sequence[Mapping[str, Any]],
    requested_connector_id: str | None = None,
    requested_connection_mode: str | None = None,
) -> dict[str, Any]:
    """Resolve one Skill without connector preference or external provider calls."""

    selected_connector = _clean_profile_value(requested_connector_id)
    selected_mode = _clean_profile_value(requested_connection_mode)
    assessments = [
        assessment
        for profile in profiles
        if isinstance(profile, Mapping)
        if (assessment := _assess_profile(skill, profile)) is not None
        and (
            selected_connector is None
            or assessment["public_profile"]["connector_id"] == selected_connector
        )
        and (
            selected_mode is None
            or assessment["public_profile"]["connection_mode"] == selected_mode
        )
    ]
    assessments.sort(
        key=lambda item: (
            item["public_profile"]["connector_id"],
            item["public_profile"]["connection_mode"],
            item["public_profile"]["environment"],
        )
    )
    ready = [item for item in assessments if item["ready"]]
    if len(ready) > 1:
        return _connector_selection_route(skill, ready)
    if len(ready) == 1:
        return _ready_route(skill, ready[0])

    local_bridges = [item for item in assessments if item["reason"] == "local_bridge_required"]
    if len(local_bridges) > 1:
        return _connector_selection_route(skill, local_bridges)
    if local_bridges:
        return _local_bridge_route(skill, local_bridges[0])
    if not assessments:
        reason = (
            "connector_profile_unavailable" if selected_connector else "connector_profile_required"
        )
        return {
            "status": "unavailable",
            "reason": reason,
            "skill_id": skill.skill_id,
            "selected_profile": None,
            "capability_resolution": [],
            "ordered_steps": [],
            "host_tool_requirements": [],
        }

    failed = next(
        (item for item in assessments if item["reason"] == "provider_capability_unavailable"),
        assessments[0],
    )
    return {
        "status": "unavailable",
        "reason": failed["reason"],
        "skill_id": skill.skill_id,
        "selected_profile": dict(failed["public_profile"]),
        "capability_resolution": failed["capability_resolution"],
        "ordered_steps": [],
        "host_tool_requirements": [],
    }
