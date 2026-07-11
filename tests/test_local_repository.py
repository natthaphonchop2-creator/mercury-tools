import json
import os
import stat
from pathlib import Path

import pytest

from mercury_tools.local.repository import (
    configure_connector,
    ensure_repository_state,
    load_repository_config,
    resolve_repository_root,
    root_paths,
)


def test_single_root_is_selected_and_scaffolded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("dist/\n")
    selected = resolve_repository_root(None, (root,))
    context = ensure_repository_state(selected)

    assert context.root == root.resolve()
    assert context.repository_id.startswith("repo_")
    assert context.credentials_path == root / ".mercury" / "credentials.env"
    assert context.config_path.read_text() == (
        '{\n  "schema_version": 1,\n  "trusted_hosts": {},\n  "connectors": {}\n}\n'
    )
    ignore_lines = (root / ".gitignore").read_text().splitlines()
    assert ignore_lines[0] == "dist/"
    assert ignore_lines[-3:] == [
        ".mercury/credentials.env",
        ".mercury/cache/",
        ".mercury/audit/",
    ]
    if os.name == "posix":
        assert stat.S_IMODE(context.mercury_dir.stat().st_mode) == 0o700


def test_multiple_roots_require_explicit_selection(tmp_path: Path) -> None:
    roots = (tmp_path / "a", tmp_path / "b")
    for root in roots:
        root.mkdir()

    with pytest.raises(ValueError, match="multiple_mcp_roots"):
        resolve_repository_root(None, roots)


def test_requested_root_must_be_inside_an_mcp_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(ValueError, match="repo_root_outside_mcp_roots"):
        resolve_repository_root(outside, (allowed,))


def test_root_paths_accept_file_uris_only(tmp_path: Path) -> None:
    assert root_paths((tmp_path.as_uri(),)) == (tmp_path.resolve(),)
    with pytest.raises(ValueError, match="unsupported_root_uri"):
        root_paths(("https://example.com/repo",))


def test_custom_connector_configuration_pins_driver_and_host(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    config = configure_connector(
        context,
        connector_id="custom-books",
        environment="production",
        driver_id="api_key_header",
        base_url="https://api.example-books.com/v2",
        auth_settings={"key_name": "X-API-Key"},
    )
    selected = config.connectors["custom-books"]["production"]
    assert selected["driver_id"] == "api_key_header"
    assert selected["base_url"] == "https://api.example-books.com/v2"
    assert config.trusted_hosts["custom-books"]["production"] == (
        "api.example-books.com",
    )


def test_connector_configuration_pins_oauth_token_host(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    config = configure_connector(
        context,
        connector_id="custom-books",
        environment="production",
        driver_id="oauth_client_credentials",
        base_url="https://api.example-books.com/v2",
        auth_settings={
            "client_id_name": "CLIENT_ID",
            "client_secret_name": "CLIENT_SECRET",
            "grant_type": "client_credentials",
            "scope": "flowaccount-api",
            "token_url": "https://auth.example-books.com/oauth/token",
        },
    )

    assert config.trusted_hosts["custom-books"]["production"] == (
        "api.example-books.com",
        "auth.example-books.com",
    )
    assert load_repository_config(context).trusted_hosts["custom-books"]["production"] == (
        "api.example-books.com",
        "auth.example-books.com",
    )
    assert config.connectors["custom-books"]["production"]["auth_settings"] == {
        "client_id_name": "CLIENT_ID",
        "client_secret_name": "CLIENT_SECRET",
        "grant_type": "client_credentials",
        "scope": "flowaccount-api",
        "token_url": "https://auth.example-books.com/oauth/token",
    }


def test_connector_configuration_rejects_unsupported_oauth_grant_type(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="unsupported_auth_setting"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="oauth_client_credentials",
            base_url="https://api.example-books.com/v2",
            auth_settings={"grant_type": "authorization_code"},
        )


@pytest.mark.parametrize(
    "scope",
    [
        "client_secret=shh",
        "read CLIENT_SECRET=shh",
        "read password",
        "read credential",
        "read authorization",
        "read bearer",
        "read api_key",
        "read access_token",
        "read refresh_token",
    ],
)
def test_connector_configuration_rejects_sensitive_scope_markers(
    tmp_path: Path,
    scope: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="secret_auth_setting_not_allowed"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="oauth_client_credentials",
            base_url="https://api.example-books.com/v2",
            auth_settings={"scope": scope},
        )


@pytest.mark.parametrize(
    "scope",
    [
        "",
        "read  write",
        "read\twrite",
        "read\nwrite",
        "x" * 257,
    ],
)
def test_connector_configuration_rejects_invalid_oauth_scope(
    tmp_path: Path,
    scope: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="unsupported_auth_setting"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="oauth_client_credentials",
            base_url="https://api.example-books.com/v2",
            auth_settings={"scope": scope},
        )


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("key_name", "X API Key"),
        ("client_id_name", "client_id=value"),
        ("client_secret_name", "X" * 129),
    ],
)
def test_connector_configuration_rejects_non_parameter_auth_names(
    tmp_path: Path,
    setting: str,
    value: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="unsupported_auth_setting"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url="https://api.example-books.com/v2",
            auth_settings={setting: value},
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example-books.com/v2?client_secret=shh",
        "https://api.example-books.com/v2;client_secret=shh",
        "https://api.example-books.com/v2#client_secret=shh",
    ],
)
def test_connector_configuration_rejects_base_url_with_persisted_parameters(
    tmp_path: Path,
    base_url: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="invalid_endpoint_url"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url=base_url,
            auth_settings={"key_name": "X-API-Key"},
        )


@pytest.mark.parametrize(
    "token_url",
    [
        "https://auth.example-books.com/oauth/token?client_secret=shh",
        "https://auth.example-books.com/oauth/token;client_secret=shh",
        "https://auth.example-books.com/oauth/token#client_secret=shh",
    ],
)
def test_connector_configuration_rejects_token_url_with_persisted_parameters(
    tmp_path: Path,
    token_url: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="invalid_endpoint_url"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="oauth_client_credentials",
            base_url="https://api.example-books.com/v2",
            auth_settings={
                "client_id_name": "CLIENT_ID",
                "client_secret_name": "CLIENT_SECRET",
                "token_url": token_url,
            },
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example-books.com:invalid/v2",
        "https://api.example-books.com:65536/v2",
    ],
)
def test_connector_configuration_rejects_malformed_or_out_of_range_ports(
    tmp_path: Path,
    base_url: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="invalid_endpoint_url"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url=base_url,
            auth_settings={"key_name": "X-API-Key"},
        )


def test_internet_urls_require_https_unless_private_network_is_enabled(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="https_required"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url="http://api.example-books.com/v2",
            auth_settings={"key_name": "X-API-Key"},
        )

    config = configure_connector(
        context,
        connector_id="local-books",
        environment="local",
        driver_id="api_key_header",
        base_url="http://localhost:8080/v2",
        auth_settings={"key_name": "X-API-Key", "allow_private_network": True},
    )

    assert config.allow_private_network("local-books", "local") is True
    assert load_repository_config(context).allow_private_network("local-books", "local") is True


@pytest.mark.parametrize("allow_private_network", ["false", 0, 1, None])
def test_load_repository_config_rejects_non_boolean_private_network_policy(
    tmp_path: Path,
    allow_private_network: object,
) -> None:
    context = ensure_repository_state(tmp_path)
    context.config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trusted_hosts": {},
                "connectors": {
                    "custom-books": {
                        "local": {
                            "driver_id": "api_key_header",
                            "base_url": "http://localhost:8080/v2",
                            "auth_settings": {},
                            "network_policy": {
                                "allow_private_network": allow_private_network,
                            },
                        }
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="invalid_network_policy"):
        load_repository_config(context)


@pytest.mark.parametrize(
    "auth_settings",
    [
        {"credential": "shh"},
        {"authorization_header": "Bearer shh"},
    ],
)
def test_connector_configuration_rejects_unknown_auth_metadata(
    tmp_path: Path,
    auth_settings: dict[str, str],
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="unsupported_auth_setting"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url="https://api.example-books.com/v2",
            auth_settings=auth_settings,
        )


def test_connector_configuration_rejects_url_collections(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="unsupported_auth_setting"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url="https://api.example-books.com/v2",
            auth_settings={
                "key_name": "X-API-Key",
                "token_urls": ["https://auth.example-books.com/oauth/token"],
            },
        )


@pytest.mark.parametrize(
    "hosts",
    [
        "api.example-books.com",
        ["api.example-books.com", ""],
        ["api.example-books.com:443"],
    ],
)
def test_load_repository_config_rejects_malformed_trusted_hosts(
    tmp_path: Path,
    hosts: object,
) -> None:
    context = ensure_repository_state(tmp_path)
    context.config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trusted_hosts": {"custom-books": {"production": hosts}},
                "connectors": {},
            },
        ),
    )

    with pytest.raises(ValueError, match="invalid_trusted_hosts"):
        load_repository_config(context)
