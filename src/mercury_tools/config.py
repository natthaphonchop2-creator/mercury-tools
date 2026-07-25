"""Runtime configuration for Mercury Tools."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from mercury_tools.v1.constants import CANONICAL_MCP_RESOURCE

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIM = 1536
DEFAULT_EMBEDDING_PROVIDER = "hash"
DEFAULT_MCP_TRANSPORT = "streamable-http"
DEFAULT_MCP_HOST = "0.0.0.0"
DEFAULT_MCP_PORT = 8000
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_CLOUD_BASE_URL = "https://mercury-tools-mcp.onrender.com"


class V1ConfigurationError(RuntimeError):
    """A secret-safe Mercury V1 configuration failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Mercury V1 configuration error: {code}")


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    openai_api_key: str
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    mercury_agent_path: Path | None = None
    mercury_home: Path | None = None
    mcp_transport: str = DEFAULT_MCP_TRANSPORT
    mcp_host: str = DEFAULT_MCP_HOST
    mcp_port: int = DEFAULT_MCP_PORT
    mcp_path: str = DEFAULT_MCP_PATH
    public_base_url: str = ""
    http_bearer_token: str = ""
    http_require_auth: bool = False
    enable_legacy_http_api: bool = False
    connect_invite_code: str = ""
    connect_signing_secret: str = ""
    openai_apps_challenge_token: str = ""
    cloud_base_url: str = DEFAULT_CLOUD_BASE_URL
    v1_enabled: bool = False
    canonical_mcp_resource: str = CANONICAL_MCP_RESOURCE
    supabase_auth_issuer: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = ""
    vault_active_key: str = ""
    vault_active_key_version: str = ""
    vault_previous_key: str = ""
    vault_previous_key_version: str = ""
    flowaccount_mcp_sandbox_url: str = ""
    flowaccount_mcp_production_url: str = ""
    peak_mcp_uat_url: str = ""
    peak_mcp_production_url: str = ""
    provider_callback_base_url: str = ""

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def embedding_configured(self) -> bool:
        if self.embedding_provider == "openai":
            return self.openai_configured
        return self.embedding_provider == "hash"

    @property
    def http_auth_configured(self) -> bool:
        return bool(self.http_bearer_token or self.connect_signing_secret)

    @property
    def mcp_endpoint(self) -> str:
        if self.public_base_url:
            return f"{self.public_base_url.rstrip('/')}{self.mcp_path}"
        return f"http://{self.mcp_host}:{self.mcp_port}{self.mcp_path}"

    def validate_v1(self) -> None:
        if not self.v1_enabled:
            return

        if not _is_server_only_https_url(self.canonical_mcp_resource):
            raise V1ConfigurationError("v1_canonical_resource_invalid")
        if self.canonical_mcp_resource != CANONICAL_MCP_RESOURCE:
            raise V1ConfigurationError("v1_canonical_resource_mismatch")

        jwks_settings = (
            self.supabase_auth_issuer,
            self.supabase_jwks_url,
            self.supabase_jwt_audience,
        )
        if not all(jwks_settings):
            raise V1ConfigurationError("v1_jwks_configuration_missing")
        if not _is_server_only_https_url(
            self.supabase_auth_issuer
        ) or not _is_server_only_https_url(self.supabase_jwks_url):
            raise V1ConfigurationError("v1_jwks_url_invalid")
        if self.supabase_jwt_audience != self.canonical_mcp_resource:
            raise V1ConfigurationError("v1_jwt_audience_mismatch")

        if not self.vault_active_key or not self.vault_active_key_version:
            raise V1ConfigurationError("v1_vault_configuration_missing")
        _validate_vault_key(self.vault_active_key)

        previous_key_configured = bool(self.vault_previous_key)
        previous_version_configured = bool(self.vault_previous_key_version)
        if previous_key_configured != previous_version_configured:
            raise V1ConfigurationError("v1_vault_configuration_missing")
        if previous_key_configured:
            _validate_vault_key(self.vault_previous_key)
            if self.vault_previous_key_version == self.vault_active_key_version:
                raise V1ConfigurationError("v1_vault_key_version_reused")

        provider_urls = (
            self.flowaccount_mcp_sandbox_url,
            self.flowaccount_mcp_production_url,
            self.peak_mcp_uat_url,
            self.peak_mcp_production_url,
            self.provider_callback_base_url,
        )
        if not all(_is_server_only_https_url(url) for url in provider_urls):
            raise V1ConfigurationError("v1_provider_url_invalid")


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(names: tuple[str, ...], *, default: int) -> int:
    for name in names:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _normalize_path(value: str, *, default: str) -> str:
    path = value.strip() or default
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _is_server_only_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and (port is None or port > 0)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
        )
    except ValueError:
        return False


def _validate_vault_key(value: str) -> None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise V1ConfigurationError("v1_vault_key_invalid") from None
    if len(decoded) != 32:
        raise V1ConfigurationError("v1_vault_key_invalid")


def load_settings(*, dotenv_path: str | Path | None = None) -> Settings:
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    embedding_dim_raw = os.environ.get("MERCURY_TOOLS_EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM))
    try:
        embedding_dim = int(embedding_dim_raw)
    except ValueError:
        embedding_dim = DEFAULT_EMBEDDING_DIM

    mercury_agent = os.environ.get("MERCURY_AGENT_PATH", "").strip()
    mercury_home = os.environ.get("MERCURY_HOME", "").strip()
    mcp_path = _normalize_path(
        os.environ.get("MERCURY_TOOLS_MCP_PATH", DEFAULT_MCP_PATH),
        default=DEFAULT_MCP_PATH,
    )
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        embedding_provider=os.environ.get(
            "MERCURY_TOOLS_EMBEDDING_PROVIDER",
            DEFAULT_EMBEDDING_PROVIDER,
        )
        .strip()
        .lower()
        or DEFAULT_EMBEDDING_PROVIDER,
        embedding_model=os.environ.get("MERCURY_TOOLS_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        embedding_dim=embedding_dim,
        mercury_agent_path=Path(mercury_agent).expanduser() if mercury_agent else None,
        mercury_home=Path(mercury_home).expanduser() if mercury_home else None,
        mcp_transport=os.environ.get("MERCURY_TOOLS_MCP_TRANSPORT", DEFAULT_MCP_TRANSPORT),
        mcp_host=os.environ.get("MERCURY_TOOLS_HOST", DEFAULT_MCP_HOST).strip() or DEFAULT_MCP_HOST,
        mcp_port=_env_int(("MERCURY_TOOLS_PORT", "PORT"), default=DEFAULT_MCP_PORT),
        mcp_path=mcp_path,
        public_base_url=os.environ.get("MERCURY_TOOLS_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        http_bearer_token=os.environ.get("MERCURY_TOOLS_HTTP_BEARER_TOKEN", "").strip(),
        http_require_auth=_env_bool("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", default=False),
        enable_legacy_http_api=_env_bool(
            "MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API",
            default=False,
        ),
        connect_invite_code=os.environ.get("MERCURY_CONNECT_INVITE_CODE", "").strip(),
        connect_signing_secret=os.environ.get("MERCURY_CONNECT_SIGNING_SECRET", "").strip(),
        openai_apps_challenge_token=os.environ.get(
            "OPENAI_APPS_CHALLENGE_TOKEN", ""
        ).strip(),
        cloud_base_url=(
            os.environ.get("MERCURY_CLOUD_BASE_URL", DEFAULT_CLOUD_BASE_URL).strip().rstrip("/")
            or DEFAULT_CLOUD_BASE_URL
        ),
        v1_enabled=_env_bool("MERCURY_V1_ENABLED", default=False),
        canonical_mcp_resource=os.environ.get(
            "MERCURY_CANONICAL_MCP_RESOURCE",
            CANONICAL_MCP_RESOURCE,
        ).strip(),
        supabase_auth_issuer=os.environ.get("SUPABASE_AUTH_ISSUER", "").strip().rstrip("/"),
        supabase_jwks_url=os.environ.get("SUPABASE_JWKS_URL", "").strip(),
        supabase_jwt_audience=os.environ.get("SUPABASE_JWT_AUDIENCE", "").strip(),
        vault_active_key=os.environ.get("MERCURY_VAULT_ACTIVE_KEY", "").strip(),
        vault_active_key_version=os.environ.get(
            "MERCURY_VAULT_ACTIVE_KEY_VERSION", ""
        ).strip(),
        vault_previous_key=os.environ.get("MERCURY_VAULT_PREVIOUS_KEY", "").strip(),
        vault_previous_key_version=os.environ.get(
            "MERCURY_VAULT_PREVIOUS_KEY_VERSION", ""
        ).strip(),
        flowaccount_mcp_sandbox_url=os.environ.get(
            "FLOWACCOUNT_MCP_SANDBOX_URL", ""
        ).strip(),
        flowaccount_mcp_production_url=os.environ.get(
            "FLOWACCOUNT_MCP_PRODUCTION_URL", ""
        ).strip(),
        peak_mcp_uat_url=os.environ.get("PEAK_MCP_UAT_URL", "").strip(),
        peak_mcp_production_url=os.environ.get("PEAK_MCP_PRODUCTION_URL", "").strip(),
        provider_callback_base_url=os.environ.get(
            "MERCURY_PROVIDER_CALLBACK_BASE_URL", ""
        ).strip(),
    )


def require_supabase(settings: Settings) -> None:
    if not settings.supabase_configured:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
