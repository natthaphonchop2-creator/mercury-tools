from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mercury_tools.release.public_tree import build_public_tree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_release_control_contract import ContractError, verify_contract  # noqa: E402


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "candidate"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Mercury Contract Test")
    _git(root, "config", "user.email", "contract@example.invalid")
    (root / "README.md").write_text("Mercury\n", encoding="utf-8")
    (root / ".env").write_text("excluded\n", encoding="utf-8")
    _git(root, "add", "README.md", ".env")
    _git(root, "commit", "-m", "candidate")
    return root, _git(root, "rev-parse", "HEAD")


def test_contract_accepts_equal_independent_output(tmp_path: Path) -> None:
    root, reviewed_sha = _repository(tmp_path)

    def equal_output(_control_root: Path, archive: bytes):
        return build_public_tree(archive).as_dict()

    result = verify_contract(
        mercury_root=root,
        control_root=tmp_path / "control",
        reviewed_sha=reviewed_sha,
        control_runner=equal_output,
    )

    assert result["status"] == "passed"
    assert result["entry_count"] == 1


def test_contract_rejects_altered_control_digest(tmp_path: Path) -> None:
    root, reviewed_sha = _repository(tmp_path)

    def altered_output(_control_root: Path, archive: bytes):
        snapshot = build_public_tree(archive)
        return {
            "digest": "0" * 64,
            "entries": list(snapshot.public_inventory()),
            "schema_version": 1,
        }

    with pytest.raises(ContractError, match="^public_tree_contract_mismatch$"):
        verify_contract(
            mercury_root=root,
            control_root=tmp_path / "control",
            reviewed_sha=reviewed_sha,
            control_runner=altered_output,
        )


def test_contract_rejects_non_commit_identity(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="^reviewed_sha_invalid$"):
        verify_contract(
            mercury_root=tmp_path,
            control_root=tmp_path,
            reviewed_sha="main",
        )
