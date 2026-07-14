"""Fail-closed verification for Mercury release trees, artifacts, and staging."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from mercury_tools.release.artifacts import (
    MANIFEST_FILE_NAME,
    ReleaseArtifact,
    ReleaseArtifactManifest,
    ScannerGate,
    _require_output_absent,
    _strict_json_loads,
    _write_candidate_tree,
    _zip_datetime,
    is_excluded_public_path,
    load_release_artifact_manifest,
    load_release_candidate,
    require_clean_worktree,
    require_task13_scanner_gate,
    source_tree_digest,
)
from mercury_tools.release.models import SecretScanPolicy
from mercury_tools.release.scanner import (
    ReleaseGateError,
    SubprocessCommandRunner,
    load_public_surface_manifest,
    scan_filesystem,
)

EXPECTED_LOCAL_MCP_TOOL_NAMES = frozenset(
    {
        "confirm_erp_write",
        "connector_status",
        "credential_status",
        "execute_erp_write",
        "get_document",
        "get_erp_action_schema",
        "get_erp_request_status",
        "import_erp_spec",
        "list_connector_drivers",
        "list_workspace_flows",
        "preview_erp_write",
        "retrieve_context_pack",
        "run_accounting_skill",
        "run_erp_read",
        "run_mercury_flow",
        "run_workspace_flow",
        "save_workspace_flow",
        "search_erp_actions",
        "search_knowledge",
    }
)
_MCP_TOOL_LIST_PROGRAM = (
    "import asyncio, json\n"
    "from mercury_tools.mcp.local_server import local_mcp\n"
    "tools = asyncio.run(local_mcp.list_tools())\n"
    "print(json.dumps(sorted(tool.name for tool in tools)))\n"
)
_MAX_COMMAND_OUTPUT = 64 * 1024


@dataclass(frozen=True)
class ReleaseVerification:
    passed: bool
    version: str
    commit_sha: str
    artifact_manifest_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_manifest_sha256": self.artifact_manifest_sha256,
            "commit_sha": self.commit_sha,
            "passed": self.passed,
            "version": self.version,
        }


@dataclass(frozen=True)
class PublicStaging:
    path: Path
    version: str
    commit_sha: str
    candidate_tree_digest: str
    staged_tree_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_tree_digest": self.candidate_tree_digest,
            "commit_sha": self.commit_sha,
            "path": str(self.path),
            "staged_tree_digest": self.staged_tree_digest,
            "version": self.version,
        }


def verify_release_tree(root: Path, *, version: str):
    """Verify source-tree contracts independent of artifact publication state."""

    candidate = load_release_candidate(root, version=version, require_clean=False)
    _require_immutable_plugin_ref(candidate.root, version)
    _require_exact_local_mcp_tools(candidate.root)
    return candidate


def verify_release(
    *,
    root: Path,
    version: str,
    artifacts: Path,
    scanner_gate: ScannerGate | None = None,
) -> ReleaseVerification:
    """Verify a clean release candidate and its exact deterministic artifact set."""

    candidate = load_release_candidate(root, version=version, require_clean=False)
    require_clean_worktree(candidate.root)
    _require_immutable_plugin_ref(candidate.root, version)
    _require_exact_local_mcp_tools(candidate.root)
    manifest = load_release_artifact_manifest(artifacts / MANIFEST_FILE_NAME)
    _require_manifest_binding(
        manifest,
        candidate.version,
        candidate.commit_sha,
        candidate.build_epoch,
    )
    _require_exact_artifact_set(artifacts, manifest)
    for artifact in manifest.artifacts:
        path = artifacts / artifact.file_name
        _require_artifact_digest(path, artifact)
        _require_normalized_archive(path, artifact.build_epoch)
    require_task13_scanner_gate(candidate.root, artifacts, scanner_gate=scanner_gate)
    return ReleaseVerification(
        passed=True,
        version=candidate.version,
        commit_sha=candidate.commit_sha,
        artifact_manifest_sha256=_sha256_file(artifacts / MANIFEST_FILE_NAME),
    )


def build_public_staging(
    *,
    root: Path,
    version: str,
    output: Path,
    artifacts: Path | None = None,
    scanner_gate: ScannerGate | None = None,
) -> PublicStaging:
    """Create a one-commit, history-free staging repository from ``git archive`` only."""

    candidate = load_release_candidate(root, version=version, require_clean=True)
    output = output.expanduser()
    _require_output_absent(output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc

    candidate_digest = source_tree_digest(candidate.entries)
    try:
        with tempfile.TemporaryDirectory(
            prefix=".mercury-public-staging-",
            dir=output.parent,
        ) as temporary:
            temporary_root = Path(temporary)
            stage = temporary_root / "stage"
            stage.mkdir()
            _write_candidate_tree(candidate.entries, stage)
            _initialize_history_free_repository(stage, candidate.build_epoch)
            _require_single_new_commit(stage, candidate.commit_sha)
            staged_candidate = load_release_candidate(stage, version=version, require_clean=True)
            staged_digest = source_tree_digest(staged_candidate.entries)
            if staged_digest != candidate_digest:
                raise ReleaseGateError("staging_tree_digest_mismatch")
            _require_staging_scanner_gate(
                candidate.root,
                stage,
                artifacts=artifacts,
                scanner_gate=scanner_gate,
            )
            os.replace(stage, output)
    except ReleaseGateError:
        raise
    except OSError as exc:
        raise ReleaseGateError("staging_build_failed") from exc
    return PublicStaging(
        path=output,
        version=version,
        commit_sha=candidate.commit_sha,
        candidate_tree_digest=candidate_digest,
        staged_tree_digest=candidate_digest,
    )


def _require_immutable_plugin_ref(root: Path, version: str) -> None:
    mcp_path = root / "plugins/mercury-finance/.mcp.json"
    plugin_path = root / "plugins/mercury-finance/.codex-plugin/plugin.json"
    try:
        mcp = _strict_json_loads(mcp_path.read_text(encoding="utf-8"))
        plugin = _strict_json_loads(plugin_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ReleaseGateError("mcp_manifest_invalid") from exc
    if not isinstance(mcp, dict) or not isinstance(plugin, dict):
        raise ReleaseGateError("mcp_manifest_invalid")
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or list(servers) != ["mercury-finance"]:
        raise ReleaseGateError("mcp_server_contract_invalid")
    server = servers["mercury-finance"]
    if not isinstance(server, dict):
        raise ReleaseGateError("mcp_server_contract_invalid")
    args = server.get("args")
    expected_ref = f"git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v{version}"
    if not isinstance(args, list) or len(args) < 2 or args[1] != expected_ref:
        raise ReleaseGateError("plugin_ref_not_immutable")
    if set(server) != {"args", "command", "cwd", "tool_timeout_sec"}:
        raise ReleaseGateError("mcp_server_contract_invalid")
    if server.get("command") != "uvx" or server.get("cwd") != ".":
        raise ReleaseGateError("mcp_server_contract_invalid")
    if server.get("tool_timeout_sec") != 900:
        raise ReleaseGateError("mcp_server_contract_invalid")
    if args != ["--from", expected_ref, "mercury", "mcp", "serve-local"]:
        raise ReleaseGateError("mcp_server_contract_invalid")
    plugin_version = plugin.get("version")
    if not isinstance(plugin_version, str) or not (
        plugin_version == version or plugin_version.startswith(f"{version}+")
    ):
        raise ReleaseGateError("plugin_version_mismatch")


def _require_exact_local_mcp_tools(root: Path) -> None:
    source_root = root / "src"
    if not source_root.is_dir():
        raise ReleaseGateError("local_mcp_contract_invalid")
    inherited_path = os.environ.get("PYTHONPATH", "")
    python_path = str(source_root)
    if inherited_path:
        python_path = f"{python_path}{os.pathsep}{inherited_path}"
    runner = SubprocessCommandRunner(
        environment={"PYTHONPATH": python_path},
        max_output_bytes=_MAX_COMMAND_OUTPUT,
        timeout_seconds=30.0,
    )
    result = runner.run((sys.executable, "-c", _MCP_TOOL_LIST_PROGRAM), cwd=root)
    if result.exit_code != 0:
        raise ReleaseGateError("local_mcp_contract_invalid")
    try:
        names = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ReleaseGateError("local_mcp_contract_invalid") from exc
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ReleaseGateError("local_mcp_contract_invalid")
    if (
        set(names) != EXPECTED_LOCAL_MCP_TOOL_NAMES
        or len(names) != len(EXPECTED_LOCAL_MCP_TOOL_NAMES)
    ):
        raise ReleaseGateError("local_mcp_contract_invalid")


def _require_manifest_binding(
    manifest: ReleaseArtifactManifest,
    version: str,
    commit_sha: str,
    build_epoch: int,
) -> None:
    if (
        manifest.version != version
        or manifest.commit_sha != commit_sha
        or manifest.build_epoch != build_epoch
    ):
        raise ReleaseGateError("artifact_binding_mismatch")
    for artifact in manifest.artifacts:
        if (
            artifact.version != version
            or artifact.commit_sha != commit_sha
            or artifact.build_epoch != build_epoch
        ):
            raise ReleaseGateError("artifact_binding_mismatch")


def _require_exact_artifact_set(artifacts: Path, manifest: ReleaseArtifactManifest) -> None:
    try:
        metadata = artifacts.lstat()
        entries = tuple(artifacts.iterdir())
    except OSError as exc:
        raise ReleaseGateError("artifact_set_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseGateError("artifact_set_invalid")
    if any(not entry.is_file() or entry.is_symlink() for entry in entries):
        raise ReleaseGateError("artifact_set_invalid")
    expected_names = {artifact.file_name for artifact in manifest.artifacts}
    expected_names.add(MANIFEST_FILE_NAME)
    actual_names = {entry.name for entry in entries}
    kinds = [artifact.kind for artifact in manifest.artifacts]
    if (
        len(expected_names) != 5
        or actual_names != expected_names
        or len(set(kinds)) != 4
        or set(kinds) != {"wheel", "sdist", "plugin", "source"}
    ):
        raise ReleaseGateError("artifact_set_invalid")
    for artifact in manifest.artifacts:
        if not _artifact_name_matches(artifact):
            raise ReleaseGateError("artifact_set_invalid")


def _artifact_name_matches(artifact: ReleaseArtifact) -> bool:
    if artifact.kind == "wheel":
        return (
            artifact.file_name.startswith(f"mercury_tools-{artifact.version}-")
            and artifact.file_name.endswith(".whl")
        )
    if artifact.kind == "sdist":
        return artifact.file_name == f"mercury_tools-{artifact.version}.tar.gz"
    if artifact.kind == "plugin":
        return artifact.file_name == f"mercury-finance-plugin-{artifact.version}.zip"
    return artifact.file_name == f"mercury-tools-{artifact.version}-source.tar.gz"


def _require_artifact_digest(path: Path, artifact: ReleaseArtifact) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ReleaseGateError("artifact_digest_mismatch") from exc
    if size != artifact.size or _sha256_file(path) != artifact.sha256:
        raise ReleaseGateError("artifact_digest_mismatch")


def _require_normalized_archive(path: Path, epoch: int) -> None:
    if path.suffix in {".whl", ".zip"}:
        _require_normalized_zip(path, epoch)
        return
    _require_normalized_tar_gz(path, epoch)


def _require_normalized_zip(path: Path, epoch: int) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if names != sorted(names) or len(names) != len({name.casefold() for name in names}):
                raise ReleaseGateError("artifact_archive_metadata_invalid")
            if archive.comment:
                raise ReleaseGateError("artifact_archive_metadata_invalid")
            for entry in entries:
                if not _safe_member_name(entry.filename) or is_excluded_public_path(entry.filename):
                    raise ReleaseGateError("artifact_archive_metadata_invalid")
                expected_mode = (
                    stat.S_IFDIR | 0o755
                    if entry.is_dir()
                    else stat.S_IFREG | 0o644
                )
                if (
                    entry.create_system != 3
                    or entry.compress_type != zipfile.ZIP_DEFLATED
                    or entry.extra
                    or (entry.external_attr >> 16) != expected_mode
                    or entry.date_time != _zip_datetime(epoch)
                ):
                    raise ReleaseGateError("artifact_archive_metadata_invalid")
    except ReleaseGateError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseGateError("artifact_archive_metadata_invalid") from exc


def _require_normalized_tar_gz(path: Path, epoch: int) -> None:
    try:
        payload = path.read_bytes()
        if (
            len(payload) < 10
            or payload[:2] != b"\x1f\x8b"
            or payload[3] != 0
            or payload[8] != 2
            or payload[9] != 255
        ):
            raise ReleaseGateError("artifact_archive_metadata_invalid")
        if int.from_bytes(payload[4:8], "little") != epoch:
            raise ReleaseGateError("artifact_archive_metadata_invalid")
        with tarfile.open(path, mode="r:gz") as archive:
            entries = archive.getmembers()
            names = [entry.name for entry in entries]
            if names != sorted(names) or len(names) != len({name.casefold() for name in names}):
                raise ReleaseGateError("artifact_archive_metadata_invalid")
            for entry in entries:
                if not _safe_member_name(entry.name) or is_excluded_public_path(entry.name):
                    raise ReleaseGateError("artifact_archive_metadata_invalid")
                if entry.isdir():
                    expected_mode = 0o755
                elif entry.isfile():
                    expected_mode = 0o644
                else:
                    raise ReleaseGateError("artifact_archive_metadata_invalid")
                if (
                    entry.uid != 0
                    or entry.gid != 0
                    or entry.uname
                    or entry.gname
                    or entry.mtime != epoch
                    or entry.mode != expected_mode
                    or entry.pax_headers
                    or getattr(entry, "sparse", None)
                ):
                    raise ReleaseGateError("artifact_archive_metadata_invalid")
    except ReleaseGateError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseGateError("artifact_archive_metadata_invalid") from exc


def _require_staging_scanner_gate(
    root: Path,
    stage: Path,
    *,
    artifacts: Path | None,
    scanner_gate: ScannerGate | None,
) -> None:
    if scanner_gate is not None:
        require_task13_scanner_gate(root, stage, scanner_gate=scanner_gate)
        return
    if artifacts is None:
        raise ReleaseGateError("release_scanner_context_unavailable")
    require_task13_scanner_gate(root, artifacts, scanner_gate=None)
    try:
        manifest = load_public_surface_manifest(root / "docs/release/public-surface-manifest.json")
        result = scan_filesystem(
            stage,
            SecretScanPolicy(scanner_versions=manifest.scanner_versions),
        )
    except ReleaseGateError:
        raise
    except Exception as exc:
        raise ReleaseGateError("release_scanner_gate_unavailable") from exc
    if result.blockers or result.findings:
        raise ReleaseGateError("release_scanner_gate_blocked")


def _initialize_history_free_repository(stage: Path, epoch: int) -> None:
    environment = {
        "GIT_AUTHOR_DATE": f"{epoch} +0000",
        "GIT_COMMITTER_DATE": f"{epoch} +0000",
    }
    runner = SubprocessCommandRunner(
        environment=environment,
        max_output_bytes=_MAX_COMMAND_OUTPUT,
        timeout_seconds=60.0,
    )
    commands = (
        ("git", "init", "--initial-branch=main"),
        ("git", "config", "user.name", "Mercury Release"),
        ("git", "config", "user.email", "release@mercury.invalid"),
        ("git", "add", "--all"),
        ("git", "commit", "--quiet", "-m", "Mercury public staging"),
    )
    for command in commands:
        result = runner.run(command, cwd=stage)
        if result.exit_code != 0:
            raise ReleaseGateError("staging_repository_init_failed")


def _require_single_new_commit(stage: Path, source_commit_sha: str) -> None:
    runner = SubprocessCommandRunner(
        max_output_bytes=_MAX_COMMAND_OUTPUT,
        timeout_seconds=60.0,
    )
    count = runner.run(("git", "rev-list", "--all", "--count"), cwd=stage)
    commits = runner.run(("git", "rev-list", "--all"), cwd=stage)
    if (
        count.exit_code != 0
        or commits.exit_code != 0
        or count.stdout.decode("ascii", errors="ignore").strip() != "1"
        or source_commit_sha in commits.stdout.decode("ascii", errors="ignore").split()
    ):
        raise ReleaseGateError("staging_history_invalid")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseGateError("artifact_digest_mismatch") from exc
    return digest.hexdigest()


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and "\\" not in name and ".." not in path.parts
