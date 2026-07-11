from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote, quote_plus

import httpx
import pytest

from mercury_tools.drivers.base import ConnectorAuthError, DriverConfigurationError
from mercury_tools.drivers.flowaccount import FlowAccountDriver, _flowaccount_body_failed
from mercury_tools.drivers.peak import PeakDriver
from mercury_tools.drivers.registry import DriverRegistry, build_generic_registry
from mercury_tools.local.repository import RepositoryConfig


@pytest.mark.asyncio
async def test_flowaccount_production_uses_exact_v1_token_and_company_probe() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path == "/v1/token":
            assert parse_qs(request.content.decode()) == {
                "grant_type": ["client_credentials"],
                "scope": ["flowaccount-api"],
                "client_id": ["client-id"],
                "client_secret": ["client-secret"],
            }
            return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx.Response(200, json={"companyName": "Example Books"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        auth = await FlowAccountDriver().prepare_auth(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            client=client,
        )
        probe = await FlowAccountDriver().validate_credentials(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            client=client,
        )

    assert auth.expires_at is not None
    assert auth.expires_at > datetime.now(UTC)
    assert probe.status == "connected"
    assert probe.company_name == "Example Books"
    assert probe.details == {"token_status": 200, "company_info_status": 200}
    assert calls == [
        ("POST", "https://openapi.flowaccount.com/v1/token"),
        ("POST", "https://openapi.flowaccount.com/v1/token"),
        ("GET", "https://openapi.flowaccount.com/v1/company/info"),
    ]


@pytest.mark.asyncio
async def test_flowaccount_sandbox_uses_exact_test_token_and_company_probe() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/test/token":
            return httpx.Response(200, json={"access_token": "sandbox-token"})
        assert request.headers["Authorization"] == "Bearer sandbox-token"
        return httpx.Response(200, json={"company_name": "Sandbox Books"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await FlowAccountDriver().validate_credentials(
            environment="sandbox",
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            client=client,
        )

    assert probe.status == "connected"
    assert probe.company_name == "Sandbox Books"
    assert calls == [
        "https://openapi.flowaccount.com/test/token",
        "https://openapi.flowaccount.com/test/company/info",
    ]


@pytest.mark.asyncio
async def test_flowaccount_requires_the_exact_credential_bundle() -> None:
    driver = FlowAccountDriver()
    transport = httpx.MockTransport(lambda request: httpx.Response(500))

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ConnectorAuthError, match="^credential_missing$"):
            await driver.prepare_auth(
                environment="production",
                credentials={"client_id": "client-id"},
                client=client,
            )
        with pytest.raises(ConnectorAuthError, match="^credential_undeclared$"):
            await driver.prepare_auth(
                environment="production",
                credentials={
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "token": "injected-token",
                },
                client=client,
            )

    assert [field.name for field in driver.credential_fields("production")] == [
        "client_id",
        "client_secret",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_payload",
    [
        {"access_token": "access-token", "status": False},
        {"status": True},
        {"access_token": ""},
    ],
)
async def test_flowaccount_http_200_token_body_failures_are_safe(
    token_payload: dict[str, object],
) -> None:
    secret = "client-secret-value"
    token = "access-token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/token"
        return httpx.Response(200, json={**token_payload, "detail": f"{secret} {token}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await FlowAccountDriver().validate_credentials(
            environment="production",
            credentials={"client_id": "client-id-value", "client_secret": secret},
            client=client,
        )

    assert probe.status == "failed"
    assert probe.details["error"] == "flowaccount_token_failed"
    rendered = json.dumps(probe.public_dict()) + repr(probe)
    assert secret not in rendered
    assert token not in rendered


@pytest.mark.asyncio
async def test_flowaccount_company_name_redacts_reversibly_encoded_secret_and_token() -> None:
    client_secret = "client secret value"
    access_token = "access token value"
    encoded = quote_plus(access_token)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": access_token})
        return httpx.Response(
            200,
            json={
                "companyName": (
                    f"Example Books?token={encoded}&secret={quote_plus(client_secret)}"
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await FlowAccountDriver().validate_credentials(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": client_secret},
            client=client,
        )

    assert probe.status == "connected"
    assert probe.company_name == "[REDACTED]"
    rendered = json.dumps(probe.public_dict()) + repr(probe)
    assert client_secret not in rendered
    assert access_token not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "submitted_secret",
    [
        quote("client +/ secret%", safe=""),
        quote_plus("client +/ secret%", safe=""),
        quote(quote_plus("client +/ secret%", safe=""), safe="").replace("%2B", "%2b"),
    ],
)
async def test_flowaccount_company_redaction_normalizes_both_candidate_and_submitted_credentials(
    submitted_secret: str,
) -> None:
    raw_secret = "client +/ secret%"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "safe-token"})
        return httpx.Response(200, json={"companyName": f"Books {raw_secret}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await FlowAccountDriver().validate_credentials(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": submitted_secret},
            client=client,
        )

    assert probe.status == "connected"
    assert probe.company_name == "[REDACTED]"


@pytest.mark.asyncio
@pytest.mark.parametrize("access_token", ["client secret", quote_plus("client secret")])
async def test_flowaccount_rejects_issued_tokens_equivalent_to_submitted_credentials_before_probe(
    access_token: str,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": access_token})
        return httpx.Response(200, json={"companyName": "Unexpected probe"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await FlowAccountDriver().validate_credentials(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": "client secret"},
            client=client,
        )

    assert probe.status == "failed"
    assert probe.details["error"] == "flowaccount_token_failed"
    assert calls == ["/v1/token"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_payload", "company_payload", "expected_calls"),
    [
        ({"access_token": "safe-token", "code": "invalid_client"}, None, ["/v1/token"]),
        ({"access_token": "safe-token"}, {"resCode": "FAILED"}, ["/v1/token", "/v1/company/info"]),
    ],
)
async def test_flowaccount_nonblank_nonnumeric_provider_codes_are_failures(
    token_payload: dict[str, str],
    company_payload: dict[str, str] | None,
    expected_calls: list[str],
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json=token_payload)
        return httpx.Response(200, json=company_payload or {"companyName": "Unexpected probe"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await FlowAccountDriver().validate_credentials(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            client=client,
        )

    assert probe.status == "failed"
    assert calls == expected_calls


@pytest.mark.parametrize("field", ["code", "resCode"])
@pytest.mark.parametrize("value", [{"nested": "zero"}, [0], (0,), object()])
def test_flowaccount_rejects_structured_provider_codes(field: str, value: object) -> None:
    assert _flowaccount_body_failed({field: value})


@pytest.mark.parametrize("field", ["code", "resCode"])
@pytest.mark.parametrize("value", [None, False, 0, 0.0, "0", " 0.0 "])
def test_flowaccount_preserves_zero_provider_codes_as_success(field: str, value: object) -> None:
    assert not _flowaccount_body_failed({field: value})


@pytest.mark.parametrize("field", ["code", "resCode"])
@pytest.mark.parametrize("value", [True, 1, -1, 0.5, "", "not-a-code"])
def test_flowaccount_rejects_nonzero_and_nonnumeric_provider_codes(
    field: str,
    value: object,
) -> None:
    assert _flowaccount_body_failed({field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_payload",
    [
        '{"access_token":"safe-token","expires_in":NaN}',
        '{"access_token":"safe-token","expires_in":Infinity}',
        '{"access_token":"safe-token","expires_in":-Infinity}',
        '{"access_token":"safe-token","expires_in":' + str(10**1000) + "}",
    ],
)
async def test_flowaccount_ignores_nonfinite_and_out_of_range_expiry_values(
    token_payload: str,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=token_payload,
                headers={"Content-Type": "application/json"},
            )
        )
    ) as client:
        auth = await FlowAccountDriver().prepare_auth(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            client=client,
        )

    assert auth.expires_at is None


def test_flowaccount_interprets_http_200_provider_body_failure_and_redacts_response(
    action_factory,
) -> None:
    result = FlowAccountDriver().interpret_response(
        action=action_factory(response_redaction=("data.account_number",)),
        response=httpx.Response(
            200,
            json={
                "status": False,
                "data": {"account_number": "visible-account"},
                "access_token": "provider-token",
            },
        ),
        dispatched=True,
    )

    assert result.status == "failed"
    assert result.summary == "provider_response_failed"
    assert result.public_dict()["data"] == {
        "status": False,
        "data": {"account_number": "[REDACTED]"},
        "access_token": "[REDACTED]",
    }


def test_generic_registry_does_not_import_provider_modules() -> None:
    script = "\n".join(
        (
            "import sys",
            "from mercury_tools.drivers.registry import build_generic_registry",
            "build_generic_registry()",
            "assert 'mercury_tools.drivers.flowaccount' not in sys.modules",
            "assert 'mercury_tools.drivers.peak' not in sys.modules",
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert build_generic_registry().public_summaries()


def test_registry_for_repository_builds_builtins_factories_and_trusted_generic_connector() -> None:
    config = RepositoryConfig(
        trusted_hosts={
            "custom-books": {
                "production": ("api.example.test", "auth.example.test"),
                "sandbox": ("sandbox.example.test", "auth.example.test"),
            }
        },
        connectors={
            "custom-books": {
                "production": {
                    "driver_id": "oauth_client_credentials",
                    "base_url": "https://api.example.test/v1",
                    "auth_settings": {"token_url": "https://auth.example.test/token"},
                    "network_policy": {"allow_private_network": False},
                },
                "sandbox": {
                    "driver_id": "oauth_client_credentials",
                    "base_url": "https://sandbox.example.test/v1",
                    "auth_settings": {"token_url": "https://auth.example.test/token"},
                    "network_policy": {"allow_private_network": False},
                },
            }
        },
    )

    registry = DriverRegistry.for_repository(config)
    custom = registry.get("custom-books")
    summaries = registry.summaries()

    assert registry.get("flowaccount").driver_id == "flowaccount_oauth"
    assert registry.get("peak").driver_id == "peak_hmac_sha1"
    assert custom.resolve_base_url("production") == "https://api.example.test/v1"
    assert custom.resolve_base_url("sandbox") == "https://sandbox.example.test/v1"
    assert [item["driver_id"] for item in summaries if item["entry_type"] == "factory"] == [
        "api_key_header",
        "api_key_query",
        "basic",
        "bearer",
        "oauth_client_credentials",
    ]
    assert json.loads(json.dumps(registry.public_summaries())) == registry.public_summaries()
    with pytest.raises(TypeError):
        summaries[0]["driver_id"] = "changed"  # type: ignore[index]


def test_registry_for_repository_revalidates_manual_config_before_loading_drivers() -> None:
    config = RepositoryConfig(
        schema_version=2,
        trusted_hosts={"custom": {"production": ("opaque.example.test",)}},
        connectors={
            "custom": {
                "production": {
                    "driver_id": "bearer",
                    "base_url": "https://opaque.example.test/v1",
                    "auth_settings": {},
                    "network_policy": {"allow_private_network": False},
                }
            }
        },
    )

    with pytest.raises(DriverConfigurationError, match="^repository_connector_invalid$") as error:
        DriverRegistry.for_repository(config)

    assert "opaque.example.test" not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.example.test/%ZZ",
        "https://api.example.test:",
        "https://api.example.test/path with-space",
        "https://api.example.test/path\\segment",
        "https://api.example.test/path\x00segment",
    ],
)
def test_registry_rejects_invalid_endpoints_before_constructing_provider_drivers(
    endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[str] = []

    def unexpected_flowaccount_constructor(self: object) -> None:
        constructed.append("flowaccount")

    def unexpected_peak_constructor(self: object) -> None:
        constructed.append("peak")

    monkeypatch.setattr(FlowAccountDriver, "__init__", unexpected_flowaccount_constructor)
    monkeypatch.setattr(PeakDriver, "__init__", unexpected_peak_constructor)
    config = RepositoryConfig(
        trusted_hosts={"custom": {"production": ("api.example.test",)}},
        connectors={
            "custom": {
                "production": {
                    "driver_id": "bearer",
                    "base_url": endpoint,
                    "auth_settings": {},
                    "network_policy": {"allow_private_network": False},
                }
            }
        },
    )

    with pytest.raises(DriverConfigurationError, match="^repository_connector_invalid$"):
        DriverRegistry.for_repository(config)

    assert constructed == []


@pytest.mark.parametrize(
    ("client_id_name", "client_secret_name"),
    [
        ("application", "APPLICATION"),
        ("grant_type", "application_secret"),
        ("application_id", "SCOPE"),
    ],
)
def test_registry_for_repository_rejects_colliding_oauth_form_parameter_names(
    client_id_name: str,
    client_secret_name: str,
) -> None:
    config = RepositoryConfig(
        trusted_hosts={"custom": {"production": ("api.example.test", "auth.example.test")}},
        connectors={
            "custom": {
                "production": {
                    "driver_id": "oauth_client_credentials",
                    "base_url": "https://api.example.test/v1",
                    "auth_settings": {
                        "token_url": "https://auth.example.test/token",
                        "client_id_name": client_id_name,
                        "client_secret_name": client_secret_name,
                    },
                    "network_policy": {"allow_private_network": False},
                }
            }
        },
    )

    with pytest.raises(DriverConfigurationError, match="^repository_connector_invalid$"):
        DriverRegistry.for_repository(config)


def test_registry_for_repository_allows_only_environment_specific_oauth_token_urls() -> None:
    config = RepositoryConfig(
        trusted_hosts={
            "custom": {
                "production": ("api.example.test", "auth.example.test"),
                "sandbox": ("sandbox.example.test", "sandbox-auth.example.test"),
            }
        },
        connectors={
            "custom": {
                "production": {
                    "driver_id": "oauth_client_credentials",
                    "base_url": "https://api.example.test/v1",
                    "auth_settings": {
                        "token_url": "https://auth.example.test/token",
                        "client_id_name": "application_id",
                        "client_secret_name": "application_secret",
                        "grant_type": "client_credentials",
                        "scope": "ledger.read",
                    },
                    "network_policy": {"allow_private_network": False},
                },
                "sandbox": {
                    "driver_id": "oauth_client_credentials",
                    "base_url": "https://sandbox.example.test/v1",
                    "auth_settings": {
                        "token_url": "https://sandbox-auth.example.test/token",
                        "client_id_name": "application_id",
                        "client_secret_name": "application_secret",
                        "grant_type": "client_credentials",
                        "scope": "ledger.read",
                    },
                    "network_policy": {"allow_private_network": False},
                },
            }
        },
    )

    driver = DriverRegistry.for_repository(config).get("custom")

    assert driver.resolve_base_url("sandbox") == "https://sandbox.example.test/v1"


@pytest.mark.parametrize(
    "config, code",
    [
        (
            RepositoryConfig(
                trusted_hosts={"custom": {"production": ("api.example.test",)}},
                connectors={
                    "custom": {
                        "production": {
                            "driver_id": "oauth_client_credentials",
                            "base_url": "https://api.example.test/v1",
                            "auth_settings": {"token_url": "https://auth.example.test/token"},
                            "network_policy": {"allow_private_network": False},
                        }
                    }
                },
            ),
            "repository_trusted_hosts_mismatch",
        ),
        (
            RepositoryConfig(
                trusted_hosts={
                    "custom": {
                        "production": ("api.example.test",),
                        "sandbox": ("sandbox.example.test",),
                    }
                },
                connectors={
                    "custom": {
                        "production": {
                            "driver_id": "bearer",
                            "base_url": "https://api.example.test/v1",
                            "auth_settings": {},
                            "network_policy": {"allow_private_network": False},
                        },
                        "sandbox": {
                            "driver_id": "basic",
                            "base_url": "https://sandbox.example.test/v1",
                            "auth_settings": {},
                            "network_policy": {"allow_private_network": False},
                        },
                    }
                },
            ),
            "repository_connector_mismatch",
        ),
        (
            RepositoryConfig(
                trusted_hosts={},
                connectors={"custom": {"production": {"base_url": "https://opaque.test"}}},
            ),
            "repository_connector_invalid",
        ),
        (
            RepositoryConfig(
                trusted_hosts={"custom": {"production": ("api.example.test",)}},
                connectors={
                    "custom": {
                        "production": {
                            "driver_id": "bearer",
                            "base_url": "https://api.example.test/v1",
                            "auth_settings": {"unexpected": "opaque-auth-value"},
                            "network_policy": {"allow_private_network": False},
                        }
                    }
                },
            ),
            "repository_connector_invalid",
        ),
    ],
)
def test_registry_for_repository_rejects_untrusted_mixed_and_malformed_records(
    config: RepositoryConfig,
    code: str,
) -> None:
    with pytest.raises(DriverConfigurationError, match=rf"^{code}$") as caught:
        DriverRegistry.for_repository(config)

    assert "opaque.test" not in str(caught.value)
