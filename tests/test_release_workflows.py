from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
GITLEAKS_SHA256 = "9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c"
TRUFFLEHOG_SHA256 = "cddd1f602da61a130580883f4dd96b3d206efaf55a22068321cc11237fbc88cd"


def _workflow(name: str) -> dict[str, Any]:
    payload = yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def _run_text(job: dict[str, Any]) -> str:
    steps = job.get("steps")
    assert isinstance(steps, list)
    return "\n".join(step.get("run", "") for step in steps if isinstance(step, dict))


def _assert_pinned_actions_and_no_bypasses(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    assert "continue-on-error" not in serialized
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    for job in jobs.values():
        assert isinstance(job, dict)
        for step in job.get("steps", []):
            assert isinstance(step, dict)
            action = step.get("uses")
            if action is not None:
                assert "@" in action
                assert FULL_SHA.fullmatch(action.rsplit("@", 1)[1])
            command = step.get("run", "")
            assert "secrets." not in command


def _assert_scanner_install_and_gates(command: str, job: dict[str, Any]) -> None:
    serialized = json.dumps(job, sort_keys=True)
    assert "gitleaks_8.24.3_linux_x64.tar.gz" in serialized
    assert GITLEAKS_SHA256 in serialized
    assert "trufflehog_3.88.32_linux_amd64.tar.gz" in serialized
    assert TRUFFLEHOG_SHA256 in serialized
    assert "sha256sum --check" in command
    assert "gitleaks version" in command
    assert "trufflehog --version" in command
    assert "gitleaks git" in command
    assert "trufflehog git" in command
    assert ">\"$" in command or "> \"$" in command


def test_ci_is_full_history_fail_closed_and_emits_exact_skip_junit() -> None:
    payload = _workflow("ci.yml")
    _assert_pinned_actions_and_no_bypasses(payload)
    test = payload["jobs"]["test"]
    checkout = test["steps"][0]
    assert checkout["with"]["fetch-depth"] == "0"
    command = _run_text(test)

    _assert_scanner_install_and_gates(command, test)
    assert "uv run ruff check ." in command
    assert "uv run pytest -q --junitxml=release-evidence/pytest.xml" in command
    assert "scripts/verify_test_skips.py" in command
    assert "docs/release/v0.2.1-test-waivers.json" in command
    assert "MERCURY_SUPABASE_VALIDATION_TEST" in json.dumps(test["env"])
    assert "SUPABASE_SERVICE_ROLE_KEY" in json.dumps(test["env"])
    assert "test -n \"$MERCURY_SUPABASE_TEST_GUARD\"" in command
    assert "uv run mercury doctor --repo-root ." in command
    assert "scripts/validate_release_plugin.py --root ." in command
    assert "scripts/smoke_local_plugin.py" in command
    assert "uv build --wheel --sdist" in command
    assert "gitleaks dir" in command
    assert "trufflehog filesystem" in command


def test_release_is_manual_sha_bound_and_publication_depends_on_every_gate() -> None:
    payload = _workflow("release.yml")
    _assert_pinned_actions_and_no_bypasses(payload)
    dispatch = payload["on"]["workflow_dispatch"]
    reviewed = dispatch["inputs"]["reviewed_main_sha"]
    assert reviewed["required"] == "true"
    assert reviewed["type"] == "string"
    staging_repo = dispatch["inputs"]["staging_repo"]
    staging_ref = dispatch["inputs"]["staging_ref"]
    assert staging_repo["required"] == "true"
    assert staging_repo["type"] == "string"
    assert staging_ref["required"] == "true"
    assert staging_ref["type"] == "string"

    jobs = payload["jobs"]
    ordered = (
        "validate-reviewed-sha",
        "quality-security",
        "supabase-migration",
        "flowaccount-coverage",
        "peak-contract",
        "build-artifacts",
        "public-staging",
        "tagged-marketplace",
        "render-release",
        "publish-assets",
    )
    assert tuple(jobs) == ordered
    for previous, current in zip(ordered, ordered[1:], strict=False):
        assert jobs[current]["needs"] == previous

    validate = _run_text(jobs["validate-reviewed-sha"])
    assert "origin/main" in validate
    assert "REVIEWED_MAIN_SHA" in validate
    assert "refs/tags/v0.2.1" not in validate
    assert "--force" not in validate

    quality = _run_text(jobs["quality-security"])
    _assert_scanner_install_and_gates(quality, jobs["quality-security"])
    assert "--junitxml=release-evidence/pytest.xml" in quality
    assert "scripts/verify_test_skips.py" in quality
    assert "scripts/validate_release_plugin.py --root ." in quality

    assert "test_validation_migration.py" in _run_text(jobs["supabase-migration"])
    assert "test_supabase_validation_knowledge.py" in _run_text(
        jobs["supabase-migration"]
    )
    flowaccount = _run_text(jobs["flowaccount-coverage"])
    assert "MERCURY_LIVE_FLOWACCOUNT_SANDBOX" in json.dumps(
        jobs["flowaccount-coverage"]
    )
    assert "catalog qualify" in flowaccount
    assert "total" in flowaccount and "190" in flowaccount
    peak = _run_text(jobs["peak-contract"])
    assert "catalog validate" in peak
    assert "total" in peak and "64" in peak and "http_attempts" in peak

    platform = json.loads(
        (ROOT / "release-toolchain" / "platform.json").read_text(encoding="utf-8")
    )
    policy = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["mercury"]["release-build"]
    descriptor_policy = policy["platform"]
    assert descriptor_policy["path"] == "release-toolchain/platform.json"
    assert descriptor_policy["sha256"] == __import__("hashlib").sha256(
        (ROOT / descriptor_policy["path"]).read_bytes()
    ).hexdigest()
    build = _run_text(jobs["build-artifacts"])
    assert "release-toolchain/platform.json" in build
    assert platform["image"] not in build
    assert 'docker run --rm --platform "$RELEASE_PLATFORM"' in build
    assert '"$RELEASE_IMAGE" sh -ceu' in build
    assert "install -d -m 700" in build
    assert "scripts/build_release_artifacts.py --version 0.2.1" in build
    assert "scripts/verify_release.py --version 0.2.1" in build
    assert "release-evidence/private" in build

    assert "scripts/build_public_staging.py" in _run_text(jobs["public-staging"])
    marketplace = _run_text(jobs["tagged-marketplace"])
    assert "scripts/smoke_tagged_marketplace.py" in marketplace
    assert '"$STAGING_REPO"' in marketplace
    assert '"$STAGING_REF"' in marketplace
    assert "history-free staging" in marketplace
    assert "refs/tags/$STAGING_REF" in marketplace
    assert "refs/tags/v0.2.1" not in marketplace
    render = _run_text(jobs["render-release"])
    assert "scripts/verify_render_release.py" in render
    assert '--commit "$REVIEWED_MAIN_SHA"' in render
    publish = _run_text(jobs["publish-assets"])
    assert "git tag -a \"$RELEASE_TAG\" \"$REVIEWED_MAIN_SHA\"" in publish
    assert "git push origin \"refs/tags/$RELEASE_TAG\"" in publish
    assert "--force" not in publish
    assert "gh release create" in publish
    assert "--verify-tag" in publish
    assert publish.index("git tag -a") < publish.index("gh release create")


def test_post_public_workflow_is_anonymous_and_exact_release_bound() -> None:
    payload = _workflow("post-public-verify.yml")
    _assert_pinned_actions_and_no_bypasses(payload)
    assert "workflow_dispatch" in payload["on"]
    assert payload["permissions"]["contents"] == "read"
    command = _run_text(payload["jobs"]["verify-public"])
    assert "scripts/verify_public_release.py" in command
    assert "--tag v0.2.1" in command
    assert "--release v0.2.1" in command
    assert "--expected-tools 19" in command
    assert "GH_TOKEN" not in json.dumps(payload["jobs"]["verify-public"].get("env", {}))
