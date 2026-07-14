from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    HOSTED_PUBLIC_SURFACES,
    HOSTED_SCANNER_VERSION,
    GhApiHostedClient,
    HostedInspection,
    scan_hosted_surface,
)
from mercury_tools.release.models import (
    PINNED_SCANNER_VERSIONS,
    REQUIRED_PUBLIC_SURFACES,
    ArtifactKind,
    ArtifactScanResult,
    GitRepositoryScanResult,
    HostedSurface,
    PublicSurfaceManifest,
    SecretScanAllowlist,
    SecretScanPolicy,
    SecretScanRequest,
)
from mercury_tools.release.scanner import CommandResult, scan_public_release


class FakeHostedClient:
    def __init__(self, inspection: HostedInspection | Exception) -> None:
        self.inspection = inspection
        self.calls: list[str] = []

    def inspect(self, surface: str) -> HostedInspection:
        self.calls.append(surface)
        if isinstance(self.inspection, Exception):
            raise self.inspection
        return self.inspection


class FakeCommandRunner:
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
        executable = Path(argv[0]).name
        if executable in self.responses and argv[1:] in {("version",), ("--version",)}:
            return self.responses[executable]
        return self.responses[argv[-1]]


def _request(tmp_path: Path, **updates: object) -> SecretScanRequest:
    request = SecretScanRequest(
        repo="example/mercury-tools",
        artifacts=tmp_path / "dist",
        all_history=True,
        hosted=True,
        manifest=PublicSurfaceManifest(
            required=REQUIRED_PUBLIC_SURFACES,
            scanner_versions=PINNED_SCANNER_VERSIONS,
        ),
        allowlist=SecretScanAllowlist(entries=()),
        policy=SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS),
    )
    return request.model_copy(update=updates)


def _complete_inspection(*chunks: bytes) -> HostedInspection:
    return HostedInspection(
        accessible=True,
        complete=True,
        chunks=chunks or (b"safe hosted fixture",),
        scanner_version=HOSTED_SCANNER_VERSION,
        exit_codes=(0,),
    )


def _hosted_clients() -> dict[str, FakeHostedClient]:
    return {
        surface: FakeHostedClient(_complete_inspection())
        for surface in HOSTED_PUBLIC_SURFACES
    }


def test_inaccessible_hosted_surface_blocks_release(tmp_path: Path) -> None:
    request = _request(
        tmp_path,
        hosted_surfaces=(HostedSurface(name="render_logs", accessible=False),),
    )

    report = scan_public_release(request)

    assert report.passed is False
    assert "hosted_surface_inaccessible:render_logs" in report.blockers


def test_hosted_stream_detects_secret_without_returning_raw_payload() -> None:
    raw_value = "xox" + "b-" + "A1b2C3d4-E5f6G7h8-I9j0K1l2"
    client = FakeHostedClient(_complete_inspection(raw_value.encode()))
    policy = SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS)

    result = scan_hosted_surface("public_mcp_responses", client, policy)
    serialized = result.model_dump_json()

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert raw_value not in serialized
    assert result.evidence_hashes


@pytest.mark.parametrize(
    ("inspection", "blocker"),
    [
        (
            HostedInspection(
                accessible=False,
                complete=False,
                chunks=(),
                scanner_version=HOSTED_SCANNER_VERSION,
                exit_codes=(),
            ),
            "hosted_surface_inaccessible:render_build_and_runtime_logs",
        ),
        (
            HostedInspection(
                accessible=True,
                complete=False,
                chunks=(b"partial",),
                scanner_version=HOSTED_SCANNER_VERSION,
                exit_codes=(0,),
            ),
            "hosted_surface_incomplete:render_build_and_runtime_logs",
        ),
        (
            HostedInspection(
                accessible=True,
                complete=True,
                chunks=(b"safe",),
                scanner_version="9.9.9",
                exit_codes=(0,),
            ),
            "hosted_scanner_version_unpinned:render_build_and_runtime_logs",
        ),
        (
            HostedInspection(
                accessible=True,
                complete=True,
                chunks=(b"safe",),
                scanner_version=HOSTED_SCANNER_VERSION,
                exit_codes=(1,),
            ),
            "hosted_command_failed:render_build_and_runtime_logs",
        ),
    ],
)
def test_hosted_unavailable_incomplete_unpinned_or_failed_is_blocking(
    inspection: HostedInspection,
    blocker: str,
) -> None:
    result = scan_hosted_surface(
        "render_build_and_runtime_logs",
        FakeHostedClient(inspection),
        SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS),
    )

    assert blocker in result.blockers


def test_hosted_client_exception_is_constant_code_only() -> None:
    raw_message = "provider payload must not survive"
    client = FakeHostedClient(RuntimeError(raw_message))

    result = scan_hosted_surface(
        "supabase_knowledge_and_storage",
        client,
        SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS),
    )

    assert result.blockers == ("hosted_inspection_failed:supabase_knowledge_and_storage",)
    assert raw_message not in result.model_dump_json()


def test_malformed_hosted_inspection_is_a_constant_blocker() -> None:
    class MalformedClient:
        def inspect(self, _surface: str) -> object:
            return object()

    result = scan_hosted_surface(
        "public_mcp_responses",
        MalformedClient(),  # type: ignore[arg-type]
        SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS),
    )

    assert result.blockers == ("hosted_inspection_malformed:public_mcp_responses",)


def test_nonbyte_hosted_chunk_blocks_raw_evidence_handling() -> None:
    inspection = HostedInspection(
        accessible=True,
        complete=True,
        chunks=("raw text",),  # type: ignore[arg-type]
        scanner_version=HOSTED_SCANNER_VERSION,
        exit_codes=(0,),
    )

    result = scan_hosted_surface(
        "public_mcp_responses",
        FakeHostedClient(inspection),
        SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS),
    )

    assert result.blockers == ("raw_evidence_handling_failed:public_mcp_responses",)


def test_gh_api_adapter_uses_injected_commands_and_never_returns_raw() -> None:
    raw_value = "gh" + "p_" + "E1f2G3h4I5j6K7l8M9n0P1q2R3s4"
    runner = FakeCommandRunner(
        {
            "releases": CommandResult(exit_code=0, stdout=raw_value.encode(), stderr=b""),
            "assets": CommandResult(exit_code=0, stdout=b"safe", stderr=b""),
        }
    )
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=runner,
        routes={"github_releases_and_assets": ("releases", "assets")},
    )

    inspection = client.inspect("github_releases_and_assets")
    result = scan_hosted_surface(
        "github_releases_and_assets",
        client,
        SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS),
    )

    assert inspection.complete is True
    assert runner.calls == [
        ("/mock/gh", "api", "--paginate", "releases"),
        ("/mock/gh", "api", "--paginate", "assets"),
        ("/mock/gh", "api", "--paginate", "releases"),
        ("/mock/gh", "api", "--paginate", "assets"),
    ]
    assert raw_value not in result.model_dump_json()
    assert any(finding.rule == "provider_token" for finding in result.findings)


def test_all_ten_surfaces_are_required_for_a_passing_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        scanner_module,
        "scan_git_repository",
        lambda *_args, **_kwargs: GitRepositoryScanResult(
            evidence_hashes=("a" * 64,),
            exit_codes=(0,),
            object_count=4,
            blob_count=1,
        ),
    )
    monkeypatch.setattr(
        scanner_module,
        "scan_artifacts",
        lambda *_args, **_kwargs: ArtifactScanResult(kinds=tuple(ArtifactKind)),
    )
    runner = FakeCommandRunner(
        {
            "gitleaks": CommandResult(
                exit_code=0,
                stdout=f"gitleaks {PINNED_SCANNER_VERSIONS['gitleaks']}".encode(),
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
        _request(tmp_path),
        command_runner=runner,
        hosted_clients=_hosted_clients(),
    )

    assert report.passed is True
    assert tuple(surface.surface for surface in report.surfaces) == REQUIRED_PUBLIC_SURFACES
    assert all(surface.status == "passed" for surface in report.surfaces)


def test_missing_required_hosted_client_blocks_full_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        scanner_module,
        "scan_git_repository",
        lambda *_args, **_kwargs: GitRepositoryScanResult(),
    )
    monkeypatch.setattr(
        scanner_module,
        "scan_artifacts",
        lambda *_args, **_kwargs: ArtifactScanResult(kinds=tuple(ArtifactKind)),
    )
    clients = _hosted_clients()
    clients.pop("public_mcp_responses")
    runner = FakeCommandRunner(
        {
            "gitleaks": CommandResult(
                exit_code=0,
                stdout=f"gitleaks {PINNED_SCANNER_VERSIONS['gitleaks']}".encode(),
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
        _request(tmp_path),
        command_runner=runner,
        hosted_clients=clients,
    )
    serialized = json.dumps(report.public_dict(), sort_keys=True)

    assert report.passed is False
    assert "hosted_client_missing:public_mcp_responses" in report.blockers
    assert "/mock" not in serialized
