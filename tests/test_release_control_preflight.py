from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "release-control" / "scaffold" / "scripts"
POLICY = ROOT / "release-control" / "policy-v0.2.1.json"
INSPECTOR_CONTRACT = ROOT / "release-control" / "inspector-contract-v1.md"
CONTROL_REPOSITORY_ID = 42
TARGET_REPOSITORY_ID = 84


def _module(name: str = "verify_remote_preflight") -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


def _configured_policy(module: ModuleType) -> dict[str, object]:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    policy["bootstrap_state"] = "configured"
    policy["repository_id"] = CONTROL_REPOSITORY_ID
    policy["reviewed_repository_id"] = TARGET_REPOSITORY_ID
    policy["required_reviewer_ids"] = [12345]
    policy["staging_repository"] = "example/mercury-public-staging"
    policy["inspector"]["sha256"] = "f" * 64
    supabase = policy["supabase"]
    assert isinstance(supabase, dict)
    supabase["project_ref"] = "abcdefghijklmnopqrst"
    supabase["migration_history_sha256"] = "1" * 64
    for index, function in enumerate(supabase["functions"], start=1):
        function["definition_sha256"] = f"{index:064x}"
    supabase["schema_sha256"] = module.build_supabase_schema_digest(supabase)
    return policy


def _snapshot(policy: dict[str, object]) -> dict[str, object]:
    return {
        "control": {
            "repository": {
                "id": policy["repository_id"],
                "full_name": policy["repository"],
                "visibility": "public",
                "default_branch": "main",
            },
            "environment": {
                "name": "production-release",
                "reviewer_ids": [12345],
                "prevent_self_review": True,
                "can_admins_bypass": False,
                "deployment_branch_policy": {
                    "protected_branches": True,
                    "custom_branch_policies": False,
                },
            },
            "branch_protection": {
                "protected": True,
                "enforce_admins": True,
                "required_approving_review_count": 1,
                "required_status_checks_strict": True,
                "required_status_checks": policy["required_status_checks"],
            },
            "environment_secrets": policy["required_environment_secrets"],
            "environment_variables": policy["required_environment_variables"],
            "repository_secrets": [],
            "repository_variables": [],
        },
        "target": {
            "repository": {
                "id": policy["reviewed_repository_id"],
                "full_name": policy["reviewed_repository"],
                "visibility": "public",
                "default_branch": "main",
            },
            "branch_protection": {
                "protected": True,
            },
            "release_tag_rulesets": [policy["release_tag_ruleset"]],
            "immutable_releases": {"enabled": True},
            "repository_secrets": [],
        },
    }


def test_remote_preflight_emits_only_strict_sanitized_protection_receipt() -> None:
    module = _module()
    policy = _configured_policy(module)

    receipt = module.validate_preflight_snapshot(policy, _snapshot(policy))

    assert receipt["environment"] == "production-release"
    assert receipt["repository_visibility"] == "public"
    assert receipt["required_reviewers"] == 1
    assert receipt["prevent_self_review"] is True
    assert receipt["admin_bypass_disabled"] is True
    assert receipt["protected_branch_only"] is True
    assert len(receipt["required_configuration_sha256"]) == 64
    serialized = json.dumps(receipt, sort_keys=True)
    for forbidden in ("token", "secret", "service_role", "abcdefghijklmnopqrst"):
        assert forbidden not in serialized.lower()


def test_remote_preflight_rejects_legacy_policy_without_immutable_repository_ids() -> None:
    module = _module()
    policy = _configured_policy(module)
    del policy["repository_id"]
    del policy["reviewed_repository_id"]

    with pytest.raises(module.PreflightError, match="policy_schema_invalid"):
        module.validate_preflight_snapshot(policy, _snapshot(_configured_policy(module)))


@pytest.mark.parametrize(
    ("scope", "field"),
    (
        ("control", "id"),
        ("control", "full_name"),
        ("target", "id"),
        ("target", "full_name"),
    ),
)
def test_remote_preflight_rejects_exact_repository_identity_mismatch(
    scope: str,
    field: str,
) -> None:
    module = _module()
    policy = _configured_policy(module)
    snapshot = _snapshot(policy)
    boundary = snapshot[scope]
    assert isinstance(boundary, dict)
    repository = boundary["repository"]
    assert isinstance(repository, dict)
    repository[field] = repository[field] + 1 if field == "id" else "example/wrong-repository"

    with pytest.raises(module.PreflightError, match=f"{scope}_repository_identity_invalid"):
        module.validate_preflight_snapshot(policy, snapshot)


@pytest.mark.parametrize(
    ("scope", "policy_id_field"),
    (
        ("control", "repository_id"),
        ("target", "reviewed_repository_id"),
    ),
)
def test_remote_preflight_rejects_boolean_repository_id(
    scope: str,
    policy_id_field: str,
) -> None:
    module = _module()
    policy = _configured_policy(module)
    policy[policy_id_field] = 1
    snapshot = _snapshot(policy)
    boundary = snapshot[scope]
    assert isinstance(boundary, dict)
    repository = boundary["repository"]
    assert isinstance(repository, dict)
    repository["id"] = True

    with pytest.raises(module.PreflightError, match=f"{scope}_repository_identity_invalid"):
        module.validate_preflight_snapshot(policy, snapshot)


@pytest.mark.parametrize(
    ("case", "code"),
    (
        ("visibility", "target_repository_protection_invalid"),
        ("default_branch", "target_repository_protection_invalid"),
        ("branch_protection", "target_branch_protection_invalid"),
        ("forbidden_secret", "target_repository_secret_forbidden"),
    ),
)
def test_remote_preflight_rejects_target_failure_after_control_passes(
    case: str,
    code: str,
) -> None:
    module = _module()
    policy = _configured_policy(module)
    snapshot = _snapshot(policy)
    target = snapshot["target"]
    assert isinstance(target, dict)

    if case == "visibility":
        repository = target["repository"]
        assert isinstance(repository, dict)
        repository["visibility"] = "private"
    elif case == "default_branch":
        repository = target["repository"]
        assert isinstance(repository, dict)
        repository["default_branch"] = "develop"
    elif case == "branch_protection":
        protection = target["branch_protection"]
        assert isinstance(protection, dict)
        protection["protected"] = False
    else:
        target["repository_secrets"] = [policy["forbidden_repository_secrets"][0]]

    with pytest.raises(module.PreflightError, match=code):
        module.validate_preflight_snapshot(policy, snapshot)


def test_policy_declares_nonempty_identity_bound_required_status_checks() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))

    checks = policy["required_status_checks"]
    assert checks
    assert all(set(check) == {"app_id", "context"} for check in checks)
    assert all(isinstance(check["app_id"], int) and check["app_id"] > 0 for check in checks)
    assert all(isinstance(check["context"], str) and check["context"].strip() for check in checks)


def test_target_repository_read_and_write_tokens_have_separate_preflight_contracts() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    contract = INSPECTOR_CONTRACT.read_text(encoding="utf-8")

    for inventory in (
        policy["required_environment_secrets"],
        policy["forbidden_repository_secrets"],
    ):
        assert "MERCURY_TARGET_REPOSITORY_READ_TOKEN" in inventory
        assert "MERCURY_TARGET_REPOSITORY_TOKEN" in inventory
        assert "MERCURY_TARGET_WORKFLOW_DISPATCH_TOKEN" in inventory
    assert "`MERCURY_TARGET_REPOSITORY_READ_TOKEN`" in contract
    assert "`MERCURY_TARGET_REPOSITORY_TOKEN`" in contract
    assert "`MERCURY_TARGET_WORKFLOW_DISPATCH_TOKEN`" in contract
    assert "`actions:write`" in contract
    assert "`release.yml`" in contract
    assert "never forward" in contract


def test_policy_declares_exact_active_v021_tag_ruleset_for_publisher() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))

    assert policy["immutable_releases_required"] is True
    ruleset = policy["release_tag_ruleset"]
    assert ruleset["target"] == "tag"
    assert ruleset["enforcement"] == "active"
    assert ruleset["conditions"] == {"ref_name": {"exclude": [], "include": ["refs/tags/v0.2.1"]}}
    assert ruleset["rules"] == [
        {"type": "deletion"},
        {
            "parameters": {"update_allows_fetch_and_merge": False},
            "type": "update",
        },
    ]
    assert ruleset["bypass_actors"] == []


@pytest.mark.parametrize("case", ("missing", "wrong", "bypassed"))
def test_remote_preflight_rejects_unapproved_v021_tag_ruleset(case: str) -> None:
    module = _module()
    policy = _configured_policy(module)
    snapshot = _snapshot(policy)

    target = snapshot["target"]
    assert isinstance(target, dict)
    if case == "missing":
        target["release_tag_rulesets"] = []
    else:
        observed_ruleset = dict(policy["release_tag_ruleset"])
        if case == "wrong":
            observed_ruleset["rules"] = [{"type": "creation"}, {"type": "deletion"}]
        else:
            observed_ruleset["bypass_actors"] = [
                {"actor_id": 999, "actor_type": "Integration", "bypass_mode": "always"}
            ]
        target["release_tag_rulesets"] = [observed_ruleset]

    with pytest.raises(module.PreflightError, match="release_tag_ruleset_invalid"):
        module.validate_preflight_snapshot(policy, snapshot)


def test_remote_preflight_rejects_disabled_immutable_releases() -> None:
    module = _module()
    policy = _configured_policy(module)
    snapshot = _snapshot(policy)
    target = snapshot["target"]
    assert isinstance(target, dict)
    target["immutable_releases"] = {"enabled": False}

    with pytest.raises(module.PreflightError, match="immutable_releases_invalid"):
        module.validate_preflight_snapshot(policy, snapshot)


@pytest.mark.parametrize(
    "case",
    ("empty", "wrong_context", "wrong_app_id", "duplicate", "malformed", "legacy_unbound"),
)
def test_remote_preflight_rejects_invalid_required_status_check_identities(case: str) -> None:
    module = _module()
    policy = _configured_policy(module)
    snapshot = _snapshot(policy)
    expected = dict(policy["required_status_checks"][0])
    control = snapshot["control"]
    assert isinstance(control, dict)
    branch = control["branch_protection"]
    assert isinstance(branch, dict)

    if case == "empty":
        branch["required_status_checks"] = []
    elif case == "wrong_context":
        branch["required_status_checks"] = [{**expected, "context": "unexpected check"}]
    elif case == "wrong_app_id":
        branch["required_status_checks"] = [{**expected, "app_id": expected["app_id"] + 1}]
    elif case == "duplicate":
        branch["required_status_checks"] = [expected, expected]
    elif case == "malformed":
        branch["required_status_checks"] = [{"app_id": expected["app_id"], "context": " "}]
    else:
        branch["required_status_checks"] = [{"context": expected["context"]}]

    with pytest.raises(module.PreflightError, match="branch_protection_invalid"):
        module.validate_preflight_snapshot(policy, snapshot)


@pytest.mark.parametrize("case", ("empty", "duplicate", "malformed"))
def test_remote_preflight_rejects_invalid_required_status_check_policy(case: str) -> None:
    module = _module()
    policy = _configured_policy(module)
    expected = dict(policy["required_status_checks"][0])

    if case == "empty":
        policy["required_status_checks"] = []
    elif case == "duplicate":
        policy["required_status_checks"] = [expected, expected]
    else:
        policy["required_status_checks"] = [{"app_id": "unbound", "context": expected["context"]}]

    with pytest.raises(module.PreflightError, match="policy_required_status_checks_invalid"):
        module.validate_preflight_snapshot(policy, _snapshot(policy))


def test_remote_snapshot_preserves_github_check_context_and_app_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    policy = _configured_policy(module)
    control_repository = policy["repository"]
    target_repository = policy["reviewed_repository"]
    environment = policy["environment"]

    def github_json(path: str, _token: str) -> dict[str, object]:
        if path == f"/repos/{control_repository}":
            return {
                "id": CONTROL_REPOSITORY_ID,
                "full_name": control_repository,
                "visibility": "public",
                "default_branch": "main",
            }
        if path == f"/repos/{target_repository}":
            return {
                "id": TARGET_REPOSITORY_ID,
                "full_name": target_repository,
                "visibility": "public",
                "default_branch": "main",
            }
        if path == f"/repos/{control_repository}/environments/{environment}":
            return {
                "name": environment,
                "prevent_self_review": True,
                "can_admins_bypass": False,
                "deployment_branch_policy": {
                    "protected_branches": True,
                    "custom_branch_policies": False,
                },
                "protection_rules": [
                    {"type": "required_reviewers", "reviewers": [{"reviewer": {"id": 12345}}]}
                ],
            }
        if path == f"/repos/{control_repository}/branches/main/protection":
            return {
                "enforce_admins": {"enabled": True},
                "required_pull_request_reviews": {"required_approving_review_count": 1},
                "required_status_checks": {
                    "strict": True,
                    "checks": policy["required_status_checks"],
                    "contexts": ["legacy context is ignored when bound checks exist"],
                },
            }
        if path == f"/repos/{target_repository}/branches/main/protection":
            return {
                "enforce_admins": {"enabled": True},
                "required_pull_request_reviews": {"required_approving_review_count": 1},
            }
        if path == f"/repos/{target_repository}/immutable-releases":
            return {"enabled": True}
        if path == f"/repos/{target_repository}/rulesets/99":
            return {"id": 99, **policy["release_tag_ruleset"]}
        if "/environments/" in path and "/secrets?" in path:
            return {
                "total_count": len(policy["required_environment_secrets"]),
                "secrets": [{"name": name} for name in policy["required_environment_secrets"]],
            }
        if "/environments/" in path and "/variables?" in path:
            return {
                "total_count": len(policy["required_environment_variables"]),
                "variables": [{"name": name} for name in policy["required_environment_variables"]],
            }
        if path.endswith("/actions/secrets?per_page=100"):
            return {"total_count": 0, "secrets": []}
        if path.endswith("/actions/variables?per_page=100"):
            return {"total_count": 0, "variables": []}
        raise AssertionError(path)

    monkeypatch.setattr(module, "_github_json", github_json)

    def github_list(path: str, _token: str) -> list[dict[str, object]]:
        if path != f"/repos/{target_repository}/rulesets?per_page=100":
            raise AssertionError(path)
        ruleset = policy["release_tag_ruleset"]
        assert isinstance(ruleset, dict)
        return [{"id": 99, "name": ruleset["name"]}]

    monkeypatch.setattr(module, "_github_list", github_list, raising=False)

    snapshot = module.collect_remote_snapshot(policy, "test")

    assert set(snapshot) == {"control", "target"}
    control = snapshot["control"]
    target = snapshot["target"]
    assert isinstance(control, dict)
    assert isinstance(target, dict)
    branch = control["branch_protection"]
    assert isinstance(branch, dict)
    assert branch["required_status_checks"] == policy["required_status_checks"]
    assert target["branch_protection"] == {"protected": True}
    assert target["release_tag_rulesets"] == [policy["release_tag_ruleset"]]
    assert target["immutable_releases"] == {"enabled": True}
    module.validate_preflight_snapshot(policy, snapshot)


def test_inspector_contract_requires_tls_database_identity_before_system_catalog_queries() -> None:
    contract = INSPECTOR_CONTRACT.read_text(encoding="utf-8")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))

    for inventory in (
        policy["required_environment_secrets"],
        policy["forbidden_repository_secrets"],
    ):
        assert "SUPABASE_DB_URL" in inventory
    for requirement in (
        "`SUPABASE_DB_URL`",
        "`sslmode=verify-full`",
        "`current_database()`",
        "`current_user`",
        "`supabase_migrations.schema_migrations`",
        "`pg_get_functiondef`",
        "PostgREST",
    ):
        assert requirement in contract


def test_pinned_inspector_requires_and_forwards_direct_database_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module("run_pinned_inspector")
    inspector = tmp_path / "bin" / "mercury-release-control-inspector"
    inspector.parent.mkdir()
    inspector.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    inspector.chmod(0o700)
    for relative_path in module._PINNED_CLOSURE:
        source = SCRIPTS.parent / relative_path
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "bootstrap_state": "configured",
                "inspector": {
                    "interface_version": 1,
                    "path": "bin/mercury-release-control-inspector",
                    "sha256": hashlib.sha256(inspector.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    allowlist = tmp_path / "allowlist.json"
    manifest.write_text("{}", encoding="utf-8")
    allowlist.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"
    captured_environment: dict[str, str] = {}

    def run(_command: list[str], **kwargs: object) -> SimpleNamespace:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        captured_environment.update(environment)
        Path(_command[-1]).write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)

    with pytest.raises(module.InspectorError, match="inspector_database_credential_missing"):
        module.run_pinned_inspector(
            root=tmp_path,
            policy_path=policy_path,
            reviewed_sha="a" * 40,
            staging_ref="v0.2.1-rc1",
            manifest=manifest,
            allowlist=allowlist,
            output=output,
        )

    monkeypatch.setenv("SUPABASE_DB_URL", "configured")
    monkeypatch.setenv("INSPECTOR_PYTHON", str(Path(sys.executable).resolve()))
    monkeypatch.setenv("MERCURY_TARGET_REPOSITORY_READ_TOKEN", "configured")
    monkeypatch.setenv("MERCURY_TARGET_REPOSITORY_TOKEN", "configured")
    module.run_pinned_inspector(
        root=tmp_path,
        policy_path=policy_path,
        reviewed_sha="a" * 40,
        staging_ref="v0.2.1-rc1",
        manifest=manifest,
        allowlist=allowlist,
        output=output,
    )

    assert "SUPABASE_DB_URL" in captured_environment
    assert "MERCURY_TARGET_REPOSITORY_READ_TOKEN" in captured_environment
    assert "MERCURY_TARGET_REPOSITORY_TOKEN" not in captured_environment


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("repository", "visibility"), "private"),
        (("environment", "reviewer_ids"), []),
        (("environment", "prevent_self_review"), False),
        (("environment", "can_admins_bypass"), True),
        (("branch_protection", "protected"), False),
    ),
)
def test_remote_preflight_rejects_missing_protections(
    path: tuple[str, str],
    value: object,
) -> None:
    module = _module()
    policy = _configured_policy(module)
    snapshot = _snapshot(policy)
    control = snapshot["control"]
    assert isinstance(control, dict)
    container = control[path[0]]
    assert isinstance(container, dict)
    container[path[1]] = value

    with pytest.raises(module.PreflightError):
        module.validate_preflight_snapshot(policy, snapshot)


def test_remote_preflight_rejects_repo_level_credential_copy() -> None:
    module = _module()
    policy = _configured_policy(module)
    snapshot = _snapshot(policy)
    control = snapshot["control"]
    assert isinstance(control, dict)
    control["repository_secrets"] = ["SUPABASE_SERVICE_ROLE_KEY"]

    with pytest.raises(module.PreflightError, match="control_repository_secret_forbidden"):
        module.validate_preflight_snapshot(policy, snapshot)


def test_bootstrap_policy_is_intentionally_unconfigured_and_fails_closed() -> None:
    module = _module()
    policy = json.loads(POLICY.read_text(encoding="utf-8"))

    assert policy["bootstrap_state"] == "unconfigured"
    with pytest.raises(module.PreflightError, match="policy_unconfigured"):
        module.validate_preflight_snapshot(policy, {})


def test_release_control_scaffold_workflow_gates_attestation_on_preflight() -> None:
    workflow = (
        ROOT / "release-control" / "scaffold" / ".github" / "workflows" / "attest-v0.2.1.yml"
    ).read_text(encoding="utf-8")

    assert "environment: production-release" in workflow
    assert "verify_remote_preflight.py" in workflow
    assert "needs: remote-preflight" in workflow
    assert "trusted-hosted-attestation.json" in workflow
    assert "github.run_attempt" in workflow
    assert "RELEASE_CONTROL_PREFLIGHT_TOKEN" in workflow
    assert 'test "${#ATTESTATION_B64}" -le 60000' in workflow
    assert "65536" not in workflow
    assert "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "mercury-tools" not in workflow.split("actions/checkout@", 1)[1].split("\n      -", 1)[0]


def test_release_ready_handoff_is_strictly_run_attempt_and_digest_bound() -> None:
    module = _module("verify_mercury_handoff")
    payload = {
        "schema_version": 2,
        "version": "0.2.1",
        "reviewed_commit_sha": "a" * 40,
        "caller": {"run_id": 123, "run_attempt": 2},
        "original_release_control": {
            "run_id": 456,
            "run_attempt": 3,
            "repository": "example/mercury-release-control",
            "repository_id": 789,
            "producer_commit_sha": "e" * 40,
            "workflow_path": ".github/workflows/attest-v0.2.1.yml",
            "artifact_id": 10,
            "artifact_digest": "b" * 64,
            "payload_sha256": "c" * 64,
            "staging_repository": "example/mercury-public-staging",
            "staging_ref": "v0.2.1-rc1",
        },
        "relayed_attestation": {
            "artifact_id": 11,
            "artifact_digest": "d" * 64,
            "payload_sha256": "c" * 64,
        },
        "release_artifacts": {
            "artifact_id": 12,
            "artifact_digest": "f" * 64,
        },
        "staging_identity": {
            "artifact_id": 13,
            "artifact_digest": "1" * 64,
        },
    }

    result = module.validate_handoff(
        payload,
        expected_reviewed_sha="a" * 40,
        expected_caller_run_id=123,
        expected_caller_run_attempt=2,
        expected_control_run_id=456,
        expected_control_run_attempt=3,
        expected_control_repository="example/mercury-release-control",
        expected_control_repository_id=789,
        expected_control_producer_sha="e" * 40,
    )

    assert result["release_artifacts"]["artifact_id"] == 12
    with pytest.raises(module.HandoffError):
        module.validate_handoff(
            payload,
            expected_reviewed_sha="a" * 40,
            expected_caller_run_id=123,
            expected_caller_run_attempt=1,
            expected_control_run_id=456,
            expected_control_run_attempt=3,
            expected_control_repository="example/mercury-release-control",
            expected_control_repository_id=789,
            expected_control_producer_sha="e" * 40,
        )


def test_release_control_publication_consumes_exact_handoff_without_candidate_checkout() -> None:
    workflow = (
        ROOT / "release-control" / "scaffold" / ".github" / "workflows" / "publish-v0.2.1.yml"
    ).read_text(encoding="utf-8")

    assert "needs: remote-preflight" in workflow
    assert "verify_remote_preflight.py" in workflow
    assert "verify_mercury_handoff.py" in workflow
    assert workflow.count("artifact-ids:") >= 3
    assert "handoff_artifact_digest" in workflow
    assert "handoff_payload_sha256" in workflow
    assert ".run_attempt == $run_attempt" in workflow
    assert ".head_sha == $head_sha" in workflow
    assert "git clone" not in workflow
    assert "uv run" not in workflow
    assert "git/tags" in workflow
    assert "--verify-tag" in workflow


def test_external_assembler_binds_exact_policy_and_sanitized_evidence() -> None:
    preflight_module = _module()
    assembler = _module("assemble_trusted_attestation")
    policy = _configured_policy(preflight_module)
    preflight = preflight_module.validate_preflight_snapshot(
        policy,
        _snapshot(policy),
    )
    completed = "2026-07-16T00:00:00+00:00"
    supabase = dict(policy["supabase"])
    supabase["project_ref_sha256"] = (
        __import__("hashlib").sha256(supabase["project_ref"].encode()).hexdigest()
    )
    evidence = {
        "schema_version": 1,
        "reviewed_repository": policy["reviewed_repository"],
        "reviewed_commit_sha": "a" * 40,
        "public_surface_manifest_sha256": "b" * 64,
        "secret_scan_allowlist_sha256": "c" * 64,
        "flowaccount": {
            "total": 190,
            "terminal_records": 190,
            "required_live_test_passed": True,
            "report_sha256": "d" * 64,
        },
        "staging": {
            "repository": policy["staging_repository"],
            "ref": "v0.2.1-rc1",
            "commit_sha": "e" * 40,
            "tree_sha256": "f" * 64,
            "local_tool_count": 19,
        },
        "render": {
            "deployment_commit": "a" * 40,
            "version": "0.2.1",
            "hosted_tool_count": 20,
            "evidence_sha256": "1" * 64,
        },
        "supabase": supabase,
        "surfaces": [
            {
                "surface": surface,
                "status": "passed",
                "scanner_versions": (
                    ["1.0.0", "3.88.32", "8.24.3"]
                    if surface in {"git_all_refs", "github_pull_request_refs"}
                    else ["1.0.0"]
                ),
                "started_at": completed,
                "completed_at": completed,
                "finding_count": 0,
                "evidence_hashes": [f"{index + 2:064x}"],
                "exit_codes": [0],
                "blocker_codes": [],
                "finding_codes": [],
            }
            for index, surface in enumerate(assembler.TRUSTED_SURFACES)
        ],
        "completed_at": completed,
    }

    attestation = assembler.assemble_attestation(
        evidence=evidence,
        preflight=preflight,
        policy=policy,
        producer_repository=policy["repository"],
        producer_sha="2" * 40,
        producer_run_id=123,
        producer_run_attempt=2,
        staging_ref="v0.2.1-rc1",
        manifest_sha256="b" * 64,
        allowlist_sha256="c" * 64,
    )

    assert attestation["schema_version"] == 2
    assert attestation["supabase"]["project_ref"] == "abcdefghijklmnopqrst"
    evidence["supabase"] = {**supabase, "project_ref": "z" * 20}
    with pytest.raises(assembler.AttestationError, match="supabase_project_mismatch"):
        assembler.assemble_attestation(
            evidence=evidence,
            preflight=preflight,
            policy=policy,
            producer_repository=policy["repository"],
            producer_sha="2" * 40,
            producer_run_id=123,
            producer_run_attempt=2,
            staging_ref="v0.2.1-rc1",
            manifest_sha256="b" * 64,
            allowlist_sha256="c" * 64,
        )
