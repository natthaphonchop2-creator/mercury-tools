import json
import os
import stat
from pathlib import Path

import pytest

import mercury_tools.local.repository as repository_module
from mercury_tools.local.repository import (
    RepositoryConfig,
    RepositoryContext,
    configure_connector,
    ensure_repository_state,
    load_repository_config,
    resolve_repository_root,
    root_paths,
)


def _write_connector_config(
    context: RepositoryContext,
    *,
    connector_id: str = "custom-books",
    environment: str = "production",
    record_updates: dict[str, object] | None = None,
    trusted_hosts: list[str] | None = None,
) -> None:
    record: dict[str, object] = {
        "driver_id": "oauth_client_credentials",
        "base_url": "https://api.example-books.com/v2",
        "auth_settings": {
            "client_id_name": "CLIENT_ID",
            "client_secret_name": "CLIENT_SECRET",
            "grant_type": "client_credentials",
            "scope": "flowaccount-api",
            "token_url": "https://auth.example-books.com/oauth/token",
        },
        "network_policy": {"allow_private_network": False},
    }
    if record_updates is not None:
        record.update(record_updates)
    context.config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trusted_hosts": {
                    connector_id: {
                        environment: trusted_hosts
                        if trusted_hosts is not None
                        else ["api.example-books.com", "auth.example-books.com"],
                    }
                },
                "connectors": {connector_id: {environment: record}},
            }
        )
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


def test_repository_state_rejects_a_preexisting_symlinked_mercury_directory(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".mercury").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="^repository_mercury_symlink$"):
        ensure_repository_state(tmp_path)

    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="descriptor-relative POSIX bootstrap")
@pytest.mark.parametrize("preexisting", [False, True])
def test_repository_bootstrap_rejects_mercury_symlink_swap_before_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preexisting: bool,
) -> None:
    mercury_dir = tmp_path / ".mercury"
    if preexisting:
        mercury_dir.mkdir()
    moved_mercury = tmp_path / "moved-mercury"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_open = getattr(repository_module, "_open_repository_mercury_fd", None)

    def race_open(root_fd: int) -> int:
        mercury_dir.rename(moved_mercury)
        mercury_dir.symlink_to(outside, target_is_directory=True)
        if real_open is not None:
            return real_open(root_fd)
        return os.open(
            ".mercury",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )

    monkeypatch.setattr(
        repository_module,
        "_open_repository_mercury_fd",
        race_open,
        raising=False,
    )

    with pytest.raises(ValueError, match="^repository_mercury_symlink$"):
        ensure_repository_state(tmp_path)

    assert list(outside.iterdir()) == []


def test_repository_state_places_required_ignores_after_mercury_negations(
    tmp_path: Path,
) -> None:
    ignore_path = tmp_path / ".gitignore"
    ignore_path.write_text(
        "dist/\n"
        ".mercury/credentials.env\n"
        "!.mercury/credentials.env\n"
        ".mercury/cache/\n"
        "!.mercury/cache/\n"
        ".mercury/audit/\n"
        "!.mercury/**\n"
        "docs/\n"
    )

    ensure_repository_state(tmp_path)
    first_result = ignore_path.read_text()

    assert first_result.splitlines() == [
        "dist/",
        "!.mercury/**",
        "docs/",
        ".mercury/credentials.env",
        ".mercury/cache/",
        ".mercury/audit/",
    ]

    ensure_repository_state(tmp_path)

    assert ignore_path.read_text() == first_result


def test_repository_state_places_required_nested_ignores_after_local_negations(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    nested_ignore_path = context.mercury_dir / ".gitignore"
    nested_ignore_path.write_text(
        "catalog/\n"
        "credentials.env\n"
        "!credentials.env\n"
        "cache/\n"
        "!cache/\n"
        "audit/\n"
        "!audit/\n"
    )

    ensure_repository_state(tmp_path)
    first_result = nested_ignore_path.read_text()

    assert first_result.splitlines() == [
        "catalog/",
        "credentials.env",
        "cache/",
        "audit/",
    ]

    ensure_repository_state(tmp_path)

    assert nested_ignore_path.read_text() == first_result


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes required")
def test_repository_state_preserves_existing_atomic_replacement_modes_and_sets_new_ignores_to_0644(
    tmp_path: Path,
) -> None:
    existing_root = tmp_path / "existing"
    existing_root.mkdir()
    context = ensure_repository_state(existing_root)
    root_ignore_path = existing_root / ".gitignore"
    nested_ignore_path = context.mercury_dir / ".gitignore"
    root_ignore_path.write_text("dist/\n")
    nested_ignore_path.write_text("catalog/\n")
    root_ignore_path.chmod(0o640)
    nested_ignore_path.chmod(0o600)
    context.config_path.chmod(0o640)

    ensure_repository_state(existing_root)
    configure_connector(
        context,
        connector_id="custom-books",
        environment="production",
        driver_id="api_key_header",
        base_url="https://api.example-books.com/v2",
        auth_settings={"key_name": "X-API-Key"},
    )

    assert stat.S_IMODE(root_ignore_path.stat().st_mode) == 0o640
    assert stat.S_IMODE(nested_ignore_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(context.config_path.stat().st_mode) == 0o640

    new_root = tmp_path / "new"
    new_root.mkdir()
    new_context = ensure_repository_state(new_root)

    assert stat.S_IMODE((new_root / ".gitignore").stat().st_mode) == 0o644
    assert stat.S_IMODE((new_context.mercury_dir / ".gitignore").stat().st_mode) == 0o644


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


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example-books.com/v1",
        "https://api.example-books.com/v1/token",
        "https://api.example-books.com/oauth/token",
        "https://api.example-books.com/v1/customer%20records",
    ],
)
def test_connector_configuration_preserves_safe_endpoint_paths(
    tmp_path: Path,
    base_url: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    config = configure_connector(
        context,
        connector_id="custom-books",
        environment="production",
        driver_id="api_key_header",
        base_url=base_url,
        auth_settings={"key_name": "X-API-Key"},
    )

    assert config.connectors["custom-books"]["production"]["base_url"] == base_url


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example-books.com/ghp_secret",
        "https://api.example-books.com/%67hp_secret",
        "https://api.example-books.com/v1/client_secret-value",
        "https://api.example-books.com/v1/%61ccess_token",
    ],
)
def test_connector_configuration_rejects_credential_material_in_decoded_path_segments(
    tmp_path: Path,
    base_url: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="secret_endpoint_path_not_allowed"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url=base_url,
            auth_settings={"key_name": "X-API-Key"},
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


def test_connector_configuration_rejects_credential_like_oauth_scope(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="secret_auth_setting_not_allowed"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="oauth_client_credentials",
            base_url="https://api.example-books.com/v2",
            auth_settings={"scope": "ghp_not_a_scope"},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("connector_id", "ghp_not_a_connector"),
        ("environment", "sk-not-an-environment"),
        ("driver_id", "ghp_not_a_driver"),
        ("driver_id", "not a driver"),
    ],
)
def test_connector_configuration_rejects_invalid_or_credential_like_identifiers(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    settings = {
        "connector_id": "custom-books",
        "environment": "production",
        "driver_id": "api_key_header",
    }
    settings[field] = value

    with pytest.raises(ValueError):
        configure_connector(
            context,
            base_url="https://api.example-books.com/v2",
            auth_settings={"key_name": "X-API-Key"},
            **settings,
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


@pytest.mark.parametrize(
    "host",
    [
        "0xa9fea9fe",
        "2852039166",
        "0251.0376.0251.0376",
    ],
)
def test_connector_configuration_rejects_legacy_ipv4_numeric_aliases(
    tmp_path: Path,
    host: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="invalid_endpoint_url"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url=f"https://{host}/v1",
            auth_settings={"key_name": "X-API-Key"},
        )


@pytest.mark.parametrize(
    "host",
    [
        "0xa9.0xfe.0xa9.0xfe",
        "0xa9.254.0251.0xfe",
    ],
)
def test_connector_configuration_rejects_mixed_legacy_ipv4_aliases(
    tmp_path: Path,
    host: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="^invalid_endpoint_url$"):
        configure_connector(
            context,
            connector_id="custom-books",
            environment="production",
            driver_id="api_key_header",
            base_url=f"https://{host}/v1",
            auth_settings={"key_name": "X-API-Key"},
        )


def test_connector_configuration_preserves_canonical_public_ipv4(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)

    config = configure_connector(
        context,
        connector_id="custom-books",
        environment="production",
        driver_id="api_key_header",
        base_url="https://93.184.216.34/v1",
        auth_settings={"key_name": "X-API-Key"},
    )

    assert config.trusted_hosts["custom-books"]["production"] == ("93.184.216.34",)


@pytest.mark.parametrize("environment", ["local", "gateway"])
def test_internet_urls_require_https_unless_private_network_is_enabled(
    tmp_path: Path,
    environment: str,
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
        connector_id=f"{environment}-books",
        environment=environment,
        driver_id="api_key_header",
        base_url="http://localhost:8080/v2",
        auth_settings={"key_name": "X-API-Key", "allow_private_network": True},
    )

    assert config.allow_private_network(f"{environment}-books", environment) is True
    assert load_repository_config(context).allow_private_network(
        f"{environment}-books", environment
    ) is True


@pytest.mark.parametrize("environment", ["local", "gateway"])
@pytest.mark.parametrize(
    ("host", "scheme"),
    [
        ("169.254.169.254", "http"),
        ("fd00:ec2::254", "http"),
        ("100.100.100.200", "https"),
        ("metadata.google.internal", "https"),
        ("metadata.goog", "https"),
        ("instance-data.ec2.internal", "https"),
    ],
)
def test_connector_configuration_rejects_metadata_targets_even_with_private_network_allowed(
    tmp_path: Path,
    environment: str,
    host: str,
    scheme: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    url_host = f"[{host}]" if ":" in host else host

    with pytest.raises(ValueError, match="^forbidden_metadata_host$"):
        configure_connector(
            context,
            connector_id=f"{environment}-metadata",
            environment=environment,
            driver_id="api_key_header",
            base_url=f"{scheme}://{url_host}/v1",
            auth_settings={"key_name": "X-API-Key", "allow_private_network": True},
        )


@pytest.mark.parametrize("environment", ["local", "gateway"])
@pytest.mark.parametrize(
    "host",
    [
        "fd00:0ec2:0:0:0:0:0:0254",
        "fd00:0ec2:0000:0000:0000:0000:0000:0254",
    ],
)
def test_connector_configuration_rejects_expanded_metadata_ipv6_targets(
    tmp_path: Path,
    environment: str,
    host: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="^forbidden_metadata_host$"):
        configure_connector(
            context,
            connector_id=f"{environment}-expanded-metadata",
            environment=environment,
            driver_id="api_key_header",
            base_url=f"http://[{host}]/v1",
            auth_settings={"key_name": "X-API-Key", "allow_private_network": True},
        )


@pytest.mark.parametrize("environment", ["local", "gateway"])
@pytest.mark.parametrize(
    "host",
    [
        "::ffff:169.254.169.254",
        "::ffff:a9fe:a9fe",
        "::ffff:100.100.100.200",
        "::ffff:6464:64c8",
    ],
)
def test_connector_configuration_rejects_ipv4_mapped_metadata_targets(
    tmp_path: Path,
    environment: str,
    host: str,
) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(ValueError, match="^forbidden_metadata_host$"):
        configure_connector(
            context,
            connector_id=f"{environment}-mapped-metadata",
            environment=environment,
            driver_id="api_key_header",
            base_url=f"https://[{host}]/v1",
            auth_settings={"key_name": "X-API-Key", "allow_private_network": True},
        )


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
    "payload",
    [
        {"schema_version": 1, "trusted_hosts": {}},
        {"schema_version": 1, "connectors": {}},
        {"trusted_hosts": {}, "connectors": {}},
        {
            "schema_version": 1,
            "trusted_hosts": {},
            "connectors": {},
            "unknown": "field",
        },
    ],
)
def test_load_repository_config_requires_exact_top_level_schema(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    context = ensure_repository_state(tmp_path)
    context.config_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="invalid_repository_config"):
        load_repository_config(context)


@pytest.mark.parametrize(
    "config_text",
    [
        "{not valid JSON",
        (
            '{"schema_version": 1, "trusted_hosts": {}, '
            '"connectors": {"client_secret": "sensitive-overwritten-value"}, '
            '"connectors": {}}'
        ),
        (
            '{"schema_version": 1, '
            '"trusted_hosts": {"custom-books": {"production": ['
            '"api.example-books.com"]}}, "connectors": {"custom-books": {'
            '"production": {"driver_id": "api_key_header", '
            '"base_url": "https://api.example-books.com/v1", '
            '"auth_settings": {"client_secret": "sensitive-overwritten-value"}, '
            '"auth_settings": {"key_name": "X-API-Key"}, '
            '"network_policy": {"allow_private_network": false}}}}}'
        ),
    ],
)
def test_load_repository_config_rejects_parse_and_duplicate_key_errors_without_echoing_values(
    tmp_path: Path,
    config_text: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    context.config_path.write_text(config_text)

    with pytest.raises(ValueError, match="^invalid_repository_config$") as error:
        load_repository_config(context)

    assert str(error.value) == "invalid_repository_config"


def test_load_repository_config_rejects_invalid_utf8_without_echoing_bytes(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    context.config_path.write_bytes(b'{"client_secret": "\xff"}')

    with pytest.raises(ValueError, match="^invalid_repository_config$") as error:
        load_repository_config(context)

    assert str(error.value) == "invalid_repository_config"


@pytest.mark.parametrize("schema_version", [True, 1.0, "1", None, 2])
def test_load_repository_config_requires_exact_integer_schema_version_one(
    tmp_path: Path,
    schema_version: object,
) -> None:
    context = ensure_repository_state(tmp_path)
    context.config_path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "trusted_hosts": {},
                "connectors": {},
            }
        )
    )

    with pytest.raises(ValueError, match="invalid_repository_config"):
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


@pytest.mark.parametrize(
    "record_updates",
    [
        {"base_url": "https://api.example-books.com:invalid/v2"},
        {"auth_settings": {"authorization_header": "Bearer secret"}},
        {"auth_settings": {"token_urls": ["https://auth.example-books.com/token"]}},
        {"auth_settings": {"grant_type": "authorization_code"}},
        {"auth_settings": {"scope": "ghp_not_a_scope"}},
        {"network_policy": {"allow_private_network": True, "extra": False}},
    ],
)
def test_load_repository_config_enforces_configure_time_record_constraints(
    tmp_path: Path,
    record_updates: dict[str, object],
) -> None:
    context = ensure_repository_state(tmp_path)
    _write_connector_config(context, record_updates=record_updates)

    with pytest.raises(ValueError):
        load_repository_config(context)


@pytest.mark.parametrize(
    ("connector_id", "environment", "record_updates"),
    [
        ("ghp_not_a_connector", "production", {}),
        ("custom-books", "sk-not-an-environment", {}),
        ("custom-books", "production", {"driver_id": "ghp_not_a_driver"}),
    ],
)
def test_load_repository_config_rejects_invalid_or_credential_like_identifiers(
    tmp_path: Path,
    connector_id: str,
    environment: str,
    record_updates: dict[str, object],
) -> None:
    context = ensure_repository_state(tmp_path)
    _write_connector_config(
        context,
        connector_id=connector_id,
        environment=environment,
        record_updates=record_updates,
    )

    with pytest.raises(ValueError):
        load_repository_config(context)


def test_load_repository_config_rejects_unknown_connector_record_keys(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    _write_connector_config(context, record_updates={"authorization_header": "Bearer secret"})

    with pytest.raises(ValueError):
        load_repository_config(context)


@pytest.mark.parametrize(
    "trusted_hosts",
    [
        ["api.example-books.com"],
        ["api.example-books.com", "auth.example-books.com", "extra.example-books.com"],
    ],
)
def test_load_repository_config_requires_exact_connector_trusted_hosts(
    tmp_path: Path,
    trusted_hosts: list[str],
) -> None:
    context = ensure_repository_state(tmp_path)
    _write_connector_config(context, trusted_hosts=trusted_hosts)

    with pytest.raises(ValueError):
        load_repository_config(context)


@pytest.mark.parametrize(
    ("config", "code"),
    [
        (RepositoryConfig(schema_version=2), "invalid_repository_config"),
        (
            RepositoryConfig(
                trusted_hosts={"custom-books": {"production": ("api.example.test",)}},
                connectors={
                    "custom-books": {
                        "production": {
                            "driver_id": "bearer",
                            "base_url": "http://api.example.test/v1",
                            "auth_settings": {},
                            "network_policy": {"allow_private_network": False},
                        }
                    }
                },
            ),
            "https_required",
        ),
        (
            RepositoryConfig(
                trusted_hosts={"custom-books": {"production": ("metadata.google.internal",)}},
                connectors={
                    "custom-books": {
                        "production": {
                            "driver_id": "bearer",
                            "base_url": "https://metadata.google.internal/v1",
                            "auth_settings": {},
                            "network_policy": {"allow_private_network": False},
                        }
                    }
                },
            ),
            "invalid_trusted_hosts",
        ),
        (
            RepositoryConfig(
                trusted_hosts={"custom-books": {"production": ("api.example.test",)}},
                connectors={
                    "custom-books": {
                        "production": {
                            "driver_id": "bearer",
                            "base_url": "https://api.example.test:invalid/v1",
                            "auth_settings": {},
                            "network_policy": {"allow_private_network": False},
                        }
                    }
                },
            ),
            "invalid_endpoint_url",
        ),
        (
            RepositoryConfig(
                trusted_hosts={"custom-books": {"production": ("127.0.0.1",)}},
                connectors={
                    "custom-books": {
                        "production": {
                            "driver_id": "bearer",
                            "base_url": "https://127.0.0.1/v1",
                            "auth_settings": {},
                            "network_policy": {"allow_private_network": False},
                        }
                    }
                },
            ),
            "private_network_not_allowed",
        ),
        (
            RepositoryConfig(
                trusted_hosts={"custom-books": {"production": ("api.example.test",)}},
                connectors={
                    "custom-books": {
                        "production": {
                            "driver_id": "bearer",
                            "base_url": "https://auth.example.test/v1",
                            "auth_settings": {},
                            "network_policy": {"allow_private_network": False},
                        }
                    }
                },
            ),
            "invalid_trusted_hosts",
        ),
    ],
)
def test_normalize_repository_config_revalidates_manually_constructed_records(
    config: RepositoryConfig,
    code: str,
) -> None:
    normalizer = getattr(repository_module, "normalize_repository_config", None)
    assert callable(normalizer)
    with pytest.raises(ValueError, match=rf"^{code}$"):
        normalizer(config)


def test_normalize_repository_config_returns_a_validated_copy_for_manually_constructed_records(
) -> None:
    config = RepositoryConfig(
        trusted_hosts={"custom-books": {"local": ["127.0.0.1"]}},  # type: ignore[dict-item]
        connectors={
            "custom-books": {
                "local": {
                    "driver_id": "bearer",
                    "base_url": "http://127.0.0.1:8080/v1",
                    "auth_settings": {},
                    "network_policy": {"allow_private_network": True},
                }
            }
        },
    )

    normalizer = getattr(repository_module, "normalize_repository_config", None)
    assert callable(normalizer)
    normalized = normalizer(config)

    assert normalized is not config
    assert normalized.trusted_hosts == {"custom-books": {"local": ("127.0.0.1",)}}
    assert normalized.connectors["custom-books"]["local"]["base_url"] == (
        "http://127.0.0.1:8080/v1"
    )


@pytest.mark.parametrize(
    ("setting", "endpoint"),
    [
        ("base_url", "https://api.example.test/%ZZ"),
        ("base_url", "https://api.example.test:"),
        ("base_url", "https://api.example.test/path with-space"),
        ("base_url", "https://api.example.test/path\\segment"),
        ("base_url", "https://api.example.test/path\x00segment"),
        ("token_url", "https://auth.example.test/%ZZ"),
        ("token_url", "https://auth.example.test:"),
        ("token_url", "https://auth.example.test/path with-space"),
        ("token_url", "https://auth.example.test/path\\segment"),
        ("token_url", "https://auth.example.test/path\x00segment"),
    ],
)
def test_normalize_repository_config_rejects_raw_invalid_endpoint_syntax(
    setting: str,
    endpoint: str,
) -> None:
    auth_settings: dict[str, str] = {"token_url": "https://auth.example.test/token"}
    base_url = "https://api.example.test/v1"
    if setting == "base_url":
        base_url = endpoint
    else:
        auth_settings["token_url"] = endpoint
    config = RepositoryConfig(
        trusted_hosts={
            "custom": {"production": ("api.example.test", "auth.example.test")}
        },
        connectors={
            "custom": {
                "production": {
                    "driver_id": "oauth_client_credentials",
                    "base_url": base_url,
                    "auth_settings": auth_settings,
                    "network_policy": {"allow_private_network": False},
                }
            }
        },
    )

    with pytest.raises(ValueError, match="^invalid_endpoint_url$"):
        repository_module.normalize_repository_config(config)
