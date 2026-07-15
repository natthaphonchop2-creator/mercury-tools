from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import textwrap
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
    ScannerVersionAttestation,
    SecretScanAllowlist,
    SecretScanPolicy,
    SecretScanReport,
    SecretScanRequest,
    SurfaceAttestation,
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
    verify_trufflehog_report,
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
        _write_regular_zip_member(archive, name, data)


def _write_regular_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.compress_type = archive.compression
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, data)


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


def _publish_remote(source: Path, remote: Path) -> Path:
    _git("clone", "--bare", str(source), str(remote))
    head = _git("rev-parse", "HEAD", cwd=source)
    _git("--git-dir", str(remote), "update-ref", "refs/pull/1/head", head)
    return remote


def _make_historical_alias_remote(tmp_path: Path) -> Path:
    source = tmp_path / "alias-source"
    remote = tmp_path / "alias-remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / ".env").write_text("safe fixture\n", encoding="utf-8")
    _git("add", ".env", cwd=source)
    _git("commit", "-m", "historical forbidden alias", cwd=source)
    _git("mv", ".env", "safe.txt", cwd=source)
    _git("commit", "-m", "rename alias", cwd=source)
    return _publish_remote(source, remote)


def _make_multibranch_remote(tmp_path: Path) -> Path:
    source = tmp_path / "branch-source"
    remote = tmp_path / "branch-remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / "README.md").write_text("main checkout\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-m", "main", cwd=source)
    _git("branch", "aaa", cwd=source)
    return _publish_remote(source, remote)


def _make_symlink_remote(tmp_path: Path) -> Path:
    source = tmp_path / "symlink-source"
    remote = tmp_path / "symlink-remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / "README.md").write_text("safe\n", encoding="utf-8")
    (source / "linked").symlink_to("README.md")
    _git("add", "README.md", "linked", cwd=source)
    _git("commit", "-m", "symlink", cwd=source)
    (source / "linked").unlink()
    _git("add", "-u", cwd=source)
    _git("commit", "-m", "remove symlink", cwd=source)
    return _publish_remote(source, remote)


def _make_gitlink_remote(tmp_path: Path) -> Path:
    source = tmp_path / "gitlink-source"
    remote = tmp_path / "gitlink-remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / "README.md").write_text("safe\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    commit = _git("rev-parse", "HEAD", cwd=source)
    _git("update-index", "--add", "--cacheinfo", f"160000,{commit},vendor", cwd=source)
    _git("commit", "-m", "gitlink", cwd=source)
    _git("rm", "--cached", "vendor", cwd=source)
    _git("commit", "-m", "remove gitlink", cwd=source)
    return _publish_remote(source, remote)


def _write_fake_scanner(path: Path, *, exit_code: int) -> None:
    if path.name == "gitleaks":
        finding = json.dumps(
            [{"File": "README.md", "RuleID": "generic", "Secret": "redacted"}]
        )
        scan_output = f"print({finding!r})"
    else:
        finding = json.dumps(
            {"SourceMetadata": {"Data": {"Git": {"file": "README.md"}}}, "Raw": "redacted"}
        )
        scan_output = f"print({finding!r})"
    path.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import pathlib
            import sys

            pathlib.Path(__file__).with_suffix('.args').write_text(
                '\\n'.join(sys.argv[1:]), encoding='utf-8'
            )
            if sys.argv[1:] in (['version'], ['--version']):
                print('{path.name} {PINNED_SCANNER_VERSIONS[path.name]}')
                raise SystemExit(0)
            {scan_output}
            print('partial scan detail that must not survive', file=sys.stderr)
            raise SystemExit({exit_code})
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o700)


def _valid_pass_report() -> SecretScanReport:
    timestamp = datetime(2026, 7, 14, tzinfo=UTC)
    scanners = tuple(
        ScannerVersionAttestation(
            scanner=name,
            version=version,
            status=GateStatus.PASSED,
            evidence_sha256=hashlib.sha256(name.encode()).hexdigest(),
            exit_code=0,
        )
        for name, version in PINNED_SCANNER_VERSIONS.items()
    )
    surfaces = tuple(
        SurfaceAttestation(
            surface=surface,
            status=GateStatus.PASSED,
            scanner_versions=(
                tuple(sorted((*PINNED_SCANNER_VERSIONS.values(), "1.0.0")))
                if surface in {"git_all_refs", "github_pull_request_refs"}
                else ("1.0.0",)
            ),
            started_at=timestamp,
            completed_at=timestamp,
            finding_count=0,
            evidence_hashes=(hashlib.sha256(surface.encode()).hexdigest(),),
            exit_codes=(0,),
        )
        for surface in REQUIRED_PUBLIC_SURFACES
    )
    return SecretScanReport(
        status=GateStatus.PASSED,
        started_at=timestamp,
        completed_at=timestamp,
        scanner_versions=scanners,
        surfaces=surfaces,
    )


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


def test_tracked_manifest_and_allowlist_are_secret_free_and_exactly_reviewed() -> None:
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
    reviewed = SecretScanAllowlist.model_validate(allowlist)
    assert len(reviewed.entries) == 19
    assert {entry.rule.value for entry in reviewed.entries} == {"scanner_finding"}
    assert {entry.reviewer_role.value for entry in reviewed.entries} == {
        "security_reviewer"
    }
    assert {entry.expires_at for entry in reviewed.entries} == {
        datetime(2026, 8, 15, tzinfo=UTC)
    }
    assert sum(
        entry.classification == AllowlistClassification.DOCUMENTATION_PLACEHOLDER
        for entry in reviewed.entries
    ) == 1
    assert all(
        entry.file.startswith("tests/")
        or entry.file
        == "docs/superpowers/plans/2026-07-13-mercury-v0.2.1-public-endpoint-validation.md"
        for entry in reviewed.entries
    )


@pytest.mark.parametrize(
    "scanner_versions",
    [
        {"gitleaks": PINNED_SCANNER_VERSIONS["gitleaks"]},
        {**PINNED_SCANNER_VERSIONS, "other": "1.2.3"},
        {**PINNED_SCANNER_VERSIONS, "gitleaks": "latest"},
        {"gitleaks": "9.9.9", "trufflehog": "1.2.3"},
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


def test_long_high_entropy_hex_and_untrusted_sha256_field_are_detected(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    credential = "0123456789abcdef" * 8
    digest = "fedcba9876543210" * 4
    (tmp_path / "opaque.txt").write_text(credential, encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifact": {"sha256": digest}}, indent=2),
        encoding="utf-8",
    )

    result = scan_filesystem(tmp_path, scan_request.policy)

    high_entropy = [finding for finding in result.findings if finding.rule == "high_entropy"]
    assert len(high_entropy) == 2
    assert credential not in result.model_dump_json()
    assert digest not in result.model_dump_json()


@pytest.mark.parametrize(
    ("updates", "blocker"),
    [
        ({"max_filesystem_entries": 1}, "filesystem_entry_limit"),
        ({"max_filesystem_bytes": 7}, "filesystem_aggregate_too_large"),
    ],
)
def test_filesystem_walk_has_entry_and_aggregate_byte_budgets(
    tmp_path: Path,
    scan_request: SecretScanRequest,
    updates: dict[str, int],
    blocker: str,
) -> None:
    (tmp_path / "one.txt").write_text("1234", encoding="utf-8")
    (tmp_path / "two.txt").write_text("5678", encoding="utf-8")
    policy = scan_request.policy.model_copy(update=updates)

    result = scan_filesystem(tmp_path, policy)

    assert blocker in result.blockers


def test_filesystem_root_symlink_is_rejected(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "safe.txt").write_text("safe", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)

    result = scan_filesystem(linked, scan_request.policy)

    assert result.blockers == ("filesystem_symlink",)


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


def test_high_entropy_hex_inside_artifact_is_detected(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    credential = ("0123456789abcdef" * 8).encode()
    _write_zip(artifacts / "mercury-finance-plugin.zip", "plugin/opaque.txt", credential)

    result = scan_artifacts(artifacts, scan_request.policy)

    assert any(finding.rule == "high_entropy" for finding in result.findings)
    assert credential.decode() not in result.model_dump_json()


@pytest.mark.parametrize("archive_format", ["zip", "tar"])
def test_archive_rejects_duplicate_canonical_member_aliases_before_scanning(
    tmp_path: Path,
    scan_request: SecretScanRequest,
    archive_format: str,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    target = artifacts / "mercury-tools-source.zip"
    if archive_format == "zip":
        with zipfile.ZipFile(target, "w") as archive:
            _write_regular_zip_member(archive, "same/path.txt", b"safe")
            _write_regular_zip_member(archive, "same//path.txt", b"safe alias")
    else:
        target.unlink()
        target = artifacts / "mercury-tools-source.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            for name, data in (
                ("same/path.txt", b"safe"),
                ("same//path.txt", b"safe alias"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    result = scan_artifacts(artifacts, scan_request.policy)

    assert "artifact_duplicate_member:source" in result.blockers


def test_archive_rejects_unicode_normalization_member_aliases(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    with zipfile.ZipFile(artifacts / "mercury-tools-source.zip", "w") as archive:
        _write_regular_zip_member(archive, "caf\u00e9.txt", b"safe")
        _write_regular_zip_member(archive, "cafe\u0301.txt", b"safe alias")

    result = scan_artifacts(artifacts, scan_request.policy)

    assert "artifact_duplicate_member:source" in result.blockers


def test_archive_cumulative_uncompressed_budget_blocks_before_member_reads(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    with zipfile.ZipFile(artifacts / "mercury-tools-source.zip", "w") as archive:
        _write_regular_zip_member(archive, "one.txt", b"a" * 6)
        _write_regular_zip_member(archive, "two.txt", b"b" * 6)
    policy = scan_request.policy.model_copy(
        update={"max_archive_member_bytes": 8, "max_archive_uncompressed_bytes": 10}
    )

    result = scan_artifacts(artifacts, policy)

    assert "artifact_uncompressed_limit:source" in result.blockers


def test_oversized_artifact_metadata_is_checked_before_hashing(
    tmp_path: Path,
    scan_request: SecretScanRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    oversized = artifacts / "mercury-tools-source.zip"
    policy = scan_request.policy.model_copy(update={"max_archive_bytes": 1})
    hashed: list[Path] = []
    original_hash = scanner_module._hash_file

    def record_hash(path: Path, *args: object, **kwargs: object) -> str:
        hashed.append(path)
        return original_hash(path, *args, **kwargs)

    monkeypatch.setattr(scanner_module, "_hash_file", record_hash)

    result = scan_artifacts(artifacts, policy)

    assert "artifact_too_large:source" in result.blockers
    assert oversized not in hashed


@pytest.mark.parametrize(
    ("updates", "blocker"),
    [
        ({"max_artifact_entries": 1}, "artifact_entry_limit"),
        ({"max_artifact_total_bytes": 10}, "artifact_aggregate_too_large"),
    ],
)
def test_artifact_walk_has_entry_and_aggregate_byte_budgets(
    tmp_path: Path,
    scan_request: SecretScanRequest,
    updates: dict[str, int],
    blocker: str,
) -> None:
    artifacts = tmp_path / "dist"
    _write_complete_artifact_set(artifacts)
    policy = scan_request.policy.model_copy(update=updates)

    result = scan_artifacts(artifacts, policy)

    assert blocker in result.blockers


def test_artifact_root_symlink_is_rejected(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    target = tmp_path / "dist"
    _write_complete_artifact_set(target)
    linked = tmp_path / "linked-dist"
    linked.symlink_to(target, target_is_directory=True)

    result = scan_artifacts(linked, scan_request.policy)

    assert "artifact_symlink" in result.blockers
    assert result.kinds == ()


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
    assert any(
        call[1:3] == ("rev-list", "--objects") and "--stdin" in call
        for call in runner.calls
    )
    assert any(call[1:3] == ("ls-tree", "-z") for call in runner.calls)
    assert any(
        Path(call[0]).name == "gitleaks" and "--log-opts=--all" in call
        for call in runner.calls
    )
    assert any(
        Path(call[0]).name == "trufflehog"
        and "--no-update" in call
        and "--no-verification" in call
        for call in runner.calls
    )


def test_release_scan_requires_an_explicit_trusted_git_runner(
    scan_request: SecretScanRequest,
) -> None:
    with pytest.raises(ReleaseGateError, match="^release_git_runner_required$"):
        scan_public_release(scan_request, require_trusted_git_runner=True)


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


def test_git_tree_inventory_detects_every_historical_path_alias(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_historical_alias_remote(tmp_path)

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=GitScannerRunner(),
    )

    assert any(finding.rule == "forbidden_path" for finding in result.findings)


def test_git_checkout_uses_verified_remote_default_branch(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_multibranch_remote(tmp_path)
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
    checkout = next(call for call in runner.calls if call[1:3] == ("checkout", "--force"))
    assert checkout[-1] == "refs/remotes/origin/main"
    assert any(call[1:4] == ("ls-remote", "--symref", "origin") for call in runner.calls)


def test_missing_symbolic_remote_default_branch_blocks(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_remote(tmp_path)

    class MissingDefaultRunner(GitScannerRunner):
        def run(
            self,
            argv: tuple[str, ...],
            *,
            cwd: Path | None = None,
            input_bytes: bytes | None = None,
        ) -> CommandResult:
            if argv[1:] == ("ls-remote", "--symref", "origin", "HEAD"):
                return CommandResult(exit_code=0, stdout=b"a" * 40 + b"\tHEAD\n", stderr=b"")
            return super().run(argv, cwd=cwd, input_bytes=input_bytes)

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=MissingDefaultRunner(),
    )

    assert "git_default_branch_unverifiable" in result.blockers


def test_git_tree_inventory_rejects_historical_symlink_mode(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_symlink_remote(tmp_path)

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=GitScannerRunner(),
    )

    assert "git_tree_symlink" in result.blockers


def test_git_tree_inventory_rejects_historical_gitlink_mode(
    tmp_path: Path,
    scan_request: SecretScanRequest,
) -> None:
    remote = _make_gitlink_remote(tmp_path)

    result = scan_git_repository(
        str(remote),
        scan_request.policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=GitScannerRunner(),
    )

    assert "git_tree_gitlink" in result.blockers


@pytest.mark.parametrize(
    ("limit_name", "limit", "blocker"),
    [
        ("max_git_commits", 1, "git_commit_limit"),
        ("max_git_tree_entries", 1, "git_tree_entry_limit"),
    ],
)
def test_git_commit_and_tree_enumeration_are_bounded(
    tmp_path: Path,
    scan_request: SecretScanRequest,
    limit_name: str,
    limit: int,
    blocker: str,
) -> None:
    remote = _make_remote(tmp_path, historical_value="safe fixture")
    policy = scan_request.policy.model_copy(update={limit_name: limit})

    result = scan_git_repository(
        str(remote),
        policy,
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=GitScannerRunner(),
    )

    assert blocker in result.blockers


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
        gitleaks=CommandResult(exit_code=0, stdout=gitleaks_output, stderr=b""),
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


def test_trufflehog_fingerprint_ignores_runtime_metadata_but_binds_location_and_raw() -> None:
    raw_value = "".join(("https://", "user:", "password", "@example.test"))

    def finding(*, source_id: int, raw: str = raw_value, commit: str = "a" * 40):
        item = {
            "SourceID": source_id,
            "DetectorName": "URI",
            "DecoderName": "PLAIN",
            "Verified": False,
            "Raw": raw,
            "SourceMetadata": {
                "Data": {
                    "Git": {
                        "file": "tests/test_fixture.py",
                        "line": 12,
                        "commit": commit,
                        "repository": f"file:///tmp/runtime-{source_id}",
                        "timestamp": f"2026-07-{source_id:02d}T00:00:00Z",
                    }
                }
            },
        }
        parsed, malformed = scanner_module._parse_trufflehog_findings(
            json.dumps(item).encode() + b"\n"
        )
        assert malformed is False
        return parsed[0]

    first = finding(source_id=1)
    second = finding(source_id=2)

    assert first == second
    assert finding(source_id=1, raw=raw_value + "/changed") != first
    assert finding(source_id=1, commit="b" * 40) != first


def test_trufflehog_report_gate_allows_only_exact_reviewed_fingerprint() -> None:
    raw_value = "".join(("https://", "user:", "password", "@example.test"))
    item = {
        "DetectorName": "URI",
        "DecoderName": "PLAIN",
        "Verified": False,
        "Raw": raw_value,
        "SourceMetadata": {
            "Data": {
                "Git": {
                    "file": "tests/test_fixture.py",
                    "line": 12,
                    "commit": "a" * 40,
                }
            }
        },
    }
    output = json.dumps(item).encode() + b"\n"
    findings, malformed = scanner_module._parse_trufflehog_findings(output)
    assert malformed is False
    entry = AllowlistEntry(
        classification=AllowlistClassification.NON_SECRET_FIXTURE,
        file="tests/test_fixture.py",
        rule="scanner_finding",
        digest=findings[0].evidence_sha256,
        reviewer_role="security_reviewer",
        expires_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    allowlist = SecretScanAllowlist(entries=(entry,))

    assert verify_trufflehog_report(
        output,
        allowlist,
        at=datetime(2026, 7, 15, tzinfo=UTC),
    ) == 1

    item["Raw"] = raw_value.replace("password", "different")
    with pytest.raises(ReleaseGateError, match="^scanner_findings_unresolved:trufflehog$"):
        verify_trufflehog_report(
            json.dumps(item).encode() + b"\n",
            allowlist,
            at=datetime(2026, 7, 15, tzinfo=UTC),
        )


@pytest.mark.parametrize("failed_scanner", ["gitleaks", "trufflehog"])
def test_real_subprocess_nonzero_scanner_exit_is_unsuppressible_after_allowlist(
    tmp_path: Path,
    failed_scanner: str,
) -> None:
    binaries = {name: tmp_path / name for name in PINNED_SCANNER_VERSIONS}
    for name, path in binaries.items():
        _write_fake_scanner(path, exit_code=9 if name == failed_scanner else 0)
    evidence_hashes: list[str] = []
    exit_codes: list[int] = []

    findings, blockers = scanner_module._run_history_scanners(
        SubprocessCommandRunner(),
        tmp_path,
        binaries,
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
    )

    assert f"scanner_command_failed:{failed_scanner}:history" in blockers
    assert 9 in exit_codes
    allowlist = SecretScanAllowlist(
        entries=tuple(
            AllowlistEntry(
                classification=AllowlistClassification.NON_SECRET_FIXTURE,
                file=finding.relative_path,
                rule=finding.rule,
                digest=finding.evidence_sha256,
                reviewer_role="security_reviewer",
                expires_at=datetime(2026, 7, 15, tzinfo=UTC),
            )
            for finding in findings
        )
    )
    attestation = scanner_module._surface_attestation(
        "git_all_refs",
        findings=tuple(findings),
        blockers=tuple(blockers),
        evidence_hashes=tuple(evidence_hashes),
        exit_codes=tuple(exit_codes),
        scanner_versions=tuple(PINNED_SCANNER_VERSIONS.values()),
        allowlist=allowlist,
        at=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert attestation.finding_count == 0
    assert attestation.status is GateStatus.BLOCKED
    assert "partial scan detail" not in attestation.model_dump_json()
    gitleaks_args = binaries["gitleaks"].with_suffix(".args").read_text(encoding="utf-8")
    assert "--exit-code=0" in gitleaks_args


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


def test_report_requires_exactly_one_attestation_for_each_compiled_scanner() -> None:
    report = _valid_pass_report()

    with pytest.raises(ValidationError, match="report_scanner_corpus_invalid"):
        report.model_copy(update={"scanner_versions": report.scanner_versions[:1]})
    with pytest.raises(ValidationError, match="report_scanner_corpus_invalid"):
        report.model_copy(
            update={"scanner_versions": (*report.scanner_versions, report.scanner_versions[0])}
        )


def test_report_rejects_unpinned_or_nonzero_passing_scanner_attestation() -> None:
    report = _valid_pass_report()

    with pytest.raises(ValidationError, match="scanner_attestation_inconsistent"):
        report.model_copy(
            update={
                "scanner_versions": (
                    report.scanner_versions[0].model_copy(update={"version": "9.9.9"}),
                    report.scanner_versions[1],
                )
            }
        )
    with pytest.raises(ValidationError, match="scanner_attestation_inconsistent"):
        report.model_copy(
            update={
                "scanner_versions": (
                    report.scanner_versions[0].model_copy(update={"exit_code": 9}),
                    report.scanner_versions[1],
                )
            }
        )


def test_surface_attestation_reconciles_status_counts_codes_and_exit_codes() -> None:
    report = _valid_pass_report()
    surface = report.surfaces[0]

    with pytest.raises(ValidationError, match="surface_finding_evidence_inconsistent"):
        surface.model_copy(update={"finding_count": 1})
    with pytest.raises(ValidationError, match="surface_exit_codes_inconsistent"):
        surface.model_copy(update={"exit_codes": (3,)})
    with pytest.raises(ValidationError, match="surface_scanner_versions_invalid"):
        surface.model_copy(update={"scanner_versions": ("9.9.9",)})


def test_report_reconciles_top_level_blocker_and_finding_codes() -> None:
    report = _valid_pass_report()
    blocked_surface = report.surfaces[0].model_copy(
        update={
            "status": GateStatus.BLOCKED,
            "blocker_codes": ("command_failed:git_checkout",),
        }
    )
    finding_surface = report.surfaces[1].model_copy(
        update={
            "status": GateStatus.BLOCKED,
            "finding_count": 1,
            "finding_codes": ("finding:provider_token",),
        }
    )

    with pytest.raises(ValidationError, match="report_blockers_inconsistent"):
        report.model_copy(
            update={
                "status": GateStatus.BLOCKED,
                "surfaces": (blocked_surface, *report.surfaces[1:]),
            }
        )
    with pytest.raises(ValidationError, match="report_findings_inconsistent"):
        report.model_copy(
            update={
                "status": GateStatus.BLOCKED,
                "surfaces": (
                    report.surfaces[0],
                    finding_surface,
                    *report.surfaces[2:],
                ),
            }
        )


def test_blocked_report_cannot_hide_all_failure_evidence() -> None:
    report = _valid_pass_report()

    with pytest.raises(ValidationError, match="report_status_inconsistent"):
        report.model_copy(update={"status": GateStatus.BLOCKED})


def test_report_writer_uses_unique_exclusive_temp_files_under_concurrency(
    tmp_path: Path,
) -> None:
    output = tmp_path / "secret-scan.json"
    payloads = [{"writer": index} for index in range(12)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        list(
            executor.map(
                lambda payload: scanner_module.write_secret_scan_report(output, payload),
                payloads,
            )
        )

    assert json.loads(output.read_text(encoding="utf-8")) in payloads
    assert not list(tmp_path.glob(".secret-scan.json.tmp-*"))


def test_report_writer_rejects_an_oversized_payload_before_creating_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "secret-scan.json"

    with pytest.raises(OSError, match="report_too_large"):
        scanner_module.write_secret_scan_report(
            output,
            {"payload": "too large"},
            max_bytes=4,
        )

    assert not os.path.lexists(output)


def test_report_writer_supports_a_verified_symlinked_parent_directory(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    output = linked_parent / "secret-scan.json"

    scanner_module.write_secret_scan_report(output, {"passed": False})

    assert json.loads(output.read_text(encoding="utf-8")) == {"passed": False}
    assert not list(real_parent.glob(".secret-scan.json.tmp-*"))


def test_cli_report_output_symlink_is_replaced_without_touching_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "target.json"
    target.write_text("target sentinel", encoding="utf-8")
    output = tmp_path / "secret-scan.json"
    output.symlink_to(target)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

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
    assert target.read_text(encoding="utf-8") == "target sentinel"
    assert output.is_file() and not output.is_symlink()
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_cli_invalidates_stale_report_before_scan_and_removes_output_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "secret-scan.json"
    output.write_text('{"passed": true}', encoding="utf-8")

    def fake_scan(*_args: object, **_kwargs: object) -> SecretScanReport:
        assert not os.path.lexists(output)
        return _valid_pass_report()

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("redacted failure")

    monkeypatch.setattr(scanner_module, "scan_public_release", fake_scan)
    monkeypatch.setattr(os, "replace", fail_replace)

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
    assert payload["blockers"] == ["report_write_failed"]
    assert not os.path.lexists(output)


def test_request_rejects_repository_url_with_embedded_credentials(
    scan_request: SecretScanRequest,
) -> None:
    with pytest.raises(ValidationError, match="repo_url_invalid"):
        scan_request.model_copy(
            update={"repo_url": "https://operator:credential@example.invalid/repo.git"}
        )
