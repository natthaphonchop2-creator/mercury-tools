from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "release-control" / "scaffold"
POLICY = ROOT / "release-control" / "policy-v0.2.1.json"
VERIFIER = SCAFFOLD / "scripts" / "verify_candidate.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_control_verify_candidate_tests",
        VERIFIER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _configured_release_control(root: Path) -> None:
    shutil.copytree(
        SCAFFOLD,
        root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    policy["bootstrap_state"] = "configured"
    policy["repository_id"] = 101
    policy["reviewed_repository_id"] = 202
    policy["staging_repository"] = "example/mercury-public-staging"
    policy["required_reviewer_ids"] = [303]
    entrypoint = root / "bin" / "mercury-release-control-inspector"
    policy["inspector"]["sha256"] = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
    (root / "policy-v0.2.1.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _unconfigured_release_control(root: Path) -> None:
    shutil.copytree(
        SCAFFOLD,
        root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    shutil.copy2(POLICY, root / "policy-v0.2.1.json")


def test_base_owned_verifier_accepts_identical_configured_control_tree(
    tmp_path: Path,
) -> None:
    module = _module()
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    _configured_release_control(trusted)
    shutil.copytree(trusted, candidate)

    module.verify_candidate(trusted_root=trusted, candidate_root=candidate)


@pytest.mark.parametrize(
    "relative_path",
    (
        ".github/workflows/ci.yml",
        ".github/workflows/publish-v0.2.1.yml",
        "bin/mercury-release-control-inspector",
        "release-notes-v0.2.1.md",
        "scripts/inspector_core.py",
        "scripts/verify_candidate.py",
    ),
)
def test_base_owned_verifier_rejects_any_trusted_control_change(
    tmp_path: Path,
    relative_path: str,
) -> None:
    module = _module()
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    _configured_release_control(trusted)
    shutil.copytree(trusted, candidate)
    target = candidate / relative_path
    target.write_bytes(target.read_bytes() + b"\n# candidate change\n")

    with pytest.raises(module.CandidateVerificationError, match="candidate_control_drift"):
        module.verify_candidate(trusted_root=trusted, candidate_root=candidate)


def test_base_owned_verifier_accepts_valid_policy_bootstrap_transition(
    tmp_path: Path,
) -> None:
    module = _module()
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    _unconfigured_release_control(trusted)
    shutil.copytree(trusted, candidate)
    policy = json.loads((candidate / "policy-v0.2.1.json").read_text(encoding="utf-8"))
    policy["bootstrap_state"] = "configured"
    policy["repository_id"] = 101
    policy["staging_repository"] = "example/mercury-public-staging"
    policy["required_reviewer_ids"] = [303]
    policy["inspector"]["sha256"] = hashlib.sha256(
        (candidate / "bin" / "mercury-release-control-inspector").read_bytes()
    ).hexdigest()
    (candidate / "policy-v0.2.1.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    module.verify_candidate(trusted_root=trusted, candidate_root=candidate)


def test_base_owned_verifier_rejects_invalid_policy_change(tmp_path: Path) -> None:
    module = _module()
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    _configured_release_control(trusted)
    shutil.copytree(trusted, candidate)
    policy = json.loads((candidate / "policy-v0.2.1.json").read_text(encoding="utf-8"))
    policy["required_reviewer_ids"] = []
    (candidate / "policy-v0.2.1.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(module.CandidateVerificationError, match="candidate_policy_invalid"):
        module.verify_candidate(trusted_root=trusted, candidate_root=candidate)


def test_base_owned_verifier_rejects_policy_inspector_digest_mismatch(
    tmp_path: Path,
) -> None:
    module = _module()
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    _configured_release_control(trusted)
    shutil.copytree(trusted, candidate)
    policy = json.loads((candidate / "policy-v0.2.1.json").read_text(encoding="utf-8"))
    policy["inspector"]["sha256"] = "f" * 64
    (candidate / "policy-v0.2.1.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(module.CandidateVerificationError, match="candidate_policy_invalid"):
        module.verify_candidate(trusted_root=trusted, candidate_root=candidate)


@pytest.mark.parametrize("suffix", [".pyc", ".pyo"])
def test_base_owned_verifier_rejects_candidate_python_bytecode(
    tmp_path: Path,
    suffix: str,
) -> None:
    module = _module()
    trusted = tmp_path / "trusted"
    candidate = tmp_path / "candidate"
    _configured_release_control(trusted)
    shutil.copytree(trusted, candidate)
    bytecode = candidate / "scripts" / "__pycache__" / f"trusted_module{suffix}"
    bytecode.parent.mkdir(exist_ok=True)
    bytecode.write_bytes(b"candidate-controlled-bytecode")

    with pytest.raises(module.CandidateVerificationError, match="candidate_control_invalid"):
        module.verify_candidate(trusted_root=trusted, candidate_root=candidate)
