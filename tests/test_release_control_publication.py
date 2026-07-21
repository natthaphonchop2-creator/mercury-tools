from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "release-control" / "scaffold"
SCRIPTS = CONTROL / "scripts"
PUBLISH_WORKFLOW = CONTROL / ".github" / "workflows" / "publish-v0.2.1.yml"
POLICY = ROOT / "release-control" / "policy-v0.2.1.json"
EXPECTED_PUBLIC_TREE = ROOT / "release-control" / "expected-public-tree.json"


def _module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


def _workflow() -> dict[str, object]:
    payload = yaml.load(PUBLISH_WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_v030_expected_tree_binds_trusted_release_control_identity() -> None:
    payload = (
        json.loads(EXPECTED_PUBLIC_TREE.read_text(encoding="utf-8"))
        if EXPECTED_PUBLIC_TREE.is_file()
        else {}
    )

    assert payload.get("trusted_release_control") == {
        "attestation_workflow": ".github/workflows/attest-v0.3.0.yml",
        "mercury_workflow": ".github/workflows/release-v0.3.0.yml",
        "migration_id": "20260719120000",
        "publication_workflow": ".github/workflows/publish-v0.3.0.yml",
        "repository": "natthaphonchop2-creator/mercury-release-control-v2",
        "required_bindings": [
            "repository_id",
            "reviewed_sha",
            "run_id",
            "run_attempt",
            "artifact_id",
            "artifact_digest",
            "annotated_tag",
            "provider_state",
        ],
    }


def _configured_policy(preflight: ModuleType) -> dict[str, object]:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    policy["bootstrap_state"] = "configured"
    policy["repository_id"] = 42
    policy["reviewed_repository_id"] = 84
    policy["required_reviewer_ids"] = [12345]
    policy["staging_repository"] = "example/mercury-public-staging"
    policy["inspector"]["sha256"] = "f" * 64
    supabase = policy["supabase"]
    supabase["project_ref"] = "abcdefghijklmnopqrst"
    supabase["migration_history_sha256"] = "1" * 64
    for index, function in enumerate(supabase["functions"], start=1):
        function["definition_sha256"] = f"{index:064x}"
    supabase["schema_sha256"] = preflight.build_supabase_schema_digest(supabase)
    return policy


def _preflight_receipt(preflight: ModuleType, policy: dict[str, object]) -> dict[str, object]:
    snapshot = {
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
            "branch_protection": {"protected": True},
            "release_tag_rulesets": [policy["release_tag_ruleset"]],
            "immutable_releases": {"enabled": True},
            "repository_secrets": [],
        },
    }
    return preflight.validate_preflight_snapshot(policy, snapshot)


def _original_attestation(
    preflight: ModuleType,
    assembler: ModuleType,
    *,
    completed_at: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    policy = _configured_policy(preflight)
    completed = completed_at.isoformat()
    supabase = dict(policy["supabase"])
    supabase["project_ref_sha256"] = hashlib.sha256(supabase["project_ref"].encode()).hexdigest()
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
        preflight=_preflight_receipt(preflight, policy),
        policy=policy,
        producer_repository=policy["repository"],
        producer_sha="2" * 40,
        producer_run_id=123,
        producer_run_attempt=2,
        staging_ref="v0.2.1-rc1",
        manifest_sha256="b" * 64,
        allowlist_sha256="c" * 64,
    )
    return policy, attestation


def test_release_ready_handoff_preserves_original_and_relayed_attestation() -> None:
    module = _module("verify_mercury_handoff")
    payload = {
        "schema_version": 2,
        "version": "0.2.1",
        "reviewed_commit_sha": "a" * 40,
        "caller": {"run_id": 123, "run_attempt": 2},
        "original_release_control": {
            "repository": "example/mercury-release-control",
            "repository_id": 987,
            "producer_commit_sha": "b" * 40,
            "workflow_path": ".github/workflows/attest-v0.2.1.yml",
            "run_id": 456,
            "run_attempt": 3,
            "artifact_id": 10,
            "artifact_digest": "c" * 64,
            "payload_sha256": "d" * 64,
            "staging_repository": "example/mercury-public-staging",
            "staging_ref": "v0.2.1-rc1",
        },
        "relayed_attestation": {
            "artifact_id": 11,
            "artifact_digest": "e" * 64,
            "payload_sha256": "d" * 64,
        },
        "release_artifacts": {"artifact_id": 12, "artifact_digest": "f" * 64},
        "staging_identity": {"artifact_id": 13, "artifact_digest": "1" * 64},
    }
    result = module.validate_handoff(
        payload,
        expected_reviewed_sha="a" * 40,
        expected_caller_run_id=123,
        expected_caller_run_attempt=2,
        expected_control_run_id=456,
        expected_control_run_attempt=3,
        expected_control_repository="example/mercury-release-control",
        expected_control_repository_id=987,
        expected_control_producer_sha="b" * 40,
    )
    assert result["original_release_control"]["artifact_id"] == 10
    assert result["relayed_attestation"]["artifact_id"] == 11

    payload["relayed_attestation"]["payload_sha256"] = "2" * 64
    with pytest.raises(module.HandoffError, match="handoff_relay_invalid"):
        module.validate_handoff(
            payload,
            expected_reviewed_sha="a" * 40,
            expected_caller_run_id=123,
            expected_caller_run_attempt=2,
            expected_control_run_id=456,
            expected_control_run_attempt=3,
            expected_control_repository="example/mercury-release-control",
            expected_control_repository_id=987,
            expected_control_producer_sha="b" * 40,
        )


def test_original_attestation_verifier_rebuilds_contract_and_enforces_freshness(
    tmp_path: Path,
) -> None:
    preflight = _module("verify_remote_preflight")
    assembler = _module("assemble_trusted_attestation")
    verifier = _module("verify_original_attestation")
    now = datetime.now(UTC)
    policy, attestation = _original_attestation(
        preflight,
        assembler,
        completed_at=now,
    )
    policy_path = tmp_path / "policy.json"
    attestation_path = tmp_path / "attestation.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    encoded = json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode()
    attestation_path.write_bytes(encoded)

    result = verifier.verify_original_attestation(
        attestation_path=attestation_path,
        policy_path=policy_path,
        expected_payload_sha256=hashlib.sha256(encoded).hexdigest(),
        expected_reviewed_sha="a" * 40,
        expected_producer_repository=policy["repository"],
        expected_producer_sha="2" * 40,
        expected_producer_run_id=123,
        expected_producer_run_attempt=2,
        expected_staging_ref="v0.2.1-rc1",
        now=now,
    )
    assert result["status"] == "ok"

    _policy, stale = _original_attestation(
        preflight,
        assembler,
        completed_at=now - timedelta(hours=25),
    )
    stale_encoded = json.dumps(stale, sort_keys=True, separators=(",", ":")).encode()
    attestation_path.write_bytes(stale_encoded)
    with pytest.raises(verifier.VerificationError, match="attestation_stale"):
        verifier.verify_original_attestation(
            attestation_path=attestation_path,
            policy_path=policy_path,
            expected_payload_sha256=hashlib.sha256(stale_encoded).hexdigest(),
            expected_reviewed_sha="a" * 40,
            expected_producer_repository=policy["repository"],
            expected_producer_sha="2" * 40,
            expected_producer_run_id=123,
            expected_producer_run_attempt=2,
            expected_staging_ref="v0.2.1-rc1",
            now=now,
        )


def test_publisher_fetches_and_strictly_verifies_original_control_artifact() -> None:
    payload = _workflow()
    assert payload["permissions"] == {"actions": "read", "contents": "read"}
    publish = payload["jobs"]["publish"]
    commands = "\n".join(step.get("run", "") for step in publish["steps"] if isinstance(step, dict))

    assert "original_release_control" in commands
    assert "repos/$GITHUB_REPOSITORY/actions/runs/$SOURCE_RUN_ID" in commands
    assert "repos/$GITHUB_REPOSITORY/actions/artifacts/$SOURCE_ARTIFACT_ID" in commands
    assert ".github/workflows/attest-v0.2.1.yml" in commands
    assert "verify_original_attestation.py" in commands
    assert "verify_release_assets.py" in commands
    source_downloads = [
        step
        for step in publish["steps"]
        if step.get("with", {}).get("github-token") == "${{ github.token }}"
    ]
    assert len(source_downloads) == 1
    assert source_downloads[0]["with"]["repository"] == "${{ github.repository }}"
    assert all(
        step.get("with", {}).get("artifact-ids") != "${{ steps.handoff.outputs.relay_artifact_id }}"
        for step in publish["steps"]
    )


def test_publisher_never_clobbers_and_publishes_verified_draft_once() -> None:
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "--clobber" not in workflow
    assert "--draft" in workflow
    assert "--draft=false" in workflow
    assert "verify_release_assets.py" in workflow
    assert "immutable" in workflow
    assert workflow.count("verify_tag_binding") >= 2


def test_target_write_token_is_scoped_to_final_publication_steps() -> None:
    payload = _workflow()
    publish = payload["jobs"]["publish"]
    assert "env" not in publish or "GH_TOKEN" not in publish.get("env", {})
    steps = publish["steps"]
    write_token_steps = [
        step
        for step in steps
        if "secrets.MERCURY_TARGET_REPOSITORY_TOKEN" in json.dumps(step, sort_keys=True)
    ]
    assert len(write_token_steps) == 1
    assert "Publish exact immutable release" in write_token_steps[0]["name"]
    assert "MERCURY_TARGET_REPOSITORY_READ_TOKEN" in json.dumps(publish, sort_keys=True)
