import hashlib
import ipaddress
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_REQUIRED_GITIGNORE_LINES = [
    ".mercury/credentials.env",
    ".mercury/cache/",
    ".mercury/audit/",
]
_PRIVATE_NETWORK_ENVIRONMENTS = {"local", "gateway"}
_FORBIDDEN_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata",
    "metadata.google.internal",
}
_SAFE_AUTH_METADATA_KEYS = {
    "audience",
    "credential_placement",
    "grant_type",
    "scope",
    "scopes",
    "token_audience",
}
_SECRET_KEY_PARTS = (
    "access_key",
    "api_key",
    "password",
    "refresh",
    "secret",
    "token",
)


@dataclass(frozen=True)
class RepositoryContext:
    repository_id: str
    root: Path
    mercury_dir: Path
    config_path: Path
    credentials_path: Path
    catalog_dir: Path
    cache_dir: Path
    audit_dir: Path


@dataclass(frozen=True)
class RepositoryConfig:
    schema_version: int = 1
    trusted_hosts: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    connectors: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def allow_private_network(self, connector_id: str, environment: str) -> bool:
        connector = self.connectors.get(connector_id, {})
        selected = connector.get(environment, {})
        policy = selected.get("network_policy", {})
        if not isinstance(policy, Mapping):
            return False
        return bool(policy.get("allow_private_network", False))


def root_paths(root_uris: Sequence[str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for root_uri in root_uris:
        parsed = urlparse(root_uri)
        if parsed.scheme != "file" or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("unsupported_root_uri")
        if parsed.netloc not in ("", "localhost"):
            raise ValueError("unsupported_root_uri")
        paths.append(Path(unquote(parsed.path)).expanduser().resolve())
    return tuple(paths)


def resolve_repository_root(
    requested: str | Path | None,
    roots: Sequence[Path],
) -> Path:
    resolved_roots = tuple(Path(root).expanduser().resolve() for root in roots)
    if not resolved_roots:
        raise ValueError("mcp_roots_required")
    if requested is None:
        if len(resolved_roots) != 1:
            raise ValueError("multiple_mcp_roots")
        return resolved_roots[0]

    candidate = Path(requested).expanduser().resolve()
    if not any(candidate == root or candidate.is_relative_to(root) for root in resolved_roots):
        raise ValueError("repo_root_outside_mcp_roots")
    return candidate


def ensure_repository_state(root: Path) -> RepositoryContext:
    root = Path(root).expanduser().resolve()
    mercury_dir = root / ".mercury"
    catalog_dir = mercury_dir / "catalog"
    cache_dir = mercury_dir / "cache"
    audit_dir = mercury_dir / "audit"

    for directory in (
        mercury_dir,
        catalog_dir / "sources",
        catalog_dir / "actions",
        cache_dir,
        audit_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        mercury_dir.chmod(0o700)

    config_path = mercury_dir / "config.json"
    if not config_path.exists():
        _write_config_atomic(config_path, RepositoryConfig())

    _ensure_gitignore(root)

    return RepositoryContext(
        repository_id="repo_" + hashlib.sha256(str(root).encode()).hexdigest()[:16],
        root=root,
        mercury_dir=mercury_dir,
        config_path=config_path,
        credentials_path=mercury_dir / "credentials.env",
        catalog_dir=catalog_dir,
        cache_dir=cache_dir,
        audit_dir=audit_dir,
    )


def load_repository_config(context: RepositoryContext) -> RepositoryConfig:
    if not context.config_path.exists():
        return RepositoryConfig()

    payload = json.loads(context.config_path.read_text())
    return RepositoryConfig(
        schema_version=int(payload.get("schema_version", 1)),
        trusted_hosts=_load_trusted_hosts(payload.get("trusted_hosts", {})),
        connectors=_load_connectors(payload.get("connectors", {})),
    )


def configure_connector(
    context: RepositoryContext,
    connector_id: str,
    environment: str,
    driver_id: str,
    base_url: str,
    auth_settings: Mapping[str, Any],
) -> RepositoryConfig:
    settings = dict(auth_settings)
    allow_private_network = bool(settings.pop("allow_private_network", False))
    if allow_private_network and environment not in _PRIVATE_NETWORK_ENVIRONMENTS:
        raise ValueError("private_network_only_for_local_or_gateway")

    hosts = [_validate_endpoint_url(base_url, environment, allow_private_network)]
    sanitized_auth_settings = _sanitize_auth_settings(settings)
    for key, value in sanitized_auth_settings.items():
        if _is_url_setting(key, value):
            host = _validate_endpoint_url(str(value), environment, allow_private_network)
            if host not in hosts:
                hosts.append(host)

    current = load_repository_config(context)
    trusted_hosts = _copy_trusted_hosts(current.trusted_hosts)
    connectors = _copy_connectors(current.connectors)

    trusted_hosts.setdefault(connector_id, {})[environment] = tuple(hosts)
    connectors.setdefault(connector_id, {})[environment] = {
        "driver_id": driver_id,
        "base_url": base_url,
        "auth_settings": sanitized_auth_settings,
        "network_policy": {"allow_private_network": allow_private_network},
    }

    updated = RepositoryConfig(
        schema_version=current.schema_version,
        trusted_hosts=trusted_hosts,
        connectors=connectors,
    )
    _write_config_atomic(context.config_path, updated)
    return updated


def _ensure_gitignore(root: Path) -> None:
    ignore_path = root / ".gitignore"
    existing = ignore_path.read_text().splitlines() if ignore_path.exists() else []
    missing = [line for line in _REQUIRED_GITIGNORE_LINES if line not in existing]
    if not missing:
        return
    _write_text_atomic(ignore_path, "\n".join([*existing, *missing]) + "\n")


def _write_config_atomic(path: Path, config: RepositoryConfig) -> None:
    _write_text_atomic(path, json.dumps(_config_payload(config), indent=2) + "\n")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=path.parent,
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(text)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _config_payload(config: RepositoryConfig) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "trusted_hosts": {
            connector_id: {
                environment: list(hosts) for environment, hosts in environments.items()
            }
            for connector_id, environments in config.trusted_hosts.items()
        },
        "connectors": config.connectors,
    }


def _load_trusted_hosts(value: Any) -> dict[str, dict[str, tuple[str, ...]]]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_trusted_hosts")
    trusted_hosts: dict[str, dict[str, tuple[str, ...]]] = {}
    for connector_id, environments in value.items():
        if not isinstance(environments, Mapping):
            raise ValueError("invalid_trusted_hosts")
        trusted_hosts[str(connector_id)] = {
            str(environment): tuple(str(host) for host in hosts)
            for environment, hosts in environments.items()
        }
    return trusted_hosts


def _load_connectors(value: Any) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_connectors")
    return _copy_connectors(value)


def _copy_trusted_hosts(
    trusted_hosts: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        str(connector_id): {
            str(environment): tuple(str(host) for host in hosts)
            for environment, hosts in environments.items()
        }
        for connector_id, environments in trusted_hosts.items()
    }


def _copy_connectors(value: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    connectors: dict[str, dict[str, dict[str, Any]]] = {}
    for connector_id, environments in value.items():
        if not isinstance(environments, Mapping):
            raise ValueError("invalid_connectors")
        connectors[str(connector_id)] = {}
        for environment, config in environments.items():
            if not isinstance(config, Mapping):
                raise ValueError("invalid_connectors")
            connectors[str(connector_id)][str(environment)] = dict(config)
    return connectors


def _sanitize_auth_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in settings.items():
        if _looks_like_secret_value(str(key)):
            raise ValueError("secret_auth_setting_not_allowed")
        if not _is_json_primitive(value):
            raise ValueError("unsupported_auth_setting")
        sanitized[str(key)] = value
    return sanitized


def _looks_like_secret_value(key: str) -> bool:
    normalized = key.lower()
    if _is_safe_auth_metadata_key(normalized):
        return False
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _is_safe_auth_metadata_key(normalized_key: str) -> bool:
    return (
        normalized_key in _SAFE_AUTH_METADATA_KEYS
        or normalized_key.endswith("_name")
        or normalized_key.endswith("_names")
        or normalized_key.endswith("_param")
        or normalized_key.endswith("_parameter")
        or normalized_key.endswith("_url")
        or normalized_key.endswith("_urls")
    )


def _is_json_primitive(value: Any) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_primitive(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_primitive(item) for key, item in value.items())
    return False


def _is_url_setting(key: str, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = key.lower()
    return normalized.endswith("_url") or normalized.endswith("_urls")


def _validate_endpoint_url(url: str, environment: str, allow_private_network: bool) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_endpoint_url")
    if parsed.username or parsed.password:
        raise ValueError("url_credentials_not_allowed")

    host = parsed.hostname.lower().rstrip(".")
    if _is_forbidden_metadata_host(host):
        raise ValueError("forbidden_metadata_host")
    if parsed.scheme != "https" and (
        not allow_private_network
        or environment not in _PRIVATE_NETWORK_ENVIRONMENTS
        or not _is_private_network_host(host)
    ):
        raise ValueError("https_required")
    return host


def _is_forbidden_metadata_host(host: str) -> bool:
    if host in _FORBIDDEN_METADATA_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_link_local


def _is_private_network_host(host: str) -> bool:
    if host in {"localhost"} or host.endswith((".localhost", ".local", ".internal", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback
