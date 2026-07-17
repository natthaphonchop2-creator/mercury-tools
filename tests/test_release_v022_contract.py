from __future__ import annotations

import hashlib
import json
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from mercury_tools.release.scanner import ReleaseGateError

NOW = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
REVIEWED_SHA = "1" * 40
CONTROL_SHA = "2" * 40
PUBLIC_TREE_DIGEST = "3" * 64
CONTROL_REPOSITORY_ID = 1303413748
MERCURY_REPOSITORY_ID = 1290137723
SURFACES = (
    "git_all_refs",
    "github_pull_request_refs",
    "github_releases_and_assets",
    "github_actions_logs_artifacts_caches",
    "github_packages_pages_wiki",
    "marketplace_snapshot",
    "render_build_and_runtime_logs",
    "supabase_knowledge_and_storage",
)


def _canonical_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _attestation_payload() -> dict[str, object]:
    issued_at = NOW.isoformat().replace("+00:00", "Z")
    started_at = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    expires_at = (NOW + timedelta(minutes=60)).isoformat().replace("+00:00", "Z")
    surfaces = []
    for name in SURFACES:
        surfaces.append(
            {
                "blocker_codes": [],
                "completed_at": issued_at,
                "evidence_hashes": ["4" * 64],
                "exit_codes": [0],
                "finding_codes": [],
                "finding_count": 0,
                "scanner_versions": (
                    ["1.0.0", "3.88.32", "8.24.3"]
                    if name in {"git_all_refs", "github_pull_request_refs"}
                    else ["1.0.0"]
                ),
                "started_at": started_at,
                "status": "passed",
                "surface": name,
            }
        )
    payload: dict[str, object] = {
        "expires_at": expires_at,
        "issued_at": issued_at,
        "preflight": {
            "admin_bypass_disabled": True,
            "control_repository_id": CONTROL_REPOSITORY_ID,
            "environment": "production-release",
            "prevent_self_review": True,
            "protected_branch_only": True,
            "required_configuration_sha256": "5" * 64,
            "required_reviewers": 1,
            "target_repository_id": MERCURY_REPOSITORY_ID,
        },
        "provider_evidence": {
            "flowaccount": {"environment": "sandbox", "read_only": True, "status": 200},
            "public_mcp": {
                "catalog_action_count": 254,
                "flowaccount_citations": 1,
                "hosted_tool_count": 20,
                "peak_citations": 1,
                "status": 200,
                "write_tools_exposed": False,
            },
            "render": {
                "catalog_action_count": 254,
                "commit": REVIEWED_SHA,
                "hosted_tool_count": 20,
                "logs_scanned": True,
                "status": "live",
                "version": "0.2.2",
            },
            "reviewed_sha": REVIEWED_SHA,
            "supabase": {
                "function_count": 10,
                "migration_id": "20260716100000",
                "project_ref_sha256": "6" * 64,
                "rag_identity_count": 254,
                "read_only": True,
                "schema_sha256": "7" * 64,
                "table_count": 17,
            },
            "version": "0.2.2",
        },
        "public_tree_digest": PUBLIC_TREE_DIGEST,
        "reviewed_sha": REVIEWED_SHA,
        "schema_version": 2,
        "staging": {
            "commit_sha": "8" * 40,
            "ref": f"v0.2.2-rc.{REVIEWED_SHA[:12]}",
            "repository": "natthaphonchop2-creator/mercury-tools-staging",
            "tag_object_sha": "9" * 40,
        },
        "surface_count": 8,
        "surface_evidence_sha256": "a" * 64,
        "surfaces": surfaces,
        "version": "0.2.2",
        "workflow": {
            "attempt": 2,
            "control_commit": CONTROL_SHA,
            "repository_id": CONTROL_REPOSITORY_ID,
            "run_id": 1001,
        },
    }
    payload["payload_sha256"] = _canonical_digest(payload)
    return payload


def _write_attestation(path: Path, payload: dict[str, object] | None = None) -> str:
    encoded = json.dumps(
        payload or _attestation_payload(), separators=(",", ":"), sort_keys=True
    ).encode()
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _load(path: Path, digest: str):
    from mercury_tools.release.trusted_attestation_v2 import load_trusted_attestation_v2

    return load_trusted_attestation_v2(
        path,
        expected_payload_sha256=digest,
        expected_reviewed_repository="natthaphonchop2-creator/mercury-tools",
        expected_reviewed_repository_id=MERCURY_REPOSITORY_ID,
        expected_reviewed_sha=REVIEWED_SHA,
        expected_control_repository_id=CONTROL_REPOSITORY_ID,
        expected_control_sha=CONTROL_SHA,
        expected_control_run_id=1001,
        expected_control_run_attempt=2,
        expected_public_tree_digest=PUBLIC_TREE_DIGEST,
        now=NOW + timedelta(minutes=5),
    )


def test_v022_attestation_is_strict_identity_bound_and_carries_staging(tmp_path) -> None:
    path = tmp_path / "trusted-hosted-attestation.json"
    digest = _write_attestation(path)

    attestation = _load(path, digest)

    assert attestation.staging.repository == (
        "natthaphonchop2-creator/mercury-tools-staging"
    )
    assert attestation.staging.ref == f"v0.2.2-rc.{REVIEWED_SHA[:12]}"
    assert tuple(attestation.surface_map()) == SURFACES


def test_v022_attestation_rejects_unknown_fields_and_identity_drift(tmp_path) -> None:
    path = tmp_path / "trusted-hosted-attestation.json"
    payload = _attestation_payload()
    payload["raw_provider_payload"] = {"access_token": "forbidden"}
    digest = _write_attestation(path, payload)
    with pytest.raises(ReleaseGateError, match="^trusted_attestation_v2_invalid$"):
        _load(path, digest)

    digest = _write_attestation(path)
    with pytest.raises(ReleaseGateError, match="^trusted_attestation_v2_mismatch$"):
        from mercury_tools.release.trusted_attestation_v2 import (
            load_trusted_attestation_v2,
        )

        load_trusted_attestation_v2(
            path,
            expected_payload_sha256=digest,
            expected_reviewed_repository="natthaphonchop2-creator/mercury-tools",
            expected_reviewed_repository_id=MERCURY_REPOSITORY_ID,
            expected_reviewed_sha="f" * 40,
            expected_control_repository_id=CONTROL_REPOSITORY_ID,
            expected_control_sha=CONTROL_SHA,
            expected_control_run_id=1001,
            expected_control_run_attempt=2,
            expected_public_tree_digest=PUBLIC_TREE_DIGEST,
            now=NOW,
        )


def test_v022_handoff_carries_exact_attempt_bound_artifact_inventory(tmp_path) -> None:
    from mercury_tools.release.handoff_v3 import write_release_ready_handoff

    attestation_path = tmp_path / "trusted-hosted-attestation.json"
    digest = _write_attestation(attestation_path)
    attestation = _load(attestation_path, digest)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    names = (
        "SHA256SUMS.json",
        "mercury-finance-plugin-0.2.2.zip",
        "mercury-tools-0.2.2-source.tar.gz",
        "mercury_tools-0.2.2-py3-none-any.whl",
        "mercury_tools-0.2.2.tar.gz",
    )
    for index, name in enumerate(names, start=1):
        (artifacts / name).write_bytes(f"artifact-{index}".encode())
    output = tmp_path / "mercury-v0.2.2-release-ready.json"

    handoff = write_release_ready_handoff(
        artifacts=artifacts,
        attestation=attestation,
        control_artifact_id=101,
        control_artifact_digest="b" * 64,
        control_payload_sha256=digest,
        mercury_repository_id=MERCURY_REPOSITORY_ID,
        mercury_run_id=2002,
        mercury_run_attempt=3,
        release_bundle_artifact_id=303,
        release_bundle_artifact_digest="c" * 64,
        output=output,
        now=NOW + timedelta(minutes=5),
    )

    assert [item.name for item in handoff.artifacts] == sorted(names)
    assert handoff.original_release_control.artifact_id == 101
    assert handoff.original_release_control.run_id == 1001
    assert handoff.original_release_control.payload_sha256 == digest
    assert handoff.release_bundle.name == (
        "mercury-v0.2.2-release-artifacts-2002-attempt-3"
    )
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 3


def test_v022_handoff_rejects_symlinks_and_existing_output(tmp_path) -> None:
    from mercury_tools.release.handoff_v3 import write_release_ready_handoff

    attestation_path = tmp_path / "trusted-hosted-attestation.json"
    digest = _write_attestation(attestation_path)
    attestation = _load(attestation_path, digest)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"artifact")
    (artifacts / "mercury_tools-0.2.2-py3-none-any.whl").symlink_to(target)
    output = tmp_path / "mercury-v0.2.2-release-ready.json"

    with pytest.raises(ReleaseGateError, match="^release_handoff_artifacts_invalid$"):
        write_release_ready_handoff(
            artifacts=artifacts,
            attestation=attestation,
            control_artifact_id=101,
            control_artifact_digest="b" * 64,
            control_payload_sha256=digest,
            mercury_repository_id=MERCURY_REPOSITORY_ID,
            mercury_run_id=2002,
            mercury_run_attempt=3,
            release_bundle_artifact_id=303,
            release_bundle_artifact_digest="c" * 64,
            output=output,
            now=NOW,
        )

def test_v022_release_workflow_is_secretless_read_only_and_attempt_bound() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[1] / ".github/workflows/release-v0.2.2.yml"
    )
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    serialized = json.dumps(workflow, sort_keys=True)

    assert workflow["permissions"] == {"contents": "read"}
    assert len(workflow["on"]["workflow_dispatch"]["inputs"]) <= 10
    assert "staging_repo" not in workflow["on"]["workflow_dispatch"]["inputs"]
    assert "staging_ref" not in workflow["on"]["workflow_dispatch"]["inputs"]
    assert workflow["env"]["RELEASE_CONTROL_REPOSITORY"] == (
        "natthaphonchop2-creator/mercury-release-control-v2"
    )
    assert len(workflow["env"]["RELEASE_CONTROL_PIN"]) == 40
    assert workflow["env"]["RELEASE_CONTROL_PIN"] != "0" * 40
    assert "secrets." not in serialized
    assert "contents: write" not in workflow_path.read_text(encoding="utf-8")
    assert "gh release" not in serialized
    assert re.search(r"(?:gh api|curl)[^\n]*/repos/[^\n]*/releases", serialized) is None
    assert "release_bundle" in serialized
    assert "schema_version" in serialized


def test_all_active_public_version_surfaces_are_v022() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads(
        (root / "plugins/mercury-finance/.codex-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )
    mcp = json.loads(
        (root / "plugins/mercury-finance/.mcp.json").read_text(encoding="utf-8")
    )
    package_source = (root / "src/mercury_tools/__init__.py").read_text(
        encoding="utf-8"
    )
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    post_public = (root / ".github/workflows/post-public-verify.yml").read_text(
        encoding="utf-8"
    )

    assert project["project"]["version"] == "0.2.2"
    assert plugin["version"] == "0.2.2+codex.20260717"
    assert package_source.strip().endswith('__version__ = "0.2.2"')
    assert mcp["mcpServers"]["mercury-finance"]["args"][1].endswith("@v0.2.2")
    assert "docs/release/v0.2.2-test-waivers.json" in ci
    assert "ref: v0.2.2" in post_public
    assert "--tag v0.2.2 --release v0.2.2" in post_public
