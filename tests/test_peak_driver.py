from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import quote_plus

import httpx
import pytest

from mercury_tools.drivers.base import ConnectorAuthError
from mercury_tools.drivers.peak import PeakDriver, peak_headers, peak_signature


def test_peak_signature_matches_known_timestamp_and_headers_include_all_required_values() -> None:
    timestamp = "20260711120000"
    assert peak_signature(timestamp, "connect-id") == hmac.new(
        b"connect-id", b"20260711120000", hashlib.sha1
    ).hexdigest()

    headers = peak_headers(
        timestamp=timestamp,
        connect_id="connect-id",
        application_code="application-code",
        client_token="client-token",
        user_token="user-token",
    )
    assert headers == {
        "Application-Code": "application-code",
        "Client-Token": "client-token",
        "User-Token": "user-token",
        "Time-Stamp": timestamp,
        "Time-Signature": peak_signature(timestamp, "connect-id"),
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "base_url"),
    [
        ("production", "https://api.peakaccount.com/api/v1"),
        ("uat", "https://peakengineapidev.azurewebsites.net/api/v1"),
        ("sandbox", "https://peakengineapidev.azurewebsites.net/api/v1"),
    ],
)
async def test_peak_uses_manifest_environment_base_and_safe_user_probe(
    environment: str,
    base_url: str,
) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.headers["Application-Code"] == "application-code"
        assert request.headers["User-Token"] == "user-token"
        assert request.headers["Content-Type"] == "application/json"
        assert len(request.headers["Time-Signature"]) == 40
        if request.url.path.endswith("/clienttoken"):
            assert request.headers["Client-Token"] == ""
            assert json.loads(request.content) == {
                "PeakClientToken": {"connectId": "connect-id", "password": "connect-key"}
            }
            return httpx.Response(
                200,
                json={"PeakClientToken": {"resCode": "200", "token": "client-token"}},
            )
        assert request.headers["Client-Token"] == "client-token"
        return httpx.Response(200, json={"PeakUser": {"resCode": "200"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        auth = await PeakDriver().prepare_auth(
            environment=environment,
            credentials={
                "connect_id": "connect-id",
                "connect_key": "connect-key",
                "application_code": "application-code",
                "user_token": "user-token",
            },
            client=client,
        )
        probe = await PeakDriver().validate_credentials(
            environment=environment,
            credentials={
                "connect_id": "connect-id",
                "connect_key": "connect-key",
                "application_code": "application-code",
                "user_token": "user-token",
            },
            client=client,
        )

    assert auth.expires_at is not None
    assert auth.expires_at > datetime.now(UTC)
    assert probe.status == "connected"
    assert probe.details == {
        "clienttoken_status": 200,
        "user_status": 200,
        "user_res_code": "200",
    }
    assert calls == [
        ("POST", f"{base_url}/clienttoken"),
        ("POST", f"{base_url}/clienttoken"),
        ("GET", f"{base_url}/user"),
    ]


@pytest.mark.asyncio
async def test_peak_requires_exact_four_credential_fields() -> None:
    driver = PeakDriver()
    transport = httpx.MockTransport(lambda request: httpx.Response(500))

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ConnectorAuthError, match="^credential_missing$"):
            await driver.prepare_auth(
                environment="production",
                credentials={"connect_id": "connect-id"},
                client=client,
            )
        with pytest.raises(ConnectorAuthError, match="^credential_undeclared$"):
            await driver.prepare_auth(
                environment="production",
                credentials={
                    "connect_id": "connect-id",
                    "connect_key": "connect-key",
                    "application_code": "application-code",
                    "user_token": "user-token",
                    "client_token": "injected-token",
                },
                client=client,
            )

    assert [field.name for field in driver.credential_fields("production")] == [
        "connect_id",
        "connect_key",
        "application_code",
        "user_token",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_node",
    [
        {"resCode": "400", "token": "client-token"},
        {"resCode": "200", "token": ""},
        {"resCode": "200"},
    ],
)
async def test_peak_http_200_token_failures_are_not_success_and_never_echo_values(
    token_node: dict[str, str],
) -> None:
    credentials = {
        "connect_id": "connect-id-value",
        "connect_key": "connect-key-value",
        "application_code": "application-code-value",
        "user_token": "user-token-value",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "PeakClientToken": {
                    **token_node,
                    "resDesc": quote_plus("connect-key-value user-token-value client-token"),
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await PeakDriver().validate_credentials(
            environment="production",
            credentials=credentials,
            client=client,
        )

    assert probe.status == "failed"
    assert probe.details["error"] == "peak_client_token_failed"
    rendered = json.dumps(probe.public_dict()) + repr(probe)
    for value in (*credentials.values(), "client-token"):
        assert value not in rendered


@pytest.mark.asyncio
async def test_peak_http_200_user_rescode_failure_is_not_success_and_is_redacted() -> None:
    credentials = {
        "connect_id": "connect-id",
        "connect_key": "connect-key",
        "application_code": "application-code",
        "user_token": "user-token",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/clienttoken"):
            return httpx.Response(
                200,
                json={"PeakClientToken": {"resCode": "200", "token": "client-token"}},
            )
        return httpx.Response(
            200,
            json={"PeakUser": {"resCode": "600", "resDesc": quote_plus("client-token")}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await PeakDriver().validate_credentials(
            environment="production",
            credentials=credentials,
            client=client,
        )

    assert probe.status == "failed"
    assert probe.details == {
        "error": "peak_user_failed",
        "clienttoken_status": 200,
        "user_status": 200,
    }
    assert "client-token" not in json.dumps(probe.public_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_node", ["PeakClientToken", "PeakUser"])
async def test_peak_auth_requires_exact_response_nodes_without_top_level_fallback(
    missing_node: str,
) -> None:
    credentials = {
        "connect_id": "connect-id",
        "connect_key": "connect-key",
        "application_code": "application-code",
        "user_token": "user-token",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/clienttoken"):
            if missing_node == "PeakClientToken":
                return httpx.Response(200, json={"resCode": "200", "token": "safe-token"})
            return httpx.Response(
                200,
                json={"PeakClientToken": {"resCode": "200", "token": "safe-token"}},
            )
        return httpx.Response(200, json={"resCode": "200"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await PeakDriver().validate_credentials(
            environment="production",
            credentials=credentials,
            client=client,
        )

    assert probe.status == "failed"
    assert probe.details["error"] in {"peak_client_token_failed", "peak_user_failed"}


@pytest.mark.asyncio
async def test_peak_rejects_issued_client_tokens_equivalent_to_submitted_credentials_before_probe(
) -> None:
    credentials = {
        "connect_id": "connect-id",
        "connect_key": "connect-key",
        "application_code": "application-code",
        "user_token": "user token",
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"PeakClientToken": {"resCode": "200", "token": quote_plus("user token")}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await PeakDriver().validate_credentials(
            environment="production",
            credentials=credentials,
            client=client,
        )

    assert probe.details["error"] == "peak_client_token_failed"
    assert calls == ["/api/v1/clienttoken"]


def test_peak_interprets_http_200_rescode_failure_and_sanitizes_sensitive_response(
    action_factory,
) -> None:
    result = PeakDriver().interpret_response(
        action=action_factory(response_redaction=("PeakUser.account",)),
        response=httpx.Response(
            200,
            json={
                "PeakUser": {
                    "resCode": "400",
                    "account": "visible-account",
                    "token": "provider-token",
                }
            },
        ),
        dispatched=True,
    )

    assert result.status == "failed"
    assert result.http_status == 200
    assert result.summary == "provider_response_failed"
    assert result.public_dict()["data"] == {
        "PeakUser": {
            "resCode": "400",
            "account": "[REDACTED]",
            "token": "[REDACTED]",
        }
    }


def test_peak_interprets_any_nested_rescode_failure_without_provider_payload_leaks(
    action_factory,
) -> None:
    result = PeakDriver().interpret_response(
        action=action_factory(),
        response=httpx.Response(
            200,
            json={
                "resCode": "200",
                "PeakInvoices": {"resCode": "400", "token": "provider-token"},
            },
        ),
        dispatched=True,
    )

    assert result.status == "failed"
    assert result.summary == "provider_response_failed"
    assert "provider-token" not in json.dumps(result.public_dict())
