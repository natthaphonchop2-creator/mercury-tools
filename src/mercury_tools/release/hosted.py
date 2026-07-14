"""Injected adapters for scanning hosted public surfaces without retaining raw data."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from mercury_tools.release.models import (
    HostedSurfaceScanResult,
    SecretFinding,
    SecretScanPolicy,
)
from mercury_tools.release.scanner import (
    CommandRunner,
    _deduplicate_findings,
    _scan_bytes,
)

HOSTED_SCANNER_VERSION = "1.0.0"
HOSTED_PUBLIC_SURFACES = (
    "github_releases_and_assets",
    "github_actions_logs_artifacts_caches",
    "github_packages_pages_wiki",
    "marketplace_snapshot",
    "render_build_and_runtime_logs",
    "supabase_knowledge_and_storage",
    "public_mcp_responses",
)
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass(frozen=True, repr=False)
class HostedInspection:
    accessible: bool
    complete: bool
    chunks: Iterable[bytes] = field(repr=False)
    scanner_version: str | None
    exit_codes: tuple[int, ...]


class HostedSurfaceClient(Protocol):
    def inspect(self, surface: str) -> HostedInspection: ...


class GhApiHostedClient:
    """Execute an operator-reviewed complete route plan through ``gh api``."""

    def __init__(
        self,
        *,
        executable: Path,
        command_runner: CommandRunner,
        routes: Mapping[str, tuple[str, ...]],
    ) -> None:
        self._executable = executable
        self._command_runner = command_runner
        self._routes = dict(routes)

    def inspect(self, surface: str) -> HostedInspection:
        routes = self._routes.get(surface)
        if not routes:
            return HostedInspection(
                accessible=False,
                complete=False,
                chunks=(),
                scanner_version=HOSTED_SCANNER_VERSION,
                exit_codes=(),
            )
        chunks: list[bytes] = []
        exit_codes: list[int] = []
        for route in routes:
            result = self._command_runner.run(
                (str(self._executable), "api", "--paginate", route)
            )
            exit_codes.append(result.exit_code)
            chunks.extend((result.stdout, result.stderr))
        return HostedInspection(
            accessible=True,
            complete=all(exit_code == 0 for exit_code in exit_codes),
            chunks=tuple(chunks),
            scanner_version=HOSTED_SCANNER_VERSION,
            exit_codes=tuple(exit_codes),
        )


def scan_hosted_surface(
    surface: str,
    client: HostedSurfaceClient,
    policy: SecretScanPolicy,
) -> HostedSurfaceScanResult:
    try:
        inspection = client.inspect(surface)
    except Exception:
        return HostedSurfaceScanResult(
            surface=surface,
            scanner_version=None,
            blockers=(f"hosted_inspection_failed:{surface}",),
        )
    if not isinstance(inspection, HostedInspection):
        return HostedSurfaceScanResult(
            surface=surface,
            scanner_version=None,
            blockers=(f"hosted_inspection_malformed:{surface}",),
        )

    blockers: list[str] = []
    findings: list[SecretFinding] = []
    evidence_hash = hashlib.sha256()
    if not inspection.accessible:
        blockers.append(f"hosted_surface_inaccessible:{surface}")
    if inspection.accessible and not inspection.complete:
        blockers.append(f"hosted_surface_incomplete:{surface}")
    if inspection.scanner_version is None or not _VERSION_PATTERN.fullmatch(
        inspection.scanner_version
    ):
        blockers.append(f"hosted_scanner_version_unverifiable:{surface}")
        scanner_version = None
    else:
        scanner_version = inspection.scanner_version
        if scanner_version != HOSTED_SCANNER_VERSION:
            blockers.append(f"hosted_scanner_version_unpinned:{surface}")
    if any(exit_code != 0 for exit_code in inspection.exit_codes):
        blockers.append(f"hosted_command_failed:{surface}")

    tail = b""
    stream_ok = True
    try:
        for chunk in inspection.chunks:
            if not isinstance(chunk, bytes):
                stream_ok = False
                break
            evidence_hash.update(len(chunk).to_bytes(8, "big"))
            evidence_hash.update(chunk)
            window = tail + chunk
            findings.extend(_scan_bytes(window, f"hosted/{surface}", policy))
            tail = window[-1024:]
    except Exception:
        stream_ok = False
    if not stream_ok:
        blockers.append(f"raw_evidence_handling_failed:{surface}")

    evidence_hashes = (evidence_hash.hexdigest(),) if stream_ok else ()
    return HostedSurfaceScanResult(
        surface=surface,
        scanner_version=scanner_version,
        findings=_deduplicate_findings(findings),
        blockers=tuple(sorted(set(blockers))),
        evidence_hashes=evidence_hashes,
        exit_codes=inspection.exit_codes,
    )
