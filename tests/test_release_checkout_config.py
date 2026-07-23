from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "normalize_release_checkout_config.py"
CHECKOUT_WORKTREE_CONFIG = (
    b"[core]\n"
    b"\tsparseCheckout = false\n"
    b"\tsparseCheckoutCone = false\n"
    b"[index]\n"
    b"\tsparse = false\n"
)


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=check,
        capture_output=True,
        timeout=10,
    )


def _make_repository(tmp_path: Path, values: tuple[str, ...]) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _run(["git", "init", "--quiet"], cwd=repository)
    for value in values:
        _run(["git", "config", "--local", "--add", "gc.auto", value], cwd=repository)
    return repository


def _gc_auto(repository: Path) -> subprocess.CompletedProcess[bytes]:
    return _run(
        ["git", "config", "--null", "--local", "--get-all", "gc.auto"],
        cwd=repository,
        check=False,
    )


def test_normalizer_removes_the_single_checkout_gc_auto_value(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path, ("0",))

    completed = _run([sys.executable, str(SCRIPT)], cwd=repository, check=False)
    remaining = _gc_auto(repository)

    assert completed.returncode == 0
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert remaining.returncode == 1
    assert remaining.stdout == b""
    assert remaining.stderr == b""


def test_normalizer_removes_exact_checkout_worktree_config(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path, ("0",))
    worktree_config = repository / ".git" / "config.worktree"
    worktree_config.write_bytes(CHECKOUT_WORKTREE_CONFIG)

    completed = _run([sys.executable, str(SCRIPT)], cwd=repository, check=False)

    assert completed.returncode == 0
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert not worktree_config.exists()
    assert _gc_auto(repository).returncode == 1


def test_normalizer_handles_sparse_checkout_disable_side_effect(
    tmp_path: Path,
) -> None:
    repository = _make_repository(tmp_path, ("0",))
    _run(["git", "sparse-checkout", "disable"], cwd=repository)
    _run(
        ["git", "config", "--local", "--unset-all", "extensions.worktreeConfig"],
        cwd=repository,
        check=False,
    )
    worktree_config = repository / ".git" / "config.worktree"

    assert worktree_config.read_bytes() == CHECKOUT_WORKTREE_CONFIG

    completed = _run([sys.executable, str(SCRIPT)], cwd=repository, check=False)

    assert completed.returncode == 0
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert not worktree_config.exists()
    assert _gc_auto(repository).returncode == 1


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        CHECKOUT_WORKTREE_CONFIG.replace(b"false", b"true", 1),
        CHECKOUT_WORKTREE_CONFIG + b"[unexpected]\n\tvalue = true\n",
    ),
    ids=("empty", "wrong-value", "extra-entry"),
)
def test_normalizer_rejects_non_exact_worktree_config_without_mutation(
    tmp_path: Path,
    payload: bytes,
) -> None:
    repository = _make_repository(tmp_path, ("0",))
    worktree_config = repository / ".git" / "config.worktree"
    worktree_config.write_bytes(payload)
    before = _gc_auto(repository)

    completed = _run([sys.executable, str(SCRIPT)], cwd=repository, check=False)
    after = _gc_auto(repository)

    assert completed.returncode == 1
    assert completed.stdout == b""
    assert completed.stderr == b"release_checkout_config_invalid\n"
    assert worktree_config.read_bytes() == payload
    assert (after.returncode, after.stdout, after.stderr) == (
        before.returncode,
        before.stdout,
        before.stderr,
    )


def test_normalizer_rejects_symlinked_worktree_config_without_mutation(
    tmp_path: Path,
) -> None:
    repository = _make_repository(tmp_path, ("0",))
    target = tmp_path / "config.worktree"
    target.write_bytes(CHECKOUT_WORKTREE_CONFIG)
    worktree_config = repository / ".git" / "config.worktree"
    worktree_config.symlink_to(target)
    before = _gc_auto(repository)

    completed = _run([sys.executable, str(SCRIPT)], cwd=repository, check=False)
    after = _gc_auto(repository)

    assert completed.returncode == 1
    assert completed.stdout == b""
    assert completed.stderr == b"release_checkout_config_invalid\n"
    assert worktree_config.is_symlink()
    assert target.read_bytes() == CHECKOUT_WORKTREE_CONFIG
    assert (after.returncode, after.stdout, after.stderr) == (
        before.returncode,
        before.stdout,
        before.stderr,
    )


@pytest.mark.parametrize(
    "values",
    (
        (),
        ("",),
        ("0", ""),
        ("0", "0"),
        ("0\n",),
        ("false",),
    ),
    ids=(
        "missing",
        "empty",
        "duplicate-empty",
        "duplicate-zero",
        "newline",
        "wrong-value",
    ),
)
def test_normalizer_rejects_non_exact_checkout_config_without_mutation(
    tmp_path: Path,
    values: tuple[str, ...],
) -> None:
    repository = _make_repository(tmp_path, values)
    before = _gc_auto(repository)

    completed = _run([sys.executable, str(SCRIPT)], cwd=repository, check=False)
    after = _gc_auto(repository)

    assert completed.returncode == 1
    assert completed.stdout == b""
    assert completed.stderr == b"release_checkout_config_invalid\n"
    assert (after.returncode, after.stdout, after.stderr) == (
        before.returncode,
        before.stdout,
        before.stderr,
    )
