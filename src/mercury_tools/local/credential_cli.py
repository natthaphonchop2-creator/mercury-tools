"""Credential and trusted-host command handlers for repository-local Mercury state."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import shutil
import stat
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from mercury_tools.catalog.local_store import LocalCatalogStore
from mercury_tools.config import load_settings
from mercury_tools.drivers.base import DriverConfigurationError
from mercury_tools.drivers.models import ConnectionProbe, CredentialStatus
from mercury_tools.drivers.registry import DriverRegistry, UnknownDriverError
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import (
    RepositoryConfig,
    RepositoryContext,
    configure_connector,
    ensure_repository_state,
    load_repository_config,
    record_connector_validation,
)
from mercury_tools.remote import DEFAULT_RENDER_URL

_BUILTIN_ENVIRONMENTS = {
    "flowaccount": ("production", "sandbox"),
    "peak": ("production", "uat", "sandbox"),
}


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True))


def _error(code: str) -> int:
    _print_json({"status": "error", "error": code})
    return 2


def _command_context(args: argparse.Namespace) -> RepositoryContext:
    return ensure_repository_state(Path(args.repo_root))


def _driver_for(
    context: RepositoryContext,
    connector_id: str,
) -> tuple[RepositoryConfig, object]:
    config = load_repository_config(context)
    return config, DriverRegistry.for_repository(config).get(connector_id)


def _credential_statuses(
    context: RepositoryContext,
    config: RepositoryConfig,
) -> list[CredentialStatus]:
    registry = DriverRegistry.for_repository(config)
    profiles = {
        (connector_id, environment)
        for connector_id, environments in config.connectors.items()
        for environment in environments
    }
    profiles.update(
        (connector_id, environment)
        for connector_id, environments in _BUILTIN_ENVIRONMENTS.items()
        for environment in environments
    )
    store = CredentialStore(context)
    statuses: list[CredentialStatus] = []
    for connector_id, environment in sorted(profiles):
        driver = registry.get(connector_id)
        statuses.append(
            store.status(
                connector_id,
                environment,
                driver.credential_fields(environment),
            )
        )
    return statuses


def cmd_credentials_setup(args: argparse.Namespace) -> int:
    try:
        context = _command_context(args)
        _, driver = _driver_for(context, args.connector)
        fields = driver.credential_fields(args.environment)
        values: dict[str, str] = {}
        for field in fields:
            prompt = f"{field.label}: "
            values[field.name] = (
                getpass.getpass(prompt) if field.secret else input(prompt)
            )
        status = CredentialStore(context).save(
            args.connector,
            args.environment,
            values,
            fields,
        )
    except (EOFError, KeyboardInterrupt, OSError):
        return _error("interactive_input_required")
    except (DriverConfigurationError, UnknownDriverError, ValueError):
        return _error("credential_setup_failed")

    _print_json(status.public_dict())
    return 0


def cmd_credentials_status(args: argparse.Namespace) -> int:
    try:
        context = _command_context(args)
        config = load_repository_config(context)
        statuses = _credential_statuses(context, config)
    except (DriverConfigurationError, UnknownDriverError, ValueError):
        return _error("credential_status_failed")

    _print_json({"status": "ok", "credentials": [item.public_dict() for item in statuses]})
    return 0


async def _validate_credentials(
    driver: object,
    *,
    environment: str,
    credentials: dict[str, str],
) -> ConnectionProbe:
    async with httpx.AsyncClient(timeout=20) as client:
        return await driver.validate_credentials(
            environment=environment,
            credentials=credentials,
            client=client,
        )


def _sanitize_company_name(value: str | None, credentials: Sequence[str]) -> str | None:
    if not isinstance(value, str):
        return None
    sanitized = value
    for credential in sorted((item for item in credentials if item), key=len, reverse=True):
        sanitized = sanitized.replace(credential, "[REDACTED]")
    normalized = " ".join(sanitized.split())
    return normalized[:256] or None


def cmd_credentials_test(args: argparse.Namespace) -> int:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return _error("credentials_test_requires_synchronous_cli")

    try:
        context = _command_context(args)
        _, driver = _driver_for(context, args.connector)
        fields = driver.credential_fields(args.environment)
        store = CredentialStore(context)
        credentials = store.load(args.connector, args.environment, fields)
        status = store.status(args.connector, args.environment, fields)
        if not status.configured:
            _print_json(
                {
                    "status": "not_configured",
                    "connector_id": args.connector,
                    "environment": args.environment,
                    "missing_fields": list(status.missing_fields),
                }
            )
            return 1
        probe = asyncio.run(
            _validate_credentials(
                driver,
                environment=args.environment,
                credentials=credentials,
            )
        )
        probe_action = driver.safe_probe_action(args.environment)
    except (DriverConfigurationError, UnknownDriverError, ValueError, httpx.HTTPError):
        return _error("credential_validation_failed")
    except Exception:
        return _error("credential_validation_failed")

    company_name = _sanitize_company_name(probe.company_name, tuple(credentials.values()))
    result = {
        "status": probe.status,
        "connector_id": args.connector,
        "environment": args.environment,
        "company_name": company_name,
        "probe_action": probe_action,
    }
    if probe.status != "connected":
        _print_json(result)
        return 1

    try:
        record_connector_validation(
            context,
            connector_id=args.connector,
            environment=args.environment,
            company_name=company_name,
            probe_action=probe_action,
            validated_at=datetime.now(UTC).isoformat(),
        )
    except ValueError:
        return _error("credential_validation_persistence_failed")

    _print_json(result)
    return 0


def cmd_credentials_clear(args: argparse.Namespace) -> int:
    if args.clear_all:
        if args.connector is not None or args.environment is not None:
            return _error("credential_clear_scope_ambiguous")
    elif args.connector is None or args.environment is None:
        return _error("credential_clear_scope_required")

    try:
        context = _command_context(args)
        cleared = CredentialStore(context).clear(
            connector_id=args.connector,
            environment=args.environment,
            clear_all=args.clear_all,
        )
    except ValueError:
        return _error("credential_clear_failed")

    _print_json({"status": "cleared", "cleared_fields": cleared})
    return 0


def _configure_auth_settings(args: argparse.Namespace) -> tuple[dict[str, str], dict[str, Any]]:
    driver_id = args.driver
    create_options: dict[str, Any] = {}
    auth_settings: dict[str, str] = {}
    if driver_id in {"api_key_header", "api_key_query"}:
        if args.key_name is not None:
            create_options["key_name"] = args.key_name
            auth_settings["key_name"] = args.key_name
    elif driver_id == "oauth_client_credentials":
        if args.token_url is None:
            raise ValueError("token_url_required")
        token_urls = {args.environment: args.token_url}
        create_options.update(
            {
                "token_urls": token_urls,
                "client_id_name": args.client_id_name or "client_id",
                "client_secret_name": args.client_secret_name or "client_secret",
                "grant_type": args.grant_type or "client_credentials",
                "scope": args.scope,
            }
        )
        auth_settings.update(
            {
                "token_url": args.token_url,
                "client_id_name": create_options["client_id_name"],
                "client_secret_name": create_options["client_secret_name"],
                "grant_type": create_options["grant_type"],
            }
        )
        if args.scope is not None:
            auth_settings["scope"] = args.scope
    elif any(
        value is not None
        for value in (
            args.key_name,
            args.token_url,
            args.scope,
            args.grant_type,
            args.client_id_name,
            args.client_secret_name,
        )
    ):
        raise ValueError("unsupported_driver_option")
    return auth_settings, create_options


def _trusted_endpoints(urls: Sequence[str]) -> tuple[tuple[str, str], ...]:
    endpoints: list[tuple[str, str]] = []
    seen_hosts: set[str] = set()
    for value in urls:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise ValueError("https_required")
        host = parsed.hostname.lower().rstrip(".")
        if host not in seen_hosts:
            endpoints.append((parsed.scheme, host))
            seen_hosts.add(host)
    return tuple(endpoints)


def _existing_connector_matches(
    config: RepositoryConfig,
    args: argparse.Namespace,
    auth_settings: dict[str, str],
) -> bool:
    existing = config.connectors.get(args.connector, {})
    if not existing:
        return True
    for record in existing.values():
        if record["driver_id"] != args.driver:
            return False
        existing_auth = record["auth_settings"]
        if args.driver == "oauth_client_credentials":
            existing_auth = {
                key: value for key, value in existing_auth.items() if key != "token_url"
            }
            candidate_auth = {
                key: value for key, value in auth_settings.items() if key != "token_url"
            }
        else:
            candidate_auth = auth_settings
        if existing_auth != candidate_auth:
            return False
    return True


def cmd_connector_configure(args: argparse.Namespace) -> int:
    if args.connector in _BUILTIN_ENVIRONMENTS:
        return _error("builtin_connector_not_configurable")
    try:
        context = _command_context(args)
        config = load_repository_config(context)
        registry = DriverRegistry.for_repository(config)
        registry.get_factory(args.driver)
        auth_settings, create_options = _configure_auth_settings(args)
        registry.create(
            args.driver,
            connector_id=args.connector,
            environments={args.environment: args.base_url},
            **create_options,
        )
        if not _existing_connector_matches(config, args, auth_settings):
            return _error("connector_configuration_mismatch")
        urls = [args.base_url]
        if args.token_url is not None:
            urls.append(args.token_url)
        endpoints = _trusted_endpoints(urls)
    except (DriverConfigurationError, UnknownDriverError, ValueError):
        return _error("connector_configuration_invalid")

    for scheme, host in endpoints:
        print(f"Trust candidate: {scheme}://{host}")
    expected = "trust " + " ".join(host for _, host in endpoints)
    try:
        confirmed = input(f"Type '{expected}' to continue: ")
    except (EOFError, KeyboardInterrupt, OSError):
        return _error("interactive_input_required")
    if confirmed != expected:
        return _error("trusted_host_confirmation_required")

    try:
        updated = configure_connector(
            context,
            connector_id=args.connector,
            environment=args.environment,
            driver_id=args.driver,
            base_url=args.base_url,
            auth_settings=auth_settings,
        )
        DriverRegistry.for_repository(updated).get(args.connector)
    except (DriverConfigurationError, UnknownDriverError, ValueError):
        return _error("connector_configuration_failed")

    _print_json(
        {
            "status": "configured",
            "connector_id": args.connector,
            "environment": args.environment,
            "driver_id": args.driver,
            "trusted_hosts": list(updated.trusted_hosts[args.connector][args.environment]),
        }
    )
    return 0


def _permission_strength(path: Path) -> str:
    if os.name != "posix":
        return "not_applicable"
    if not path.exists():
        return "missing"
    return "owner_only" if stat.S_IMODE(path.stat().st_mode) & 0o077 == 0 else "too_permissive"


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        context = _command_context(args)
        config = load_repository_config(context)
        statuses = _credential_statuses(context, config)
        try:
            catalog_count = len(LocalCatalogStore(context).list_actions())
        except ValueError:
            catalog_count = None
    except (DriverConfigurationError, UnknownDriverError, ValueError):
        return _error("doctor_failed")

    settings = load_settings()
    _print_json(
        {
            "python_version": sys.version.split()[0],
            "uvx_available": shutil.which("uvx") is not None,
            "repository": str(context.root),
            "posix_permissions": {
                "mercury_dir": _permission_strength(context.mercury_dir),
                "credentials_file": _permission_strength(context.credentials_path),
            },
            "cloud_url": settings.public_base_url or DEFAULT_RENDER_URL,
            "local_catalog_count": catalog_count,
            "configured_connectors": sorted(config.connectors),
            "missing_fields": [
                {
                    "connector_id": status.connector_id,
                    "environment": status.environment,
                    "fields": list(status.missing_fields),
                }
                for status in statuses
                if status.missing_fields
            ],
        }
    )
    return 0


def add_credential_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo-root", default=".")
    doctor.set_defaults(func=cmd_doctor)

    credentials = sub.add_parser("credentials")
    commands = credentials.add_subparsers(dest="credentials_command", required=True)

    setup = commands.add_parser("setup")
    setup.add_argument("connector")
    setup.add_argument("--env", dest="environment", required=True)
    setup.add_argument("--repo-root", default=".")
    setup.set_defaults(func=cmd_credentials_setup)

    status = commands.add_parser("status")
    status.add_argument("--repo-root", default=".")
    status.set_defaults(func=cmd_credentials_status)

    test = commands.add_parser("test")
    test.add_argument("connector")
    test.add_argument("--env", dest="environment", required=True)
    test.add_argument("--repo-root", default=".")
    test.set_defaults(func=cmd_credentials_test)

    clear = commands.add_parser("clear")
    clear.add_argument("connector", nargs="?")
    clear.add_argument("--env", dest="environment")
    clear.add_argument("--all", dest="clear_all", action="store_true")
    clear.add_argument("--repo-root", default=".")
    clear.set_defaults(func=cmd_credentials_clear)

    connector = sub.add_parser("connector")
    connector_commands = connector.add_subparsers(dest="connector_command", required=True)
    configure = connector_commands.add_parser("configure")
    configure.add_argument("connector")
    configure.add_argument("--env", dest="environment", required=True)
    configure.add_argument("--driver", required=True)
    configure.add_argument("--base-url", required=True)
    configure.add_argument("--key-name")
    configure.add_argument("--token-url")
    configure.add_argument("--scope")
    configure.add_argument("--grant-type")
    configure.add_argument("--client-id-name")
    configure.add_argument("--client-secret-name")
    configure.add_argument("--repo-root", default=".")
    configure.set_defaults(func=cmd_connector_configure)
