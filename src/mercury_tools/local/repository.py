import hashlib
import ipaddress
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_ROOT_GITIGNORE_LINES = [
    ".mercury/credentials.env",
    ".mercury/cache/",
    ".mercury/audit/",
]
_MERCURY_GITIGNORE_LINES = ["credentials.env", "cache/", "audit/"]
_REPOSITORY_CONFIG_KEYS = {"schema_version", "trusted_hosts", "connectors"}
_PRIVATE_NETWORK_ENVIRONMENTS = {"local", "gateway"}
_FORBIDDEN_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata",
    "metadata.google.internal",
}
_AUTH_METADATA_KEYS = {
    "client_id_name",
    "client_secret_name",
    "grant_type",
    "key_name",
    "scope",
    "token_url",
}
_CONNECTOR_RECORD_KEYS = {
    "driver_id",
    "base_url",
    "auth_settings",
    "network_policy",
}
_NETWORK_POLICY_KEYS = {"allow_private_network"}
_AUTH_PARAMETER_NAME_KEYS = {
    "client_id_name",
    "client_secret_name",
    "key_name",
}
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_OAUTH_SCOPE_PATTERN = re.compile(
    r"^[\x21\x23-\x5b\x5d-\x7e]+(?: [\x21\x23-\x5b\x5d-\x7e]+)*$"
)
_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HEX_INTEGER_HOST_PATTERN = re.compile(r"^0x[0-9a-f]+$")
_LEGACY_IPV4_COMPONENT_PATTERN = re.compile(r"^(?:\d+|0x[0-9a-f]+)$")
_SENSITIVE_CREDENTIAL_MARKERS = (
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "api_key",
    "access_token",
    "refresh_token",
    "client_secret",
)
_MAX_OAUTH_SCOPE_LENGTH = 256
_CREDENTIAL_VALUE_PREFIXES = (
    "bearer ",
    "basic ",
    "digest ",
    "ghp_",
    "github_pat_",
    "sk-",
    "sk_",
    "xoxb-",
    "xoxp-",
    "ya29.",
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
        value = policy.get("allow_private_network", False)
        return value if isinstance(value, bool) else False


class _DuplicateConfigKeyError(ValueError):
    pass


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

    try:
        payload = json.loads(
            context.config_path.read_text(),
            object_pairs_hook=_reject_duplicate_config_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateConfigKeyError) as exc:
        raise ValueError("invalid_repository_config") from exc
    if not isinstance(payload, Mapping) or set(payload) != _REPOSITORY_CONFIG_KEYS:
        raise ValueError("invalid_repository_config")
    schema_version = payload["schema_version"]
    if type(schema_version) is not int or schema_version != 1:
        raise ValueError("invalid_repository_config")
    trusted_hosts = _load_trusted_hosts(payload["trusted_hosts"])
    return RepositoryConfig(
        schema_version=schema_version,
        trusted_hosts=trusted_hosts,
        connectors=_load_connectors(payload["connectors"], trusted_hosts),
    )


def configure_connector(
    context: RepositoryContext,
    connector_id: str,
    environment: str,
    driver_id: str,
    base_url: str,
    auth_settings: Mapping[str, Any],
) -> RepositoryConfig:
    if not isinstance(auth_settings, Mapping):
        raise ValueError("unsupported_auth_setting")
    settings = dict(auth_settings)
    allow_private_network = settings.pop("allow_private_network", False)
    record, hosts = _normalize_connector_record(
        connector_id,
        environment,
        {
            "driver_id": driver_id,
            "base_url": base_url,
            "auth_settings": settings,
            "network_policy": {"allow_private_network": allow_private_network},
        },
    )

    current = load_repository_config(context)
    trusted_hosts = _copy_trusted_hosts(current.trusted_hosts)
    connectors = {
        configured_connector_id: {
            configured_environment: dict(config)
            for configured_environment, config in environments.items()
        }
        for configured_connector_id, environments in current.connectors.items()
    }

    trusted_hosts.setdefault(connector_id, {})[environment] = hosts
    connectors.setdefault(connector_id, {})[environment] = record

    updated = RepositoryConfig(
        schema_version=current.schema_version,
        trusted_hosts=trusted_hosts,
        connectors=connectors,
    )
    _write_config_atomic(context.config_path, updated)
    return updated


def _ensure_gitignore(root: Path) -> None:
    _ensure_gitignore_rules(root / ".gitignore", _ROOT_GITIGNORE_LINES)
    _ensure_gitignore_rules(root / ".mercury" / ".gitignore", _MERCURY_GITIGNORE_LINES)


def _ensure_gitignore_rules(ignore_path: Path, required_lines: Sequence[str]) -> None:
    existing_text = ignore_path.read_text() if ignore_path.exists() else ""
    existing = existing_text.splitlines()
    managed_lines = set(required_lines) | {f"!{line}" for line in required_lines}
    preserved = [line for line in existing if line not in managed_lines]
    updated_text = "\n".join([*preserved, *required_lines]) + "\n"
    if existing_text == updated_text:
        return
    _write_text_atomic(ignore_path, updated_text)


def _write_config_atomic(path: Path, config: RepositoryConfig) -> None:
    _write_text_atomic(path, json.dumps(_config_payload(config), indent=2) + "\n")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = _existing_file_mode(path)
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
        if os.name == "posix":
            temporary_path.chmod(existing_mode if existing_mode is not None else 0o644)
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _existing_file_mode(path: Path) -> int | None:
    if os.name != "posix":
        return None
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def _reject_duplicate_config_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    object_value: dict[str, Any] = {}
    for key, value in pairs:
        if key in object_value:
            raise _DuplicateConfigKeyError()
        object_value[key] = value
    return object_value


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
        normalized_connector_id = _validate_connector_identifier(connector_id)
        trusted_hosts[normalized_connector_id] = {}
        for environment, hosts in environments.items():
            normalized_environment = _validate_connector_identifier(environment)
            trusted_hosts[normalized_connector_id][normalized_environment] = (
                _trusted_host_tuple(hosts)
            )
    return trusted_hosts


def _load_connectors(
    value: Any,
    trusted_hosts: Mapping[str, Mapping[str, tuple[str, ...]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_connectors")
    connectors: dict[str, dict[str, dict[str, Any]]] = {}
    for connector_id, environments in value.items():
        if not isinstance(environments, Mapping):
            raise ValueError("invalid_connectors")
        normalized_connector_id = _validate_connector_identifier(connector_id)
        connectors[normalized_connector_id] = {}
        for environment, record in environments.items():
            if not isinstance(record, Mapping):
                raise ValueError("invalid_connectors")
            normalized_environment = _validate_connector_identifier(environment)
            normalized_record, expected_hosts = _normalize_connector_record(
                normalized_connector_id,
                normalized_environment,
                record,
            )
            stored_hosts = trusted_hosts.get(normalized_connector_id, {}).get(
                normalized_environment
            )
            if stored_hosts is None or set(stored_hosts) != set(expected_hosts):
                raise ValueError("invalid_trusted_hosts")
            connectors[normalized_connector_id][normalized_environment] = normalized_record

    if set(trusted_hosts) != set(connectors):
        raise ValueError("invalid_trusted_hosts")
    for connector_id, environments in trusted_hosts.items():
        if set(environments) != set(connectors[connector_id]):
            raise ValueError("invalid_trusted_hosts")
    return connectors


def _copy_trusted_hosts(
    trusted_hosts: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    return {
        str(connector_id): {
            str(environment): _trusted_host_tuple(hosts)
            for environment, hosts in environments.items()
        }
        for connector_id, environments in trusted_hosts.items()
    }


def _sanitize_auth_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, Mapping):
        raise ValueError("unsupported_auth_setting")
    sanitized: dict[str, Any] = {}
    for key, value in settings.items():
        if not isinstance(key, str) or key not in _AUTH_METADATA_KEYS:
            raise ValueError("unsupported_auth_setting")
        sanitized[key] = _sanitize_auth_setting(key, value)
    return sanitized


def _sanitize_auth_setting(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("unsupported_auth_setting")
    if key == "grant_type":
        if value != "client_credentials":
            raise ValueError("unsupported_auth_setting")
        return value
    if key == "scope":
        return _validate_oauth_scope(value)
    if key in _AUTH_PARAMETER_NAME_KEYS:
        return _validate_auth_parameter_name(value)
    if _looks_like_credential_material(value):
        raise ValueError("secret_auth_setting_not_allowed")
    return value


def _validate_auth_parameter_name(value: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError("unsupported_auth_setting")
    if _looks_like_credential_material(value):
        raise ValueError("secret_auth_setting_not_allowed")
    return value


def _validate_oauth_scope(value: str) -> str:
    if any(_looks_like_credential_material(scope) for scope in value.split()):
        raise ValueError("secret_auth_setting_not_allowed")
    normalized = value.casefold()
    if any(marker in normalized for marker in _SENSITIVE_CREDENTIAL_MARKERS):
        raise ValueError("secret_auth_setting_not_allowed")
    if len(value) > _MAX_OAUTH_SCOPE_LENGTH or not _OAUTH_SCOPE_PATTERN.fullmatch(value):
        raise ValueError("unsupported_auth_setting")
    return value


def _looks_like_credential_material(value: str) -> bool:
    normalized = value.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _CREDENTIAL_VALUE_PREFIXES)


def _normalize_connector_record(
    connector_id: Any,
    environment: Any,
    record: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    _validate_connector_identifier(connector_id)
    normalized_environment = _validate_connector_identifier(environment)
    if not isinstance(record, Mapping) or set(record) != _CONNECTOR_RECORD_KEYS:
        raise ValueError("invalid_connector_record")

    driver_id = _validate_connector_identifier(record["driver_id"])
    network_policy = _normalize_network_policy(
        record["network_policy"],
        normalized_environment,
    )
    base_url = record["base_url"]
    hosts = [
        _validate_endpoint_url(
            base_url,
            normalized_environment,
            network_policy["allow_private_network"],
        )
    ]
    auth_settings = _sanitize_auth_settings(record["auth_settings"])
    token_url = auth_settings.get("token_url")
    if token_url is not None:
        token_host = _validate_endpoint_url(
            token_url,
            normalized_environment,
            network_policy["allow_private_network"],
        )
        if token_host not in hosts:
            hosts.append(token_host)
    return (
        {
            "driver_id": driver_id,
            "base_url": base_url,
            "auth_settings": auth_settings,
            "network_policy": network_policy,
        },
        tuple(hosts),
    )


def _validate_connector_identifier(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError("invalid_connector_identifier")
    if _looks_like_credential_material(value):
        raise ValueError("secret_connector_identifier_not_allowed")
    return value


def _normalize_network_policy(
    policy: Any,
    environment: str,
) -> dict[str, bool]:
    if not isinstance(policy, Mapping) or set(policy) != _NETWORK_POLICY_KEYS:
        raise ValueError("invalid_network_policy")
    allow_private_network = policy["allow_private_network"]
    if not isinstance(allow_private_network, bool):
        raise ValueError("invalid_network_policy")
    if allow_private_network and environment not in _PRIVATE_NETWORK_ENVIRONMENTS:
        raise ValueError("private_network_only_for_local_or_gateway")
    return {"allow_private_network": allow_private_network}


def _validate_endpoint_url(url: str, environment: str, allow_private_network: bool) -> str:
    if not isinstance(url, str):
        raise ValueError("invalid_endpoint_url")
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError("invalid_endpoint_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid_endpoint_url")
    if parsed.username or parsed.password:
        raise ValueError("url_credentials_not_allowed")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid_endpoint_url") from exc
    if parsed_port is not None and not 0 <= parsed_port <= 65535:
        raise ValueError("invalid_endpoint_url")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("invalid_endpoint_url")
    _validate_endpoint_path(parsed.path)

    host = parsed.hostname.lower().rstrip(".")
    if _is_forbidden_metadata_host(host):
        raise ValueError("forbidden_metadata_host")
    if not _is_valid_trusted_host(host):
        raise ValueError("invalid_endpoint_url")
    if parsed.scheme != "https" and (
        not allow_private_network
        or environment not in _PRIVATE_NETWORK_ENVIRONMENTS
        or not _is_private_network_host(host)
    ):
        raise ValueError("https_required")
    return host


def _validate_endpoint_path(path: str) -> None:
    for encoded_segment in path.split("/"):
        for decoded_segment in unquote(encoded_segment).split("/"):
            normalized = decoded_segment.casefold()
            if _looks_like_credential_material(decoded_segment) or any(
                marker in normalized for marker in _SENSITIVE_CREDENTIAL_MARKERS
            ):
                raise ValueError("secret_endpoint_path_not_allowed")


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


def _trusted_host_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("invalid_trusted_hosts")
    if not value:
        raise ValueError("invalid_trusted_hosts")
    hosts: list[str] = []
    for host in value:
        if not isinstance(host, str) or not _is_valid_trusted_host(host):
            raise ValueError("invalid_trusted_hosts")
        hosts.append(host)
    if len(set(hosts)) != len(hosts):
        raise ValueError("invalid_trusted_hosts")
    return tuple(hosts)


def _is_valid_trusted_host(host: str) -> bool:
    if not host or host.strip() != host or host.lower() != host or host.endswith("."):
        return False
    if "/" in host or "@" in host:
        return False
    if _is_forbidden_metadata_host(host):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    if _is_legacy_ipv4_numeric_alias(host):
        return False
    if host == "localhost":
        return True
    return all(_HOST_LABEL_PATTERN.fullmatch(label) for label in host.split("."))


def _is_legacy_ipv4_numeric_alias(host: str) -> bool:
    if "." not in host:
        return host.isdigit() or bool(_HEX_INTEGER_HOST_PATTERN.fullmatch(host))
    return all(_LEGACY_IPV4_COMPONENT_PATTERN.fullmatch(component) for component in host.split("."))
