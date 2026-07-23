from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE_CONTROL_WORKFLOWS = ROOT / "release-control" / "scaffold" / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
GITLEAKS_SHA256 = "9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c"
TRUFFLEHOG_SHA256 = "cddd1f602da61a130580883f4dd96b3d206efaf55a22068321cc11237fbc88cd"
PSYCOPG_VERSION = "3.2.10"
PSYCOPG_SHA256 = "ab5caf09a9ec42e314a21f5216dbcceac528e0e05142e42eea83a3b28b320ac3"
PSYCOPG_BINARY_SHA256 = "14bcbcac0cab465d88b2581e43ec01af4b01c9833e663f1352e05cb41be19e44"
SUPABASE_CLI_VERSION = "2.109.1"
SUPABASE_CLI_PACKAGE = f"supabase@{SUPABASE_CLI_VERSION}"
LOCAL_SUPABASE_SCRIPT = ROOT / "scripts" / "start_ephemeral_supabase.sh"
INSPECTOR_REQUIREMENTS = ROOT / "release-control" / "scaffold" / "requirements-inspector.txt"
RELEASE_CONTROL_REQUIRED_CONTEXT = "Mercury release-control CI / required"
ACTIVE_RELEASE_WORKFLOW = "release-v0.3.0.yml"
ACTIVE_RELEASE_VERSION = "0.3.0"
ACTIVE_RELEASE_TAG = "v0.3.0"
ACTIVE_TEST_WAIVERS = "docs/release/v0.3.0-test-waivers.json"
POST_PUBLIC_SETUP_NODE_SHA = "49933ea5288caeca8642d1e84afbd3f7d6820020"
POST_PUBLIC_NODE_VERSION = "22.22.0"
POST_PUBLIC_CODEX_PACKAGE = "@openai/codex@0.144.6"


def _workflow(name: str) -> dict[str, Any]:
    payload = yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def _release_control_workflow(name: str) -> dict[str, Any]:
    payload = yaml.load(
        (RELEASE_CONTROL_WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
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
    trufflehog_invocations = [
        line.strip()
        for line in command.splitlines()
        if re.search(r"\btrufflehog (?:git|filesystem)\b", line)
    ]
    assert trufflehog_invocations
    assert all("--no-update" in line for line in trufflehog_invocations)
    assert all("--no-verification" in line for line in trufflehog_invocations)
    assert all("--concurrency=1" in line for line in trufflehog_invocations)
    assert all("--json" in line for line in trufflehog_invocations)
    assert command.count('>"$TRUFFLEHOG_REPORT" 2>/dev/null') == len(trufflehog_invocations)
    assert "trufflehog-history.log" not in command
    assert "trufflehog-artifacts.log" not in command
    assert "scripts/verify_trufflehog_report.py" in command
    assert command.count("--config .gitleaks.toml") == (
        command.count("gitleaks git") + command.count("gitleaks dir")
    )
    assert '>"$' in command or '> "$' in command


def _assert_ephemeral_local_supabase(job: dict[str, Any]) -> None:
    serialized = json.dumps(job, sort_keys=True)
    command = _run_text(job)
    env = job.get("env")
    assert isinstance(env, dict)

    assert env["MERCURY_SUPABASE_VALIDATION_TEST"] == "1"
    assert env["MERCURY_SUPABASE_TEST_LOCAL_ONLY"] == "1"
    assert env["MERCURY_SUPABASE_TEST_ISOLATED"] == "0"
    assert "secrets.SUPABASE_" not in serialized
    assert "MERCURY_SUPABASE_TEST_GUARD" not in serialized

    assert "supabase/setup-cli@" not in serialized
    setup_steps = [
        step for step in job["steps"] if SUPABASE_CLI_PACKAGE in step.get("run", "")
    ]
    assert len(setup_steps) == 1
    setup_command = setup_steps[0]["run"]
    assert 'CLI_ROOT="$RUNNER_TEMP/mercury-supabase-cli"' in setup_command
    assert 'npm install --prefix "$CLI_ROOT"' in setup_command
    assert "--no-audit --no-fund --save-exact" in setup_command
    assert '>>"$GITHUB_PATH"' in setup_command
    assert f'test "$INSTALLED_VERSION" = "{SUPABASE_CLI_VERSION}"' in setup_command

    node_steps = [
        step
        for step in job["steps"]
        if step.get("uses") == f"actions/setup-node@{POST_PUBLIC_SETUP_NODE_SHA}"
    ]
    assert len(node_steps) == 1
    assert node_steps[0]["with"]["node-version"] == POST_PUBLIC_NODE_VERSION
    assert job["steps"].index(node_steps[0]) < job["steps"].index(setup_steps[0])

    assert "bash scripts/start_ephemeral_supabase.sh" in command
    assert "supabase stop --no-backup" in command

    cleanup_steps = [
        step for step in job["steps"] if "supabase stop --no-backup" in step.get("run", "")
    ]
    assert len(cleanup_steps) == 1
    assert cleanup_steps[0]["if"] == "always()"


def _assert_pinned_codex_cli_before_tests(job: dict[str, Any]) -> None:
    steps = job["steps"]
    setup_node_steps = [
        step
        for step in steps
        if step.get("uses") == f"actions/setup-node@{POST_PUBLIC_SETUP_NODE_SHA}"
    ]
    assert len(setup_node_steps) == 1
    assert setup_node_steps[0]["with"]["node-version"] == POST_PUBLIC_NODE_VERSION

    install_steps = [
        step for step in steps if POST_PUBLIC_CODEX_PACKAGE in step.get("run", "")
    ]
    assert len(install_steps) == 1
    assert "--no-audit --no-fund" in install_steps[0]["run"]

    version_steps = [step for step in steps if step.get("run", "").strip() == "codex --version"]
    assert len(version_steps) == 1
    test_index = next(
        index for index, step in enumerate(steps) if "uv run pytest" in step.get("run", "")
    )
    assert steps.index(setup_node_steps[0]) < steps.index(install_steps[0])
    assert steps.index(install_steps[0]) < steps.index(version_steps[0]) < test_index


def test_local_supabase_bootstrap_is_loopback_bound_and_exports_test_auth() -> None:
    command = LOCAL_SUPABASE_SCRIPT.read_text(encoding="utf-8")

    assert "supabase start" in command
    assert (
        "--exclude edge-runtime,imgproxy,logflare,mailpit,postgres-meta,"
        "realtime,storage-api,studio,supavisor,vector" in command
    )
    assert '>"$START_LOG" 2>&1' in command
    assert 'cat "$START_LOG"' not in command
    assert "supabase status -o env" in command
    assert "api.url=SUPABASE_URL" in command
    assert "auth.anon_key=SUPABASE_ANON_KEY" in command
    assert "auth.service_role_key=SUPABASE_SERVICE_ROLE_KEY" in command
    assert '2>"$STATUS_LOG"' in command
    assert 'cat "$STATUS_LOG"' not in command
    assert 'test "$SUPABASE_URL" = "http://127.0.0.1:54321"' in command
    assert "/auth/v1/signup" in command
    assert "SUPABASE_AUTHENTICATED_TEST_JWT" in command
    assert '>>"$GITHUB_ENV"' in command
    assert "SUPABASE_URL" not in command.split("supabase start", 1)[0]
    assert command.index("::add-mask::%s\\n") < command.index("/auth/v1/signup")


def test_local_supabase_config_is_disposable_and_seed_free() -> None:
    config = tomllib.loads((ROOT / "supabase" / "config.toml").read_text(encoding="utf-8"))

    assert config["project_id"] == "mercury-tools-ci"
    assert config["api"]["enabled"] is True
    assert config["api"]["port"] == 54321
    assert config["db"]["port"] == 54322
    assert config["db"]["major_version"] == 17
    assert config["db"]["migrations"]["enabled"] is True
    assert config["db"]["seed"]["enabled"] is False
    assert config["auth"]["enabled"] is True
    assert config["auth"]["email"]["enable_confirmations"] is False


def test_gitleaks_fixture_allowlists_are_exact_and_fail_closed() -> None:
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))

    assert set(config) == {"title", "extend", "rules"}
    assert config["extend"] == {"useDefault": True}
    rules = config["rules"]
    assert [rule["id"] for rule in rules] == [
        "generic-api-key",
        "aws-access-token",
        "jwt",
    ]

    expected_signatures = {
        ("generic-api-key", r"^tests/test_release_secret_scanner\.py$"): (
            r'^\s*credential\s*=\s*"[0-9a-f]{16}"\s*\*\s*8\s*$',
        ),
        ("generic-api-key", r"^tests/test_cloud_secret_removal\.py$"): (
            r'^\s*\{"id":\s*"event-1",\s*"summary":\s*\{"api_key":\s*'
            r'"live-api-key-123456789"\},\s*"metadata":\s*\{\}\}\s*$',
        ),
        ("generic-api-key", r"^tests/test_plugin_package\.py$"): (
            r'^\s*\("api_key",\s*"sk-live-[0-9a-f]{24}"\),\s*$',
            r'^\s*\("client_secret",\s*"v1\.[A-Za-z0-9]{20}"\),\s*$',
        ),
        ("generic-api-key", r"^tests/test_cloud_api\.py$"): (
            r'^\s*for secret in \("inbound-secret",\s*"inbound-secret2",'
            r'\s*"inbound-session"\):\s*$',
        ),
        ("aws-access-token", r"^tests/test_cloud_secret_removal\.py$"): (
            r'^\s*"aws=AKIA[0-9]{10}ABCDEF",\s*$',
            r'^\s*"AKIA[0-9]{10}ABCDEF",\s*$',
        ),
        ("jwt", r"^tests/test_cloud_secret_removal\.py$"): (
            r'^\s*"jwt=eyJhbGciOiJIUzI1NiJ9\.eyJzdWIiOiIxMjM0NTY3ODkwIn0\.signaturevalue1234",\s*$',
        ),
    }
    actual_signatures: dict[tuple[str, str], tuple[str, ...]] = {}
    for rule in rules:
        assert set(rule) == {"id", "allowlists"}
        for allowlist in rule["allowlists"]:
            assert set(allowlist) == {
                "description",
                "condition",
                "regexTarget",
                "paths",
                "regexes",
            }
            assert allowlist["condition"] == "AND"
            assert allowlist["regexTarget"] == "line"
            assert len(allowlist["paths"]) == 1
            assert all(".*" not in regex for regex in allowlist["regexes"])
            assert all(
                regex.startswith("^") and regex.endswith("$") for regex in allowlist["regexes"]
            )
            signature = (rule["id"], allowlist["paths"][0])
            assert signature not in actual_signatures
            actual_signatures[signature] = tuple(allowlist["regexes"])

    assert actual_signatures == expected_signatures


def test_ci_is_full_history_fail_closed_and_emits_exact_skip_junit() -> None:
    payload = _workflow("ci.yml")
    _assert_pinned_actions_and_no_bypasses(payload)
    test = payload["jobs"]["test"]
    assert test["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    checkout = test["steps"][0]
    assert checkout["with"]["fetch-depth"] == "0"
    command = _run_text(test)

    _assert_scanner_install_and_gates(command, test)
    assert "uv run ruff check ." in command
    assert "uv run pytest -q" in command
    assert "--ignore=tests/integration/test_flowaccount_sandbox_qualification.py" in command
    assert "--junitxml=release-evidence/pytest.xml" in command
    assert "scripts/verify_test_skips.py" in command
    assert ACTIVE_TEST_WAIVERS in command
    _assert_ephemeral_local_supabase(test)
    test_serialized = json.dumps(test, sort_keys=True)
    assert "secrets." not in test_serialized
    assert "FLOWACCOUNT_SANDBOX_CLIENT_ID" not in test_serialized
    assert "FLOWACCOUNT_SANDBOX_CLIENT_SECRET" not in test_serialized
    assert test["env"]["MERCURY_LIVE_FLOWACCOUNT_SANDBOX"] == "0"
    assert "Configure repository-local FlowAccount sandbox credentials" not in (test_serialized)
    assert "credentials test flowaccount" not in command
    assert "uv run mercury doctor --repo-root ." in command
    assert "scripts/validate_release_plugin.py --root ." in command
    assert "scripts/smoke_local_plugin.py" in command
    assert "uv build --wheel --sdist" in command
    assert "gitleaks dir" in command
    assert "trufflehog filesystem" in command
    _assert_pinned_codex_cli_before_tests(test)

    public = payload["jobs"]["public"]
    assert public["if"] == "github.event_name == 'pull_request' || github.ref != 'refs/heads/main'"
    assert "secrets." not in json.dumps(public)
    public_command = _run_text(public)
    assert "uv run pytest -q --ignore=tests/integration" in public_command
    assert "gitleaks git" in public_command
    assert "trufflehog git" in public_command
    _assert_pinned_codex_cli_before_tests(public)


def test_trusted_attestation_bootstraps_checksum_verified_inspector_dependencies() -> None:
    workflow = _release_control_workflow("attest-v0.2.1.yml")
    job = workflow["jobs"]["trusted-attestation"]
    steps = job["steps"]
    step_names = [step.get("name") for step in steps]
    bootstrap_index = step_names.index("Install checksum-verified inspector prerequisites")
    inspector_index = step_names.index("Run separately pinned trusted inspector")
    bootstrap = steps[bootstrap_index]
    command = bootstrap["run"]
    serialized = json.dumps(bootstrap, sort_keys=True)

    assert bootstrap_index < inspector_index
    assert "secrets." not in serialized
    assert (
        "https://github.com/gitleaks/gitleaks/releases/download/v8.24.3/"
        "gitleaks_8.24.3_linux_x64.tar.gz" in serialized
    )
    assert GITLEAKS_SHA256 in serialized
    assert (
        "https://github.com/trufflesecurity/trufflehog/releases/download/v3.88.32/"
        "trufflehog_3.88.32_linux_amd64.tar.gz" in serialized
    )
    assert TRUFFLEHOG_SHA256 in serialized
    assert command.count("sha256sum --check") == 2
    assert "tar --extract --gzip" in command
    assert "chmod 0555" in command
    assert 'INSPECTOR_GITLEAKS=$SCANNER_BIN/gitleaks' in command
    assert 'INSPECTOR_TRUFFLEHOG=$SCANNER_BIN/trufflehog' in command
    assert 'INSPECTOR_GIT=$GIT_BIN' in command
    assert 'INSPECTOR_PYTHON=$INSPECTOR_VENV/bin/python' in command

    assert 'test "$(uname -m)" = "x86_64"' in command
    assert "sys.version_info[:2]" in command
    assert '"$INSPECTOR_VENV/bin/python" -m pip install' in command
    for option in (
        "--disable-pip-version-check",
        "--isolated",
        "--no-cache-dir",
        "--no-deps",
        "--only-binary=:all:",
        "--require-hashes",
        "--requirement requirements-inspector.txt",
    ):
        assert option in command
    assert 'psycopg.__version__ == "3.2.10"' in command
    assert 'psycopg.pq.__impl__ == "binary"' in command

    requirements = INSPECTOR_REQUIREMENTS.read_text(encoding="utf-8")
    assert f"psycopg=={PSYCOPG_VERSION}" in requirements
    assert f"psycopg-binary=={PSYCOPG_VERSION}" in requirements
    assert PSYCOPG_SHA256 in requirements
    assert PSYCOPG_BINARY_SHA256 in requirements

    inspector_command = steps[inspector_index]["run"]
    assert '"$INSPECTOR_PYTHON" scripts/run_pinned_inspector.py' in inspector_command

    target_index = step_names.index("Bind policy target identity before protected input reads")
    candidate_index = step_names.index("Download candidate inputs as untrusted data only")
    target = steps[target_index]
    target_command = target["run"]
    assert target_index < candidate_index < bootstrap_index < inspector_index
    assert target["id"] == "target"
    assert target["env"] == {"CONFIGURED_TARGET_REPOSITORY": "${{ vars.TARGET_REPOSITORY }}"}
    assert ".reviewed_repository" in target_command
    assert ".reviewed_repository_id" in target_command
    assert "https://api.github.com/repos/$POLICY_TARGET_REPOSITORY" in target_command
    assert "$observed.id == $repository_id" in target_command
    assert "secrets." not in json.dumps(target, sort_keys=True)

    inspector_env = steps[inspector_index]["env"]
    assert "MERCURY_TOOLS_HTTP_BEARER_TOKEN" not in inspector_env
    assert "SUPABASE_SERVICE_ROLE_KEY" not in inspector_env


def test_release_control_ci_is_base_owned_and_checks_candidate_as_data_only() -> None:
    workflow = _release_control_workflow("ci.yml")
    _assert_pinned_actions_and_no_bypasses(workflow)

    assert workflow["name"] == "Mercury release-control CI"
    assert set(workflow["on"]) == {"pull_request_target", "push"}
    assert workflow["on"]["pull_request_target"] == {"branches": ["main"]}
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert workflow["permissions"] == {"contents": "read", "statuses": "write"}
    assert set(workflow["jobs"]) == {"reporter"}

    job = workflow["jobs"]["reporter"]
    assert job["name"] == "reporter"
    assert job["runs-on"] == "ubuntu-24.04"
    assert "environment" not in job
    serialized = json.dumps(workflow, sort_keys=True)
    for forbidden in (
        "secrets.",
        "vars.",
        "production-release",
        "RELEASE_CONTROL_PREFLIGHT_TOKEN",
        "MERCURY_TARGET_REPOSITORY",
        "FLOWACCOUNT_",
        "RENDER_",
        "SUPABASE_",
    ):
        assert forbidden not in serialized

    checkouts = [step for step in job["steps"] if "actions/checkout@" in step.get("uses", "")]
    assert len(checkouts) == 2
    assert checkouts[0]["with"] == {
        "ref": (
            "${{ github.event_name == 'pull_request_target' && "
            "github.event.pull_request.base.sha || github.sha }}"
        ),
        "fetch-depth": "1",
        "persist-credentials": "false",
        "path": "trusted",
    }
    assert checkouts[1]["with"] == {
        "ref": (
            "${{ github.event_name == 'pull_request_target' && "
            "github.event.pull_request.head.sha || github.sha }}"
        ),
        "fetch-depth": "1",
        "persist-credentials": "false",
        "path": "candidate",
    }

    command = _run_text(job)
    assert "python3 trusted/scripts/verify_candidate.py" in command
    assert "--trusted-root trusted" in command
    assert "--candidate-root candidate" in command
    assert "python3 candidate/" not in command
    assert "candidate/bin/" not in command
    assert "candidate/scripts/" not in command
    assert "repos/$GITHUB_REPOSITORY/statuses/$CANDIDATE_SHA" in command
    assert "steps.verify.outputs.outcome" in command
    assert RELEASE_CONTROL_REQUIRED_CONTEXT in command
    assert "pip install" not in command
    assert "continue-on-error" not in serialized


def test_release_control_remote_preflight_remains_a_dispatch_only_privileged_gate() -> None:
    ci = _release_control_workflow("ci.yml")
    attestation = _release_control_workflow("attest-v0.2.1.yml")

    assert "workflow_dispatch" not in ci["on"]
    assert set(attestation["on"]) == {"workflow_dispatch"}
    assert "pull_request" not in attestation["on"]
    assert "push" not in attestation["on"]

    remote_preflight = attestation["jobs"]["remote-preflight"]
    assert remote_preflight["environment"] == "production-release"
    serialized = json.dumps(remote_preflight, sort_keys=True)
    assert "secrets.RELEASE_CONTROL_PREFLIGHT_TOKEN" in serialized
    assert "scripts/verify_remote_preflight.py" in _run_text(remote_preflight)

    ci_serialized = json.dumps(ci, sort_keys=True)
    assert "environment" not in ci["jobs"]["reporter"]
    assert "secrets." not in ci_serialized
    assert "RELEASE_CONTROL_PREFLIGHT_TOKEN" not in ci_serialized


def test_release_is_manual_sha_bound_and_handoff_depends_on_every_gate() -> None:
    payload = _workflow(ACTIVE_RELEASE_WORKFLOW)
    _assert_pinned_actions_and_no_bypasses(payload)
    dispatch = payload["on"]["workflow_dispatch"]
    required_inputs = {
        "reviewed_main_sha",
        "release_control_commit",
        "release_control_run_id",
        "release_control_run_attempt",
        "release_control_attestation_artifact_id",
        "release_control_attestation_artifact_digest",
        "release_control_attestation_payload_sha256",
        "release_control_attestation_gzip_b64",
        "release_control_repository_id",
    }
    assert set(dispatch["inputs"]) == required_inputs
    assert all(item["required"] == "true" for item in dispatch["inputs"].values())
    assert all(item["type"] == "string" for item in dispatch["inputs"].values())

    jobs = payload["jobs"]
    ordered = (
        "validate-reviewed-sha",
        "quality-security",
        "supabase-migration",
        "peak-contract",
        "relayed-release-control",
        "build-artifacts",
        "release-ready",
    )
    assert tuple(jobs) == ordered
    assert jobs["quality-security"]["needs"] == "validate-reviewed-sha"
    assert jobs["supabase-migration"]["needs"] == "quality-security"
    assert jobs["peak-contract"]["needs"] == "supabase-migration"
    assert jobs["relayed-release-control"]["needs"] == "peak-contract"
    assert jobs["build-artifacts"]["needs"] == "relayed-release-control"
    assert jobs["release-ready"]["needs"] == [
        "relayed-release-control",
        "build-artifacts",
    ]

    trusted_outputs = jobs["relayed-release-control"]["outputs"]
    assert trusted_outputs["producer_repository"] == ("${{ steps.producer.outputs.repository }}")
    assert trusted_outputs["producer_sha"] == "${{ steps.producer.outputs.sha }}"

    job_env = jobs["build-artifacts"]["env"]
    assert job_env["MERCURY_RELEASE_CONTROL_REPOSITORY_ID"] == (
        "${{ inputs.release_control_repository_id }}"
    )
    assert job_env["MERCURY_RELEASE_CONTROL_SHA"] == (
        "${{ inputs.release_control_commit }}"
    )
    assert job_env["MERCURY_REVIEWED_REPOSITORY_ID"] == (
        "${{ github.repository_id }}"
    )
    assert "MERCURY_RELEASE_STAGING_REF" not in job_env
    assert "MERCURY_RELEASE_STAGING_REPOSITORY" not in job_env

    validate = _run_text(jobs["validate-reviewed-sha"])
    assert "origin/main" in validate
    assert "REVIEWED_MAIN_SHA" in validate
    assert "existing annotated release tag" in validate
    assert 'test "$RELEASE_CONTROL_PIN" !=' in validate
    assert FULL_SHA.fullmatch(payload["env"]["RELEASE_CONTROL_PIN"])
    assert payload["env"]["RELEASE_CONTROL_PIN"] != "0" * 40
    assert "--force" not in validate

    quality = _run_text(jobs["quality-security"])
    _assert_scanner_install_and_gates(quality, jobs["quality-security"])
    _assert_ephemeral_local_supabase(jobs["quality-security"])
    assert "--junitxml=release-evidence/pytest.xml" in quality
    assert "scripts/verify_test_skips.py" in quality
    assert "scripts/validate_release_plugin.py --root ." in quality
    assert ACTIVE_TEST_WAIVERS in quality
    assert "test_flowaccount_sandbox_qualification.py" in quality
    assert "FLOWACCOUNT_SANDBOX_CLIENT_ID" not in json.dumps(jobs["quality-security"])
    _assert_pinned_codex_cli_before_tests(jobs["quality-security"])

    _assert_ephemeral_local_supabase(jobs["supabase-migration"])
    migration = _run_text(jobs["supabase-migration"])
    assert "test_validation_migration.py" in migration
    assert "test_validation_pg17_hotfix_migration.py" in migration
    assert "test_supabase_validation_knowledge.py" in migration
    assert "test_connector_neutral_profile_migration.py" in migration

    peak = _run_text(jobs["peak-contract"])
    assert "catalog validate" in peak
    assert "total" in peak and "64" in peak and "http_attempts" in peak
    assert "github.run_attempt" in json.dumps(jobs["peak-contract"])

    trusted = jobs["relayed-release-control"]
    trusted_run = _run_text(trusted)
    assert "actions/checkout" in json.dumps(trusted)
    assert "secrets." not in json.dumps(trusted, sort_keys=True)
    assert "python -m mercury_tools.release.relay" in trusted_run
    assert "release_control_attestation_gzip_b64" in json.dumps(trusted, sort_keys=True)
    assert "release_control_attestation_b64" not in json.dumps(trusted, sort_keys=True)
    assert "RELEASE_CONTROL_PIN" in trusted_run
    assert "trusted-hosted-attestation.json" in trusted_run
    assert "untrusted relay" in json.dumps(trusted).lower()

    platform = json.loads(
        (ROOT / "release-toolchain" / "platform.json").read_text(encoding="utf-8")
    )
    policy = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "mercury"
    ]["release-build"]
    descriptor_policy = policy["platform"]
    assert descriptor_policy["path"] == "release-toolchain/platform.json"
    assert (
        descriptor_policy["sha256"]
        == __import__("hashlib").sha256((ROOT / descriptor_policy["path"]).read_bytes()).hexdigest()
    )
    build = _run_text(jobs["build-artifacts"])
    assert "release-toolchain/platform.json" in build
    assert build.count(platform["image"]) == 1
    assert 'docker run --rm --platform "$RELEASE_PLATFORM"' in build
    assert '"$RELEASE_IMAGE" sh -ceu' in build
    assert f"scripts/build_release_artifacts.py --version {ACTIVE_RELEASE_VERSION}" in build
    assert f"scripts/verify_release.py --version {ACTIVE_RELEASE_VERSION}" in build
    assert "--offline --frozen --no-dev" in build
    assert "mercury-build-output" in build

    handoff = _run_text(jobs["release-ready"])
    assert "mercury_tools.release.handoff_v3" in handoff
    assert "schema_version == 3" in handoff
    assert "release_bundle" in handoff
    assert "github.run_attempt" in json.dumps(jobs["release-ready"])
    serialized = json.dumps(payload, sort_keys=True)
    assert "git tag -a" not in serialized
    assert "git push origin" not in serialized
    assert "gh release create" not in serialized
    assert "verify_render_release.py" not in serialized
    assert "smoke_tagged_marketplace.py" not in serialized


def test_release_dependency_prefetch_is_pinned_secretless_and_container_only() -> None:
    payload = _workflow(ACTIVE_RELEASE_WORKFLOW)
    jobs = payload["jobs"]
    platform = json.loads(
        (ROOT / "release-toolchain" / "platform.json").read_text(encoding="utf-8")
    )
    release_policy = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["mercury"]["release-build"]
    uv_policy = release_policy["uv"]
    expected_lock_sha256 = release_policy["lock_sha256"]
    expected_uv_sha256 = uv_policy["sha256"]

    assert re.fullmatch(r"docker\.io/library/python@sha256:[0-9a-f]{64}", platform["image"])
    assert uv_policy["version"] == "0.11.9"
    assert (
        __import__("hashlib").sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        == expected_lock_sha256
    )
    assert (
        __import__("hashlib").sha256((ROOT / uv_policy["path"]).read_bytes()).hexdigest()
        == expected_uv_sha256
    )

    prefetch_locations: list[tuple[str, str]] = []
    for job_name, job in jobs.items():
        for step in job.get("steps", []):
            command = step.get("run", "")
            flattened = " ".join(command.replace("\\\n", " ").split())
            assert "uv sync --frozen --no-dev --no-install-project" not in flattened
            if "release-toolchain/uv-linux-x86_64 sync" in flattened:
                prefetch_locations.append((job_name, step.get("name", "")))

    assert prefetch_locations == [
        ("build-artifacts", "Prepare immutable offline dependency cache"),
    ]

    for job_name, _step_name in prefetch_locations:
        matching_steps = [
            step
            for step in jobs[job_name]["steps"]
            if step.get("name") == "Prepare immutable offline dependency cache"
        ]
        assert len(matching_steps) == 1
        step = matching_steps[0]
        command = step["run"]
        flattened = " ".join(command.replace("\\\n", " ").split())
        serialized = json.dumps(step, sort_keys=True)

        assert "secrets." not in serialized
        assert "GH_TOKEN" not in serialized
        assert "--env RENDER" not in command
        assert "--env SUPABASE" not in command
        assert "--env MERCURY_" not in command
        assert re.search(r"(?<![\w./-])uv sync\b", flattened) is None
        assert "docker run --rm --platform linux/amd64" in flattened
        assert "--read-only --cap-drop ALL" in flattened
        assert "--security-opt no-new-privileges:true" in flattened
        assert '--volume "$GITHUB_WORKSPACE:/workspace:ro"' in command
        assert platform["image"] in command
        assert command.count("sha256sum --check") == 2
        assert re.search(
            rf"{expected_uv_sha256}\s+\\\n\s+release-toolchain/uv-linux-x86_64 "
            r"\| sha256sum --check",
            command,
        )
        assert re.search(
            rf"{expected_lock_sha256}\s+\\\n\s+uv\.lock \| sha256sum --check",
            command,
        )
        sync_command = (
            "release-toolchain/uv-linux-x86_64 sync "
            "--frozen --no-dev --no-install-project --no-build"
        )
        assert sync_command in flattened
        sync_index = command.index("release-toolchain/uv-linux-x86_64 sync")
        assert command.index("sh -ceu '") < sync_index < command.rindex("\n  '\n")


def test_release_relay_artifact_name_matches_control_publisher_contract() -> None:
    release = _workflow(ACTIVE_RELEASE_WORKFLOW)
    relay_steps = release["jobs"]["relayed-release-control"]["steps"]
    relay_upload = next(
        step
        for step in relay_steps
        if step.get("name") == "Retain exact untrusted relay for final publisher verification"
    )

    assert relay_upload["with"]["name"] == (
        "mercury-v0.3.0-trusted-attestation-"
        "${{ github.run_id }}-attempt-${{ github.run_attempt }}"
    )


def test_mercury_release_jobs_never_receive_production_provider_credentials() -> None:
    payload = _workflow(ACTIVE_RELEASE_WORKFLOW)
    jobs = payload["jobs"]
    candidate_container_jobs = {
        name for name, job in jobs.items() if "docker run" in _run_text(job)
    }

    assert candidate_container_jobs == {"build-artifacts"}
    for name in candidate_container_jobs:
        job = jobs[name]
        serialized = json.dumps(job, sort_keys=True)
        command = _run_text(job)
        assert "secrets." not in serialized
        assert "MERCURY_TRUSTED_HOSTED_ATTESTATION" in serialized
        assert "trusted-hosted-attestation.json" in serialized
        assert "--env GH_TOKEN" not in command
        assert "--env RENDER" not in command
        assert "--env SUPABASE" not in command
        assert "--env MERCURY_PUBLIC_MCP" not in command

    trusted = jobs["relayed-release-control"]
    trusted_serialized = json.dumps(trusted, sort_keys=True)
    assert trusted["needs"] == "peak-contract"
    assert "environment" not in trusted
    assert "actions/checkout" in trusted_serialized
    assert "secrets." not in trusted_serialized
    assert "release_control_attestation_gzip_b64" in trusted_serialized
    assert "release_control_attestation_b64" not in trusted_serialized
    assert "python -m mercury_tools.release.relay" in _run_text(trusted)
    assert 'test "${#ATTESTATION_GZIP_B64}" -le 60000' in _run_text(trusted)
    assert "--max-compressed-bytes 45000" in _run_text(trusted)
    assert "--max-output-bytes 1048576" in _run_text(trusted)
    assert jobs["build-artifacts"]["needs"] == "relayed-release-control"


def test_release_relay_gzip_transport_uses_bounded_verified_decoder() -> None:
    payload = _workflow(ACTIVE_RELEASE_WORKFLOW)
    dispatch_inputs = payload["on"]["workflow_dispatch"]["inputs"]
    relay_steps = payload["jobs"]["relayed-release-control"]["steps"]
    decode = next(
        step
        for step in relay_steps
        if step.get("name") == "Decode untrusted sanitized attestation gzip transport"
    )
    verify = next(
        step
        for step in relay_steps
        if step.get("name") == "Verify exact sanitized relay payload"
    )

    assert "release_control_attestation_gzip_b64" in dispatch_inputs
    assert "release_control_attestation_b64" not in dispatch_inputs
    assert decode["env"]["ATTESTATION_GZIP_B64"] == (
        "${{ inputs.release_control_attestation_gzip_b64 }}"
    )

    command = decode["run"]
    assert 'test "${#ATTESTATION_GZIP_B64}" -le 60000' in command
    assert "python -m mercury_tools.release.relay" in command
    assert '--expected-sha256 "$EXPECTED_PAYLOAD_SHA256"' in command
    assert '--max-encoded-chars 60000' in command
    assert '--max-compressed-bytes 45000' in command
    assert '--max-output-bytes 1048576' in command
    assert 'trusted-hosted-attestation.json.gz' not in command

    verify_command = verify["run"]
    assert 'trusted-hosted-attestation.json' in verify_command
    assert 'find "$RUNNER_TEMP/relayed-release-control" -type f' in verify_command
    assert ' = "1"' in verify_command
    assert "EXPECTED_PAYLOAD_SHA256" in verify_command


def test_release_relay_removes_only_checkout_gc_auto_before_candidate_inspection() -> None:
    payload = _workflow(ACTIVE_RELEASE_WORKFLOW)
    relay_steps = payload["jobs"]["relayed-release-control"]["steps"]
    verify = next(
        step
        for step in relay_steps
        if step.get("name") == "Verify exact sanitized relay payload"
    )
    command = verify["run"]

    read_gc_auto = 'GC_AUTO_VALUES="$(git config --local --get-all gc.auto || true)"'
    require_checkout_value = 'test "$GC_AUTO_VALUES" = "0"'
    remove_checkout_value = "git config --local --unset-all gc.auto"
    inspect_candidate = "load_release_candidate("

    assert read_gc_auto in command
    assert require_checkout_value in command
    assert remove_checkout_value in command
    assert command.index(read_gc_auto) < command.index(require_checkout_value)
    assert command.index(require_checkout_value) < command.index(remove_checkout_value)
    assert command.index(remove_checkout_value) < command.index(inspect_candidate)
    assert "git config --local --remove-section" not in command


def test_release_control_transport_and_candidate_containers_are_fail_closed() -> None:
    payload = _workflow(ACTIVE_RELEASE_WORKFLOW)
    serialized = json.dumps(payload, sort_keys=True)
    jobs = payload["jobs"]

    for forbidden in (
        "FLOWACCOUNT_SANDBOX_CLIENT_ID",
        "FLOWACCOUNT_SANDBOX_CLIENT_SECRET",
        "MERCURY_MARKETPLACE_SNAPSHOT_URL",
        "MERCURY_PUBLIC_MCP_TOKEN",
        "MERCURY_PUBLIC_MCP_URL",
        "MERCURY_TOOLS_HTTP_BEARER_TOKEN",
        "RENDER_API_TOKEN",
        "RENDER_API_KEY",
        "RENDER_SERVICE_ID",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_URL",
        "MERCURY_RELEASE_CONTROL_READ_TOKEN",
    ):
        assert forbidden not in serialized

    trusted = jobs["relayed-release-control"]
    trusted_text = json.dumps(trusted, sort_keys=True)
    assert "actions/checkout" in trusted_text
    assert "python -m mercury_tools.release.relay" in _run_text(trusted)
    assert "release_control_attestation_artifact_id" in trusted_text
    assert "release_control_attestation_artifact_digest" in trusted_text
    assert "release_control_attestation_payload_sha256" in trusted_text
    assert "release_control_run_id" in trusted_text
    assert "release_control_run_attempt" in trusted_text
    assert "github.run_attempt" in trusted_text
    assert "artifact-id" in trusted_text and "artifact-digest" in trusted_text

    candidate_jobs = {name: job for name, job in jobs.items() if "docker run" in _run_text(job)}
    assert set(candidate_jobs) == {"build-artifacts"}
    for job in candidate_jobs.values():
        command = _run_text(job)
        assert "--network none" in command
        assert "--read-only" in command
        assert "--cap-drop ALL" in command
        assert "--security-opt no-new-privileges" in command
        assert ':ro"' in command
        assert "--tmpfs /tmp:" in command
        assert "mount -t tmpfs" in command
        assert "size=2147483648" in command
        assert "validate_candidate_output.py" in command
        assert "umount" in command
        assert "secrets." not in json.dumps(job, sort_keys=True)
        assert "--env GH_TOKEN" not in command
        assert "--env RENDER" not in command
        assert "--env SUPABASE" not in command
        assert "--env MERCURY_PUBLIC_MCP" not in command


def test_release_artifact_handoffs_use_attempt_names_and_exact_ids() -> None:
    jobs = _workflow(ACTIVE_RELEASE_WORKFLOW)["jobs"]
    serialized = json.dumps(jobs, sort_keys=True)

    assert "github.run_attempt" in serialized
    for job_name in ("relayed-release-control", "build-artifacts", "release-ready"):
        job = jobs[job_name]
        uploads = [step for step in job["steps"] if "upload-artifact@" in step.get("uses", "")]
        assert uploads
        assert "outputs" in job
        for step in uploads:
            assert "github.run_attempt" in step["with"]["name"]
            assert step.get("id")

    for job_name in ("build-artifacts", "release-ready"):
        downloads = [
            step for step in jobs[job_name]["steps"] if "download-artifact@" in step.get("uses", "")
        ]
        assert downloads
        assert all("artifact-ids" in step["with"] for step in downloads)
        assert all("name" not in step["with"] for step in downloads)


def test_artifact_id_downloads_extract_directly_into_declared_paths() -> None:
    workflows = (
        _workflow(ACTIVE_RELEASE_WORKFLOW),
        _release_control_workflow("attest-v0.2.1.yml"),
        _release_control_workflow("publish-v0.2.1.yml"),
    )
    downloads = [
        step
        for workflow in workflows
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "download-artifact@" in step.get("uses", "")
        and "artifact-ids" in step.get("with", {})
    ]

    assert len(downloads) == 7
    assert all(step["with"].get("merge-multiple") == "true" for step in downloads)


def test_trusted_publisher_derives_assets_without_host_candidate_execution() -> None:
    workflow = _release_control_workflow("publish-v0.2.1.yml")
    policy = json.loads(
        (ROOT / "release-control" / "policy-v0.2.1.json").read_text(encoding="utf-8")
    )
    publish = workflow["jobs"]["publish"]
    steps = publish["steps"]
    names = [step.get("name") for step in steps]

    bind_index = names.index("Bind publication target to reviewed policy")
    fetch_index = names.index("Independently fetch exact reviewed Git source")
    prepare_index = names.index("Materialize canonical source from reviewed Git objects")
    acquire_index = names.index("Acquire pinned build dependencies without candidate input")
    rebuild_index = names.index("Reproduce wheel and sdist in networkless isolation")
    compare_index = names.index("Compare candidate assets to independent reproduction")
    publish_index = names.index("Publish exact immutable release")
    assert (
        bind_index
        < fetch_index
        < prepare_index
        < acquire_index
        < rebuild_index
        < compare_index
        < publish_index
    )

    binding = steps[bind_index]
    binding_command = binding["run"]
    assert binding["id"] == "target"
    assert binding["env"] == {
        "CONFIGURED_TARGET_REPOSITORY": "${{ vars.TARGET_REPOSITORY }}",
        "GH_TOKEN": "${{ secrets.MERCURY_TARGET_REPOSITORY_READ_TOKEN }}",
    }
    assert policy["reviewed_repository"] == "natthaphonchop2-creator/mercury-tools"
    assert policy["reviewed_repository_id"] == 1290137723
    assert '.["reviewed_repository"]' in binding_command
    assert '.["reviewed_repository_id"]' in binding_command
    assert 'gh api "repos/$POLICY_TARGET_REPOSITORY"' in binding_command
    assert ".full_name == $repository" in binding_command
    assert ".id == $repository_id" in binding_command
    assert 'test "$CONFIGURED_TARGET_REPOSITORY" = "$POLICY_TARGET_REPOSITORY"' in (
        binding_command
    )
    assert 'repository=$POLICY_TARGET_REPOSITORY' in binding_command
    assert 'repository_id=$POLICY_TARGET_REPOSITORY_ID' in binding_command

    serialized = json.dumps(workflow, sort_keys=True)
    assert serialized.count("vars.TARGET_REPOSITORY") == 1
    assert "${{ steps.target.outputs.repository }}" in serialized
    for step in steps[:publish_index]:
        assert "secrets.MERCURY_TARGET_REPOSITORY_TOKEN" not in json.dumps(
            step, sort_keys=True
        )
    assert steps[publish_index]["env"]["GH_TOKEN"] == (
        "${{ secrets.MERCURY_TARGET_REPOSITORY_TOKEN }}"
    )

    fetch = steps[fetch_index]
    assert fetch["uses"] == "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
    assert fetch["with"] == {
        "repository": "${{ steps.target.outputs.repository }}",
        "ref": "${{ inputs.reviewed_commit_sha }}",
        "token": "${{ secrets.MERCURY_TARGET_REPOSITORY_READ_TOKEN }}",
        "fetch-depth": "1",
        "persist-credentials": "false",
        "path": "trusted-reviewed-source",
    }

    acquire = steps[acquire_index]
    acquire_command = acquire["run"]
    acquire_serialized = json.dumps(acquire, sort_keys=True)
    assert "secrets." not in acquire_serialized
    assert "trusted-reviewed-source" not in acquire_serialized
    assert "pyproject.toml" not in acquire_command
    assert "uv.lock" not in acquire_command
    assert "uv sync" not in acquire_command
    assert "uv build" not in acquire_command
    assert "docker.io/library/python@sha256:" in acquire_command
    assert "ghcr.io/astral-sh/uv@sha256:" in acquire_command
    assert "files.pythonhosted.org" in acquire_command
    assert acquire_command.count("sha256sum --check") >= 3
    assert "--read-only" in acquire_command
    assert "--cap-drop ALL" in acquire_command
    assert "--security-opt no-new-privileges:true" in acquire_command

    rebuild = steps[rebuild_index]
    rebuild_command = rebuild["run"]
    rebuild_serialized = json.dumps(rebuild, sort_keys=True)
    assert "secrets." not in rebuild_serialized
    assert "--network none" in rebuild_command
    assert "--read-only" in rebuild_command
    assert "--cap-drop ALL" in rebuild_command
    assert "--security-opt no-new-privileges:true" in rebuild_command
    assert 'trusted-reviewed-source"' not in rebuild_command
    assert ':/trusted-source:ro"' in rebuild_command
    assert ':/trusted-dependencies:ro"' in rebuild_command
    assert "release-toolchain/uv-linux-x86_64" not in rebuild_command
    assert "/trusted-dependencies/uv build" in rebuild_command
    assert "scripts/build_release_artifacts.py" not in rebuild_command

    compare_command = steps[compare_index]["run"]
    assert "scripts/verify_release_assets.py verify" in compare_command
    assert "--source-repository" in compare_command
    assert "--canonical-source" in compare_command
    assert "--reproduced-distributions" in compare_command
    assert "--artifact-root" in compare_command


def test_post_public_workflow_is_anonymous_and_exact_release_bound() -> None:
    payload = _workflow("post-public-verify.yml")
    _assert_pinned_actions_and_no_bypasses(payload)
    assert "workflow_dispatch" in payload["on"]
    assert payload["permissions"]["contents"] == "read"
    command = _run_text(payload["jobs"]["verify-public"])
    assert "scripts/verify_public_release.py" in command
    assert f"--tag {ACTIVE_RELEASE_TAG}" in command
    assert f"--release {ACTIVE_RELEASE_TAG}" in command
    assert "--expected-hosted-tools 24" in command
    assert "GH_TOKEN" not in json.dumps(payload["jobs"]["verify-public"].get("env", {}))


def test_post_public_workflow_provisions_pinned_codex_before_public_verification() -> None:
    payload = _workflow("post-public-verify.yml")
    job = payload["jobs"]["verify-public"]
    steps = job["steps"]

    setup_node_steps = [
        step
        for step in steps
        if step.get("uses") == f"actions/setup-node@{POST_PUBLIC_SETUP_NODE_SHA}"
    ]
    assert len(setup_node_steps) == 1
    setup_node = setup_node_steps[0]
    assert setup_node["with"]["node-version"] == POST_PUBLIC_NODE_VERSION

    install_steps = [
        step
        for step in steps
        if step.get("run", "").strip() == f"npm install -g {POST_PUBLIC_CODEX_PACKAGE}"
    ]
    assert len(install_steps) == 1

    version_steps = [step for step in steps if step.get("run", "").strip() == "codex --version"]
    assert len(version_steps) == 1

    verifier_index = next(
        index
        for index, step in enumerate(steps)
        if "scripts/verify_public_release.py" in step.get("run", "")
    )
    assert steps.index(setup_node) < steps.index(install_steps[0]) < steps.index(version_steps[0])
    assert steps.index(version_steps[0]) < verifier_index
