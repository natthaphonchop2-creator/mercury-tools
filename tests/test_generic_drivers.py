from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from mercury_tools.drivers.generic import (
    ConnectorAuthError,
    DriverConfigurationError,
    GenericApiKeyDriver,
    GenericBearerDriver,
    GenericOAuthClientCredentialsDriver,
)
from mercury_tools.drivers.registry import UnknownDriverError, build_generic_registry


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
    with pytest.raises(DriverConfigurationError, match="^unsupported_environment$"):
        driver.credential_fields("sandbox")


def test_oauth_credential_fields_require_a_token_url_for_the_environment() -> None:
    driver = GenericOAuthClientCredentialsDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
        token_urls={},
    )

    with pytest.raises(DriverConfigurationError, match="^unsupported_environment$"):
        driver.credential_fields("production")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credentials", "code"),
    [
        ({}, "credential_missing"),
        ({"token": " \t "}, "credential_blank"),
        ({"token": "valid-token", "extra": "opaque-extra"}, "credential_undeclared"),
    ],
)
async def test_generic_driver_requires_an_exact_credential_bundle(
    credentials: dict[str, str],
    code: str,
) -> None:
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConnectorAuthError, match=rf"^{code}$") as error:
            await driver.prepare_auth(
                environment="production",
                credentials=credentials,
                client=client,
            )

    assert "opaque-extra" not in str(error.value)


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


@pytest.mark.asyncio
async def test_probe_replaces_exact_credential_echoes_and_omits_provider_details() -> None:
    secret = "opaque-probe-secret"
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test/v1"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return httpx.Response(
            200,
            json={"company_name": f"Company {secret}", "details": {"echo": secret}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await driver.validate_credentials(
            environment="production",
            credentials={"token": secret},
            client=client,
        )

    assert probe.company_name == "Company [REDACTED]"
    assert probe.details == {"http_status": 200}
    assert secret not in json.dumps({"company_name": probe.company_name, "details": probe.details})


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
    opaque_plaintext = "opaque-secret-that-must-never-be-returned"
    text_response = httpx.Response(502, text=opaque_plaintext)

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
    assert text_result.summary == "plaintext_response"
    assert opaque_plaintext not in text_result.summary


def test_interpret_response_distinguishes_json_null_from_json_decode_failure(
    action_factory,
) -> None:
    driver = GenericBearerDriver(connector_id="custom", environments={})
    action = action_factory(connector_id="custom")

    json_null = driver.interpret_response(
        action=action,
        response=httpx.Response(200, content=b"null"),
        dispatched=True,
    )
    malformed_json = driver.interpret_response(
        action=action,
        response=httpx.Response(200, content=b"{"),
        dispatched=True,
    )

    assert json_null.data is None
    assert json_null.summary == "json_response"
    assert malformed_json.data is None
    assert malformed_json.summary == "plaintext_response"


def test_terminal_wildcard_redaction_replaces_every_child_value(action_factory) -> None:
    driver = GenericBearerDriver(connector_id="custom", environments={})
    action = action_factory(
        connector_id="custom",
        response_redaction=("credentials.*", "records.*"),
    )

    result = driver.interpret_response(
        action=action,
        response=httpx.Response(
            200,
            json={
                "credentials": {
                    "api_key": "first-secret",
                    "nested": {"token": "second-secret"},
                    "values": ["third-secret", {"key": "fourth-secret"}],
                },
                "records": ["fifth-secret", {"token": "sixth-secret"}],
            },
        ),
        dispatched=True,
    )

    assert result.data == {
        "credentials": {
            "api_key": "[REDACTED]",
            "nested": "[REDACTED]",
            "values": "[REDACTED]",
        },
        "records": ("[REDACTED]", "[REDACTED]"),
    }


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


def test_prepare_files_requires_exact_multipart_type_and_existing_directory_roots(
    tmp_path: Path,
    action_factory,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    document = root / "document.txt"
    document.write_text("document")
    driver = GenericBearerDriver(connector_id="custom", environments={})
    inputs = {"files": {"document": str(document)}}

    accepted = action_factory(
        content_type="Multipart/Form-Data; boundary=expected",
        input_schema={"files": {"document": {}}},
    )
    assert driver.prepare_files(action=accepted, inputs=inputs, roots=(root,))[0].path == document

    invalid_type = action_factory(
        content_type="multipart/form-data-malformed",
        input_schema={"files": {"document": {}}},
    )
    with pytest.raises(DriverConfigurationError, match="^multipart_content_type_required$"):
        driver.prepare_files(action=invalid_type, inputs=inputs, roots=(root,))
    with pytest.raises(DriverConfigurationError, match="^multipart_root_invalid$"):
        driver.prepare_files(action=accepted, inputs=inputs, roots=(document,))


@pytest.mark.skipif(os.name != "posix", reason="symlink loop requires POSIX symlinks")
def test_prepare_files_converts_symlink_loop_failures_to_path_free_codes(
    tmp_path: Path,
    action_factory,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    loop = root / "loop"
    loop.symlink_to(loop)
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={"files": {"document": {}}},
    )
    driver = GenericBearerDriver(connector_id="custom", environments={})

    with pytest.raises(DriverConfigurationError, match="^multipart_file_invalid$") as file_error:
        driver.prepare_files(
            action=action,
            inputs={"files": {"document": str(loop)}},
            roots=(root,),
        )
    with pytest.raises(DriverConfigurationError, match="^multipart_root_invalid$") as root_error:
        driver.prepare_files(
            action=action,
            inputs={"files": {"document": str(root / "unused.txt")}},
            roots=(loop,),
        )

    assert str(loop) not in str(file_error.value)
    assert str(loop) not in str(root_error.value)


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


@pytest.mark.asyncio
async def test_generic_registry_factory_lookup_creates_usable_configured_drivers() -> None:
    registry = build_generic_registry()

    factory = registry.get_factory("bearer")
    driver = registry.create(
        "bearer",
        connector_id="custom",
        environments={"production": "https://erp.example.test/v1"},
    )

    assert factory.credential_schema == driver.credential_schema
    assert driver.resolve_base_url("production") == "https://erp.example.test/v1"
    assert driver.credential_fields("production") == factory.credential_schema
    async with httpx.AsyncClient() as client:
        auth = await driver.prepare_auth(
            environment="production",
            credentials={"token": "configured-token"},
            client=client,
        )
    assert auth.headers == {"Authorization": "Bearer configured-token"}
    with pytest.raises(UnknownDriverError, match="^connector_driver_factory_not_found$"):
        registry.get_factory("missing")
    with pytest.raises(DriverConfigurationError, match="^driver_environments_required$"):
        registry.create("basic", connector_id="custom", environments={})
