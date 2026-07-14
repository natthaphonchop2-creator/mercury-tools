from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.models import (
    PINNED_SCANNER_VERSIONS,
    REQUIRED_PUBLIC_SURFACES,
    AllowlistClassification,
    AllowlistEntry,
    ArtifactKind,
    GateStatus,
    HostedSurface,
    PublicSurfaceManifest,
    SecretScanAllowlist,
    SecretScanPolicy,
    SecretScanRequest,
)
from mercury_tools.release.scanner import (
    CommandResult,
    ReleaseGateError,
    SubprocessCommandRunner,
    apply_allowlist,
    build_blocked_report,
    load_known_secret_digests,
    load_public_surface_manifest,
    scan_artifacts,
    scan_filesystem,
    scan_git_repository,
    scan_public_release,
    validate_allowlist,
)


class VersionRunner:
    def __init__(self, responses: dict[str, CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
    ) -> CommandResult:
        del cwd, input_bytes
        self.calls.append(argv)
        return self.responses[Path(argv[0]).name]


class GitScannerRunner:
    def __init__(
        self,
        *,
        gitleaks: CommandResult | None = None,
        trufflehog: CommandResult | None = None,
        omit_local_pull_refs: bool = False,
        malformed_remote_pull_refs: bool = False,
    ) -> None:
        self.results = {
            "gitleaks": gitleaks or CommandResult(exit_code=0, stdout=b"[]", stderr=b""),
            "trufflehog": trufflehog or CommandResult(exit_code=0, stdout=b"", stderr=b""),
        }
        self.omit_local_pull_refs = omit_local_pull_refs
        self.malformed_remote_pull_refs = malformed_remote_pull_refs
        self.calls: list[tuple[str, ...]] = []
        self.delegate = SubprocessCommandRunner()

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
    ) -> CommandResult:
        self.calls.append(argv)
        executable = Path(argv[0]).name
        if executable in self.results:
            return self.results[executable]
        result = self.delegate.run(argv, cwd=cwd, input_bytes=input_bytes)
        if (
            self.malformed_remote_pull_refs
            and argv[1:] == ("ls-remote", "origin", "refs/pull/*/head")
        ):
            return CommandResult(
                exit_code=0,
                stdout=b"malformed-ref-output",
                stderr=b"raw detail must not survive",
            )
        if self.omit_local_pull_refs and len(argv) > 1 and argv[1] == "for-each-ref":
            lines = [
                line
                for line in result.stdout.splitlines()
                if not line.startswith(b"refs/remotes/pull/")
            ]
            return CommandResult(
                exit_code=result.exit_code,
                stdout=b"\n".join(lines) + (b"\n" if lines else b""),
                stderr=result.stderr,
            )
        return result


def _write_zip(path: Path, name: str = "README.txt", data: bytes = b"safe fixture") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, data)


def _write_tar(path: Path, name: str = "README.txt", data: bytes = b"safe fixture") -> None:
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))


def _write_complete_artifact_set(root: Path) -> None:
    root.mkdir()
    _write_zip(root / "mercury_tools-0.2.1-py3-none-any.whl")
    _write_tar(root / "mercury_tools-0.2.1.tar.gz")
    _write_zip(root / "mercury-finance-plugin.zip")
    _write_zip(root / "mercury-tools-source.zip")


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Release Test",
            "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
            "GIT_COMMITTER_NAME": "Release Test",
            "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
        },
    )
    return completed.stdout.strip()


def _make_remote(tmp_path: Path, *, historical_value: str | None = None) -> Path:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / "README.md").write_text("safe release fixture\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    _git("tag", "v0.0.1", cwd=source)
    if historical_value is not None:
        (source / "history.txt").write_text(historical_value, encoding="utf-8")
        _git("add", "history.txt", cwd=source)
        _git("commit", "-m", "historical fixture", cwd=source)
        (source / "history.txt").unlink()
        _git("add", "-u", cwd=source)
        _git("commit", "-m", "remove historical fixture", cwd=source)
    _git("clone", "--bare", str(source), str(remote))
    head = _git("rev-parse", "HEAD", cwd=source)
    _git("--git-dir", str(remote), "update-ref", "refs/pull/1/head", head)
    return remote


@pytest.fixture
def scan_request(tmp_path: Path) -> SecretScanRequest:
    return SecretScanRequest(
        repo="example/mercury-tools",
        artifacts=tmp_path / "dist",
        all_history=True,
        hosted=True,
        manifest=PublicSurfaceManifest(
            required=REQUIRED_PUBLIC_SURFACES,
            scanner_versions=PINNED_SCANNER_VERSIONS,
        ),
        allowlist=SecretScanAllowlist(entries=()),
        policy=SecretScanPolicy(
            scanner_versions=PINNED_SCANNER_VERSIONS,
        ),
    )


def test_missing_independent_scanner_blocks_release(
    monkeypatch: pytest.MonkeyPatch,
    scan_request: SecretScanRequest,
) -> None:
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: None if name == "gitleaks" else "/bin/tool",
    )

    report = scan_public_release(scan_request)

    assert report.passed is False
    assert "scanner_missing:gitleaks" in report.blockers


def test_forbidden_local_paths_are_detected(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    (tmp_path / ".mercury").mkdir()
    (tmp_path / ".mercury" / "credentials.json").write_text("{}", encoding="utf-8")

    report = scan_filesystem(tmp_path, scan_request.policy)

    assert report.findings[0].rule == "forbidden_path"


@pytest.mark.parametrize(
    "required",
    [
        REQUIRED_PUBLIC_SURFACES[:-1],
        REQUIRED_PUBLIC_SURFACES[:-1] + ("replacement_surface",),
        REQUIRED_PUBLIC_SURFACES + ("extra_surface",),
    ],
)
def test_manifest_requires_exact_declared_corpus(required: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="public_surface_manifest_invalid"):
        PublicSurfaceManifest(
            required=required,
            scanner_versions=PINNED_SCANNER_VERSIONS,
        )


def test_tracked_manifest_and_allowlist_are_secret_free_strict_defaults() -> None:
    root = Path(__file__).resolve().parents[1]

    manifest = json.loads(
        (root / "docs/release/public-surface-manifest.json").read_text(encoding="utf-8")
    )
    allowlist = json.loads(
        (root / "docs/release/secret-scan-allowlist.json").read_text(encoding="utf-8")
    )

    assert manifest == {
        "schema_version": 1,
        "required": list(REQUIRED_PUBLIC_SURFACES),
        "scanner_versions": PINNED_SCANNER_VERSIONS,
    }
    assert allowlist == {"schema_version": 1, "entries": []}


@pytest.mark.parametrize(
    "scanner_versions",
    [
        {"gitleaks": PINNED_SCANNER_VERSIONS["gitleaks"]},
        {**PINNED_SCANNER_VERSIONS, "other": "1.2.3"},
        {**PINNED_SCANNER_VERSIONS, "gitleaks": "latest"},
    ],
)
def test_policy_rejects_missing_extra_or_unpinned_scanners(
    scanner_versions: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="scanner_pins_invalid"):
        SecretScanPolicy(scanner_versions=scanner_versions)


def test_known_credentials_accept_only_sha256_fingerprints() -> None:
    fingerprint = "a" * 64
    policy = SecretScanPolicy(
        scanner_versions=PINNED_SCANNER_VERSIONS,
        known_secret_digests=(fingerprint,),
    )

    assert policy.known_secret_digests == (fingerprint,)
    with pytest.raises(ValidationError, match="known_credential_fingerprint_invalid"):
        SecretScanPolicy(
            scanner_versions=PINNED_SCANNER_VERSIONS,
            known_secret_digests=("source-secret-value",),
        )


@pytest.mark.parametrize(
    "file",
    [
        "*",
        ".",
        "tests/**",
        "tests//fixture.txt",
        "tests/./fixture.txt",
        "/tmp/fixture.txt",
        "../fixture.txt",
    ],
)
def test_allowlist_rejects_broad_or_nonrelative_files(file: str) -> None:
    with pytest.raises(ValidationError, match="allowlist_file_invalid"):
        AllowlistEntry(
            classification=AllowlistClassification.NON_SECRET_FIXTURE,
            file=file,
            rule="provider_token",
            digest="b" * 64,
            reviewer_role="security_reviewer",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )


def test_expired_allowlist_is_a_release_blocker() -> None:
    allowlist = SecretScanAllowlist(
        entries=(
            AllowlistEntry(
                classification=AllowlistClassification.DOCUMENTATION_PLACEHOLDER,
                file="tests/fixtures/example.txt",
                rule="provider_token",
                digest="c" * 64,
                reviewer_role="release_reviewer",
                expires_at=datetime(2026, 7, 13, tzinfo=UTC),
            ),
        )
    )

    with pytest.raises(ReleaseGateError, match="allowlist_expired"):
        validate_allowlist(allowlist, at=datetime(2026, 7, 14, tzinfo=UTC))


def test_unverifiable_scanner_version_blocks_before_remote_access(
    monkeypatch: pytest.MonkeyPatch,
    scan_request: SecretScanRequest,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/mock/{name}")
    runner = VersionRunner(
        {
            "gitleaks": CommandResult(exit_code=0, stdout=b"development", stderr=b""),
            "trufflehog": CommandResult(
                exit_code=0,
                stdout=f"trufflehog {PINNED_SCANNER_VERSIONS['trufflehog']}".encode(),
                stderr=b"",
            ),
        }
    )

    report = scan_public_release(scan_request, command_runner=runner)

    assert report.passed is False
    assert "scanner_version_unverifiable:gitleaks" in report.blockers
    assert runner.calls == [
        ("/mock/gitleaks", "version"),
        ("/mock/trufflehog", "--version"),
    ]


def test_unpinned_scanner_version_and_raw_output_never_enter_report(
    monkeypatch: pytest.MonkeyPatch,
    scan_request: SecretScanRequest,
) -> None:
    raw_secret = "ghp_value-that-must-never-enter-the-report"
    monkeypatch.setattr(shutil, "which", lambda name: f"/mock/{name}")
    runner = VersionRunner(
        {
            "gitleaks": CommandResult(
                exit_code=0,
                stdout=f"gitleaks 9.9.9 {raw_secret}".encode(),
                stderr=b"",
            ),
            "trufflehog": CommandResult(
                exit_code=0,
                stdout=f"trufflehog {PINNED_SCANNER_VERSIONS['trufflehog']}".encode(),
                stderr=b"",
            ),
        }
    )

    report = scan_public_release(
        scan_request.model_copy(
            update={
                "hosted_surfaces": (HostedSurface(name="render_logs", accessible=False),)
            }
        ),
        command_runner=runner,
    )
    serialized = json.dumps(report.public_dict(), sort_keys=True)

    assert "scanner_version_unpinned:gitleaks" in report.blockers
    assert raw_secret not in serialized
    assert tuple(surface.surface for surface in report.surfaces) == REQUIRED_PUBLIC_SURFACES


@pytest.mark.parametrize(
    "relative_path",
    [
        ".env",
        ".env.production",
        ".mercury/credentials.json",
        "config/credentials.json",
        "state/credential-store.json",
        "logs/audit-ledger.jsonl",
        "downloads/provider-payload.json",
        "downloads/raw-provider-response.json",
        "logs/validation-traffic.jsonl",
    ],
)
def test_forbidden_path_family_is_detected(
    tmp_path: Path,
    scan_request: SecretScanRequest,
    relative_path: str,
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")

    result = scan_filesystem(tmp_path, scan_request.policy)

    assert any(finding.rule == "forbidden_path" for finding in result.findings)


def test_provider_token_is_detected_without_echoing_value_or_path(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    candidate = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    (tmp_path / "fixture.txt").write_text(candidate, encoding="utf-8")

    result = scan_filesystem(tmp_path, scan_request.policy)
    serialized = result.model_dump_json()

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert candidate not in serialized
    assert "fixture.txt" not in serialized


def test_high_entropy_credential_assignment_is_detected(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    candidate = "".join(
        ("Ab3/", "Cd5+", "Ef7_", "Gh9-", "Jk2/", "Lm4+", "Np6_", "Qr8-")
    )
    (tmp_path / "config.txt").write_text(f'client_secret = "{candidate}"', encoding="utf-8")

    result = scan_filesystem(tmp_path, scan_request.policy)

    rules = {finding.rule for finding in result.findings}
    assert "credential_assignment" in rules
    assert "high_entropy" in rules


def test_known_credential_fingerprint_matches_tokenized_content(tmp_path: Path) -> None:
    candidate = "known" + "-credential-" + "A1b2C3d4E5f6G7h8"
    fingerprint = hashlib.sha256(candidate.encode()).hexdigest()
    policy = SecretScanPolicy(
        scanner_versions=PINNED_SCANNER_VERSIONS,
        known_secret_digests=(fingerprint,),
    )
    (tmp_path / "content.txt").write_text(f"prefix {candidate} suffix", encoding="utf-8")

    result = scan_filesystem(tmp_path, policy)

    assert any(finding.rule == "known_credential" for finding in result.findings)


def test_known_fingerprint_matches_exact_json_scalar_with_punctuation(tmp_path: Path) -> None:
    candidate = "opaque" + "@value$with!punctuation#123456"
    fingerprint = hashlib.sha256(candidate.encode()).hexdigest()
    policy = SecretScanPolicy(
        scanner_versions=PINNED_SCANNER_VERSIONS,
        known_secret_digests=(fingerprint,),
    )
    (tmp_path / "payload.json").write_text(
        json.dumps({"api_key": candidate}),
        encoding="utf-8",
    )

    result = scan_filesystem(tmp_path, policy)

    assert any(finding.rule == "known_credential" for finding in result.findings)


def test_allowlist_matches_only_exact_file_rule_and_digest(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    candidate = "gh" + "p_" + "B1c2D3e4F5g6H7i8J9k0L1m2N3o4"
    (tmp_path / "fixture.txt").write_text(candidate, encoding="utf-8")
    finding = next(
        item
        for item in scan_filesystem(tmp_path, scan_request.policy).findings
        if item.rule == "provider_token"
    )
    entry = AllowlistEntry(
        classification=AllowlistClassification.NON_SECRET_FIXTURE,
        file="fixture.txt",
        rule=finding.rule,
        digest=finding.evidence_sha256,
        reviewer_role="security_reviewer",
        expires_at=datetime(2026, 7, 15, tzinfo=UTC),
    )

    assert apply_allowlist(
        (finding,),
        SecretScanAllowlist(entries=(entry,)),
        at=datetime(2026, 7, 14, tzinfo=UTC),
    ) == ()
    mismatched = entry.model_copy(update={"file": "other.txt"})
    assert apply_allowlist(
        (finding,),
        SecretScanAllowlist(entries=(mismatched,)),
        at=datetime(2026, 7, 14, tzinfo=UTC),
    ) == (finding,)


def test_artifact_scan_requires_and_reads_all_four_archive_kinds(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)

    result = scan_artifacts(artifacts, scan_request.policy)

    assert result.blockers == ()
    assert result.findings == ()
    assert set(result.kinds) == set(ArtifactKind)
    assert len(result.evidence_hashes) == 4


def test_wheel_record_checksum_is_not_treated_as_high_entropy_secret(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    checksum = "sha256=" + "Ab3Cd5Ef7Gh9Jk2Lm4Np6Qr8St1Uv3Wx5Yz7-_"
    _write_zip(
        artifacts / "mercury_tools-0.2.1-py3-none-any.whl",
        "mercury_tools-0.2.1.dist-info/RECORD",
        f"mercury_tools/__init__.py,{checksum},123\n".encode(),
    )

    result = scan_artifacts(artifacts, scan_request.policy)

    assert not any(finding.rule == "high_entropy" for finding in result.findings)


def test_missing_artifact_kind_blocks_release(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    (artifacts / "mercury-finance-plugin.zip").unlink()

    result = scan_artifacts(artifacts, scan_request.policy)

    assert "artifact_kind_missing:plugin" in result.blockers


def test_artifact_sidecars_are_scanned_for_forbidden_paths_and_content(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    candidate = "AIza" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4P5q6"
    sidecar = artifacts / "raw-provider-payload.json"
    sidecar.write_text(json.dumps({"value": candidate}), encoding="utf-8")

    result = scan_artifacts(artifacts, scan_request.policy)

    rules = {finding.rule for finding in result.findings}
    assert "forbidden_path" in rules
    assert "provider_token" in rules
    assert len(result.evidence_hashes) == 5


def test_archive_path_traversal_is_rejected_without_extraction(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    _write_zip(artifacts / "mercury-tools-source.zip", "../.env", b"unsafe fixture")

    result = scan_artifacts(artifacts, scan_request.policy)

    assert any(finding.rule == "archive_unsafe" for finding in result.findings)
    assert not (tmp_path / ".env").exists()


def test_provider_token_inside_archive_is_detected_in_memory(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    candidate = ("sk" + "_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4").encode()
    _write_zip(artifacts / "mercury-finance-plugin.zip", "plugin/data.txt", candidate)

    result = scan_artifacts(artifacts, scan_request.policy)

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert candidate.decode() not in result.model_dump_json()


def test_fresh_clone_fetches_all_ref_classes_and_scans_reachable_history(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    historical_value = "gh" + "p_" + "C1d2E3f4G5h6I7j8K9l0M1n2P3q4"
    remote = _make_remote(tmp_path, historical_value=historical_value)
    runner = GitScannerRunner()

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=runner,
    )

    assert result.blockers == ()
    assert result.object_count > 0
    assert result.blob_count > 0
    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert historical_value not in result.model_dump_json()
    fetch = next(call for call in runner.calls if len(call) > 1 and call[1] == "fetch")
    assert "+refs/heads/*:refs/remotes/origin/*" in fetch
    assert "+refs/tags/*:refs/tags/*" in fetch
    assert "+refs/pull/*/head:refs/remotes/pull/*/head" in fetch
    assert any(call[1:4] == ("rev-list", "--objects", "--all") for call in runner.calls)
    assert any(
        Path(call[0]).name == "gitleaks" and "--log-opts=--all" in call
        for call in runner.calls
    )
    assert any(
        Path(call[0]).name == "trufflehog" and "--no-update" in call
        for call in runner.calls
    )


def test_incomplete_pull_request_ref_inventory_blocks_repository_scan(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_remote(tmp_path)
    runner = GitScannerRunner(omit_local_pull_refs=True)

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=runner,
    )

    assert "git_refs_incomplete:pull_requests" in result.blockers


def test_malformed_remote_ref_inventory_is_a_secret_safe_blocker(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_remote(tmp_path)
    runner = GitScannerRunner(malformed_remote_pull_refs=True)

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=runner,
    )

    assert result.blockers == ("git_ref_inventory_malformed",)
    assert "raw detail" not in result.model_dump_json()


def test_external_scanner_findings_are_opaque_and_both_scanners_run(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_remote(tmp_path)
    raw_value = "sk" + "-" + "D1e2F3g4H5i6J7k8L9m0N1p2Q3r4"
    gitleaks_output = json.dumps(
        [{"File": "README.md", "RuleID": "generic", "Secret": raw_value}]
    ).encode()
    trufflehog_output = json.dumps(
        {"SourceMetadata": {"Data": {"Git": {"file": "README.md"}}}, "Raw": raw_value}
    ).encode()
    runner = GitScannerRunner(
        gitleaks=CommandResult(exit_code=1, stdout=gitleaks_output, stderr=b""),
        trufflehog=CommandResult(exit_code=0, stdout=trufflehog_output + b"\n", stderr=b""),
    )

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=runner,
    )
    serialized = result.model_dump_json()

    assert sum(finding.rule == "scanner_finding" for finding in result.findings) == 2
    assert raw_value not in serialized
    assert any(Path(call[0]).name == "gitleaks" for call in runner.calls)
    assert any(Path(call[0]).name == "trufflehog" for call in runner.calls)


def test_external_scanner_command_failure_blocks_without_stderr_echo(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_remote(tmp_path)
    raw_stderr = ("provider failure " + "gh" + "p_" + "must-not-echo").encode()
    runner = GitScannerRunner(
        gitleaks=CommandResult(exit_code=2, stdout=b"", stderr=raw_stderr)
    )

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=runner,
    )

    assert "scanner_command_failed:gitleaks:history" in result.blockers
    assert raw_stderr.decode() not in result.model_dump_json()


def test_gitleaks_finding_exit_without_json_evidence_blocks(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_remote(tmp_path)
    runner = GitScannerRunner(
        gitleaks=CommandResult(exit_code=1, stdout=b"", stderr=b"redacted")
    )

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=runner,
    )

    assert "raw_evidence_handling_failed:gitleaks" in result.blockers


def test_release_scan_parser_exposes_exact_fail_closed_command() -> None:
    from mercury_tools.cli import build_parser

    args = build_parser().parse_args(
        [
            "release",
            "scan-secrets",
            "--all-history",
            "--hosted",
            "--artifacts",
            "dist",
            "--repo",
            "example/mercury-tools",
            "--output",
            "release-evidence/secret-scan.json",
            "--known-fingerprint-stdin",
            "--known-fingerprint-file",
            "/tmp/fingerprints",
        ]
    )

    assert args.release_command == "scan-secrets"
    assert args.all_history is True
    assert args.hosted is True
    assert args.artifacts == "dist"
    assert args.repo == "example/mercury-tools"
    assert args.output == "release-evidence/secret-scan.json"
    assert args.known_fingerprint_stdin is True
    assert args.known_fingerprint_file == ["/tmp/fingerprints"]
    assert callable(args.func)


def test_cli_missing_scanners_fails_closed_and_writes_same_safe_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "evidence" / "secret-scan.json"
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        scanner_module,
        "scan_git_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote_accessed")),
    )

    exit_code = cli.main(
        [
            "release",
            "scan-secrets",
            "--all-history",
            "--hosted",
            "--artifacts",
            str(tmp_path / "dist"),
            "--repo",
            "example/mercury-tools",
            "--manifest",
            str(root / "docs/release/public-surface-manifest.json"),
            "--allowlist",
            str(root / "docs/release/secret-scan-allowlist.json"),
            "--output",
            str(output),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert "scanner_missing:gitleaks" in payload["blockers"]
    assert "scanner_missing:trufflehog" in payload["blockers"]
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert str(tmp_path) not in json.dumps(payload, sort_keys=True)


@pytest.mark.parametrize(
    ("filename", "payload", "blocker"),
    [
        (
            "manifest.json",
            {"schema_version": 1, "required": ["substituted"], "scanner_versions": {}},
            "public_surface_manifest_malformed",
        ),
        (
            "allowlist.json",
            {
                "schema_version": 1,
                "entries": [
                    {
                        "classification": "non_secret_fixture",
                        "file": "**",
                        "rule": "provider_token",
                        "digest": "a" * 64,
                        "reviewer_role": "security_reviewer",
                        "expires_at": "2026-07-15T00:00:00Z",
                    }
                ],
            },
            "secret_scan_allowlist_malformed",
        ),
    ],
)
def test_cli_malformed_manifest_or_allowlist_returns_constant_blocker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    payload: dict[str, object],
    blocker: str,
) -> None:
    from mercury_tools import cli

    root = Path(__file__).resolve().parents[1]
    malformed = tmp_path / filename
    malformed.write_text(json.dumps(payload), encoding="utf-8")
    manifest = (
        malformed
        if filename == "manifest.json"
        else root / "docs/release/public-surface-manifest.json"
    )
    allowlist = (
        malformed
        if filename == "allowlist.json"
        else root / "docs/release/secret-scan-allowlist.json"
    )

    exit_code = cli.main(
        [
            "release",
            "scan-secrets",
            "--all-history",
            "--hosted",
            "--artifacts",
            str(tmp_path / "dist"),
            "--repo",
            "example/mercury-tools",
            "--manifest",
            str(manifest),
            "--allowlist",
            str(allowlist),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["blockers"] == [blocker]
    assert "substituted" not in json.dumps(output, sort_keys=True)


def test_duplicate_json_keys_make_manifest_malformed(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    required = json.dumps(list(REQUIRED_PUBLIC_SURFACES))
    pins = json.dumps(dict(PINNED_SCANNER_VERSIONS))
    manifest.write_text(
        f'{{"schema_version":1,"schema_version":1,"required":{required},'
        f'"scanner_versions":{pins}}}',
        encoding="utf-8",
    )

    with pytest.raises(ReleaseGateError, match="public_surface_manifest_malformed"):
        load_public_surface_manifest(manifest)


def test_cli_unexpected_scan_failure_is_constant_secret_safe_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    root = Path(__file__).resolve().parents[1]
    raw_message = "unexpected " + "gh" + "p_" + "value-must-not-echo"
    monkeypatch.setattr(
        scanner_module,
        "scan_public_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(raw_message)),
    )

    exit_code = cli.main(
        [
            "release",
            "scan-secrets",
            "--all-history",
            "--hosted",
            "--artifacts",
            str(tmp_path / "dist"),
            "--repo",
            "example/mercury-tools",
            "--manifest",
            str(root / "docs/release/public-surface-manifest.json"),
            "--allowlist",
            str(root / "docs/release/secret-scan-allowlist.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["blockers"] == ["release_scan_failed"]
    assert raw_message not in json.dumps(payload, sort_keys=True)


class InteractiveFingerprintStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_known_fingerprints_load_only_from_interactive_stdin_or_untracked_file(
    tmp_path: Path,
) -> None:
    first = "d" * 64
    second = "e" * 64
    local_file = tmp_path / "fingerprints.txt"
    local_file.write_text(second + "\n", encoding="utf-8")
    stream = InteractiveFingerprintStream(first + "\n\n")

    fingerprints = load_known_secret_digests(
        paths=(local_file,),
        interactive=True,
        repo_root=Path(__file__).resolve().parents[1],
        stdin=stream,
    )

    assert fingerprints == (first, second)


def test_tracked_or_nonfingerprint_known_credential_input_is_rejected(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(ReleaseGateError, match="fingerprint_source_tracked"):
        load_known_secret_digests(
            paths=(root / "pyproject.toml",),
            interactive=False,
            repo_root=root,
        )

    invalid = tmp_path / "fingerprints.txt"
    invalid.write_text("source-secret-value\n", encoding="utf-8")
    with pytest.raises(ReleaseGateError, match="known_credential_fingerprint_invalid"):
        load_known_secret_digests(
            paths=(invalid,),
            interactive=False,
            repo_root=root,
        )


def test_fingerprint_file_tracked_in_another_repository_is_rejected(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    _git("init", "-b", "main", cwd=other_repo)
    tracked = other_repo / "fingerprints.txt"
    tracked.write_text("f" * 64 + "\n", encoding="utf-8")
    _git("add", "fingerprints.txt", cwd=other_repo)
    _git("commit", "-m", "tracked fingerprint", cwd=other_repo)

    with pytest.raises(ReleaseGateError, match="fingerprint_source_tracked"):
        load_known_secret_digests(
            paths=(tracked,),
            interactive=False,
            repo_root=root,
        )


def test_scanner_pins_are_immutable_after_validation() -> None:
    policy = SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS)
    manifest = PublicSurfaceManifest(
        required=REQUIRED_PUBLIC_SURFACES,
        scanner_versions=PINNED_SCANNER_VERSIONS,
    )

    with pytest.raises(TypeError, match="immutable_mapping"):
        policy.scanner_versions["gitleaks"] = "9.9.9"
    with pytest.raises(TypeError, match="immutable_mapping"):
        manifest.scanner_versions["trufflehog"] = "9.9.9"


def test_report_cannot_claim_passed_while_any_surface_is_blocked() -> None:
    blocked = build_blocked_report("scanner_missing:gitleaks")

    with pytest.raises(ValidationError, match="passing_report_has_blocked_surface"):
        blocked.model_copy(update={"status": GateStatus.PASSED, "blockers": ()})


def test_request_rejects_repository_url_with_embedded_credentials(
    scan_request: SecretScanRequest,
) -> None:
    with pytest.raises(ValidationError, match="repo_url_invalid"):
        scan_request.model_copy(
            update={"repo_url": "https://operator:credential@example.invalid/repo.git"}
        )
