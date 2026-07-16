#!/usr/bin/env python3
"""Fail-closed GitHub release-control environment preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path

REQUIRED_MIGRATION = "20260716100000"
CANONICAL_TABLES = (
    "erp_action_catalog",
    "erp_action_observations",
    "erp_action_validation_knowledge",
    "erp_action_versions",
    "erp_spec_sources",
    "knowledge_chunks",
    "knowledge_documents",
    "knowledge_sources",
    "mcp_audit_events",
    "mercury_client_tokens",
    "mercury_connector_profiles",
    "mercury_product_events",
    "mercury_skill_catalog",
    "mercury_skill_uploads",
    "mercury_workspace_members",
    "mercury_workspace_skills",
    "mercury_workspaces",
)
REQUIRED_FUNCTIONS = (
    "public.jsonb_has_forbidden_validation_key(jsonb)",
    "public.jsonb_has_forbidden_validation_value(jsonb)",
    "public.jsonb_is_safe_validation_response_shape(jsonb)",
    (
        "public.match_knowledge_chunks("
        "text,vector,integer,text,text,text,text,text,date,text,text,text,text,text)"
    ),
    "public.reject_validation_evidence_mutation()",
    "public.resolve_erp_action_validation_batch(jsonb,timestamp with time zone)",
    "public.validation_label_kind(text)",
    "public.validation_text_has_forbidden_value(text)",
    "public.validation_text_has_label_assignment_contamination(text)",
    "public.validation_text_has_safe_label_assignment(text)",
)

_POLICY_KEYS = {
    "bootstrap_state",
    "branch",
    "environment",
    "forbidden_repository_secrets",
    "immutable_releases_required",
    "inspector",
    "release_tag_ruleset",
    "repository",
    "repository_id",
    "required_environment_secrets",
    "required_environment_variables",
    "required_reviewer_ids",
    "required_status_checks",
    "reviewed_repository",
    "reviewed_repository_id",
    "schema_version",
    "staging_repository",
    "supabase",
}
_SUPABASE_KEYS = {
    "functions",
    "migration_history_sha256",
    "migration_id",
    "project_ref",
    "schema_sha256",
    "storage_buckets",
    "tables",
}
_SNAPSHOT_KEYS = {"control", "target"}
_CONTROL_SNAPSHOT_KEYS = {
    "branch_protection",
    "environment",
    "environment_secrets",
    "environment_variables",
    "repository",
    "repository_secrets",
    "repository_variables",
}
_TARGET_SNAPSHOT_KEYS = {
    "branch_protection",
    "immutable_releases",
    "release_tag_rulesets",
    "repository",
    "repository_secrets",
}
_REPOSITORY_KEYS = {"default_branch", "full_name", "id", "visibility"}
_ENVIRONMENT_KEYS = {
    "can_admins_bypass",
    "deployment_branch_policy",
    "name",
    "prevent_self_review",
    "reviewer_ids",
}
_DEPLOYMENT_BRANCH_POLICY_KEYS = {"custom_branch_policies", "protected_branches"}
_CONTROL_BRANCH_PROTECTION_KEYS = {
    "enforce_admins",
    "protected",
    "required_approving_review_count",
    "required_status_checks",
    "required_status_checks_strict",
}
_TARGET_BRANCH_PROTECTION_KEYS = {"protected"}
_RELEASE_TAG_RULESET_KEYS = {
    "bypass_actors",
    "conditions",
    "enforcement",
    "name",
    "rules",
    "target",
}
_RELEASE_TAG_RULESET_FIELDS = (
    "bypass_actors",
    "conditions",
    "enforcement",
    "name",
    "rules",
    "target",
)
_TAG_RULESET_CONDITIONS_KEYS = {"ref_name"}
_TAG_RULESET_REF_NAME_KEYS = {"exclude", "include"}
_STATUS_CHECK_KEYS = {"app_id", "context"}
_BYPASS_ACTOR_KEYS = {"actor_id", "actor_type", "bypass_mode"}
_IMMUTABLE_RELEASES_KEYS = {"enabled"}
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CONFIG_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_STATUS_CHECKS = 64
_MAX_RULESETS = 100


class PreflightError(RuntimeError):
    """A constant-code release-control configuration failure."""


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PreflightError(code)
    return value


def _require_exact_keys(
    value: Mapping[str, object],
    keys: set[str],
    code: str,
) -> None:
    if set(value) != keys:
        raise PreflightError(code)


def _require_positive_int(value: object, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PreflightError(code)
    return value


def _require_sha256(value: object, code: str, *, allow_zero: bool = False) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise PreflightError(code)
    if not allow_zero and value == "0" * 64:
        raise PreflightError(code)
    return value


def _require_sorted_names(value: object, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or _CONFIG_NAME_PATTERN.fullmatch(item) is None for item in value
    ):
        raise PreflightError(code)
    names = tuple(value)
    if names != tuple(sorted(set(names))):
        raise PreflightError(code)
    return names


def _require_text(value: object, code: str, *, maximum_length: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum_length
        or value != value.strip()
        or "\x00" in value
    ):
        raise PreflightError(code)
    return value


def _required_status_check_identities(
    value: object,
    code: str,
    *,
    require_canonical_order: bool = False,
) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, list) or not value or len(value) > _MAX_STATUS_CHECKS:
        raise PreflightError(code)
    identities: list[tuple[str, int]] = []
    for entry in value:
        check = _require_mapping(entry, code)
        _require_exact_keys(check, _STATUS_CHECK_KEYS, code)
        context = _require_text(check.get("context"), code)
        app_id = check.get("app_id")
        if not isinstance(app_id, int) or isinstance(app_id, bool) or app_id <= 0:
            raise PreflightError(code)
        identities.append((context, app_id))
    canonical = tuple(sorted(identities))
    if len(set(identities)) != len(identities) or (
        require_canonical_order and tuple(identities) != canonical
    ):
        raise PreflightError(code)
    return canonical


def _normalize_bypass_actors(
    value: object,
    code: str,
    *,
    require_canonical_order: bool = False,
) -> tuple[tuple[int, str, str], ...]:
    if not isinstance(value, list):
        raise PreflightError(code)
    actors: list[tuple[int, str, str]] = []
    for entry in value:
        actor = _require_mapping(entry, code)
        _require_exact_keys(actor, _BYPASS_ACTOR_KEYS, code)
        actor_id = actor.get("actor_id")
        if not isinstance(actor_id, int) or isinstance(actor_id, bool) or actor_id <= 0:
            raise PreflightError(code)
        actor_type = _require_text(actor.get("actor_type"), code, maximum_length=64)
        bypass_mode = _require_text(actor.get("bypass_mode"), code, maximum_length=64)
        actors.append((actor_id, actor_type, bypass_mode))
    canonical = tuple(sorted(actors))
    if len(set(actors)) != len(actors) or (require_canonical_order and tuple(actors) != canonical):
        raise PreflightError(code)
    return canonical


def _normalize_release_tag_ruleset(
    value: object,
    code: str,
    *,
    require_canonical_order: bool = False,
) -> dict[str, object]:
    ruleset = _require_mapping(value, code)
    _require_exact_keys(ruleset, _RELEASE_TAG_RULESET_KEYS, code)
    name = _require_text(ruleset.get("name"), code)
    if ruleset.get("target") != "tag" or ruleset.get("enforcement") != "active":
        raise PreflightError(code)
    conditions = _require_mapping(ruleset.get("conditions"), code)
    _require_exact_keys(conditions, _TAG_RULESET_CONDITIONS_KEYS, code)
    ref_name = _require_mapping(conditions.get("ref_name"), code)
    _require_exact_keys(ref_name, _TAG_RULESET_REF_NAME_KEYS, code)
    if ref_name.get("include") != ["refs/tags/v0.2.1"] or ref_name.get("exclude") != []:
        raise PreflightError(code)

    rules = ruleset.get("rules")
    if not isinstance(rules, list) or len(rules) != 2:
        raise PreflightError(code)
    rule_types: list[str] = []
    for entry in rules:
        rule = _require_mapping(entry, code)
        rule_type = rule.get("type")
        if not isinstance(rule_type, str):
            raise PreflightError(code)
        if rule_type == "update":
            _require_exact_keys(rule, {"parameters", "type"}, code)
            parameters = _require_mapping(rule.get("parameters"), code)
            _require_exact_keys(parameters, {"update_allows_fetch_and_merge"}, code)
            if parameters.get("update_allows_fetch_and_merge") is not False:
                raise PreflightError(code)
        else:
            _require_exact_keys(rule, {"type"}, code)
        rule_types.append(rule_type)
    canonical_rule_types = ("deletion", "update")
    if tuple(sorted(rule_types)) != canonical_rule_types or (
        require_canonical_order and tuple(rule_types) != canonical_rule_types
    ):
        raise PreflightError(code)

    bypass_actors = _normalize_bypass_actors(
        ruleset.get("bypass_actors"),
        code,
        require_canonical_order=require_canonical_order,
    )
    if bypass_actors:
        raise PreflightError(code)
    return {
        "bypass_actors": [
            {"actor_id": actor_id, "actor_type": actor_type, "bypass_mode": bypass_mode}
            for actor_id, actor_type, bypass_mode in bypass_actors
        ],
        "conditions": {"ref_name": {"exclude": [], "include": ["refs/tags/v0.2.1"]}},
        "enforcement": "active",
        "name": name,
        "rules": [
            {"type": "deletion"},
            {
                "parameters": {"update_allows_fetch_and_merge": False},
                "type": "update",
            },
        ],
        "target": "tag",
    }


def build_supabase_schema_digest(supabase: Mapping[str, object]) -> str:
    functions = supabase.get("functions")
    if not isinstance(functions, list):
        raise PreflightError("supabase_functions_invalid")
    payload = {
        "functions": functions,
        "migration_history_sha256": supabase.get("migration_history_sha256"),
        "migration_id": supabase.get("migration_id"),
        "storage_buckets": supabase.get("storage_buckets"),
        "tables": supabase.get("tables"),
    }
    return _canonical_sha256(payload)


def _validate_policy(policy: Mapping[str, object]) -> Mapping[str, object]:
    if policy.get("bootstrap_state") != "configured":
        raise PreflightError("policy_unconfigured")
    _require_exact_keys(policy, _POLICY_KEYS, "policy_schema_invalid")
    if policy.get("schema_version") != 1:
        raise PreflightError("policy_schema_invalid")
    for key in ("repository", "reviewed_repository", "staging_repository"):
        value = policy.get(key)
        if not isinstance(value, str) or _REPOSITORY_PATTERN.fullmatch(value) is None:
            raise PreflightError("policy_repository_invalid")
    if (
        len(
            {
                policy["repository"],
                policy["reviewed_repository"],
                policy["staging_repository"],
            }
        )
        != 3
    ):
        raise PreflightError("policy_repository_invalid")
    control_repository_id = _require_positive_int(
        policy.get("repository_id"),
        "policy_repository_identity_invalid",
    )
    target_repository_id = _require_positive_int(
        policy.get("reviewed_repository_id"),
        "policy_repository_identity_invalid",
    )
    if control_repository_id == target_repository_id:
        raise PreflightError("policy_repository_identity_invalid")
    if policy.get("branch") != "main" or policy.get("environment") != "production-release":
        raise PreflightError("policy_release_boundary_invalid")
    inspector = _require_mapping(policy.get("inspector"), "policy_inspector_invalid")
    _require_exact_keys(
        inspector,
        {"interface_version", "path", "sha256"},
        "policy_inspector_invalid",
    )
    if (
        inspector.get("interface_version") != 1
        or inspector.get("path") != "bin/mercury-release-control-inspector"
    ):
        raise PreflightError("policy_inspector_invalid")
    _require_sha256(inspector.get("sha256"), "policy_inspector_invalid")

    if policy.get("immutable_releases_required") is not True:
        raise PreflightError("policy_immutable_releases_invalid")
    _normalize_release_tag_ruleset(
        policy.get("release_tag_ruleset"),
        "release_tag_ruleset_invalid",
        require_canonical_order=True,
    )

    reviewers = policy.get("required_reviewer_ids")
    if (
        not isinstance(reviewers, list)
        or not reviewers
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in reviewers
        )
    ):
        raise PreflightError("policy_reviewers_invalid")
    if tuple(reviewers) != tuple(sorted(set(reviewers))):
        raise PreflightError("policy_reviewers_invalid")

    required_secrets = _require_sorted_names(
        policy.get("required_environment_secrets"),
        "policy_environment_secrets_invalid",
    )
    _require_sorted_names(
        policy.get("required_environment_variables"),
        "policy_environment_variables_invalid",
    )
    _required_status_check_identities(
        policy.get("required_status_checks"),
        "policy_required_status_checks_invalid",
        require_canonical_order=True,
    )
    forbidden_secrets = _require_sorted_names(
        policy.get("forbidden_repository_secrets"),
        "policy_repository_secrets_invalid",
    )
    if not set(required_secrets).issubset(forbidden_secrets):
        raise PreflightError("policy_repository_secrets_invalid")

    supabase = _require_mapping(policy.get("supabase"), "supabase_policy_invalid")
    _require_exact_keys(supabase, _SUPABASE_KEYS, "supabase_policy_invalid")
    project_ref = supabase.get("project_ref")
    if not isinstance(project_ref, str) or re.fullmatch(r"[a-z0-9]{20}", project_ref) is None:
        raise PreflightError("supabase_project_ref_invalid")
    if supabase.get("migration_id") != REQUIRED_MIGRATION:
        raise PreflightError("supabase_migration_invalid")
    _require_sha256(
        supabase.get("migration_history_sha256"),
        "supabase_migration_history_invalid",
    )
    if tuple(supabase.get("tables", ())) != CANONICAL_TABLES:
        raise PreflightError("supabase_table_inventory_invalid")
    if supabase.get("storage_buckets") != []:
        raise PreflightError("supabase_bucket_inventory_invalid")
    functions = supabase.get("functions")
    if (
        not isinstance(functions, list)
        or tuple(item.get("signature") if isinstance(item, Mapping) else None for item in functions)
        != REQUIRED_FUNCTIONS
    ):
        raise PreflightError("supabase_function_inventory_invalid")
    for item in functions:
        function = _require_mapping(item, "supabase_function_inventory_invalid")
        _require_exact_keys(
            function,
            {"definition_sha256", "signature"},
            "supabase_function_inventory_invalid",
        )
        _require_sha256(
            function.get("definition_sha256"),
            "supabase_function_digest_invalid",
        )
    expected_schema = build_supabase_schema_digest(supabase)
    if (
        _require_sha256(
            supabase.get("schema_sha256"),
            "supabase_schema_digest_invalid",
        )
        != expected_schema
    ):
        raise PreflightError("supabase_schema_digest_invalid")
    return policy


def _name_set(value: object, code: str) -> set[str]:
    return set(_require_sorted_names(value, code))


def _validate_repository_snapshot(
    value: object,
    *,
    expected_name: object,
    expected_id: object,
    expected_branch: object,
    identity_code: str,
    protection_code: str,
) -> Mapping[str, object]:
    repository = _require_mapping(value, identity_code)
    _require_exact_keys(repository, _REPOSITORY_KEYS, identity_code)
    observed_id = repository.get("id")
    if (
        not isinstance(observed_id, int)
        or isinstance(observed_id, bool)
        or observed_id != expected_id
        or repository.get("full_name") != expected_name
    ):
        raise PreflightError(identity_code)
    if (
        repository.get("visibility") != "public"
        or repository.get("default_branch") != expected_branch
    ):
        raise PreflightError(protection_code)
    return repository


def validate_preflight_snapshot(
    policy: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    """Validate remote settings and return a sanitized attestation fragment."""

    _validate_policy(policy)
    _require_exact_keys(snapshot, _SNAPSHOT_KEYS, "snapshot_schema_invalid")
    control = _require_mapping(snapshot.get("control"), "control_snapshot_schema_invalid")
    target = _require_mapping(snapshot.get("target"), "target_snapshot_schema_invalid")
    _require_exact_keys(control, _CONTROL_SNAPSHOT_KEYS, "control_snapshot_schema_invalid")
    _require_exact_keys(target, _TARGET_SNAPSHOT_KEYS, "target_snapshot_schema_invalid")

    _validate_repository_snapshot(
        control.get("repository"),
        expected_name=policy["repository"],
        expected_id=policy["repository_id"],
        expected_branch=policy["branch"],
        identity_code="control_repository_identity_invalid",
        protection_code="control_repository_protection_invalid",
    )

    environment = _require_mapping(control.get("environment"), "environment_invalid")
    _require_exact_keys(environment, _ENVIRONMENT_KEYS, "environment_invalid")
    reviewer_ids = environment.get("reviewer_ids")
    if (
        environment.get("name") != policy["environment"]
        or reviewer_ids != policy["required_reviewer_ids"]
        or environment.get("prevent_self_review") is not True
        or environment.get("can_admins_bypass") is not False
    ):
        raise PreflightError("environment_protection_invalid")
    deployment_policy = _require_mapping(
        environment.get("deployment_branch_policy"),
        "environment_branch_policy_invalid",
    )
    _require_exact_keys(
        deployment_policy,
        _DEPLOYMENT_BRANCH_POLICY_KEYS,
        "environment_branch_policy_invalid",
    )
    if (
        deployment_policy.get("protected_branches") is not True
        or deployment_policy.get("custom_branch_policies") is not False
    ):
        raise PreflightError("environment_branch_policy_invalid")

    branch = _require_mapping(
        control.get("branch_protection"),
        "control_branch_protection_invalid",
    )
    _require_exact_keys(
        branch,
        _CONTROL_BRANCH_PROTECTION_KEYS,
        "control_branch_protection_invalid",
    )
    if (
        branch.get("protected") is not True
        or branch.get("enforce_admins") is not True
        or not isinstance(branch.get("required_approving_review_count"), int)
        or isinstance(branch.get("required_approving_review_count"), bool)
        or branch["required_approving_review_count"] < 1
        or branch.get("required_status_checks_strict") is not True
    ):
        raise PreflightError("control_branch_protection_invalid")
    expected_status_checks = _required_status_check_identities(
        policy["required_status_checks"],
        "policy_required_status_checks_invalid",
        require_canonical_order=True,
    )
    observed_status_checks = _required_status_check_identities(
        branch.get("required_status_checks"),
        "control_branch_protection_invalid",
    )
    if observed_status_checks != expected_status_checks:
        raise PreflightError("control_branch_protection_invalid")

    required_secrets = set(policy["required_environment_secrets"])
    required_variables = set(policy["required_environment_variables"])
    environment_secrets = _name_set(
        control.get("environment_secrets"),
        "environment_secret_inventory_invalid",
    )
    environment_variables = _name_set(
        control.get("environment_variables"),
        "environment_variable_inventory_invalid",
    )
    if not required_secrets <= environment_secrets:
        raise PreflightError("environment_secret_inventory_invalid")
    if not required_variables <= environment_variables:
        raise PreflightError("environment_variable_inventory_invalid")
    control_repository_secrets = _name_set(
        control.get("repository_secrets"),
        "control_repository_secret_inventory_invalid",
    )
    control_repository_variables = _name_set(
        control.get("repository_variables"),
        "repository_variable_inventory_invalid",
    )
    forbidden_secrets = set(policy["forbidden_repository_secrets"])
    if forbidden_secrets & control_repository_secrets:
        raise PreflightError("control_repository_secret_forbidden")
    if required_variables & control_repository_variables:
        raise PreflightError("repository_variable_forbidden")

    _validate_repository_snapshot(
        target.get("repository"),
        expected_name=policy["reviewed_repository"],
        expected_id=policy["reviewed_repository_id"],
        expected_branch=policy["branch"],
        identity_code="target_repository_identity_invalid",
        protection_code="target_repository_protection_invalid",
    )
    target_branch = _require_mapping(
        target.get("branch_protection"),
        "target_branch_protection_invalid",
    )
    _require_exact_keys(
        target_branch,
        _TARGET_BRANCH_PROTECTION_KEYS,
        "target_branch_protection_invalid",
    )
    if target_branch.get("protected") is not True:
        raise PreflightError("target_branch_protection_invalid")

    expected_tag_ruleset = _normalize_release_tag_ruleset(
        policy["release_tag_ruleset"],
        "release_tag_ruleset_invalid",
        require_canonical_order=True,
    )
    observed_tag_rulesets = target.get("release_tag_rulesets")
    if not isinstance(observed_tag_rulesets, list) or len(observed_tag_rulesets) != 1:
        raise PreflightError("release_tag_ruleset_invalid")
    observed_tag_ruleset = _normalize_release_tag_ruleset(
        observed_tag_rulesets[0],
        "release_tag_ruleset_invalid",
    )
    if observed_tag_ruleset != expected_tag_ruleset:
        raise PreflightError("release_tag_ruleset_invalid")
    immutable_releases = _require_mapping(
        target.get("immutable_releases"),
        "immutable_releases_invalid",
    )
    _require_exact_keys(
        immutable_releases,
        _IMMUTABLE_RELEASES_KEYS,
        "immutable_releases_invalid",
    )
    if immutable_releases.get("enabled") is not True:
        raise PreflightError("immutable_releases_invalid")
    target_repository_secrets = _name_set(
        target.get("repository_secrets"),
        "target_repository_secret_inventory_invalid",
    )
    if forbidden_secrets & target_repository_secrets:
        raise PreflightError("target_repository_secret_forbidden")

    supabase = _require_mapping(policy["supabase"], "supabase_policy_invalid")
    project_ref = supabase["project_ref"]
    assert isinstance(project_ref, str)
    return {
        "environment": "production-release",
        "repository_visibility": "public",
        "required_reviewers": len(policy["required_reviewer_ids"]),
        "prevent_self_review": True,
        "admin_bypass_disabled": True,
        "protected_branch_only": True,
        "required_configuration_sha256": _canonical_sha256(policy),
        "approved_supabase_project_ref_sha256": hashlib.sha256(
            project_ref.encode("utf-8")
        ).hexdigest(),
        "approved_supabase_migration_history_sha256": supabase["migration_history_sha256"],
        "approved_supabase_schema_sha256": supabase["schema_sha256"],
    }


def _github_payload(path: str, token: str) -> object:
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "mercury-release-control-preflight/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            encoded = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise PreflightError("github_api_unavailable") from exc
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise PreflightError("github_api_response_too_large")
    try:
        return json.loads(encoded)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError("github_api_response_invalid") from exc


def _github_json(path: str, token: str) -> Mapping[str, object]:
    return _require_mapping(_github_payload(path, token), "github_api_response_invalid")


def _github_list(path: str, token: str) -> list[Mapping[str, object]]:
    payload = _github_payload(path, token)
    if not isinstance(payload, list):
        raise PreflightError("github_api_response_invalid")
    return [_require_mapping(record, "github_api_response_invalid") for record in payload]


def _inventory(payload: Mapping[str, object], key: str) -> list[str]:
    records = payload.get(key)
    total = payload.get("total_count")
    if not isinstance(records, list) or not isinstance(total, int) or total != len(records):
        raise PreflightError("github_inventory_truncated")
    names = [item.get("name") if isinstance(item, Mapping) else None for item in records]
    if any(not isinstance(name, str) for name in names):
        raise PreflightError("github_inventory_invalid")
    return sorted(names)


def _collect_repository_snapshot(
    *,
    repository: str,
    repository_id: int,
    token: str,
    identity_code: str,
) -> dict[str, object]:
    repo = _github_json(f"/repos/{repository}", token)
    observed_id = repo.get("id")
    if (
        not isinstance(observed_id, int)
        or isinstance(observed_id, bool)
        or observed_id != repository_id
        or repo.get("full_name") != repository
    ):
        raise PreflightError(identity_code)
    return {
        "id": observed_id,
        "full_name": repo.get("full_name"),
        "visibility": repo.get("visibility"),
        "default_branch": repo.get("default_branch"),
    }


def _environment_reviewer_ids(environment: Mapping[str, object]) -> list[int]:
    reviewer_ids: list[int] = []
    rules = environment.get("protection_rules")
    if not isinstance(rules, list):
        raise PreflightError("environment_protection_invalid")
    for rule in rules:
        if not isinstance(rule, Mapping) or rule.get("type") != "required_reviewers":
            continue
        reviewers = rule.get("reviewers")
        if not isinstance(reviewers, list):
            raise PreflightError("environment_protection_invalid")
        for reviewer_record in reviewers:
            reviewer_mapping = _require_mapping(
                reviewer_record,
                "environment_protection_invalid",
            )
            reviewer = reviewer_mapping.get("reviewer", reviewer_mapping)
            reviewer_id = _require_mapping(
                reviewer,
                "environment_protection_invalid",
            ).get("id")
            if (
                not isinstance(reviewer_id, int)
                or isinstance(reviewer_id, bool)
                or reviewer_id <= 0
            ):
                raise PreflightError("environment_protection_invalid")
            reviewer_ids.append(reviewer_id)
    return sorted(set(reviewer_ids))


def _collect_control_snapshot(
    *,
    repository: str,
    repository_id: int,
    branch: str,
    environment_name: str,
    token: str,
) -> dict[str, object]:
    repository_snapshot = _collect_repository_snapshot(
        repository=repository,
        repository_id=repository_id,
        token=token,
        identity_code="control_repository_identity_invalid",
    )
    encoded_environment_name = urllib.parse.quote(environment_name, safe="")
    branch_name = urllib.parse.quote(branch, safe="")
    environment_payload = _github_json(
        f"/repos/{repository}/environments/{encoded_environment_name}",
        token,
    )
    protection = _github_json(
        f"/repos/{repository}/branches/{branch_name}/protection",
        token,
    )
    environment_secret_payload = _github_json(
        f"/repositories/{repository_id}/environments/{encoded_environment_name}"
        "/secrets?per_page=100",
        token,
    )
    environment_variable_payload = _github_json(
        f"/repositories/{repository_id}/environments/{encoded_environment_name}"
        "/variables?per_page=100",
        token,
    )
    repository_secret_payload = _github_json(
        f"/repos/{repository}/actions/secrets?per_page=100",
        token,
    )
    repository_variable_payload = _github_json(
        f"/repos/{repository}/actions/variables?per_page=100",
        token,
    )
    enforce_admins = _require_mapping(
        protection.get("enforce_admins"),
        "control_branch_protection_invalid",
    )
    pull_reviews = _require_mapping(
        protection.get("required_pull_request_reviews"),
        "control_branch_protection_invalid",
    )
    status_checks = _require_mapping(
        protection.get("required_status_checks"),
        "control_branch_protection_invalid",
    )
    required_status_checks = _required_status_check_identities(
        status_checks.get("checks"),
        "control_branch_protection_invalid",
    )
    return {
        "repository": repository_snapshot,
        "environment": {
            "name": environment_payload.get("name"),
            "reviewer_ids": _environment_reviewer_ids(environment_payload),
            "prevent_self_review": environment_payload.get("prevent_self_review"),
            "can_admins_bypass": environment_payload.get("can_admins_bypass"),
            "deployment_branch_policy": environment_payload.get("deployment_branch_policy"),
        },
        "branch_protection": {
            "protected": True,
            "enforce_admins": enforce_admins.get("enabled"),
            "required_approving_review_count": pull_reviews.get("required_approving_review_count"),
            "required_status_checks_strict": status_checks.get("strict"),
            "required_status_checks": [
                {"app_id": app_id, "context": context} for context, app_id in required_status_checks
            ],
        },
        "environment_secrets": _inventory(environment_secret_payload, "secrets"),
        "environment_variables": _inventory(environment_variable_payload, "variables"),
        "repository_secrets": _inventory(repository_secret_payload, "secrets"),
        "repository_variables": _inventory(repository_variable_payload, "variables"),
    }


def _collect_target_snapshot(
    *,
    repository: str,
    repository_id: int,
    branch: str,
    expected_tag_ruleset_name: str,
    token: str,
) -> dict[str, object]:
    repository_snapshot = _collect_repository_snapshot(
        repository=repository,
        repository_id=repository_id,
        token=token,
        identity_code="target_repository_identity_invalid",
    )
    branch_name = urllib.parse.quote(branch, safe="")
    _github_json(
        f"/repos/{repository}/branches/{branch_name}/protection",
        token,
    )
    rulesets = _github_list(f"/repos/{repository}/rulesets?per_page=100", token)
    if len(rulesets) >= _MAX_RULESETS:
        raise PreflightError("github_ruleset_inventory_truncated")
    immutable_releases = _github_json(f"/repos/{repository}/immutable-releases", token)
    repository_secret_payload = _github_json(
        f"/repos/{repository}/actions/secrets?per_page=100",
        token,
    )
    release_tag_rulesets: list[Mapping[str, object]] = []
    for summary in rulesets:
        if summary.get("name") != expected_tag_ruleset_name:
            continue
        ruleset_id = summary.get("id")
        if not isinstance(ruleset_id, int) or isinstance(ruleset_id, bool) or ruleset_id <= 0:
            raise PreflightError("release_tag_ruleset_invalid")
        ruleset = _github_json(f"/repos/{repository}/rulesets/{ruleset_id}", token)
        if ruleset.get("id") != ruleset_id or ruleset.get("name") != expected_tag_ruleset_name:
            raise PreflightError("release_tag_ruleset_invalid")
        release_tag_rulesets.append(ruleset)
    return {
        "repository": repository_snapshot,
        "branch_protection": {"protected": True},
        "release_tag_rulesets": [
            {field: ruleset.get(field) for field in _RELEASE_TAG_RULESET_FIELDS}
            for ruleset in release_tag_rulesets
        ],
        "immutable_releases": {"enabled": immutable_releases.get("enabled")},
        "repository_secrets": _inventory(repository_secret_payload, "secrets"),
    }


def collect_remote_snapshot(policy: Mapping[str, object], token: str) -> dict[str, object]:
    _validate_policy(policy)
    expected_tag_ruleset = _require_mapping(
        policy.get("release_tag_ruleset"),
        "release_tag_ruleset_invalid",
    )
    expected_tag_ruleset_name = _require_text(
        expected_tag_ruleset.get("name"),
        "release_tag_ruleset_invalid",
    )
    branch = str(policy["branch"])
    return {
        "control": _collect_control_snapshot(
            repository=str(policy["repository"]),
            repository_id=_require_positive_int(
                policy["repository_id"],
                "policy_repository_identity_invalid",
            ),
            branch=branch,
            environment_name=str(policy["environment"]),
            token=token,
        ),
        "target": _collect_target_snapshot(
            repository=str(policy["reviewed_repository"]),
            repository_id=_require_positive_int(
                policy["reviewed_repository_id"],
                "policy_repository_identity_invalid",
            ),
            branch=branch,
            expected_tag_ruleset_name=expected_tag_ruleset_name,
            token=token,
        ),
    }


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        encoded = path.read_bytes()
        if not encoded or len(encoded) > _MAX_RESPONSE_BYTES:
            raise PreflightError("policy_input_invalid")
        return _require_mapping(json.loads(encoded), "policy_input_invalid")
    except PreflightError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightError("policy_input_invalid") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token = os.environ.get("RELEASE_CONTROL_PREFLIGHT_TOKEN", "").strip()
    if not token:
        print("release-control preflight failed: token_missing", file=sys.stderr)
        return 1
    try:
        policy = _load_json(args.policy)
        snapshot = collect_remote_snapshot(policy, token)
        receipt = validate_preflight_snapshot(policy, snapshot)
        args.output.write_text(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    except (OSError, PreflightError) as exc:
        print(f"release-control preflight failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
