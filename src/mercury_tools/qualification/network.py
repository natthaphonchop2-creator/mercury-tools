"""Fail-closed FlowAccount sandbox origin and tenant qualification."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar
from urllib.parse import urlsplit

import httpx

from mercury_tools.catalog.models import CatalogAction
from mercury_tools.drivers.models import AuthContext, ConnectionProbe
from mercury_tools.qualification.manifest import (
    SandboxActionPolicy,
    SandboxExecutionManifest,
)
from mercury_tools.safety.network import NetworkPolicy, NetworkPolicyError, ResolvedTarget

if TYPE_CHECKING:
    from mercury_tools.drivers.flowaccount import FlowAccountDriver

SANDBOX_API_URL = "https://openapi.flowaccount.com/test"
SANDBOX_TOKEN_URL = "https://openapi.flowaccount.com/test/token"
_SANDBOX_HOST = "openapi.flowaccount.com"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REDACTED_LABEL = "[redacted]"
_TRANSPORT_ELIGIBLE_KEY = "mercury_sandbox_transport_eligible"
_TRANSPORT_ELIGIBLE_MARKER = object()

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
CredentialLoadHook = Callable[[], dict[str, str]]


class SandboxRequestHook(Protocol[T_co]):
    def __call__(
        self,
        *,
        client: httpx.AsyncClient,
        auth: AuthContext,
        binding: SandboxTenantBinding,
        origins: SandboxOrigins,
        policy: SandboxActionPolicy,
    ) -> Awaitable[T_co]: ...


@dataclass(frozen=True, slots=True)
class SandboxOrigins:
    api_url: str
    token_url: str


@dataclass(frozen=True, slots=True, repr=False)
class SandboxTenantBinding:
    connector_id: str
    environment: str
    company_label_sha256: str

    def __post_init__(self) -> None:
        if (
            self.connector_id != "flowaccount"
            or self.environment != "sandbox"
            or _SHA256.fullmatch(self.company_label_sha256) is None
        ):
            raise ValueError("flowaccount_sandbox_tenant_binding_invalid")

    def __repr__(self) -> str:
        return (
            "SandboxTenantBinding("
            f"connector_id={self.connector_id!r}, "
            f"environment={self.environment!r}, "
            "company_label_sha256='[SHA256]'"
            ")"
        )


def validate_sandbox_url(value: str, *, expected: str) -> str:
    """Require one exact compile-time FlowAccount sandbox URL."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        raise ValueError("flowaccount_sandbox_origin_invalid") from None
    if (
        value != expected
        or expected not in {SANDBOX_API_URL, SANDBOX_TOKEN_URL}
        or parsed.scheme != "https"
        or parsed.hostname != _SANDBOX_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ValueError("flowaccount_sandbox_origin_invalid")
    return value


def validate_flowaccount_sandbox_origins(driver: FlowAccountDriver) -> SandboxOrigins:
    """Bind a FlowAccount driver to the exact sandbox API and token paths."""
    try:
        configured_api_url = driver.BASE_URLS["sandbox"]
        token_url = driver.TOKEN_URLS["sandbox"]
        resolved_api_url = driver.resolve_base_url("sandbox")
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError("flowaccount_sandbox_origin_invalid") from None
    validate_sandbox_url(configured_api_url, expected=SANDBOX_API_URL)
    return SandboxOrigins(
        api_url=validate_sandbox_url(resolved_api_url, expected=SANDBOX_API_URL),
        token_url=validate_sandbox_url(token_url, expected=SANDBOX_TOKEN_URL),
    )


def sandbox_http_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    network: NetworkPolicy | None = None,
) -> httpx.AsyncClient:
    """Build a proxy-free, redirect-free client with DNS and peer pinning hooks."""
    policy = network or NetworkPolicy()

    async def validate_request(request: httpx.Request) -> None:
        parsed = urlsplit(str(request.url))
        try:
            port = parsed.port
        except ValueError:
            raise NetworkPolicyError("flowaccount_sandbox_request_invalid") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != _SANDBOX_HOST
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or not (parsed.path == "/test" or parsed.path.startswith("/test/"))
            or "%" in parsed.path
            or "\\" in parsed.path
            or "//" in parsed.path
            or parsed.path.startswith("/test/token/")
            or (parsed.path == "/test/token" and bool(parsed.query))
            or bool(parsed.fragment)
        ):
            raise NetworkPolicyError("flowaccount_sandbox_request_invalid")
        target = policy.validate_request_url(
            str(request.url),
            allowed_hosts={_SANDBOX_HOST},
            allow_private_network=False,
        )
        request.extensions["mercury_resolved_target"] = target
        request.extensions[_TRANSPORT_ELIGIBLE_KEY] = _TRANSPORT_ELIGIBLE_MARKER

    async def validate_response(response: httpx.Response) -> None:
        target = response.request.extensions.get("mercury_resolved_target")
        if not isinstance(target, ResolvedTarget):
            raise NetworkPolicyError("remote_peer_unverified")
        response.request.extensions["mercury_response_received"] = True
        stream = response.extensions.get("network_stream")
        if stream is None or not hasattr(stream, "get_extra_info"):
            raise NetworkPolicyError("remote_peer_unverified")
        try:
            peer = stream.get_extra_info("server_addr")
        except Exception:
            raise NetworkPolicyError("remote_peer_unverified") from None
        if not (isinstance(peer, tuple) and peer and isinstance(peer[0], str)):
            raise NetworkPolicyError("remote_peer_unverified")
        target.verify_peer(peer[0])

    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(20.0, connect=10.0),
        transport=transport,
        trust_env=False,
        event_hooks={"request": [validate_request], "response": [validate_response]},
    )


def sandbox_request_transport_eligible(request: httpx.Request) -> bool:
    """Return whether sandbox validation completed before transport entry."""
    return request.extensions.get(_TRANSPORT_ELIGIBLE_KEY) is _TRANSPORT_ELIGIBLE_MARKER


def require_verified_sandbox_tenant(
    probe: ConnectionProbe,
    *,
    expected: SandboxTenantBinding | None = None,
) -> SandboxTenantBinding:
    """Reduce a connected sandbox probe to a stable, secret-safe tenant binding."""
    company_name = probe.company_name
    if (
        probe.status != "connected"
        or probe.connector_id != "flowaccount"
        or probe.environment != "sandbox"
        or not isinstance(company_name, str)
    ):
        raise ValueError("flowaccount_sandbox_tenant_unverified")
    label = _sanitize_company_label(company_name)
    if not label or label == _REDACTED_LABEL:
        raise ValueError("flowaccount_sandbox_tenant_unverified")
    binding = SandboxTenantBinding(
        connector_id="flowaccount",
        environment="sandbox",
        company_label_sha256=hashlib.sha256(label.encode("utf-8")).hexdigest(),
    )
    if expected is not None and binding != expected:
        raise ValueError("flowaccount_sandbox_tenant_mismatch")
    return binding


async def execute_flowaccount_sandbox_action(
    *,
    driver: FlowAccountDriver,
    environment: str,
    load_credentials: CredentialLoadHook,
    action: CatalogAction,
    manifest: SandboxExecutionManifest,
    request_hook: SandboxRequestHook[T],
    expected_tenant: SandboxTenantBinding,
    transport: httpx.AsyncBaseTransport | None = None,
    network: NetworkPolicy | None = None,
) -> T:
    """Qualify one sandbox tenant, authorize one exact action, then invoke its request hook."""
    if not isinstance(expected_tenant, SandboxTenantBinding):
        raise ValueError("flowaccount_sandbox_expected_tenant_invalid")
    if environment != "sandbox":
        raise ValueError("flowaccount_sandbox_environment_invalid")
    origins = validate_flowaccount_sandbox_origins(driver)
    try:
        credentials = load_credentials()
    except Exception:
        raise ValueError("flowaccount_sandbox_credentials_unavailable") from None
    if not isinstance(credentials, dict):
        raise ValueError("flowaccount_sandbox_credentials_invalid")
    try:
        async with sandbox_http_client(transport=transport, network=network) as client:
            auth, probe = await driver.prepare_sandbox_auth_and_probe(
                environment=environment,
                credentials=credentials,
                client=client,
                origins=origins,
            )
            binding = require_verified_sandbox_tenant(probe, expected=expected_tenant)
            if not isinstance(manifest, SandboxExecutionManifest):
                raise PermissionError("sandbox_manifest_required")
            policy = manifest.require_executable(action)
            try:
                return await request_hook(
                    client=client,
                    auth=auth,
                    binding=binding,
                    origins=origins,
                    policy=policy,
                )
            except Exception:
                raise RuntimeError("flowaccount_sandbox_request_failed") from None
    finally:
        credentials.clear()


def _sanitize_company_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


__all__ = [
    "SANDBOX_API_URL",
    "SANDBOX_TOKEN_URL",
    "CredentialLoadHook",
    "SandboxOrigins",
    "SandboxRequestHook",
    "SandboxTenantBinding",
    "execute_flowaccount_sandbox_action",
    "require_verified_sandbox_tenant",
    "sandbox_http_client",
    "sandbox_request_transport_eligible",
    "validate_flowaccount_sandbox_origins",
    "validate_sandbox_url",
]
