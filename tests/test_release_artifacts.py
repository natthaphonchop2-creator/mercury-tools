from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import zipfile
import zlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mercury_tools.release import artifacts as release_artifacts
from mercury_tools.release.artifacts import (
    ReleaseCandidate,
    build_release_artifacts,
    validate_canonical_archive_member_names,
)
from mercury_tools.release.models import (
    EXPECTED_SURFACE_SCANNER_VERSIONS,
    PINNED_SCANNER_VERSIONS,
    REQUIRED_PUBLIC_SURFACES,
    GateStatus,
    ScannerVersionAttestation,
    SecretScanReport,
    SurfaceAttestation,
)
from mercury_tools.release.scanner import ReleaseGateError, build_blocked_report
from mercury_tools.release.verify import (
    RELEASE_CROSS_FILESYSTEM_CAPABILITY_SKIP_REASON,
    RELEASE_CROSS_FILESYSTEM_KNOWN_LINUX_DEVICE,
    RELEASE_REQUIRED_CROSS_FILESYSTEM_TEST_IDS,
    RELEASE_TEST_SKIP_AUDIT_CONTRACT,
    verify_release,
)

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.1"
FIXTURE_TIMESTAMP = "2026-07-14T00:00:00+00:00"
_FIXTURE_BUILD_TOOL_VERSION = "0.0.1"
_FIXTURE_SETUPOOLS_VERSION = "80.0.0"
_FIXTURE_WHEEL_VERSION = "0.0.1"
_TOOLCHAIN_POLICY_MARKER = "[tool.mercury.release-build]"
_FIXTURE_INTERPRETER = Path(sys.executable).resolve(strict=True)
_FIXTURE_INTERPRETER_SHA256 = hashlib.sha256(_FIXTURE_INTERPRETER.read_bytes()).hexdigest()
_FIXTURE_PLATFORM = platform.system()
_FIXTURE_ARCHITECTURE = platform.machine()
_FIXTURE_IMPLEMENTATION = sys.implementation.name
_FIXTURE_PYTHON_VERSION = platform.python_version()
_FIXTURE_STDLIB_VERSION = sysconfig.get_python_version()
_FIXTURE_ZLIB_RUNTIME_VERSION = zlib.ZLIB_RUNTIME_VERSION
_ZERO_SHA256 = "0" * 64


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _archive_head(destination: Path) -> None:
    payload = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                assert source is not None
                target.write_bytes(source.read())
            else:
                raise AssertionError(f"unexpected fixture archive member: {member.name}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_fixture_wheel(path: Path, *, package: str, version: str) -> None:
    normalized = package.replace("-", "_")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{normalized}/__init__.py", b"\n")
        archive.writestr(
            f"{normalized}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n",
        )
        archive.writestr(
            f"{normalized}-{version}.dist-info/WHEEL",
            (
                "Wheel-Version: 1.0\nGenerator: release fixture\n"
                "Root-Is-Purelib: true\nTag: py3-none-any\n"
            ),
        )
        archive.writestr(f"{normalized}-{version}.dist-info/RECORD", "")


def _write_fixture_uv(path: Path) -> None:
    path.write_text(
        f"""#!{_FIXTURE_INTERPRETER}
from __future__ import annotations

import gzip
import io
import os
import stat
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

VERSION = {_FIXTURE_BUILD_TOOL_VERSION!r}
EXPECTED_ENVIRONMENT = {{
    "HOME",
    "LC_ALL",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONNOUSERSITE",
    "PYTHONPATH",
    "SOURCE_DATE_EPOCH",
    "TMPDIR",
    "UV_CACHE_DIR",
    "UV_FROZEN",
    "UV_NO_CONFIG",
    "UV_NO_INDEX",
    "UV_NO_PROGRESS",
    "UV_OFFLINE",
    "UV_PYTHON",
    "UV_PYTHON_PREFERENCE",
    "UV_PYTHON_DOWNLOADS",
    "UV_REQUIRE_HASHES",
}}


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 64


def _require_isolated_environment() -> int:
    actual_environment = set(os.environ)
    permitted_environments = {{
        frozenset(EXPECTED_ENVIRONMENT),
        frozenset((*EXPECTED_ENVIRONMENT, "__CF_USER_TEXT_ENCODING")),
    }}
    if frozenset(actual_environment) not in permitted_environments:
        return _fail("unexpected build environment")
    expected_values = {{
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "UV_FROZEN": "1",
        "UV_NO_CONFIG": "1",
        "UV_NO_INDEX": "1",
        "UV_NO_PROGRESS": "1",
        "UV_OFFLINE": "1",
        "UV_PYTHON": {str(_FIXTURE_INTERPRETER)!r},
        "UV_PYTHON_PREFERENCE": "only-system",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_REQUIRE_HASHES": "1",
    }}
    for key, value in expected_values.items():
        if os.environ.get(key) != value:
            return _fail(f"unexpected {{key}}")
    return 0


def _has_option(arguments: list[str], option: str) -> bool:
    return option in arguments


def _option_value(arguments: list[str], option: str) -> str | None:
    try:
        return arguments[arguments.index(option) + 1]
    except (ValueError, IndexError):
        return None


def _write_raw_distributions(output: Path) -> int:
    try:
        project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        version = project["project"]["version"]
    except Exception:
        return _fail("fixture project invalid")
    if not isinstance(version, str):
        return _fail("fixture project version invalid")
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / f"mercury_tools-{{version}}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mercury_tools/__init__.py", f"__version__ = {{version!r}}\\n")
        archive.writestr(
            f"mercury_tools-{{version}}.dist-info/METADATA",
            f"Metadata-Version: 2.1\\nName: mercury-tools\\nVersion: {{version}}\\n",
        )
        archive.writestr(
            f"mercury_tools-{{version}}.dist-info/WHEEL",
            (
                "Wheel-Version: 1.0\\nGenerator: release fixture\\n"
                "Root-Is-Purelib: true\\nTag: py3-none-any\\n"
            ),
        )
        archive.writestr(f"mercury_tools-{{version}}.dist-info/RECORD", "")
    source = output / f"mercury_tools-{{version}}.tar.gz"
    with source.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                payload = b"fixture source\\n"
                member = tarfile.TarInfo(f"mercury-tools-{{version}}/README.md")
                member.size = len(payload)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(payload))
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    if arguments == ["--version"]:
        print(f"uv {{VERSION}}")
        return 0
    environment_status = _require_isolated_environment()
    if environment_status:
        return environment_status
    if not arguments:
        return _fail("missing command")
    required = {{"--offline", "--no-config", "--no-index", "--no-sources", "--no-python-downloads"}}
    if not required.issubset(arguments):
        return _fail("isolated flags missing")
    if arguments[0] == "lock":
        if "--check" not in arguments or not Path("uv.lock").is_file():
            return _fail("frozen lock check missing")
        return 0
    if arguments[0] != "build":
        return _fail("unexpected command")
    if not {{"--wheel", "--sdist", "--require-hashes"}}.issubset(arguments):
        return _fail("build flags missing")
    output = _option_value(arguments, "--out-dir")
    constraints = _option_value(arguments, "--build-constraints")
    wheelhouse = _option_value(arguments, "--find-links")
    if output is None or constraints is None or wheelhouse is None:
        return _fail("build policy inputs missing")
    if not Path(constraints).is_file() or not Path(wheelhouse).is_dir():
        return _fail("build policy inputs invalid")
    if "--hash=sha256:" not in Path(constraints).read_text(encoding="utf-8"):
        return _fail("hash constraints missing")
    return _write_raw_distributions(Path(output))


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _install_exact_build_toolchain_fixture(root: Path) -> None:
    toolchain = root / "release-toolchain"
    wheelhouse = toolchain / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    uv = toolchain / "uv"
    _write_fixture_uv(uv)

    dependencies = (
        ("setuptools", _FIXTURE_SETUPOOLS_VERSION),
        ("wheel", _FIXTURE_WHEEL_VERSION),
    )
    dependency_records: list[tuple[str, str, str, int, str]] = []
    for package, version in dependencies:
        wheel = wheelhouse / f"{package}-{version}-py3-none-any.whl"
        _write_fixture_wheel(wheel, package=package, version=version)
        payload = wheel.read_bytes()
        dependency_records.append(
            (package, version, _sha256_bytes(payload), len(payload), wheel.name)
        )

    constraints = toolchain / "build-constraints.txt"
    constraints.write_text(
        "".join(
            f"{package}=={version} --hash=sha256:{digest}\n"
            for package, version, digest, _size, _name in dependency_records
        ),
        encoding="utf-8",
    )
    constraints_sha256 = _sha256_bytes(constraints.read_bytes())
    lock = root / "uv.lock"
    lock.write_text(
        'version = 1\nrevision = 1\nrequires-python = ">=3.11,<3.14"\n\n'
        + "\n".join(
            "\n".join(
                (
                    "[[package]]",
                    f'name = "{package}"',
                    f'version = "{version}"',
                    "wheels = [",
                    (
                        '    { url = "file://release-toolchain/wheelhouse/'
                        f'{file_name}", hash = "sha256:{digest}", size = {size} }},'
                    ),
                    "]",
                )
            )
            for package, version, digest, size, file_name in dependency_records
        )
        + "\n",
        encoding="utf-8",
    )
    lock_sha256 = _sha256_bytes(lock.read_bytes())
    uv_sha256 = _sha256_bytes(uv.read_bytes())

    pyproject = root / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")
    source = source.replace(
        'requires = ["setuptools>=77", "wheel"]',
        (
            'requires = ["setuptools=='
            + _FIXTURE_SETUPOOLS_VERSION
            + '", "wheel=='
            + _FIXTURE_WHEEL_VERSION
            + '"]'
        ),
        1,
    )
    source += (
        "\n"
        "[tool.mercury.release-build]\n"
        "schema_version = 2\n"
        f'lock_sha256 = "{lock_sha256}"\n\n'
        "[tool.mercury.release-build.uv]\n"
        'path = "release-toolchain/uv"\n'
        f'version = "{_FIXTURE_BUILD_TOOL_VERSION}"\n'
        f'sha256 = "{uv_sha256}"\n\n'
        "[tool.mercury.release-build.build]\n"
        'command = "uv build"\n'
        f'version = "{_FIXTURE_BUILD_TOOL_VERSION}"\n'
        f'sha256 = "{uv_sha256}"\n'
        'constraints = "release-toolchain/build-constraints.txt"\n'
        f'constraints_sha256 = "{constraints_sha256}"\n'
        'wheelhouse = "release-toolchain/wheelhouse"\n\n'
        "[tool.mercury.release-build.backend]\n"
        'module = "setuptools.build_meta"\n\n'
        + "\n".join(
            "\n".join(
                (
                    "[[tool.mercury.release-build.backend.requirements]]",
                    f'name = "{package}"',
                    f'version = "{version}"',
                    f'sha256 = "{digest}"',
                    f'file = "release-toolchain/wheelhouse/{file_name}"',
                )
            )
            for package, version, digest, _size, file_name in dependency_records
        )
        + "\n"
        + "[[tool.mercury.release-build.platforms]]\n"
        + f"system = {json.dumps(_FIXTURE_PLATFORM)}\n"
        + f"architecture = {json.dumps(_FIXTURE_ARCHITECTURE)}\n\n"
        + "[tool.mercury.release-build.platforms.interpreter]\n"
        + f"path = {json.dumps(str(_FIXTURE_INTERPRETER))}\n"
        + f"sha256 = {json.dumps(_FIXTURE_INTERPRETER_SHA256)}\n"
        + f"implementation = {json.dumps(_FIXTURE_IMPLEMENTATION)}\n"
        + f"version = {json.dumps(_FIXTURE_PYTHON_VERSION)}\n"
        + f"stdlib_version = {json.dumps(_FIXTURE_STDLIB_VERSION)}\n"
        + f"zlib_runtime_version = {json.dumps(_FIXTURE_ZLIB_RUNTIME_VERSION)}\n\n"
        + "[tool.mercury.release-build.platforms.normalizer]\n"
        + 'name = "mercury-release-normalizer"\n'
        + 'version = "1"\n'
        + 'zip_format = "stored-v1"\n'
        + 'gzip_format = "stored-deflate-v1"\n'
        + 'tar_format = "gnu-v1"\n'
    )
    pyproject.write_text(source, encoding="utf-8")


def _commit_release_tree(root: Path, message: str) -> None:
    _run(["git", "add", "-A"], cwd=root)
    _run(["git", "commit", "-m", message], cwd=root)


def make_unpinned_release_tree(tmp_path: Path) -> Path:
    root = make_release_tree(tmp_path)
    pyproject = root / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        source.split(_TOOLCHAIN_POLICY_MARKER, 1)[0].rstrip() + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(root / "release-toolchain")
    _commit_release_tree(root, "remove build toolchain policy")
    return root


def make_release_tree(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    root.mkdir()
    _archive_head(root)

    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'version = "0.2.0"',
            'version = "0.2.1"',
            1,
        ),
        encoding="utf-8",
    )
    mcp_path = root / "plugins/mercury-finance/.mcp.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["mcpServers"]["mercury-finance"]["args"][1] = (
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.1"
    )
    mcp_path.write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    plugin_path = root / "plugins/mercury-finance/.codex-plugin/plugin.json"
    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    plugin["version"] = str(plugin["version"]).replace("0.2.0", "0.2.1", 1)
    plugin_path.write_text(json.dumps(plugin, indent=2) + "\n", encoding="utf-8")
    _install_exact_build_toolchain_fixture(root)

    _run(["git", "init", "--initial-branch", "main"], cwd=root)
    _run(["git", "config", "user.name", "Release Fixture"], cwd=root)
    _run(["git", "config", "user.email", "release-fixture@example.test"], cwd=root)
    _run(["git", "add", "-A"], cwd=root)
    fixture_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": FIXTURE_TIMESTAMP,
        "GIT_COMMITTER_DATE": FIXTURE_TIMESTAMP,
    }
    _run(["git", "commit", "-m", "release fixture"], cwd=root, env=fixture_env)

    (root / ".env").write_text("local-secret-state\n", encoding="utf-8")
    local_state = root / ".mercury"
    local_state.mkdir()
    (local_state / "audit-ledger.jsonl").write_text("local-audit-state\n", encoding="utf-8")
    assert _run(["git", "status", "--porcelain"], cwd=root) == ""
    return root


def passing_task13_report() -> SecretScanReport:
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
            scanner_versions=EXPECTED_SURFACE_SCANNER_VERSIONS[surface],
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


def install_task13_runner(
    monkeypatch: pytest.MonkeyPatch,
    report: object | None = None,
) -> list[tuple[object, Path, Path]]:
    calls: list[tuple[object, Path, Path]] = []

    def run(candidate: object, snapshot: Path, target: Path) -> object:
        calls.append((candidate, snapshot, target))
        return passing_task13_report() if report is None else report

    monkeypatch.setattr(release_artifacts, "_run_task13_artifact_gate", run)
    return calls


def _replace_fixture_uv(root: Path, payload: str) -> None:
    uv = root / "release-toolchain/uv"
    original_sha256 = _sha256_bytes(uv.read_bytes())
    uv.write_text(payload, encoding="utf-8")
    uv.chmod(0o755)
    replacement_sha256 = _sha256_bytes(uv.read_bytes())
    pyproject = root / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")
    assert source.count(original_sha256) == 2
    pyproject.write_text(
        source.replace(original_sha256, replacement_sha256),
        encoding="utf-8",
    )
    _commit_release_tree(root, "replace fixture uv launcher")


def _delegating_uv_script(shebang: str, original: str) -> str:
    lines = original.splitlines()
    assert lines[0].startswith("#!")
    assert lines[1] == "from __future__ import annotations"
    body = "\n".join(lines[2:])
    marker = "MERCURY_FIXTURE_UV_DELEGATED"
    return (
        f"#!{shebang}\n"
        "from __future__ import annotations\n"
        "import os\n"
        "import sys\n"
        f"if os.environ.pop({marker!r}, None) != '1':\n"
        "    environment = dict(os.environ)\n"
        f"    environment[{marker!r}] = '1'\n"
        f"    os.execve({_FIXTURE_INTERPRETER.as_posix()!r}, "
        f"[{_FIXTURE_INTERPRETER.as_posix()!r}, *sys.argv], environment)\n"
        f"{body}\n"
    )


def _write_release_gate_fake_scanners(directory: Path) -> None:
    for scanner, version_flag in (("gitleaks", "version"), ("trufflehog", "--version")):
        path = directory / scanner
        version = PINNED_SCANNER_VERSIONS[scanner]
        scan_output = "[]" if scanner == "gitleaks" else ""
        path.write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = \"{version_flag}\" ]; then\n"
            f"  printf '%s\\n' '{scanner} {version}'\n"
            "  exit 0\n"
            "fi\n"
            f"printf '%s' '{scan_output}'\n",
            encoding="utf-8",
        )
        path.chmod(0o755)


def incomplete_task13_report() -> SecretScanReport:
    timestamp = datetime(2026, 7, 14, tzinfo=UTC)
    return SecretScanReport.model_construct(
        status=GateStatus.PASSED,
        started_at=timestamp,
        completed_at=timestamp,
        scanner_versions=(),
        surfaces=(),
        blockers=(),
        finding_codes=(),
    )


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [item.filename for item in archive.infolist()]
    with tarfile.open(path, mode="r:gz") as archive:
        return [item.name for item in archive.getmembers()]


@pytest.mark.parametrize(
    "names",
    (
        ("a/./b", "a/b"),
        ("/absolute",),
        ("a\\b",),
        ("a//b",),
        ("a/../b",),
        ("README.md", "readme.md"),
        ("caf\u00e9.txt", "cafe\u0301.txt"),
    ),
)
def test_canonical_archive_member_validator_rejects_noncanonical_paths(
    names: tuple[str, ...],
) -> None:
    with pytest.raises(ReleaseGateError, match="^release_archive_member_invalid$"):
        validate_canonical_archive_member_names(names)


def test_release_artifacts_are_reproducible_and_bound_to_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    calls = install_task13_runner(monkeypatch)

    first = build_release_artifacts(
        root,
        version=VERSION,
        output=first_output,
    )
    second = build_release_artifacts(
        root,
        version=VERSION,
        output=second_output,
    )

    assert first.version == VERSION
    assert first.commit_sha == _run(["git", "rev-parse", "HEAD"], cwd=root)
    assert {item.kind for item in first.artifacts} == {"wheel", "sdist", "plugin", "source"}
    assert all(len(item.sha256) == 64 for item in first.artifacts)
    assert first.as_dict() == second.as_dict()
    assert {path.name: path.read_bytes() for path in first_output.iterdir()} == {
        path.name: path.read_bytes() for path in second_output.iterdir()
    }
    assert {path.name for path in first_output.iterdir()} == {
        *(item.file_name for item in first.artifacts),
        "SHA256SUMS.json",
    }
    assert _run(["git", "status", "--porcelain"], cwd=root) == ""
    assert len(calls) == 2
    assert all(snapshot != root for _candidate, snapshot, _target in calls)

    for artifact in first.artifacts:
        path = first_output / artifact.file_name
        names = _archive_names(path)
        assert names == sorted(names)
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)

    source = next(item for item in first.artifacts if item.kind == "source")
    with tarfile.open(first_output / source.file_name, mode="r:gz") as archive:
        members = archive.getmembers()
    assert all(member.uid == 0 and member.gid == 0 for member in members)
    assert all(member.mtime == first.build_epoch for member in members)
    source_names = _archive_names(first_output / source.file_name)
    assert not any(Path(name).name == ".env" for name in source_names)
    assert not any(".mercury" in Path(name).parts for name in source_names)


def test_release_runtime_policy_records_exact_platform_interpreter_and_normalizer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)

    manifest = build_release_artifacts(root, version=VERSION, output=output)

    provenance = manifest.as_dict()["builder_provenance"]
    assert provenance["runtime"] == {
        "architecture": _FIXTURE_ARCHITECTURE,
        "interpreter": {
            "implementation": _FIXTURE_IMPLEMENTATION,
            "path": str(_FIXTURE_INTERPRETER),
            "sha256": _FIXTURE_INTERPRETER_SHA256,
            "stdlib_version": _FIXTURE_STDLIB_VERSION,
            "version": _FIXTURE_PYTHON_VERSION,
            "zlib_runtime_version": _FIXTURE_ZLIB_RUNTIME_VERSION,
        },
        "normalizer": {
            "gzip_format": "stored-deflate-v1",
            "name": "mercury-release-normalizer",
            "tar_format": "gnu-v1",
            "version": "1",
            "zip_format": "stored-v1",
        },
        "system": _FIXTURE_PLATFORM,
    }
    for artifact in manifest.artifacts:
        path = output / artifact.file_name
        if path.suffix in {".whl", ".zip"}:
            with zipfile.ZipFile(path) as archive:
                assert all(
                    member.compress_type == zipfile.ZIP_STORED for member in archive.infolist()
                )
    source = next(item for item in manifest.artifacts if item.kind == "source")
    assert (output / source.file_name).read_bytes()[8] == 0


@pytest.mark.parametrize(
    ("needle", "replacement"),
    (
        (
            f"system = {json.dumps(_FIXTURE_PLATFORM)}",
            'system = "unsupported-release-platform"',
        ),
        (
            f"sha256 = {json.dumps(_FIXTURE_INTERPRETER_SHA256)}",
            f'sha256 = "{_ZERO_SHA256}"',
        ),
        (
            f"zlib_runtime_version = {json.dumps(_FIXTURE_ZLIB_RUNTIME_VERSION)}",
            'zlib_runtime_version = "unsupported-compression-runtime"',
        ),
    ),
)
def test_runtime_policy_blocks_wrong_platform_hash_or_compression_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    needle: str,
    replacement: str,
) -> None:
    root = make_release_tree(tmp_path)
    pyproject = root / "pyproject.toml"
    source = pyproject.read_text(encoding="utf-8")
    assert needle in source
    pyproject.write_text(source.replace(needle, replacement, 1), encoding="utf-8")
    _commit_release_tree(root, "mutate runtime policy")
    output = tmp_path / "blocked"

    def unexpected_build(*_args: object, **_kwargs: object) -> object:
        pytest.fail("artifact build ran before runtime provenance validation")

    monkeypatch.setattr(release_artifacts, "_build_artifact_set", unexpected_build)

    with pytest.raises(ReleaseGateError, match="^release_build_toolchain_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_runtime_policy_overrides_an_inherited_alternate_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)
    monkeypatch.setenv("UV_PYTHON", "/bin/sh")
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "foreign-python-home"))

    manifest = build_release_artifacts(root, version=VERSION, output=output)

    runtime = manifest.as_dict()["builder_provenance"]["runtime"]
    assert runtime["interpreter"]["path"] == str(_FIXTURE_INTERPRETER)


def test_release_git_bootstrap_ignores_path_shadow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    expected_commit = _run(["git", "rev-parse", "HEAD"], cwd=root)
    output = tmp_path / "artifacts"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_marker = tmp_path / "fake-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\nprintf '%s' fake > '{fake_marker}'\nexit 97\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    install_task13_runner(monkeypatch)
    monkeypatch.setenv("PATH", str(fake_bin))

    manifest = build_release_artifacts(root, version=VERSION, output=output)

    assert manifest.commit_sha == expected_commit
    assert not fake_marker.exists()
    assert (output / "SHA256SUMS.json").is_file()


def test_release_git_bootstrap_ignores_foreign_repository_and_config_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    expected_commit = _run(["git", "rev-parse", "HEAD"], cwd=root)
    foreign_parent = tmp_path / "foreign-parent"
    foreign_parent.mkdir()
    foreign = make_release_tree(foreign_parent)
    (foreign / "foreign-candidate.txt").write_text("foreign\n", encoding="utf-8")
    _commit_release_tree(foreign, "foreign candidate")
    foreign_commit = _run(["git", "rev-parse", "HEAD"], cwd=foreign)
    output = tmp_path / "artifacts"
    poison_config = tmp_path / "poison.gitconfig"
    poison_config.write_text("[core]\nrepositoryformatversion = 0\n", encoding="utf-8")
    poison_template = tmp_path / "poison-template"
    poison_template.mkdir()
    install_task13_runner(monkeypatch)
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign / ".git" / "index"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(poison_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(poison_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(poison_template))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(poison_template))
    monkeypatch.setenv("GIT_REPLACE_REF_BASE", str(foreign / "replace"))

    manifest = build_release_artifacts(root, version=VERSION, output=output)

    assert manifest.commit_sha == expected_commit
    assert manifest.commit_sha != foreign_commit
    assert (output / "SHA256SUMS.json").is_file()


def test_release_git_bootstrap_blocks_untrusted_executable_without_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "artifacts"
    untrusted_git = tmp_path / "git"
    untrusted_git.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    untrusted_git.chmod(0o755)
    monkeypatch.setattr(
        release_artifacts,
        "_TRUSTED_SYSTEM_GIT_PATHS",
        (untrusted_git,),
    )

    with pytest.raises(ReleaseGateError, match="^release_repository_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_release_git_bootstrap_ignores_local_replace_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    tree = _run(["git", "rev-parse", f"{head}^{{tree}}"], cwd=root)
    replacement_environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-15T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-07-15T00:00:00+00:00",
    }
    replacement = _run(
        ["git", "commit-tree", tree, "-m", "replacement candidate"],
        cwd=root,
        env=replacement_environment,
    )
    _run(["git", "replace", head, replacement], cwd=root)
    expected_epoch = int(
        _run(
            ["git", "--no-replace-objects", "show", "-s", "--format=%ct", head],
            cwd=root,
        )
    )
    output = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)

    manifest = build_release_artifacts(root, version=VERSION, output=output)

    assert manifest.commit_sha == head
    assert manifest.build_epoch == expected_epoch
    assert (output / "SHA256SUMS.json").is_file()


def test_release_task13_gate_uses_trusted_git_for_real_scanner_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    remote = tmp_path / "gate-remote.git"
    _run(["git", "clone", "--bare", str(root), str(remote)], cwd=tmp_path)
    foreign_parent = tmp_path / "foreign-parent"
    foreign_parent.mkdir()
    foreign = make_release_tree(foreign_parent)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    marker = tmp_path / "fake-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\nprintf '%s' fake > '{marker}'\nexit 97\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    _write_release_gate_fake_scanners(fake_bin)
    poison_config = tmp_path / "poison.gitconfig"
    poison_config.write_text("[core]\nrepositoryformatversion = 0\n", encoding="utf-8")
    poison_template = tmp_path / "poison-template"
    poison_template.mkdir()

    candidate = release_artifacts.load_release_candidate(
        root,
        version=VERSION,
        require_clean=True,
    )
    gate_candidate = replace(
        candidate,
        origin_url=str(remote),
        repository_name="example/mercury-tools",
    )
    with release_artifacts.materialize_release_candidate(candidate) as snapshot:
        artifacts = tmp_path / "gate-artifacts"
        artifacts.mkdir()
        release_artifacts._build_artifact_set(candidate, snapshot, artifacts)
        monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
        monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
        monkeypatch.setenv("GIT_INDEX_FILE", str(foreign / ".git" / "index"))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(poison_config))
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(poison_config))
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(poison_template))
        monkeypatch.setenv("GIT_TEMPLATE_DIR", str(poison_template))
        monkeypatch.setenv("GIT_REPLACE_REF_BASE", str(foreign / "replace"))
        monkeypatch.delenv("GH_TOKEN", raising=False)

        report = release_artifacts._run_task13_artifact_gate(
            gate_candidate,
            snapshot,
            artifacts,
        )

    git_surface = next(surface for surface in report.surfaces if surface.surface == "git_all_refs")
    assert "command_failed:git_clone" not in report.blockers
    assert not any(code.startswith("command_failed:git_") for code in git_surface.blocker_codes)
    assert git_surface.exit_codes
    assert not marker.exists()


def test_task13_trusted_git_runner_rejects_unallowlisted_executables_and_argv(
    tmp_path: Path,
) -> None:
    root = make_release_tree(tmp_path)
    candidate = release_artifacts.load_release_candidate(
        root,
        version=VERSION,
        require_clean=True,
    )
    runner = release_artifacts._ReleaseTask13GitRunner.for_candidate(
        replace(
            candidate,
            origin_url="https://github.com/example/mercury-tools.git",
            repository_name="example/mercury-tools",
        )
    )

    with pytest.raises(ReleaseGateError, match="^release_repository_invalid$"):
        runner.run(("/usr/bin/git", "status"))
    with pytest.raises(ReleaseGateError, match="^release_repository_invalid$"):
        runner.run(("git", "status"))


def test_release_candidate_rejects_repointed_dot_git_to_foreign_same_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    foreign = tmp_path / "foreign"
    _run(["git", "clone", str(root), str(foreign)], cwd=tmp_path)
    _run(["git", "config", "user.name", "Release Fixture"], cwd=foreign)
    _run(["git", "config", "user.email", "release-fixture@example.test"], cwd=foreign)
    foreign_tree = _run(["git", "rev-parse", "HEAD^{tree}"], cwd=foreign)
    replacement_commit = _run(
        ["git", "commit-tree", foreign_tree, "-m", "foreign same-tree candidate"],
        cwd=foreign,
    )
    _run(["git", "update-ref", "refs/heads/main", replacement_commit], cwd=foreign)
    _run(["git", "reset", "--hard", replacement_commit], cwd=foreign)
    assert _run(["git", "rev-parse", "HEAD^{tree}"], cwd=root) == foreign_tree
    assert _run(["git", "rev-parse", "HEAD"], cwd=foreign) != _run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
    )
    original_git = tmp_path / "original-dot-git"
    (root / ".git").rename(original_git)
    (root / ".git").write_text(f"gitdir: {foreign / '.git'}\n", encoding="utf-8")
    output = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)

    with pytest.raises(ReleaseGateError, match="^release_repository_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_release_candidate_accepts_a_physically_bound_linked_worktree(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    linked = tmp_path / "linked-candidate"
    _run(["git", "worktree", "add", "--detach", str(linked), "HEAD"], cwd=root)

    candidate = release_artifacts.load_release_candidate(
        linked,
        version=VERSION,
        require_clean=True,
    )

    assert (linked / ".git").is_file()
    assert candidate.git_metadata.root == linked.resolve()
    assert candidate.git_metadata.git_dir.parent == candidate.git_metadata.common_dir / "worktrees"


def test_release_candidate_rejects_git_alternates_without_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    alternates = root / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(str(tmp_path / "foreign-objects") + "\n", encoding="utf-8")
    output = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)

    with pytest.raises(ReleaseGateError, match="^release_repository_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_release_candidate_rejects_submodule_gitdir_pointer(tmp_path: Path) -> None:
    source_parent = tmp_path / "source-parent"
    source_parent.mkdir()
    source = make_release_tree(source_parent)
    parent = tmp_path / "parent"
    parent.mkdir()
    _run(["git", "init", "--initial-branch", "main"], cwd=parent)
    _run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(source), "module"],
        cwd=parent,
    )

    with pytest.raises(ReleaseGateError, match="^release_repository_invalid$"):
        release_artifacts.load_release_candidate(
            parent / "module",
            version=VERSION,
            require_clean=True,
        )


def test_release_runtime_rejects_hash_valid_env_shebang_uv_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    uv = root / "release-toolchain/uv"
    _replace_fixture_uv(
        root,
        _delegating_uv_script(f"/usr/bin/env {_FIXTURE_INTERPRETER}", uv.read_text()),
    )
    output = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)

    with pytest.raises(ReleaseGateError, match="^release_build_toolchain_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_release_runtime_rejects_hash_valid_uv_launcher_with_different_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    foreign_interpreter = tmp_path / "foreign-python"
    foreign_interpreter.symlink_to(_FIXTURE_INTERPRETER)
    uv = root / "release-toolchain/uv"
    _replace_fixture_uv(
        root,
        "#!" + str(foreign_interpreter) + "\n" + uv.read_text(encoding="utf-8").split("\n", 1)[1],
    )
    output = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)

    with pytest.raises(ReleaseGateError, match="^release_build_toolchain_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_current_v020_source_fails_closed_for_v021_request(tmp_path: Path) -> None:
    with pytest.raises(ReleaseGateError, match="^release_version_mismatch$"):
        build_release_artifacts(
            ROOT,
            version=VERSION,
            output=tmp_path / "dist",
        )


def test_artifact_builder_does_not_publish_when_task13_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "blocked"
    install_task13_runner(monkeypatch, build_blocked_report("scanner_missing"))

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_blocked$"):
        build_release_artifacts(
            root,
            version=VERSION,
            output=output,
        )

    assert not output.exists()


def test_artifact_builder_rejects_incomplete_task13_report_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "incomplete"
    install_task13_runner(monkeypatch, incomplete_task13_report())

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_unavailable$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_artifact_builder_rechecks_candidate_identity_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "mutated"

    def mutate_candidate(
        candidate: ReleaseCandidate,
        _snapshot: Path,
        _target: Path,
    ) -> SecretScanReport:
        readme = candidate.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        return passing_task13_report()

    monkeypatch.setattr(release_artifacts, "_run_task13_artifact_gate", mutate_candidate)

    with pytest.raises(ReleaseGateError, match="^release_candidate_changed$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_candidate_without_exact_build_toolchain_policy_blocks_before_artifact_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_unpinned_release_tree(tmp_path)
    output = tmp_path / "blocked"

    def unexpected_build(*_args: object, **_kwargs: object) -> object:
        pytest.fail("artifact build ran before toolchain validation")

    monkeypatch.setattr(release_artifacts, "_build_artifact_set", unexpected_build)

    with pytest.raises(ReleaseGateError, match="^release_build_toolchain_policy_missing$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_candidate_rejects_unpinned_backend_before_artifact_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            f"setuptools=={_FIXTURE_SETUPOOLS_VERSION}",
            f"setuptools>={_FIXTURE_SETUPOOLS_VERSION}",
            1,
        ),
        encoding="utf-8",
    )
    _commit_release_tree(root, "unpin fixture backend")
    output = tmp_path / "blocked"

    def unexpected_build(*_args: object, **_kwargs: object) -> object:
        pytest.fail("artifact build ran before backend validation")

    monkeypatch.setattr(release_artifacts, "_build_artifact_set", unexpected_build)

    with pytest.raises(ReleaseGateError, match="^release_build_toolchain_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


@pytest.mark.parametrize("relative_path", ("release-toolchain/uv", "uv.lock"))
def test_candidate_rejects_changed_toolchain_input_before_artifact_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
) -> None:
    root = make_release_tree(tmp_path)
    target = root / relative_path
    target.write_bytes(target.read_bytes() + b"\n# candidate toolchain mutation\n")
    _commit_release_tree(root, "mutate fixture toolchain input")
    output = tmp_path / "blocked"

    def unexpected_build(*_args: object, **_kwargs: object) -> object:
        pytest.fail("artifact build ran before toolchain hash validation")

    monkeypatch.setattr(release_artifacts, "_build_artifact_set", unexpected_build)

    with pytest.raises(ReleaseGateError, match="^release_build_toolchain_invalid$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_candidate_owned_toolchain_scrubs_inherited_uv_path_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "artifacts"
    poison_bin = tmp_path / "poison-bin"
    poison_bin.mkdir()
    poison_uv = poison_bin / "uv"
    poison_uv.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    poison_uv.chmod(0o755)
    poison_config = tmp_path / "poison-uv.toml"
    poison_config.write_text("offline = false\n", encoding="utf-8")
    install_task13_runner(monkeypatch)
    monkeypatch.setenv("PATH", f"{poison_bin}:/usr/bin:/bin")
    monkeypatch.setenv("UV_INDEX_URL", "https://invalid.example/simple")
    monkeypatch.setenv("UV_CONFIG_FILE", str(poison_config))
    monkeypatch.setenv("UV_BUILD_CONSTRAINT", str(poison_config))
    monkeypatch.setenv("PIP_INDEX_URL", "https://invalid.example/simple")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(poison_config))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "poison-backend"))

    manifest = build_release_artifacts(root, version=VERSION, output=output)

    provenance = manifest.as_dict()["builder_provenance"]
    assert provenance["uv"]["version"] == _FIXTURE_BUILD_TOOL_VERSION
    assert provenance["build"]["sha256"] == provenance["uv"]["sha256"]
    assert provenance["backend"]["module"] == "setuptools.build_meta"


def test_verifier_rejects_changed_builder_provenance_before_artifact_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)
    build_release_artifacts(root, version=VERSION, output=artifacts)
    manifest_path = artifacts / "SHA256SUMS.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["builder_provenance"]["uv"]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseGateError, match="^artifact_candidate_mismatch$"):
        verify_release(root=root, version=VERSION, artifacts=artifacts)


def _write_owned_publish_tree(path: Path) -> None:
    path.mkdir()
    nested = path / "nested"
    nested.mkdir()
    (path / "safe.txt").write_text("safe\n", encoding="utf-8")
    (nested / "child.txt").write_text("child\n", encoding="utf-8")


def _close_output_destination(destination: object) -> None:
    close = getattr(destination, "close", None)
    if callable(close):
        close()


def _private_staging_paths(parent: Path) -> list[Path]:
    return list(parent.glob(f"{release_artifacts._STAGING_NAME_PREFIX}*"))


def _open_file_descriptor_count() -> int | None:
    for directory in (Path("/dev/fd"), Path("/proc/self/fd")):
        try:
            return len(os.listdir(directory))
        except OSError:
            continue
    return None


def test_publish_holds_verified_parent_fd_when_parent_is_replaced_by_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    external = tmp_path / "external-target"
    external.mkdir()
    moved_parent = tmp_path / "output-parent-moved"
    original = release_artifacts._require_output_absent
    calls = 0

    def swap_parent(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            parent.rename(moved_parent)
            parent.symlink_to(external, target_is_directory=True)
            return
        original(path)

    monkeypatch.setattr(release_artifacts, "_require_output_absent", swap_parent)
    destination = release_artifacts._prepare_output_destination(parent / "release")

    try:
        release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert not (external / "release").exists()
    assert (moved_parent / "release" / "safe.txt").read_text(encoding="utf-8") == "safe\n"
    assert source.exists()


def test_publish_rejects_racing_destination_without_overwrite_or_staging_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    original = release_artifacts._require_output_absent
    calls = 0
    competitor_inode: list[int] = []

    def race_output_absent(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.mkdir()
            competitor_inode.append(path.stat().st_ino)
            return
        original(path)

    monkeypatch.setattr(release_artifacts, "_require_output_absent", race_output_absent)
    destination = release_artifacts._prepare_output_destination(output)

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert output.stat().st_ino == competitor_inode[0]
    assert source.exists()
    assert not list(parent.glob(".mercury-release-*"))


def test_publish_rejects_depth_overflow_and_cleans_private_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    source.mkdir()
    current = source
    for index in range(5):
        current = current / f"depth-{index}"
        current.mkdir()
    (current / "leaf.txt").write_text("deep\n", encoding="utf-8")
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    monkeypatch.setattr(release_artifacts, "_MAX_PUBLICATION_DEPTH", 3, raising=False)
    destination = release_artifacts._prepare_output_destination(output)

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert not output.exists()
    assert _private_staging_paths(parent) == []


def test_publish_rejects_oversized_directory_before_copying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    source.mkdir()
    for index in range(3):
        (source / f"entry-{index}.txt").write_text("safe\n", encoding="utf-8")
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    monkeypatch.setattr(
        release_artifacts,
        "_MAX_PUBLICATION_DIRECTORY_ENTRIES",
        2,
        raising=False,
    )
    destination = release_artifacts._prepare_output_destination(output)

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert not output.exists()
    assert _private_staging_paths(parent) == []


def test_publish_rejects_oversized_file_and_cleans_private_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    source.mkdir()
    (source / "oversized.txt").write_text("four", encoding="utf-8")
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    monkeypatch.setattr(
        release_artifacts,
        "_MAX_PUBLICATION_FILE_BYTES",
        3,
        raising=False,
    )
    destination = release_artifacts._prepare_output_destination(output)

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert not output.exists()
    assert _private_staging_paths(parent) == []


def test_publish_translates_enumeration_failure_and_cleans_private_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    original_scandir = release_artifacts.os.scandir
    calls = 0

    def fail_first_scandir(path: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("enumeration failure")
        return original_scandir(path)

    monkeypatch.setattr(release_artifacts.os, "scandir", fail_first_scandir)
    destination = release_artifacts._prepare_output_destination(output)

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert calls >= 1
    assert not output.exists()
    assert _private_staging_paths(parent) == []


def test_publish_cleans_staging_when_its_initial_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    original_open = release_artifacts._open_directory_at_no_follow
    failed = False

    def fail_first_staging_open(parent_fd: int, name: str) -> int:
        nonlocal failed
        if name.startswith(release_artifacts._STAGING_NAME_PREFIX) and not failed:
            failed = True
            raise OSError("staging open failure")
        return original_open(parent_fd, name)

    monkeypatch.setattr(
        release_artifacts,
        "_open_directory_at_no_follow",
        fail_first_staging_open,
    )
    destination = release_artifacts._prepare_output_destination(output)

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert failed
    assert not output.exists()
    assert _private_staging_paths(parent) == []


def test_preidentity_staging_open_failure_never_removes_external_competitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    external = tmp_path / "external-target"
    external.mkdir()
    (external / "keep.txt").write_text("external\n", encoding="utf-8")
    original_open = release_artifacts._open_directory_at_no_follow
    staging_path: list[Path] = []

    def replace_staging_then_fail(parent_fd: int, name: str) -> int:
        if name.startswith(release_artifacts._STAGING_NAME_PREFIX):
            staging = parent / name
            staging.rmdir()
            external.rename(staging)
            staging_path.append(staging)
            raise OSError("staging open failure after replacement")
        return original_open(parent_fd, name)

    monkeypatch.setattr(
        release_artifacts,
        "_open_directory_at_no_follow",
        replace_staging_then_fail,
    )
    destination = release_artifacts._prepare_output_destination(output)
    before = _open_file_descriptor_count()
    after: int | None = None

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
        after = _open_file_descriptor_count()
    finally:
        _close_output_destination(destination)

    assert len(staging_path) == 1
    assert (staging_path[0] / "keep.txt").read_text(encoding="utf-8") == "external\n"
    if before is not None and after is not None:
        assert after <= before
    assert not output.exists()


def test_publish_never_stats_private_staging_before_no_follow_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    original_stat = release_artifacts.os.stat
    original_fstat = release_artifacts.os.fstat
    original_open = release_artifacts._open_directory_at_no_follow
    staging_fds: set[int] = set()
    identity_ready = False
    staging_stat_calls = 0

    def record_staging_open(parent_fd: int, name: str) -> int:
        fd = original_open(parent_fd, name)
        if name.startswith(release_artifacts._STAGING_NAME_PREFIX):
            staging_fds.add(fd)
        return fd

    def record_staging_fstat(fd: int):
        nonlocal identity_ready
        metadata = original_fstat(fd)
        if fd in staging_fds:
            identity_ready = True
        return metadata

    def fail_staging_path_stat(path: object, *args: object, **kwargs: object):
        nonlocal staging_stat_calls
        if (
            isinstance(path, str)
            and path.startswith(release_artifacts._STAGING_NAME_PREFIX)
            and kwargs.get("dir_fd") is not None
        ):
            staging_stat_calls += 1
            if not identity_ready:
                raise OSError("staging pathname stat ran before no-follow identity")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(release_artifacts, "_open_directory_at_no_follow", record_staging_open)
    monkeypatch.setattr(release_artifacts.os, "fstat", record_staging_fstat)
    monkeypatch.setattr(release_artifacts.os, "stat", fail_staging_path_stat)
    destination = release_artifacts._prepare_output_destination(output)

    try:
        release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    assert identity_ready
    assert staging_stat_calls >= 1
    assert (output / "nested" / "child.txt").read_text(encoding="utf-8") == "child\n"
    assert _private_staging_paths(parent) == []


def test_publish_cleans_staging_when_its_initial_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    staging_fds: set[int] = set()
    original_open = release_artifacts._open_directory_at_no_follow
    original_fstat = release_artifacts.os.fstat
    failed = False

    def record_staging_open(parent_fd: int, name: str) -> int:
        fd = original_open(parent_fd, name)
        if name.startswith(release_artifacts._STAGING_NAME_PREFIX):
            staging_fds.add(fd)
        return fd

    def fail_initial_staging_fstat(fd: int):
        nonlocal failed
        if fd in staging_fds and not failed:
            failed = True
            raise OSError("staging fstat failure")
        return original_fstat(fd)

    monkeypatch.setattr(
        release_artifacts,
        "_open_directory_at_no_follow",
        record_staging_open,
    )
    monkeypatch.setattr(release_artifacts.os, "fstat", fail_initial_staging_fstat)
    destination = release_artifacts._prepare_output_destination(output)
    before = _open_file_descriptor_count()
    after: int | None = None

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
        after = _open_file_descriptor_count()
    finally:
        _close_output_destination(destination)
        for fd in staging_fds:
            with contextlib.suppress(OSError):
                os.close(fd)

    assert failed
    if before is not None and after is not None:
        assert after <= before
    assert not output.exists()
    assert _private_staging_paths(parent) == []


def test_publish_closes_source_fd_when_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    source_fds: set[int] = set()
    original_open = release_artifacts._open_directory_path_no_follow
    original_fstat = release_artifacts.os.fstat
    failed = False

    def record_source_open(path: Path) -> int:
        fd = original_open(path)
        if path == source:
            source_fds.add(fd)
        return fd

    def fail_source_fstat(fd: int):
        nonlocal failed
        if fd in source_fds and not failed:
            failed = True
            raise OSError("source fstat failure")
        return original_fstat(fd)

    monkeypatch.setattr(release_artifacts, "_open_directory_path_no_follow", record_source_open)
    monkeypatch.setattr(release_artifacts.os, "fstat", fail_source_fstat)
    destination = release_artifacts._prepare_output_destination(output)
    before = _open_file_descriptor_count()
    after: int | None = None

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
        after = _open_file_descriptor_count()
    finally:
        _close_output_destination(destination)
        for fd in source_fds:
            with contextlib.suppress(OSError):
                os.close(fd)

    assert failed
    if before is not None and after is not None:
        assert after <= before
    assert not output.exists()
    assert _private_staging_paths(parent) == []


def test_private_staging_cleanup_never_removes_a_competing_external_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "release"
    external = tmp_path / "external-target"
    external.mkdir()
    (external / "keep.txt").write_text("external\n", encoding="utf-8")

    def replace_staging_with_external(_source: Path, _destination_fd: int) -> None:
        staging = next(iter(_private_staging_paths(parent)))
        staging.rmdir()
        external.rename(staging)
        raise OSError("copy failure after staging replacement")

    monkeypatch.setattr(release_artifacts, "_copy_verified_tree", replace_staging_with_external)
    destination = release_artifacts._prepare_output_destination(output)

    try:
        with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
            release_artifacts._publish_owned_directory(source, destination)
    finally:
        _close_output_destination(destination)

    staged_external = next(iter(_private_staging_paths(parent)))
    assert (staged_external / "keep.txt").read_text(encoding="utf-8") == "external\n"
    assert not output.exists()


def _second_writable_filesystem(tmp_path: Path) -> Path:
    baseline_device = tmp_path.stat().st_dev
    candidates = (
        Path(RELEASE_CROSS_FILESYSTEM_KNOWN_LINUX_DEVICE),
        Path("/Volumes"),
        Path("/private/var/tmp"),
        Path(os.environ.get("TMPDIR", "/tmp")),
        Path.home(),
    )
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if not candidate.is_dir() or candidate.stat().st_dev == baseline_device:
                continue
            created = Path(tempfile.mkdtemp(prefix="mercury-release-device-", dir=candidate))
        except OSError:
            continue
        if created.stat().st_dev != baseline_device:
            return created
        shutil.rmtree(created)
    pytest.skip(RELEASE_CROSS_FILESYSTEM_CAPABILITY_SKIP_REASON)


def test_publish_copies_verified_tree_to_distinct_destination_device(tmp_path: Path) -> None:
    source = tmp_path / "owned-source"
    _write_owned_publish_tree(source)
    mount_root = _second_writable_filesystem(tmp_path)
    destination = release_artifacts._prepare_output_destination(mount_root / "release")

    try:
        release_artifacts._publish_owned_directory(source, destination)
        assert (mount_root / "release" / "nested" / "child.txt").read_text(
            encoding="utf-8"
        ) == "child\n"
    finally:
        _close_output_destination(destination)
        shutil.rmtree(mount_root, ignore_errors=True)

    assert source.exists()


def test_release_artifacts_publish_to_distinct_destination_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    mount_root = _second_writable_filesystem(tmp_path)
    output = mount_root / "artifacts"
    install_task13_runner(monkeypatch)

    try:
        manifest = build_release_artifacts(root, version=VERSION, output=output)
        assert (output / "SHA256SUMS.json").is_file()
        assert {artifact.file_name for artifact in manifest.artifacts} == {
            path.name for path in output.iterdir() if path.name != "SHA256SUMS.json"
        }
    finally:
        shutil.rmtree(mount_root, ignore_errors=True)


def test_cross_filesystem_release_coverage_is_required_and_not_integration_marked() -> None:
    tests = (
        test_publish_copies_verified_tree_to_distinct_destination_device,
        test_release_artifacts_publish_to_distinct_destination_device,
    )

    expected_test_ids = {
        "tests/test_release_artifacts.py::"
        "test_publish_copies_verified_tree_to_distinct_destination_device",
        "tests/test_release_artifacts.py::"
        "test_release_artifacts_publish_to_distinct_destination_device",
    }
    assert expected_test_ids == RELEASE_REQUIRED_CROSS_FILESYSTEM_TEST_IDS
    expected_skip_audit_contract = {
        "capability_skip_reason": RELEASE_CROSS_FILESYSTEM_CAPABILITY_SKIP_REASON,
        "known_linux_distinct_device": RELEASE_CROSS_FILESYSTEM_KNOWN_LINUX_DEVICE,
        "required_nodeids": tuple(sorted(expected_test_ids)),
    }
    assert expected_skip_audit_contract == RELEASE_TEST_SKIP_AUDIT_CONTRACT
    assert {function.__name__ for function in tests} == {
        "test_publish_copies_verified_tree_to_distinct_destination_device",
        "test_release_artifacts_publish_to_distinct_destination_device",
    }
    for function in tests:
        marks = getattr(function, "pytestmark", ())
        assert all(mark.name != "integration" for mark in marks)
