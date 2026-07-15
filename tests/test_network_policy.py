import socket

import httpx
import pytest

from mercury_tools.qualification.network import sandbox_http_client
from mercury_tools.safety.network import NetworkPolicy, NetworkPolicyError


def _answer(address: str, port: int = 443) -> list[tuple[object, ...]]:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, port))]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://127.0.0.1/api",
        "file:///etc/passwd",
    ],
)
def test_production_network_policy_blocks_metadata_private_and_non_http(url: str) -> None:
    with pytest.raises(NetworkPolicyError):
        NetworkPolicy().validate_base_url(url, allow_private_network=False)


def test_network_policy_resolves_every_validation_and_returns_exact_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def resolve(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
        calls.append((host, port))
        return _answer("93.184.216.34", port)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    policy = NetworkPolicy()

    first = policy.validate_base_url(
        "https://ERP.Example.com:8443/v1", allow_private_network=False
    )
    second = policy.validate_request_url(
        "https://erp.example.com:8443/v1/invoices?page=1",
        allowed_hosts={"erp.example.com"},
        allow_private_network=False,
    )

    assert calls == [("erp.example.com", 8443), ("erp.example.com", 8443)]
    assert first.origin == "https://erp.example.com:8443"
    assert first.base_path == "/v1"
    assert second.hostname == "erp.example.com"


def test_network_policy_allows_explicit_private_http_only_for_private_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answer(
            "10.0.0.8" if host == "erp.internal" else "93.184.216.34", port
        ),
    )

    target = NetworkPolicy().validate_base_url(
        "http://erp.internal:8080/api", allow_private_network=True
    )
    assert target.addresses == ("10.0.0.8",)

    with pytest.raises(NetworkPolicyError, match="^https_required$"):
        NetworkPolicy().validate_base_url(
            "http://erp.example.com/api", allow_private_network=True
        )


def test_request_url_requires_an_exact_trusted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answer("93.184.216.34", port),
    )

    with pytest.raises(NetworkPolicyError, match="^request_host_not_trusted$"):
        NetworkPolicy().validate_request_url(
            "https://evil.example.com/collect",
            allowed_hosts={"erp.example.com"},
            allow_private_network=False,
        )


def test_validated_target_rejects_dns_rebinding_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answer("93.184.216.34", port),
    )
    target = NetworkPolicy().validate_base_url(
        "https://erp.example.com/v1", allow_private_network=False
    )

    target.verify_peer("93.184.216.34")
    with pytest.raises(NetworkPolicyError, match="^remote_peer_address_mismatch$"):
        target.verify_peer("127.0.0.1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://openapi.flowaccount.com/v1/company/info",
        "https://openapi.flowaccount.com/test/token/extra",
        "https://openapi.flowaccount.com/test/token?redirect=/v1/token",
        "https://openapi.flowaccount.com/test/%2e%2e/v1/company/info",
        "https://openapi.flowaccount.com/test//company/info",
    ],
)
async def test_flowaccount_sandbox_client_blocks_production_path_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answer("93.184.216.34", port),
    )
    calls: list[str] = []
    transport = httpx.MockTransport(
        lambda request: calls.append(str(request.url)) or httpx.Response(200)
    )

    async with sandbox_http_client(transport=transport) as client:
        with pytest.raises(
            NetworkPolicyError,
            match="^flowaccount_sandbox_request_invalid$",
        ):
            await client.get(url)

    assert calls == []


@pytest.mark.asyncio
async def test_flowaccount_sandbox_client_disables_redirects_proxies_and_unbounded_timeouts() -> (
    None
):
    client = sandbox_http_client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        assert client.follow_redirects is False
        assert client._trust_env is False
        assert client.timeout.connect == 10.0
        assert client.timeout.read == 20.0
        assert client.timeout.write == 20.0
        assert client.timeout.pool == 20.0
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_flowaccount_sandbox_client_retains_dns_peer_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: _answer("93.184.216.34", port),
    )

    class WrongPeerStream:
        def get_extra_info(self, name: str) -> tuple[str, int] | None:
            return ("93.184.216.35", 443) if name == "server_addr" else None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={},
            extensions={"network_stream": WrongPeerStream()},
        )

    async with sandbox_http_client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NetworkPolicyError, match="^remote_peer_address_mismatch$"):
            await client.get("https://openapi.flowaccount.com/test/company/info")
