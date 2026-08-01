from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.models import GateStatus

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "infra/aws/wave0/environment.yaml"


def test_config_locks_region_accounts_and_repository() -> None:
    config = load_wave0_config(CONFIG_PATH)
    assert config.primary_region == "ap-southeast-1"
    assert tuple(item.alias for item in config.accounts) == (
        "mercury-nonprod",
        "mercury-prod",
    )
    assert tuple(item.github_environment for item in config.accounts) == (
        "nonprod",
        "production",
    )
    assert config.github_repository == "natthaphonchop2-creator/mercury-tools"


def test_config_rejects_non_singapore_region(tmp_path: Path) -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["primary_region"] = "us-east-1"
    path = tmp_path / "environment.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_wave0_config(path)


def test_config_requires_distinct_accounts(tmp_path: Path) -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["accounts"][1]["alias"] = "mercury-nonprod"
    path = tmp_path / "environment.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_wave0_config(path)


def test_gate_status_values_are_stable() -> None:
    assert {item.value for item in GateStatus} == {
        "ready",
        "blocked_tooling",
        "blocked_account_access",
        "blocked_region_service",
        "blocked_identity_compatibility",
    }


def test_node_tools_are_exactly_pinned() -> None:
    package = __import__("json").loads((ROOT / "package.json").read_text())
    assert package["private"] is True
    assert package["devDependencies"] == {
        "@aws/agentcore": "0.25.0",
        "aws-cdk": "2.1134.0",
    }
