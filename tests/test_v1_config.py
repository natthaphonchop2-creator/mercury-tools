from __future__ import annotations

import base64
from pathlib import Path

import pytest
import yaml

from mercury_tools.config import V1ConfigurationError, load_settings
from mercury_tools.v1.constants import (
    CANONICAL_MCP_RESOURCE,
    MAX_BATCH_DOCUMENTS,
    PREVIEW_TTL_SECONDS,
    V1_VERSION,
)

V1_ENVIRONMENT_VARIABLES = (
    "MERCURY_V1_ENABLED",
    "MERCURY_CANONICAL_MCP_RESOURCE",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_AUTH_ISSUER",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_JWKS_URL",
    "SUPABASE_JWT_AUDIENCE",
    "MERCURY_VAULT_ACTIVE_KEY",
    "MERCURY_VAULT_ACTIVE_KEY_VERSION",
    "MERCURY_VAULT_PREVIOUS_KEY",
    "MERCURY_VAULT_PREVIOUS_KEY_VERSION",
    "FLOWACCOUNT_MCP_SANDBOX_URL",
    "FLOWACCOUNT_MCP_PRODUCTION_URL",
    "FLOWACCOUNT_OAUTH_SANDBOX_AUTHORIZATION_SERVER_ORIGIN",
    "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN",
    "PEAK_MCP_UAT_URL",
    "PEAK_MCP_PRODUCTION_URL",
    "MERCURY_PROVIDER_CALLBACK_BASE_URL",
)


def _isolate_v1_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in V1_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _enable_valid_v1_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_v1_environment(monkeypatch)
    active_key = base64.b64encode(b"a" * 32).decode("ascii")
    previous_key = base64.b64encode(b"b" * 32).decode("ascii")
    values = {
        "MERCURY_V1_ENABLED": "true",
        "MERCURY_CANONICAL_MCP_RESOURCE": CANONICAL_MCP_RESOURCE,
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role-test",
        "SUPABASE_AUTH_ISSUER": "https://project.supabase.co/auth/v1",
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
        "SUPABASE_JWKS_URL": ("https://project.supabase.co/auth/v1/.well-known/jwks.json"),
        "SUPABASE_JWT_AUDIENCE": CANONICAL_MCP_RESOURCE,
        "MERCURY_VAULT_ACTIVE_KEY": active_key,
        "MERCURY_VAULT_ACTIVE_KEY_VERSION": "v1",
        "MERCURY_VAULT_PREVIOUS_KEY": previous_key,
        "MERCURY_VAULT_PREVIOUS_KEY_VERSION": "v0",
        "FLOWACCOUNT_MCP_SANDBOX_URL": "https://flowaccount-sandbox.example.com/mcp",
        "FLOWACCOUNT_MCP_PRODUCTION_URL": "https://flowaccount.example.com/mcp",
        "FLOWACCOUNT_OAUTH_SANDBOX_AUTHORIZATION_SERVER_ORIGIN": (
            "https://identity-sandbox.flowaccount.example.com"
        ),
        "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN": (
            "https://identity.flowaccount.example.com"
        ),
        "PEAK_MCP_UAT_URL": "https://peak-uat.example.com/mcp",
        "PEAK_MCP_PRODUCTION_URL": "https://peak.example.com/mcp",
        "MERCURY_PROVIDER_CALLBACK_BASE_URL": "https://mercury.example.com",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _assert_v1_error(monkeypatch: pytest.MonkeyPatch, *, code: str) -> None:
    with pytest.raises(V1ConfigurationError) as exc_info:
        load_settings().validate_v1()
    assert exc_info.value.code == code


def test_v1_is_disabled_without_explicit_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_v1_environment(monkeypatch)

    settings = load_settings()

    assert settings.v1_enabled is False
    settings.validate_v1()


def test_v1_requires_canonical_https_resource_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    load_settings().validate_v1()

    monkeypatch.setenv("MERCURY_CANONICAL_MCP_RESOURCE", "http://localhost:8000/mcp")
    _assert_v1_error(monkeypatch, code="v1_canonical_resource_invalid")

    monkeypatch.setenv(
        "MERCURY_CANONICAL_MCP_RESOURCE",
        "https://mercury.example.com/mcp",
    )
    _assert_v1_error(monkeypatch, code="v1_canonical_resource_mismatch")

    monkeypatch.setenv("MERCURY_CANONICAL_MCP_RESOURCE", CANONICAL_MCP_RESOURCE)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", "https://mercury.example.com/mcp")
    _assert_v1_error(monkeypatch, code="v1_jwt_audience_mismatch")


@pytest.mark.parametrize(
    ("name", "value", "error_code"),
    [
        ("SUPABASE_AUTH_ISSUER", None, "v1_jwks_configuration_missing"),
        ("SUPABASE_PUBLISHABLE_KEY", None, "v1_publishable_key_missing"),
        ("SUPABASE_JWKS_URL", None, "v1_jwks_configuration_missing"),
        ("SUPABASE_JWT_AUDIENCE", None, "v1_jwks_configuration_missing"),
        ("SUPABASE_JWKS_URL", "http://project.supabase.co/jwks", "v1_jwks_url_invalid"),
        ("MERCURY_VAULT_ACTIVE_KEY", None, "v1_vault_configuration_missing"),
        ("MERCURY_VAULT_ACTIVE_KEY_VERSION", None, "v1_vault_configuration_missing"),
        ("MERCURY_VAULT_ACTIVE_KEY", "not-base64", "v1_vault_key_invalid"),
        (
            "MERCURY_VAULT_ACTIVE_KEY",
            base64.b64encode(b"a" * 31).decode("ascii"),
            "v1_vault_key_invalid",
        ),
        (
            "MERCURY_VAULT_PREVIOUS_KEY_VERSION",
            "",
            "v1_vault_configuration_missing",
        ),
        ("MERCURY_VAULT_PREVIOUS_KEY_VERSION", "v1", "v1_vault_key_version_reused"),
    ],
)
def test_v1_rejects_missing_jwks_or_vault_key_configuration(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
    error_code: str,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    _assert_v1_error(monkeypatch, code=error_code)


@pytest.mark.parametrize(
    "invalid_url",
    (
        "http://project.supabase.co",
        "https://embedded-secret@project.supabase.co",
        "https://project.supabase.co?redirect=attacker",
        "https://project.supabase.co#attacker",
        "https://project.supabase.co/rest/v1",
    ),
)
def test_v1_supabase_data_api_requires_clean_https_project_origin(
    monkeypatch: pytest.MonkeyPatch,
    invalid_url: str,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", invalid_url)

    with pytest.raises(V1ConfigurationError) as exc_info:
        load_settings().validate_v1()

    assert exc_info.value.code == "v1_supabase_url_invalid"
    assert "embedded-secret" not in repr(exc_info.value)
    assert "redirect=attacker" not in repr(exc_info.value)


def test_v1_supabase_data_api_must_match_auth_issuer_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://attacker.supabase.co")

    with pytest.raises(V1ConfigurationError) as exc_info:
        load_settings().validate_v1()

    assert exc_info.value.code == "v1_supabase_origin_mismatch"
    assert "attacker.supabase.co" not in repr(exc_info.value)


def test_v1_requires_service_role_for_durable_provider_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")

    _assert_v1_error(monkeypatch, code="v1_service_role_key_missing")


@pytest.mark.parametrize(
    ("name", "invalid_url"),
    [
        ("FLOWACCOUNT_MCP_SANDBOX_URL", "http://provider.example.com/mcp"),
        (
            "FLOWACCOUNT_MCP_PRODUCTION_URL",
            "https://provider.example.com/mcp?tenant=secret",
        ),
        ("FLOWACCOUNT_MCP_SANDBOX_URL", "https://provider.example.com/mcp?"),
        ("PEAK_MCP_UAT_URL", "https://provider.example.com/mcp#fragment"),
        ("PEAK_MCP_UAT_URL", "https://provider.example.com/mcp#"),
        ("PEAK_MCP_PRODUCTION_URL", "https://provider.example.com:invalid/mcp"),
        (
            "MERCURY_PROVIDER_CALLBACK_BASE_URL",
            "https://user:password@provider.example.com/callback",
        ),
    ],
)
def test_provider_endpoint_overrides_are_server_only_https_urls(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    invalid_url: str,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    monkeypatch.setenv(name, invalid_url)

    _assert_v1_error(monkeypatch, code="v1_provider_url_invalid")


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        (
            "FLOWACCOUNT_OAUTH_SANDBOX_AUTHORIZATION_SERVER_ORIGIN",
            None,
            "v1_flowaccount_authorization_server_origin_missing",
        ),
        (
            "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN",
            "https://identity.flowaccount.example.com/oauth",
            "v1_flowaccount_authorization_server_origin_invalid",
        ),
        (
            "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN",
            "http://identity.flowaccount.example.com",
            "v1_flowaccount_authorization_server_origin_invalid",
        ),
    ],
)
def test_flowaccount_authorization_server_origins_are_explicit_clean_origins(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str | None,
    code: str,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)

    _assert_v1_error(monkeypatch, code=code)


def test_provider_callback_base_is_an_exact_https_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_valid_v1_environment(monkeypatch)
    monkeypatch.setenv(
        "MERCURY_PROVIDER_CALLBACK_BASE_URL",
        "https://mercury.example.com/oauth",
    )

    _assert_v1_error(
        monkeypatch,
        code="v1_provider_callback_base_url_invalid",
    )


def test_v1_preview_ttl_is_exactly_thirty_minutes() -> None:
    assert V1_VERSION == "1.0.0"
    assert CANONICAL_MCP_RESOURCE == "https://mercury-tools-mcp.onrender.com/mcp"
    assert PREVIEW_TTL_SECONDS == 30 * 60
    assert MAX_BATCH_DOCUMENTS == 25


def test_publishable_key_is_loaded_and_declared_without_literal_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_valid_v1_environment(monkeypatch)

    settings = load_settings()
    render = yaml.safe_load((Path(__file__).resolve().parents[1] / "render.yaml").read_text())
    env_vars = {item["key"]: item for item in render["services"][0]["envVars"]}

    assert settings.supabase_publishable_key == "sb_publishable_test"
    assert env_vars["SUPABASE_PUBLISHABLE_KEY"] == {
        "key": "SUPABASE_PUBLISHABLE_KEY",
        "sync": False,
    }
    for name in (
        "FLOWACCOUNT_OAUTH_SANDBOX_AUTHORIZATION_SERVER_ORIGIN",
        "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN",
    ):
        assert env_vars[name] == {
            "key": name,
            "sync": False,
        }
