from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from mercury_tools.drivers.generic import (
    DriverConfigurationError,
    GenericApiKeyDriver,
    GenericBearerDriver,
    GenericOAuthClientCredentialsDriver,
)
from mercury_tools.drivers.registry import build_generic_registry


@pytest.mark.asyncio
async def test_bearer_driver_adds_authorization_only_to_operation_scoped_auth_context() -> None:
    token = "secret-token"
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test/v1"},
    )

    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        auth = await driver.prepare_auth(
            environment="production",
            credentials={"token": token},
            client=client,
        )

    assert auth.headers == {"Authorization": f"Bearer {token}"}
    assert auth.query == {}
    assert "token" not in driver.__dict__
    assert token not in json.dumps(dict(driver.__dict__))


def test_api_key_query_driver_rejects_unknown_environment() -> None:
    driver = GenericApiKeyDriver(
        connector_id="custom",
        placement="query",
        key_name="api_key",
        environments={"production": "https://erp.example.test"},
    )

    with pytest.raises(DriverConfigurationError, match="^unsupported_environment$"):
        driver.resolve_base_url("sandbox")


@pytest.mark.asyncio
async def test_oauth_client_credentials_keeps_client_secret_out_of_exceptions_and_probe() -> None:
    secret = "super-secret-client-secret"
    driver = GenericOAuthClientCredentialsDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test/v1"},
        token_urls={"production": "https://auth.example.test/token"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://auth.example.test/token"
        assert secret in request.content.decode()
        return httpx.Response(400, text=f"client_secret={secret}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception) as raised:
            await driver.prepare_auth(
                environment="production",
                credentials={"client_id": "client", "client_secret": secret},
                client=client,
            )

    assert secret not in str(raised.value)


def test_interpret_response_honors_body_error_rules_bounds_non_json_and_redacts_response(
    action_factory,
) -> None:
    driver = GenericBearerDriver(connector_id="custom", environments={})
    action = action_factory(
        connector_id="custom",
        error_rules={"body": {"path": "meta.status", "equals": "error"}},
        response_redaction=("customer.tax_id", "access_token"),
    )
    body_error = httpx.Response(
        200,
        json={
            "meta": {"status": "error"},
            "customer": {"tax_id": "1234567890123", "name": "Ada"},
            "access_token": "response-token",
        },
    )
    text_response = httpx.Response(502, text="x" * 2_000)

    body_result = driver.interpret_response(action=action, response=body_error, dispatched=True)
    text_result = driver.interpret_response(action=action, response=text_response, dispatched=True)

    assert body_result.status == "failed"
    assert body_result.http_status == 200
    assert body_result.data == {
        "meta": {"status": "error"},
        "customer": {"tax_id": "[REDACTED]", "name": "Ada"},
        "access_token": "[REDACTED]",
    }
    assert text_result.status == "failed"
    assert len(text_result.summary) <= 1024
    assert text_result.summary == "x" * 1024


def test_prepare_files_requires_declared_file_inside_active_root_and_rejects_symlink_escape(
    tmp_path: Path,
    action_factory,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "document.pdf"
    inside.write_bytes(b"pdf")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    escape = root / "escape.pdf"
    if os.name == "posix":
        escape.symlink_to(outside)

    action = action_factory(
        content_type="multipart/form-data",
        input_schema={"files": {"document": {}}},
    )
    driver = GenericBearerDriver(connector_id="custom", environments={})

    prepared = driver.prepare_files(
        action=action,
        inputs={"files": {"document": str(inside)}},
        roots=(root,),
    )
    assert prepared[0].path == inside.resolve()
    assert prepared[0].filename == "document.pdf"
    assert prepared[0].content_type == "application/pdf"

    with pytest.raises(DriverConfigurationError, match="^multipart_file_outside_roots$"):
        driver.prepare_files(
            action=action,
            inputs={"files": {"document": str(outside)}},
            roots=(root,),
        )
    if os.name == "posix":
        with pytest.raises(DriverConfigurationError, match="^multipart_file_outside_roots$"):
            driver.prepare_files(
                action=action,
                inputs={"files": {"document": str(escape)}},
                roots=(root,),
            )


def test_generic_registry_contains_all_five_generic_drivers_without_provider_modules() -> None:
    registry = build_generic_registry()

    assert [item["driver_id"] for item in registry.summaries()] == [
        "api_key_header",
        "api_key_query",
        "basic",
        "bearer",
        "oauth_client_credentials",
    ]
    oauth = next(
        item for item in registry.summaries() if item["driver_id"] == "oauth_client_credentials"
    )
    assert oauth["credential_fields"] == ("client_id", "client_secret")
