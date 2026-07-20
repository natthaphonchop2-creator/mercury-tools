"""Fail-closed verification for Mercury release trees, artifacts, and staging."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile
from dataclasses import dataclass
from pathlib import Path

from mercury_tools.release.artifacts import (
    MANIFEST_FILE_NAME,
    ReleaseArtifact,
    ReleaseArtifactManifest,
    ReleaseCandidate,
    _build_artifact_set,
    _decode_stored_gzip,
    _ensure_candidate_unchanged,
    _prepare_output_destination,
    _publish_owned_directory,
    _ReleaseGitRunner,
    _require_private_destination_parent,
    _strict_json_loads,
    _write_candidate_tree,
    _zip_datetime,
    is_excluded_public_path,
    load_release_artifact_manifest,
    load_release_candidate,
    materialize_release_candidate,
    require_task13_scanner_gate,
    source_tree_digest,
    validate_canonical_archive_member_names,
)
from mercury_tools.release.scanner import (
    ReleaseGateError,
    SubprocessCommandRunner,
)

EXPECTED_LOCAL_MCP_TOOL_NAMES = frozenset(
    {
        "connector_status",
        "credential_status",
        "execute_erp_create",
        "execute_erp_update",
        "execute_sensitive_erp_action",
        "get_document",
        "get_erp_action_schema",
        "get_erp_request_status",
        "import_erp_spec",
        "list_connector_drivers",
        "list_workspace_flows",
        "prepare_erp_mutation",
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
EXPECTED_HOSTED_MCP_SERVER = {
    "type": "http",
    "url": "https://mercury-tools-mcp.onrender.com/mcp",
    "note": "Mercury Accounting and ERP connector platform.",
}
_MCP_TOOL_LIST_PROGRAM = (
    "import asyncio, json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from mercury_tools.mcp.local_server import local_mcp\n"
    "tools = asyncio.run(local_mcp.list_tools())\n"
    "print(json.dumps(sorted(tool.name for tool in tools)))\n"
)
_MAX_COMMAND_OUTPUT = 64 * 1024
_MAX_JUNIT_BYTES = 16 * 1024 * 1024
RELEASE_REQUIRED_CROSS_FILESYSTEM_TEST_IDS = frozenset(
    {
        "tests/test_release_artifacts.py::test_publish_copies_verified_tree_to_distinct_destination_device",
        "tests/test_release_artifacts.py::test_release_artifacts_publish_to_distinct_destination_device",
    }
)
RELEASE_CROSS_FILESYSTEM_KNOWN_LINUX_DEVICE = "/dev/shm"
RELEASE_CROSS_FILESYSTEM_CAPABILITY_SKIP_REASON = "no_writable_second_device"
RELEASE_TEST_SKIP_AUDIT_CONTRACT = {
    "capability_skip_reason": RELEASE_CROSS_FILESYSTEM_CAPABILITY_SKIP_REASON,
    "known_linux_distinct_device": RELEASE_CROSS_FILESYSTEM_KNOWN_LINUX_DEVICE,
    "required_nodeids": tuple(sorted(RELEASE_REQUIRED_CROSS_FILESYSTEM_TEST_IDS)),
}


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


def verify_required_release_test_skips(
    junit: Path,
    *,
    known_device: bool = True,
) -> None:
    """Require the cross-device release tests to pass on a known-device job."""

    if not isinstance(known_device, bool):
        raise ReleaseGateError("release_test_skip_audit_invalid")
    root = _load_junit_root(junit)
    results: dict[str, list[tuple[str, str | None]]] = {
        nodeid: [] for nodeid in RELEASE_REQUIRED_CROSS_FILESYSTEM_TEST_IDS
    }
    for testcase in root.iter():
        if _xml_local_name(testcase.tag) != "testcase":
            continue
        outcome = _junit_testcase_outcome(testcase)
        for nodeid in _junit_testcase_nodeids(testcase):
            if nodeid in results:
                results[nodeid].append(outcome)
    for nodeid in RELEASE_REQUIRED_CROSS_FILESYSTEM_TEST_IDS:
        outcomes = results[nodeid]
        if len(outcomes) != 1:
            raise ReleaseGateError("release_test_skip_audit_failed")
        state, reason = outcomes[0]
        if state == "passed":
            continue
        if (
            not known_device
            and state == "skipped"
            and reason == RELEASE_CROSS_FILESYSTEM_CAPABILITY_SKIP_REASON
        ):
            continue
        raise ReleaseGateError("release_test_skip_audit_failed")


def _load_junit_root(path: Path) -> ElementTree.Element:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ReleaseGateError("release_test_skip_audit_invalid")
        if metadata.st_size > _MAX_JUNIT_BYTES:
            raise ReleaseGateError("release_test_skip_audit_invalid")
        payload = path.read_bytes()
        if b"<!" in payload:
            raise ReleaseGateError("release_test_skip_audit_invalid")
        root = ElementTree.fromstring(payload)
    except ReleaseGateError:
        raise
    except (ElementTree.ParseError, OSError, ValueError) as exc:
        raise ReleaseGateError("release_test_skip_audit_invalid") from exc
    if _xml_local_name(root.tag) not in {"testsuite", "testsuites"}:
        raise ReleaseGateError("release_test_skip_audit_invalid")
    return root


def _junit_testcase_nodeids(testcase: ElementTree.Element) -> tuple[str, ...]:
    name = testcase.attrib.get("name")
    if not name or "\0" in name:
        return ()
    candidates = {testcase.attrib.get("nodeid", "")}
    file_name = testcase.attrib.get("file")
    if file_name:
        candidates.add(file_name.replace("\\", "/") + "::" + name)
    classname = testcase.attrib.get("classname")
    if classname:
        normalized = classname.replace(".", "/")
        if not normalized.endswith(".py"):
            normalized += ".py"
        candidates.add(f"{normalized}::{name}")
    return tuple(candidate for candidate in candidates if candidate)


def _junit_testcase_outcome(testcase: ElementTree.Element) -> tuple[str, str | None]:
    children = tuple(testcase)
    if any(_xml_local_name(child.tag) in {"failure", "error"} for child in children):
        return "failed", None
    skipped = next(
        (child for child in children if _xml_local_name(child.tag) == "skipped"),
        None,
    )
    if skipped is None:
        return "passed", None
    reason = skipped.attrib.get("message") or (skipped.text or "").strip()
    return "skipped", reason


def _xml_local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def verify_release_tree(root: Path, *, version: str):
    """Verify source-tree contracts independent of artifact publication state."""

    candidate = load_release_candidate(root, version=version, require_clean=False)
    with materialize_release_candidate(candidate) as snapshot:
        _require_plugin_contract(snapshot, version)
        _require_exact_local_mcp_tools(snapshot)
    return candidate


def verify_release(
    *,
    root: Path,
    version: str,
    artifacts: Path,
) -> ReleaseVerification:
    """Verify a clean release candidate and its exact deterministic artifact set."""

    candidate = load_release_candidate(root, version=version, require_clean=True)
    try:
        with materialize_release_candidate(candidate) as snapshot:
            _require_plugin_contract(snapshot, version)
            _require_exact_local_mcp_tools(snapshot)
            with tempfile.TemporaryDirectory(prefix=".mercury-release-verify-") as temporary:
                expected_artifacts = Path(temporary) / "expected-artifacts"
                expected_artifacts.mkdir()
                expected_manifest = _build_artifact_set(
                    candidate,
                    snapshot,
                    expected_artifacts,
                )
                _require_artifacts_match_expected(
                    artifacts,
                    expected_artifacts,
                    expected_manifest,
                )
                require_task13_scanner_gate(candidate, snapshot, expected_artifacts)
                _require_artifacts_match_expected(
                    artifacts,
                    expected_artifacts,
                    expected_manifest,
                )
                _ensure_candidate_unchanged(candidate)
                return ReleaseVerification(
                    passed=True,
                    version=candidate.version,
                    commit_sha=candidate.commit_sha,
                    artifact_manifest_sha256=_sha256_file(artifacts / MANIFEST_FILE_NAME),
                )
    except ReleaseGateError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        raise ReleaseGateError("artifact_candidate_mismatch") from exc


def build_public_staging(
    *,
    root: Path,
    version: str,
    output: Path,
    artifacts: Path | None = None,
) -> PublicStaging:
    """Create a one-commit, history-free staging repository from ``git archive`` only.

    ``output.parent`` is a release-output trust boundary: it must remain an
    owner-controlled, exclusive namespace for this call. It must be owned by
    the effective UID and not be group- or world-writable; another same-UID
    process must not concurrently mutate it. Same-UID processes are treated as
    the same local principal.
    """

    destination = _prepare_output_destination(output)
    try:
        _require_private_destination_parent(destination)
        candidate = load_release_candidate(root, version=version, require_clean=True)
        candidate_digest = source_tree_digest(candidate.entries)
        with materialize_release_candidate(candidate) as snapshot:
            _require_plugin_contract(snapshot, version)
            _require_exact_local_mcp_tools(snapshot)
            with tempfile.TemporaryDirectory(prefix=".mercury-public-staging-") as temporary:
                temporary_root = Path(temporary)
                stage = temporary_root / "stage"
                expected_artifacts = temporary_root / "expected-artifacts"
                stage.mkdir()
                expected_artifacts.mkdir()
                expected_manifest = _build_artifact_set(
                    candidate,
                    snapshot,
                    expected_artifacts,
                )
                if artifacts is not None:
                    _require_artifacts_match_expected(
                        artifacts,
                        expected_artifacts,
                        expected_manifest,
                    )
                _write_candidate_tree(candidate.entries, stage)
                _require_staging_scanner_gate(
                    candidate,
                    snapshot,
                    expected_artifacts,
                )
                _initialize_history_free_repository(stage, candidate.build_epoch)
                _require_single_new_commit(stage, candidate.commit_sha)
                staged_candidate = load_release_candidate(
                    stage,
                    version=version,
                    require_clean=True,
                )
                staged_digest = source_tree_digest(staged_candidate.entries)
                if staged_digest != candidate_digest:
                    raise ReleaseGateError("staging_tree_digest_mismatch")
                _ensure_candidate_unchanged(candidate)
                _publish_owned_directory(stage, destination)
        return PublicStaging(
            path=output,
            version=version,
            commit_sha=candidate.commit_sha,
            candidate_tree_digest=candidate_digest,
            staged_tree_digest=candidate_digest,
        )
    except ReleaseGateError:
        raise
    except OSError as exc:
        raise ReleaseGateError("staging_build_failed") from exc
    finally:
        destination.close()


def _require_plugin_contract(root: Path, version: str) -> None:
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
    if server != EXPECTED_HOSTED_MCP_SERVER:
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
    runner = SubprocessCommandRunner(
        max_output_bytes=_MAX_COMMAND_OUTPUT,
        timeout_seconds=30.0,
    )
    result = runner.run(
        (sys.executable, "-I", "-c", _MCP_TOOL_LIST_PROGRAM, str(source_root)),
        cwd=root,
    )
    if result.exit_code != 0:
        raise ReleaseGateError("local_mcp_contract_invalid")
    try:
        names = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ReleaseGateError("local_mcp_contract_invalid") from exc
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ReleaseGateError("local_mcp_contract_invalid")
    if set(names) != EXPECTED_LOCAL_MCP_TOOL_NAMES or len(names) != len(
        EXPECTED_LOCAL_MCP_TOOL_NAMES
    ):
        raise ReleaseGateError("local_mcp_contract_invalid")


def _require_artifacts_match_expected(
    artifacts: Path,
    expected_artifacts: Path,
    expected_manifest: ReleaseArtifactManifest,
) -> None:
    submitted_manifest = load_release_artifact_manifest(artifacts / MANIFEST_FILE_NAME)
    if submitted_manifest.builder_provenance != expected_manifest.builder_provenance:
        raise ReleaseGateError("artifact_candidate_mismatch")
    _require_exact_artifact_set(artifacts, expected_manifest)
    for artifact in expected_manifest.artifacts:
        _require_normalized_archive(
            artifacts / artifact.file_name,
            expected_manifest.build_epoch,
        )
    if submitted_manifest.as_dict() != expected_manifest.as_dict():
        raise ReleaseGateError("artifact_candidate_mismatch")
    _require_files_equal(
        artifacts / MANIFEST_FILE_NAME,
        expected_artifacts / MANIFEST_FILE_NAME,
    )
    for artifact in expected_manifest.artifacts:
        submitted = artifacts / artifact.file_name
        _require_artifact_digest(submitted, artifact)
        _require_files_equal(submitted, expected_artifacts / artifact.file_name)


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
        return artifact.file_name.startswith(
            f"mercury_tools-{artifact.version}-"
        ) and artifact.file_name.endswith(".whl")
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


def _require_files_equal(submitted: Path, expected: Path) -> None:
    try:
        with submitted.open("rb") as submitted_stream, expected.open("rb") as expected_stream:
            while True:
                submitted_chunk = submitted_stream.read(1024 * 1024)
                expected_chunk = expected_stream.read(1024 * 1024)
                if submitted_chunk != expected_chunk:
                    raise ReleaseGateError("artifact_candidate_mismatch")
                if not submitted_chunk:
                    return
    except ReleaseGateError:
        raise
    except OSError as exc:
        raise ReleaseGateError("artifact_candidate_mismatch") from exc


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
            try:
                validate_canonical_archive_member_names(names)
            except ReleaseGateError as exc:
                raise ReleaseGateError("artifact_archive_metadata_invalid") from exc
            if names != sorted(names) or any(entry.is_dir() for entry in entries):
                raise ReleaseGateError("artifact_archive_metadata_invalid")
            if archive.comment:
                raise ReleaseGateError("artifact_archive_metadata_invalid")
            for entry in entries:
                if is_excluded_public_path(entry.filename):
                    raise ReleaseGateError("artifact_archive_metadata_invalid")
                expected_mode = stat.S_IFREG | 0o644
                if (
                    entry.create_system != 3
                    or entry.compress_type != zipfile.ZIP_STORED
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
            or payload[8] != 0
            or payload[9] != 255
        ):
            raise ReleaseGateError("artifact_archive_metadata_invalid")
        if int.from_bytes(payload[4:8], "little") != epoch:
            raise ReleaseGateError("artifact_archive_metadata_invalid")
        with tarfile.open(
            fileobj=io.BytesIO(_decode_stored_gzip(payload)),
            mode="r:",
        ) as archive:
            entries = archive.getmembers()
            names = [entry.name for entry in entries]
            try:
                validate_canonical_archive_member_names(names)
            except ReleaseGateError as exc:
                raise ReleaseGateError("artifact_archive_metadata_invalid") from exc
            if names != sorted(names) or any(entry.isdir() for entry in entries):
                raise ReleaseGateError("artifact_archive_metadata_invalid")
            for entry in entries:
                if is_excluded_public_path(entry.name):
                    raise ReleaseGateError("artifact_archive_metadata_invalid")
                if entry.isfile():
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
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise ReleaseGateError("artifact_archive_metadata_invalid") from exc


def _require_staging_scanner_gate(
    candidate: ReleaseCandidate,
    snapshot: Path,
    expected_artifacts: Path,
) -> None:
    require_task13_scanner_gate(candidate, snapshot, expected_artifacts)


def _initialize_history_free_repository(stage: Path, epoch: int) -> None:
    initializer = _ReleaseGitRunner.for_new_repository(stage)
    result = initializer.run_unbound(("init", "--initial-branch=main"))
    if result.exit_code != 0:
        raise ReleaseGateError("staging_repository_init_failed")
    commands = (
        ("config", "user.name", "Mercury Release"),
        ("config", "user.email", "release@mercury.invalid"),
        ("add", "--all"),
    )
    for command in commands:
        result = initializer.run_unbound(command)
        if result.exit_code != 0:
            raise ReleaseGateError("staging_repository_init_failed")
    result = initializer.run_unbound(
        ("commit", "--quiet", "-m", "Mercury public staging"),
        extra_environment={
            "GIT_AUTHOR_DATE": f"{epoch} +0000",
            "GIT_COMMITTER_DATE": f"{epoch} +0000",
        },
    )
    if result.exit_code != 0:
        raise ReleaseGateError("staging_repository_init_failed")


def _require_single_new_commit(stage: Path, source_commit_sha: str) -> None:
    runner = _ReleaseGitRunner.for_repository(stage)
    count = runner.run(("rev-list", "--all", "--count"))
    commits = runner.run(("rev-list", "--all"))
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
