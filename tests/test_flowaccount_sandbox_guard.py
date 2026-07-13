from __future__ import annotations

import socket
from collections.abc import Awaitable, Callable
from dataclasses import fields
from pathlib import Path
from typing import Any

import httpx
import pytest

from mercury_tools.drivers.flowaccount import FlowAccountDriver
from mercury_tools.drivers.models import ConnectionProbe
from mercury_tools.qualification.manifest import (
    LIVE_READS,
    SandboxExecutionManifest,
    load_sandbox_execution_manifest,
)
from mercury_tools.qualification.network import (
    SANDBOX_API_URL,
    SANDBOX_TOKEN_URL,
    SandboxTenantBinding,
    execute_flowaccount_sandbox_action,
    require_verified_sandbox_tenant,
    validate_flowaccount_sandbox_origins,
    validate_sandbox_url,
)
from mercury_tools.qualification.semantics import load_actions

ROOT = Path(__file__).resolve().parents[1]
FLOWACCOUNT_ACTIONS = ROOT / "catalog/global/flowaccount/actions.json"
FLOWACCOUNT_MANIFEST = ROOT / "catalog/global/flowaccount/sandbox-execution-manifest.json"


class PeerStream:
    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return ("93.184.216.34", 443) if name == "server_addr" else None


def provider_response(
    request: httpx.Request,
    status: int,
    payload: object,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status,
        request=request,
        json=payload,
        headers=headers,
        extensions={"network_stream": PeerStream()},
    )


@pytest.fixture(autouse=True)
def flowaccount_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )


@pytest.fixture(scope="module")
def flowaccount_actions():
    return load_actions(FLOWACCOUNT_ACTIONS)


@pytest.fixture(scope="module")
def sandbox_manifest() -> SandboxExecutionManifest:
    return load_sandbox_execution_manifest(FLOWACCOUNT_MANIFEST, FLOWACCOUNT_ACTIONS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://openapi.flowaccount.com/test", SANDBOX_API_URL),
        ("https://user:pass@openapi.flowaccount.com/test", SANDBOX_API_URL),
        ("https://openapi.flowaccount.com:444/test", SANDBOX_API_URL),
        ("https://openapi.flowaccount.com:443/test", SANDBOX_API_URL),
        ("https://evil.example/test", SANDBOX_API_URL),
        ("https://openapi.flowaccount.com/v1", SANDBOX_API_URL),
        ("https://openapi.flowaccount.com/test/", SANDBOX_API_URL),
        ("https://openapi.flowaccount.com/test?redirect=/v1", SANDBOX_API_URL),
        ("https://openapi.flowaccount.com/test#fragment", SANDBOX_API_URL),
        ("https://openapi.flowaccount.com/v1/token", SANDBOX_TOKEN_URL),
    ],
)
def test_sandbox_guard_rejects_every_non_exact_origin(value: str, expected: str) -> None:
    with pytest.raises(ValueError, match="^flowaccount_sandbox_origin_invalid$") as raised:
        validate_sandbox_url(value, expected=expected)

    assert value not in str(raised.value)


def test_sandbox_guard_accepts_only_compile_time_api_and_token_urls() -> None:
    origins = validate_flowaccount_sandbox_origins(FlowAccountDriver())

    assert origins.api_url == SANDBOX_API_URL
    assert origins.token_url == SANDBOX_TOKEN_URL


@pytest.mark.asyncio
async def test_production_origin_is_rejected_before_credentials_or_network(
    monkeypatch: pytest.MonkeyPatch,
    action_factory: Callable[..., Any],
) -> None:
    monkeypatch.setitem(
        FlowAccountDriver.TOKEN_URLS,
        "sandbox",
        "https://openapi.flowaccount.com/v1/token",
    )
    calls: list[str] = []

    def load_credentials() -> dict[str, str]:
        calls.append("credentials")
        return {"client_id": "never-read", "client_secret": "never-read"}

    async def request_hook(**_: object) -> object:
        calls.append("request")
        return object()

    transport = httpx.MockTransport(lambda request: calls.append("network") or httpx.Response(500))

    with pytest.raises(ValueError, match="^flowaccount_sandbox_origin_invalid$"):
        await execute_flowaccount_sandbox_action(
            driver=FlowAccountDriver(),
            environment="sandbox",
            load_credentials=load_credentials,
            action=action_factory(),
            manifest=object(),
            request_hook=request_hook,
            transport=transport,
        )

    assert calls == []


def test_verified_tenant_binding_contains_only_sanitized_identity_hash() -> None:
    probe = ConnectionProbe(
        status="connected",
        connector_id="flowaccount",
        environment="sandbox",
        company_name="  Example\tBooks  ",
        details={"company_info_status": 200},
    )

    binding = require_verified_sandbox_tenant(probe)

    assert [field.name for field in fields(binding)] == [
        "connector_id",
        "environment",
        "company_label_sha256",
    ]
    assert binding.connector_id == "flowaccount"
    assert binding.environment == "sandbox"
    assert len(binding.company_label_sha256) == 64
    assert "Example" not in repr(binding)


@pytest.mark.parametrize(
    "probe",
    [
        ConnectionProbe("failed", "flowaccount", "sandbox", None, {}),
        ConnectionProbe("connected", "flowaccount", "production", "Books", {}),
        ConnectionProbe("connected", "other", "sandbox", "Books", {}),
        ConnectionProbe("connected", "flowaccount", "sandbox", None, {}),
        ConnectionProbe("connected", "flowaccount", "sandbox", "[REDACTED]", {}),
    ],
)
def test_unverified_sandbox_tenant_fails_with_payload_free_error(
    probe: ConnectionProbe,
) -> None:
    with pytest.raises(ValueError, match="^flowaccount_sandbox_tenant_unverified$") as raised:
        require_verified_sandbox_tenant(probe)

    assert "Books" not in str(raised.value)


def _unused_hook() -> Callable[..., Awaitable[object]]:
    async def hook(**_: object) -> object:
        raise AssertionError("request hook must not run")

    return hook


def test_tenant_binding_rejects_invalid_hash_without_echoing_it() -> None:
    with pytest.raises(ValueError, match="^flowaccount_sandbox_tenant_binding_invalid$"):
        SandboxTenantBinding(
            connector_id="flowaccount",
            environment="sandbox",
            company_label_sha256="not-a-sha256",
        )


@pytest.mark.asyncio
async def test_orchestration_orders_probe_binding_manifest_and_request_hook(
    monkeypatch: pytest.MonkeyPatch,
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
) -> None:
    action = next(
        item for item in flowaccount_actions if (item.action_id, item.version_id) in LIVE_READS
    )
    events: list[str] = []
    original_require = SandboxExecutionManifest.require_executable

    def tracked_require(self: SandboxExecutionManifest, candidate):
        events.append("manifest")
        return original_require(self, candidate)

    monkeypatch.setattr(SandboxExecutionManifest, "require_executable", tracked_require)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/test/token":
            events.append("token")
            return provider_response(request, 200, {"access_token": "issued-token"})
        if request.url.path == "/test/company/info":
            events.append("probe")
            return provider_response(request, 200, {"companyName": "Example Books"})
        events.append("action")
        return provider_response(request, 200, {"status": True})

    credentials = {"client_id": "client-id", "client_secret": "client-secret"}

    def load_credentials() -> dict[str, str]:
        events.append("credentials")
        return credentials

    async def request_hook(**values: object) -> str:
        events.append("hook")
        client = values["client"]
        auth = values["auth"]
        origins = values["origins"]
        assert isinstance(client, httpx.AsyncClient)
        response = await client.get(
            f"{origins.api_url}/invoices",
            headers=dict(auth.headers),
        )
        assert response.status_code == 200
        return "sent"

    result = await execute_flowaccount_sandbox_action(
        driver=FlowAccountDriver(),
        environment="sandbox",
        load_credentials=load_credentials,
        action=action,
        manifest=sandbox_manifest,
        request_hook=request_hook,
        transport=httpx.MockTransport(handler),
    )

    assert result == "sent"
    assert events == ["credentials", "token", "probe", "manifest", "hook", "action"]
    assert credentials == {}


@pytest.mark.asyncio
async def test_tenant_mismatch_fails_before_action_request(
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
) -> None:
    action = next(
        item for item in flowaccount_actions if (item.action_id, item.version_id) in LIVE_READS
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        payload = (
            {"access_token": "issued-token"}
            if request.url.path == "/test/token"
            else {"companyName": "Unexpected Tenant"}
        )
        return provider_response(request, 200, payload)

    with pytest.raises(ValueError, match="^flowaccount_sandbox_tenant_mismatch$"):
        await execute_flowaccount_sandbox_action(
            driver=FlowAccountDriver(),
            environment="sandbox",
            load_credentials=lambda: {
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
            action=action,
            manifest=sandbox_manifest,
            request_hook=_unused_hook(),
            expected_tenant=SandboxTenantBinding("flowaccount", "sandbox", "0" * 64),
            transport=httpx.MockTransport(handler),
        )

    assert calls == ["/test/token", "/test/company/info"]


@pytest.mark.asyncio
async def test_redirected_probe_is_not_followed_and_fails_before_action(
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
) -> None:
    action = next(
        item for item in flowaccount_actions if (item.action_id, item.version_id) in LIVE_READS
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/test/token":
            return provider_response(request, 200, {"access_token": "issued-token"})
        return provider_response(
            request,
            302,
            {},
            headers={"Location": "https://openapi.flowaccount.com/v1/company/info"},
        )

    with pytest.raises(ValueError, match="^flowaccount_sandbox_tenant_unverified$"):
        await execute_flowaccount_sandbox_action(
            driver=FlowAccountDriver(),
            environment="sandbox",
            load_credentials=lambda: {
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
            action=action,
            manifest=sandbox_manifest,
            request_hook=_unused_hook(),
            transport=httpx.MockTransport(handler),
        )

    assert calls == ["/test/token", "/test/company/info"]


@pytest.mark.asyncio
async def test_manifest_denial_fails_before_action_request(
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
) -> None:
    action = next(
        item for item in flowaccount_actions if (item.action_id, item.version_id) not in LIVE_READS
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        payload = (
            {"access_token": "issued-token"}
            if request.url.path == "/test/token"
            else {"companyName": "Example Books"}
        )
        return provider_response(request, 200, payload)

    with pytest.raises(PermissionError, match="^sandbox_action_not_executable$"):
        await execute_flowaccount_sandbox_action(
            driver=FlowAccountDriver(),
            environment="sandbox",
            load_credentials=lambda: {
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
            action=action,
            manifest=sandbox_manifest,
            request_hook=_unused_hook(),
            transport=httpx.MockTransport(handler),
        )

    assert calls == ["/test/token", "/test/company/info"]


@pytest.mark.asyncio
async def test_wrong_environment_fails_before_credentials_and_network(
    action_factory: Callable[..., Any],
) -> None:
    calls: list[str] = []

    with pytest.raises(ValueError, match="^flowaccount_sandbox_environment_invalid$"):
        await execute_flowaccount_sandbox_action(
            driver=FlowAccountDriver(),
            environment="production",
            load_credentials=lambda: calls.append("credentials") or {},
            action=action_factory(),
            manifest=object(),
            request_hook=_unused_hook(),
            transport=httpx.MockTransport(
                lambda request: calls.append("network") or httpx.Response(500)
            ),
        )

    assert calls == []


@pytest.mark.asyncio
async def test_credential_loader_error_is_sanitized_before_network(
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
) -> None:
    action = next(
        item for item in flowaccount_actions if (item.action_id, item.version_id) in LIVE_READS
    )
    secret = "raw-client-secret"
    calls: list[str] = []

    def load_credentials() -> dict[str, str]:
        raise RuntimeError(f"credential failure: {secret}")

    with pytest.raises(
        ValueError,
        match="^flowaccount_sandbox_credentials_unavailable$",
    ) as raised:
        await execute_flowaccount_sandbox_action(
            driver=FlowAccountDriver(),
            environment="sandbox",
            load_credentials=load_credentials,
            action=action,
            manifest=sandbox_manifest,
            request_hook=_unused_hook(),
            transport=httpx.MockTransport(
                lambda request: calls.append(str(request.url)) or httpx.Response(500)
            ),
        )

    assert calls == []
    assert secret not in str(raised.value)


@pytest.mark.asyncio
async def test_request_hook_error_does_not_expose_provider_or_request_values(
    flowaccount_actions,
    sandbox_manifest: SandboxExecutionManifest,
) -> None:
    action = next(
        item for item in flowaccount_actions if (item.action_id, item.version_id) in LIVE_READS
    )
    raw_value = "raw-request-or-response-value"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = (
            {"access_token": "issued-token"}
            if request.url.path == "/test/token"
            else {"companyName": "Example Books"}
        )
        return provider_response(request, 200, payload)

    async def failing_hook(**_: object) -> object:
        raise RuntimeError(f"request failed: {raw_value}")

    with pytest.raises(
        RuntimeError,
        match="^flowaccount_sandbox_request_failed$",
    ) as raised:
        await execute_flowaccount_sandbox_action(
            driver=FlowAccountDriver(),
            environment="sandbox",
            load_credentials=lambda: {
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
            action=action,
            manifest=sandbox_manifest,
            request_hook=failing_hook,
            transport=httpx.MockTransport(handler),
        )

    assert raw_value not in str(raised.value)
