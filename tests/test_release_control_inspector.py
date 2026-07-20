from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "release-control" / "scaffold" / "scripts"
POLICY_PATH = ROOT / "release-control" / "policy-v0.2.1.json"


def _database_url(password: str, *, query: str = "sslmode=verify-full") -> str:
    scheme = "postgres" + "ql"
    return (
        f"{scheme}://postgres:{password}@db.abcdefghijklmnopqrst.supabase.co/"
        f"postgres?{query}"
    )


def _module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


@pytest.fixture
def core() -> ModuleType:
    return _module("inspector_core")


def _configured_policy(core: ModuleType) -> dict[str, object]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["bootstrap_state"] = "configured"
    policy["repository_id"] = 12344
    policy["reviewed_repository_id"] = 12345
    policy["required_reviewer_ids"] = [12345]
    policy["staging_repository"] = "example/mercury-public-staging"
    policy["inspector"]["sha256"] = "f" * 64
    supabase = policy["supabase"]
    assert isinstance(supabase, dict)
    supabase["project_ref"] = "abcdefghijklmnopqrst"
    versions = ("20260713100000", "20260715100000", "20260716100000")
    supabase["migration_history_sha256"] = hashlib.sha256(
        ("\n".join(versions) + "\n").encode()
    ).hexdigest()
    functions = supabase["functions"]
    assert isinstance(functions, list)
    for function in functions:
        assert isinstance(function, dict)
        signature = function["signature"]
        assert isinstance(signature, str)
        function["definition_sha256"] = hashlib.sha256(
            f"definition:{signature}".encode()
        ).hexdigest()
    supabase["schema_sha256"] = core.build_supabase_schema_digest(supabase)
    return policy


def _environment(policy: dict[str, object]) -> dict[str, str]:
    supabase = policy["supabase"]
    assert isinstance(supabase, dict)
    return {
        "FLOWACCOUNT_SANDBOX_BASE_URL": "https://openapi.flowaccount.com/test",
        "FLOWACCOUNT_SANDBOX_CLIENT_ID": "fixture-client",
        "FLOWACCOUNT_SANDBOX_CLIENT_SECRET": "fixture-secret",
        "MERCURY_MARKETPLACE_SNAPSHOT_URL": "https://example.invalid/marketplace.json",
        "MERCURY_PUBLIC_MCP_TOKEN": "fixture-mcp-token",
        "MERCURY_PUBLIC_MCP_URL": "https://mercury.example.invalid",
        "MERCURY_STAGING_REPOSITORY_TOKEN": "fixture-staging-token",
        "MERCURY_TARGET_REPOSITORY_READ_TOKEN": "fixture-read-token",
        "RENDER_API_TOKEN": "fixture-render-token",
        "RENDER_API_URL": "https://api.render.com",
        "RENDER_SERVICE_ID": "srv_fixture",
        "STAGING_REPOSITORY": str(policy["staging_repository"]),
        "SUPABASE_DB_URL": _database_url("never-print-this"),
        "SUPABASE_URL": f"https://{supabase['project_ref']}.supabase.co",
        "TARGET_REPOSITORY": str(policy["reviewed_repository"]),
        "INSPECTOR_GIT": "/bin/sh",
        "INSPECTOR_GITLEAKS": "/bin/sh",
        "INSPECTOR_TRUFFLEHOG": "/bin/sh",
    }


def _write_inputs(tmp_path: Path, policy: dict[str, object]) -> tuple[Path, Path, Path]:
    policy_path = tmp_path / "policy.json"
    manifest_path = tmp_path / "manifest.json"
    allowlist_path = tmp_path / "allowlist.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "required": [
                    "git_all_refs",
                    "github_pull_request_refs",
                    "github_releases_and_assets",
                    "github_actions_logs_artifacts_caches",
                    "github_packages_pages_wiki",
                    "marketplace_snapshot",
                    "render_build_and_runtime_logs",
                    "supabase_knowledge_and_storage",
                    "wheel_sdist_plugin_source_archives",
                    "public_mcp_responses",
                ],
                "scanner_versions": {"gitleaks": "8.24.3", "trufflehog": "3.88.32"},
            }
        ),
        encoding="utf-8",
    )
    allowlist_path.write_text(json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8")
    return policy_path, manifest_path, allowlist_path


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _supabase_evidence(policy: dict[str, object]) -> dict[str, object]:
    supabase = policy["supabase"]
    assert isinstance(supabase, dict)
    evidence = dict(supabase)
    project_ref = evidence["project_ref"]
    assert isinstance(project_ref, str)
    evidence["project_ref_sha256"] = _sha(project_ref)
    return evidence


def test_strict_json_rejects_duplicate_keys_before_any_network_boundary(
    core: ModuleType, tmp_path: Path
) -> None:
    policy = _configured_policy(core)
    policy_path, manifest_path, allowlist_path = _write_inputs(tmp_path, policy)
    manifest_path.write_bytes(b'{"schema_version":1,"schema_version":1}')

    with pytest.raises(core.InspectionError, match="^manifest_invalid$"):
        core.inspect(
            policy_path=policy_path,
            reviewed_sha="a" * 40,
            staging_ref="v0.2.1-rc1",
            manifest_path=manifest_path,
            allowlist_path=allowlist_path,
            output_path=tmp_path / "evidence.json",
            environment=_environment(policy),
        )


def test_database_url_requires_verify_full_and_never_echoes_credential(core: ModuleType) -> None:
    secret = "do-not-expose-this-password"
    url = _database_url(secret, query="sslmode=require")

    with pytest.raises(core.InspectionError, match="^database_tls_invalid$") as raised:
        core.parse_database_url(url, project_ref="abcdefghijklmnopqrst")

    assert secret not in str(raised.value)


def test_database_identity_precedes_migrations_and_function_queries(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _configured_policy(core)
    supabase = policy["supabase"]
    assert isinstance(supabase, dict)
    expected_identities = core._static_validation_identities(
        core.ArchiveSnapshot(
            tree_sha256="a" * 64,
            static_files={
                "catalog/global/flowaccount/actions.json": (
                    ROOT / "catalog/global/flowaccount/actions.json"
                ).read_bytes(),
                "catalog/global/peak/actions.json": (
                    ROOT / "catalog/global/peak/actions.json"
                ).read_bytes(),
            },
        )
    )
    calls: list[str] = []

    class Cursor:
        last = ""
        parameters: tuple[object, ...] = ()

        def execute(self, query: str, parameters: tuple[object, ...] = ()) -> None:
            calls.append(query)
            self.last = query
            self.parameters = parameters

        def fetchone(self) -> tuple[object, ...]:
            if self.last == "SELECT current_database(), session_user, current_user":
                return ("postgres", "postgres", "postgres")
            if "pg_get_functiondef" in self.last:
                signature = self.parameters[0]
                assert isinstance(signature, str)
                for candidate in supabase["functions"]:
                    assert isinstance(candidate, dict)
                    if candidate["signature"] in signature:
                        return (f"definition:{candidate['signature']}",)
                return ("definition:unknown",)
            raise AssertionError(self.last)

        def fetchall(self) -> list[tuple[object, ...]]:
            if "schema_migrations" in self.last:
                return [
                    ("20260713100000",),
                    ("20260715100000",),
                    ("20260716100000",),
                ]
            if "pg_catalog.pg_tables" in self.last:
                return [(name,) for name in supabase["tables"]]
            if "storage.buckets" in self.last:
                return []
            if "knowledge_documents" in self.last and "knowledge_chunks" in self.last:
                return [(*identity, 1, 1) for identity in expected_identities]
            if (
                "SELECT connector_id, action_id, version_id" in self.last
                and "erp_action_validation_knowledge" in self.last
            ):
                return list(expected_identities)
            raise AssertionError(self.last)

    class Connection:
        def __init__(self) -> None:
            self.cursor_instance = Cursor()
            self.info = SimpleNamespace(ssl_in_use=True)
            self.rolled_back = False
            self.closed = False

        def cursor(self) -> Cursor:
            return self.cursor_instance

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    fake_driver = SimpleNamespace(connect=lambda *_args, **_kwargs: connection)
    monkeypatch.setitem(sys.modules, "psycopg", fake_driver)

    observed, flowaccount = core.inspect_database(
        policy=policy,
        database_url=_database_url("secret"),
        expected_validation_identities=expected_identities,
    )

    current_identity = calls.index("SELECT current_database(), session_user, current_user")
    migration = next(index for index, query in enumerate(calls) if "schema_migrations" in query)
    function = next(index for index, query in enumerate(calls) if "pg_get_functiondef" in query)
    assert calls[:2] == ["BEGIN READ ONLY", "SET LOCAL statement_timeout = '15000ms'"]
    assert current_identity < migration < function
    assert observed["schema_sha256"] == supabase["schema_sha256"]
    assert flowaccount["terminal_records"] == 190
    assert any("knowledge_documents" in query for query in calls)
    assert any("knowledge_chunks" in query for query in calls)
    assert connection.rolled_back and connection.closed


def test_inspector_uses_fixture_scanners_with_minimal_environment(
    core: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, version, flag in (
        ("gitleaks", "8.24.3", "version"),
        ("trufflehog", "3.88.32", "--version"),
    ):
        executable = tmp_path / name
        executable.write_text(
            "#!/bin/sh\n"
            'test -z "${SUPABASE_DB_URL:-}" || exit 9\n'
            f"if [ \"$1\" = \"{flag}\" ]; then printf '%s\\n' '{name} {version}'; exit 0; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
    monkeypatch.setenv("SUPABASE_DB_URL", "must-not-reach-subprocess")
    home = tmp_path / "home"
    home.mkdir()

    environment = core._minimal_process_env(home)
    environment.update(
        {
            "INSPECTOR_GITLEAKS": str(tmp_path / "gitleaks"),
            "INSPECTOR_TRUFFLEHOG": str(tmp_path / "trufflehog"),
        }
    )
    gitleaks, trufflehog = core._require_scanner_versions(environment, home)

    assert gitleaks == tmp_path / "gitleaks"
    assert trufflehog == tmp_path / "trufflehog"


def test_minimal_process_environment_whitelists_only_validated_tool_paths(
    core: ModuleType, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = {
        "INSPECTOR_GIT": "/usr/bin/git",
        "INSPECTOR_GITLEAKS": "/opt/release/bin/gitleaks",
        "INSPECTOR_TRUFFLEHOG": "/opt/release/bin/trufflehog",
        "SUPABASE_DB_URL": _database_url("must-not-reach-subprocess"),
        "MERCURY_TARGET_REPOSITORY_READ_TOKEN": "must-not-reach-subprocess",
    }

    environment = core._minimal_process_env(home, tool_paths=tools)

    assert environment["INSPECTOR_GIT"] == tools["INSPECTOR_GIT"]
    assert environment["INSPECTOR_GITLEAKS"] == tools["INSPECTOR_GITLEAKS"]
    assert environment["INSPECTOR_TRUFFLEHOG"] == tools["INSPECTOR_TRUFFLEHOG"]
    assert "SUPABASE_DB_URL" not in environment
    assert "MERCURY_TARGET_REPOSITORY_READ_TOKEN" not in environment


def test_git_scanners_use_literal_git_subcommand_and_pinned_git_path(
    core: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def run_silent(
        command: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
    ) -> None:
        assert cwd == tmp_path
        commands.append((command, environment))

    monkeypatch.setattr(core, "_run_silent", run_silent)
    core._scan_git(
        tmp_path,
        log_options="--all",
        gitleaks=Path("/opt/release/bin/gitleaks"),
        trufflehog=Path("/opt/release/bin/trufflehog"),
        environment={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "INSPECTOR_GIT": "/usr/bin/git",
        },
    )

    assert [command[:2] for command, _environment in commands] == [
        ("/opt/release/bin/gitleaks", "git"),
        ("/opt/release/bin/trufflehog", "git"),
    ]
    assert all(
        environment["PATH"].split(":", 1)[0] == "/usr/bin"
        for _command, environment in commands
    )


def test_static_catalog_validation_identities_are_exact_and_sorted(
    core: ModuleType,
) -> None:
    snapshot = core.ArchiveSnapshot(
        tree_sha256="a" * 64,
        static_files={
            "catalog/global/flowaccount/actions.json": (
                ROOT / "catalog/global/flowaccount/actions.json"
            ).read_bytes(),
            "catalog/global/peak/actions.json": (
                ROOT / "catalog/global/peak/actions.json"
            ).read_bytes(),
        },
    )

    identities = core._static_validation_identities(snapshot)

    assert len(identities) == 254
    assert identities == tuple(sorted(identities))
    assert sum(identity[0] == "flowaccount" for identity in identities) == 190
    assert sum(identity[0] == "peak" for identity in identities) == 64


@pytest.mark.parametrize("surface", ["validation", "rag"])
def test_exact_database_identity_coverage_rejects_identity_swap(
    core: ModuleType,
    surface: str,
) -> None:
    expected = (
        ("flowaccount", "act_" + "1" * 24, "av_" + "2" * 64),
        ("peak", "act_" + "3" * 24, "av_" + "4" * 64),
    )
    validation_rows: list[tuple[object, ...]] = list(expected)
    rag_rows: list[tuple[object, ...]] = [
        (*identity, 1, 1) for identity in expected
    ]
    replacement = ("peak", "act_" + "5" * 24, "av_" + "6" * 64)
    if surface == "validation":
        validation_rows[-1] = replacement
        error = "validation_coverage_invalid"
    else:
        rag_rows[-1] = (*replacement, 1, 1)
        error = "validation_rag_coverage_invalid"

    with pytest.raises(core.InspectionError, match=f"^{error}$"):
        core._require_exact_validation_identity_coverage(
            validation_rows=validation_rows,
            rag_rows=rag_rows,
            expected_identities=expected,
        )


def test_render_status_requires_runtime_deployment_commit(
    core: ModuleType,
) -> None:
    payload = {
        "status": "ok",
        "version": "0.2.1",
        "deployment_commit": "b" * 40,
        "mcp_endpoint": "https://mercury.example.invalid/mcp",
    }

    with pytest.raises(core.InspectionError, match="^render_status_invalid$"):
        core._render_status_endpoint(
            payload,
            base_url="https://mercury.example.invalid",
            reviewed_sha="a" * 40,
        )


def _validation_tool_result(connector: str, field: str) -> dict[str, object]:
    action_id = "act_" + "a" * 24
    version_id = "av_" + "b" * 64
    run_id = "run_01JZ8M3RC7RYF1NANC3P8B1C2D"
    document_uri = (
        f"mercury://wiki/validation/{connector}/"
        f"{action_id}/{version_id}/{run_id}"
    )
    structured = {
        "status": "ok",
        field: [
            {
                "chunk_id": "123e4567-e89b-42d3-a456-426614174000",
                "document_uri": document_uri,
                "source_uri": document_uri,
                "text": "\n".join(
                    (
                        f"Action ID: {action_id}",
                        f"Version ID: {version_id}",
                        "Evidence ID: ev_01JZ8M3RC7RYF1NANC3P8B1C2D",
                        f"Evidence digest: {'c' * 64}",
                    )
                ),
                "citation": {
                    "source_uri": document_uri,
                    "heading": "Invoices",
                },
                "metadata": {
                    "connector": connector,
                    "doc_type": "endpoint_validation",
                    "review_status": "reviewed",
                    "action_id": action_id,
                    "version_id": version_id,
                },
            }
        ],
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, sort_keys=True),
            }
        ],
        "structuredContent": structured,
        "isError": False,
    }


def test_mcp_response_accepts_exact_structured_json_duplicate_with_public_uuid(
    core: ModuleType,
) -> None:
    result = _validation_tool_result("peak", "results")
    response = core.HttpResponse(
        200,
        {"content-type": "application/json"},
        json.dumps({"jsonrpc": "2.0", "id": 7, "result": result}).encode(),
    )

    parsed = core._mcp_response_json(response, request_id=7)

    assert parsed == result


def test_connector_validation_payload_requires_exact_connector_and_citation(
    core: ModuleType,
) -> None:
    result = _validation_tool_result("peak", "results")
    core._require_connector_validation_payload(
        result,
        connector="peak",
        result_field="results",
    )

    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    rows = structured["results"]
    assert isinstance(rows, list)
    row = rows[0]
    assert isinstance(row, dict)
    metadata = row["metadata"]
    assert isinstance(metadata, dict)
    metadata["connector"] = "flowaccount"

    with pytest.raises(core.InspectionError, match="^public_mcp_validation_rag_invalid$"):
        core._require_connector_validation_payload(
            result,
            connector="peak",
            result_field="results",
        )


def test_successful_evidence_is_atomic_mode_0600_and_accepted_by_assembler(
    core: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _configured_policy(core)
    policy_path, manifest_path, allowlist_path = _write_inputs(tmp_path, policy)
    evidence_path = tmp_path / "hosted-evidence-v1.json"
    digest = _sha("fixture")
    staging = {
        "repository": policy["staging_repository"],
        "ref": "v0.2.1-rc1",
        "commit_sha": "b" * 40,
        "tree_sha256": digest,
        "local_tool_count": 19,
    }
    render = {
        "deployment_commit": "a" * 40,
        "version": "0.2.1",
        "hosted_tool_count": 20,
        "evidence_sha256": digest,
    }
    flowaccount = {
        "total": 190,
        "terminal_records": 190,
        "required_live_test_passed": False,
        "report_sha256": digest,
    }
    monkeypatch.setattr(core, "_require_scanner_versions", lambda *_args: (Path("/g"), Path("/t")))
    monkeypatch.setattr(
        core,
        "_inspect_git_and_staging",
        lambda **_kwargs: (
            [digest],
            [digest],
            staging,
            (("flowaccount", "act_" + "a" * 24, "av_" + "b" * 64),),
        ),
    )
    monkeypatch.setattr(core, "_inspect_github_releases", lambda **_kwargs: [digest])
    monkeypatch.setattr(core, "_inspect_github_actions", lambda **_kwargs: [digest])
    monkeypatch.setattr(core, "_inspect_github_packages_pages_wiki", lambda **_kwargs: [digest])
    monkeypatch.setattr(core, "_inspect_marketplace", lambda **_kwargs: [digest])
    monkeypatch.setattr(
        core,
        "_inspect_render_and_public_mcp",
        lambda **_kwargs: (render, [digest], [digest]),
    )
    monkeypatch.setattr(
        core, "inspect_database", lambda **_kwargs: (_supabase_evidence(policy), flowaccount)
    )
    monkeypatch.setattr(core, "_flowaccount_live_read", lambda _environment: digest)
    now = datetime(2026, 7, 16, tzinfo=UTC)

    evidence = core.inspect(
        policy_path=policy_path,
        reviewed_sha="a" * 40,
        staging_ref="v0.2.1-rc1",
        manifest_path=manifest_path,
        allowlist_path=allowlist_path,
        output_path=evidence_path,
        environment=_environment(policy),
        clock=lambda: now,
    )

    assert set(evidence) == {
        "schema_version",
        "reviewed_repository",
        "reviewed_commit_sha",
        "public_surface_manifest_sha256",
        "secret_scan_allowlist_sha256",
        "flowaccount",
        "staging",
        "render",
        "supabase",
        "surfaces",
        "completed_at",
    }
    assert stat.S_IMODE(evidence_path.stat().st_mode) == 0o600
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == evidence
    with pytest.raises(core.InspectionError, match="^evidence_output_exists$"):
        core._atomic_write_new_json(evidence_path, {"already": "exists"})

    preflight = _module("verify_remote_preflight")
    assembler = _module("assemble_trusted_attestation")
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
    receipt = preflight.validate_preflight_snapshot(policy, snapshot)
    attestation = assembler.assemble_attestation(
        evidence=evidence,
        preflight=receipt,
        policy=policy,
        producer_repository=str(policy["repository"]),
        producer_sha="c" * 40,
        producer_run_id=123,
        producer_run_attempt=1,
        staging_ref="v0.2.1-rc1",
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        allowlist_sha256=hashlib.sha256(allowlist_path.read_bytes()).hexdigest(),
    )
    assert attestation["schema_version"] == 2


def test_entrypoint_imports_only_trusted_release_control_modules() -> None:
    source = (SCRIPTS / "inspector_core.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    imports.extend(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert not any(name == "mercury_tools" or name.startswith("mercury_tools.") for name in imports)
    entrypoint = ROOT / "release-control" / "scaffold" / "bin" / "mercury-release-control-inspector"
    assert entrypoint.stat().st_mode & stat.S_IXUSR


def test_pinned_entrypoint_rejects_tampered_core_bytes(tmp_path: Path) -> None:
    scaffold = tmp_path / "scaffold"
    bin_dir = scaffold / "bin"
    scripts_dir = scaffold / "scripts"
    bin_dir.mkdir(parents=True)
    scripts_dir.mkdir()
    entrypoint = bin_dir / "mercury-release-control-inspector"
    core_path = scripts_dir / "inspector_core.py"
    shutil.copy2(
        ROOT / "release-control" / "scaffold" / "bin" / "mercury-release-control-inspector",
        entrypoint,
    )
    shutil.copy2(SCRIPTS / "inspector_core.py", core_path)

    clean = subprocess.run(
        [sys.executable, str(entrypoint), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert clean.returncode == 0, clean.stderr

    core_path.write_bytes(core_path.read_bytes() + b"\n# tampered\n")
    tampered = subprocess.run(
        [sys.executable, str(entrypoint), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert tampered.returncode != 0
    assert "inspector_core_digest_mismatch" in tampered.stderr


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("classification", "arbitrary"),
        ("reviewer_role", "arbitrary"),
        ("expires_at", "2000-01-01T00:00:00Z"),
    ],
)
def test_allowlist_rejects_unknown_enums_and_expired_entries(
    core: ModuleType, field: str, value: str
) -> None:
    entry = {
        "classification": "non_secret_fixture",
        "file": "tests/fixture.txt",
        "rule": "scanner_finding",
        "digest": "a" * 64,
        "reviewer_role": "security_reviewer",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    entry[field] = value

    with pytest.raises(core.InspectionError, match="^allowlist_invalid$"):
        core.validate_allowlist(
            {"schema_version": 1, "entries": [entry]},
            at=datetime(2026, 7, 16, tzinfo=UTC),
        )


def test_release_control_accepts_tracked_mercury_allowlist_contract(core: ModuleType) -> None:
    allowlist = json.loads(
        (ROOT / "docs" / "release" / "secret-scan-allowlist.json").read_text(
            encoding="utf-8"
        )
    )

    core.validate_allowlist(
        allowlist,
        at=datetime(2026, 7, 16, tzinfo=UTC),
    )


def test_release_control_allowlist_enums_match_mercury_model(core: ModuleType) -> None:
    assert frozenset({"documentation_placeholder", "non_secret_fixture"}) == (
        core._ALLOWLIST_CLASSIFICATIONS
    )
    assert frozenset({"release_reviewer", "security_reviewer"}) == (
        core._ALLOWLIST_REVIEWER_ROLES
    )


@pytest.mark.parametrize("scanner", ["gitleaks", "trufflehog"])
def test_scanner_fingerprint_binds_secret_specific_evidence(
    core: ModuleType,
    tmp_path: Path,
    scanner: str,
) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "fixture.txt").write_text("scanner fixture\n", encoding="utf-8")
    report = tmp_path / f"{scanner}.json"

    def finding(secret: str) -> frozenset[tuple[str, str, str]]:
        if scanner == "gitleaks":
            payload = [
                {
                    "File": "fixture.txt",
                    "RuleID": "generic-api-key",
                    "Commit": "",
                    "StartLine": 1,
                    "Secret": secret,
                }
            ]
            report.write_text(json.dumps(payload), encoding="utf-8")
        else:
            payload = {
                "DetectorName": "URI",
                "DecoderName": "PLAIN",
                "Verified": False,
                "Raw": secret,
                "RawV2": None,
                "SourceMetadata": {
                    "Data": {
                        "Filesystem": {
                            "file": "fixture.txt",
                            "line": 1,
                        }
                    }
                },
            }
            report.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return core._scanner_findings(scanner, report, root=root)

    first = finding("fixture-secret-one")
    second = finding("fixture-secret-two")

    assert first != second
    assert len(first) == len(second) == 1
    assert "fixture-secret" not in repr(first)
    assert "fixture-secret" not in repr(second)


def test_archive_snapshot_rejects_forbidden_member_instead_of_filtering(
    core: ModuleType, tmp_path: Path
) -> None:
    archive_path = tmp_path / "tree.tar"
    with tarfile.open(archive_path, "w") as archive:
        payload = b"credential=must-not-be-hidden"
        member = tarfile.TarInfo(".env")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(core.InspectionError, match="^archive_forbidden_path$"):
        core._archive_snapshot(archive_path)


def test_local_mcp_static_parser_requires_exact_v030_advanced_local_tool_names(
    core: ModuleType,
) -> None:
    source = (ROOT / "src" / "mercury_tools" / "mcp" / "local_server.py").read_bytes()

    assert core._static_mcp_tool_names(source) == (
        "search_knowledge",
        "retrieve_context_pack",
        "get_document",
        "connector_status",
        "run_accounting_skill",
        "run_mercury_flow",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
        "search_erp_actions",
        "get_erp_action_schema",
        "run_erp_read",
        "prepare_erp_mutation",
        "execute_erp_create",
        "execute_erp_update",
        "execute_sensitive_erp_action",
        "get_erp_request_status",
        "import_erp_spec",
        "list_connector_drivers",
        "credential_status",
    )


def test_github_redirect_strips_authorization_cross_origin(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[object] = []

    class Response:
        def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
            self.status = status
            self.headers = headers
            self._body = io.BytesIO(body)

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def getcode(self) -> int:
            return self.status

        def read(self, size: int = -1) -> bytes:
            return self._body.read(size)

    responses = iter(
        (
            Response(
                302,
                {
                    "Location": "https://objects.githubusercontent.com/signed/object?sig=opaque",
                },
                b"",
            ),
            Response(200, {"Content-Type": "application/octet-stream"}, b"payload"),
        )
    )

    def open_url(request: object, *, timeout: float) -> Response:
        del timeout
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(core, "_open_url", open_url)
    response = core.request_bytes(
        "https://api.github.com/repos/example/project/actions/runs/1/logs",
        headers={"Authorization": "Bearer target-token", "X-GitHub-Api-Version": "2022-11-28"},
        code="github_actions_log_download_failed",
    )

    assert response.body == b"payload"
    assert len(requests) == 2
    assert requests[0].get_header("Authorization") == "Bearer target-token"
    assert requests[1].get_header("Authorization") is None
    assert requests[1].get_header("X-github-api-version") is None


def test_database_url_rejects_all_libpq_indirection(core: ModuleType) -> None:
    url = _database_url(
        "secret",
        query="sslmode=verify-full&hostaddr=127.0.0.1",
    )

    with pytest.raises(core.InspectionError, match="^database_url_invalid$"):
        core.parse_database_url(url, project_ref="abcdefghijklmnopqrst")


def test_flowaccount_live_read_sends_scope_and_accepts_no_explicit_failure_marker(
    core: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[tuple[str, dict[str, object]]] = []
    responses = iter(
        (
            core.HttpResponse(200, {}, b'{"access_token":"opaque-access-token"}'),
            core.HttpResponse(200, {}, b'{"data":{"company":"fixture"}}'),
        )
    )

    def request(url: str, **kwargs: object) -> object:
        requests.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(core, "request_bytes", request)
    digest = core._flowaccount_live_read(
        {
            "FLOWACCOUNT_SANDBOX_BASE_URL": "https://openapi.flowaccount.com/test",
            "FLOWACCOUNT_SANDBOX_CLIENT_ID": "client",
            "FLOWACCOUNT_SANDBOX_CLIENT_SECRET": "secret",
        }
    )

    token_fields = urllib.parse.parse_qs(requests[0][1]["body"].decode("ascii"))
    assert token_fields["scope"] == ["flowaccount-api"]
    assert len(digest) == 64


def test_wrapper_does_not_forward_unused_bearer_or_service_role_credentials() -> None:
    wrapper = _module("run_pinned_inspector")

    assert "MERCURY_TOOLS_HTTP_BEARER_TOKEN" not in wrapper._FORWARDED_NAMES
    assert "SUPABASE_SERVICE_ROLE_KEY" not in wrapper._FORWARDED_NAMES
