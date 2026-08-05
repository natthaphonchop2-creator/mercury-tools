from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _smoke_module():
    path = ROOT / "scripts" / "smoke_hosted_plugin.py"
    spec = importlib.util.spec_from_file_location("mercury_hosted_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_access_token_is_explicit_and_whitespace_is_not_accepted(monkeypatch) -> None:
    smoke = _smoke_module()
    monkeypatch.delenv("MERCURY_SMOKE_ACCESS_TOKEN", raising=False)
    assert smoke._access_token_from_environment() is None

    monkeypatch.setenv("MERCURY_SMOKE_ACCESS_TOKEN", "   ")
    assert smoke._access_token_from_environment() is None

    monkeypatch.setenv("MERCURY_SMOKE_ACCESS_TOKEN", "  test-access-token  ")
    assert smoke._access_token_from_environment() == "test-access-token"


def test_v1_health_contract_requires_auth_and_disables_legacy_api() -> None:
    smoke = _smoke_module()

    assert (
        smoke._validate_health_payload(
            {
                "status": "ok",
                "v1_enabled": True,
                "http_auth_required": True,
                "legacy_http_api": "disabled",
            }
        )
        is True
    )


@pytest.mark.parametrize(
    "payload",
    (
        {"status": "degraded", "v1_enabled": True, "http_auth_required": True},
        {"status": "ok", "v1_enabled": True, "http_auth_required": False},
        {
            "status": "ok",
            "v1_enabled": True,
            "http_auth_required": True,
            "legacy_http_api": "enabled",
        },
    ),
)
def test_v1_health_contract_rejects_unsafe_deployment_state(payload) -> None:
    smoke = _smoke_module()

    with pytest.raises(smoke.SmokeError):
        smoke._validate_health_payload(payload)


def test_legacy_health_remains_migration_safe_before_the_v1_deploy() -> None:
    smoke = _smoke_module()

    assert (
        smoke._validate_health_payload(
            {
                "status": "ok",
                "v1_enabled": False,
                "http_auth_required": False,
                "legacy_http_api": "disabled",
            }
        )
        is False
    )
