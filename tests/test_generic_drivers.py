from __future__ import annotations

import json
import os
from base64 import b64encode
from pathlib import Path
from urllib.parse import quote, quote_plus

import httpx
import pytest

from mercury_tools.drivers.generic import (
    ConnectorAuthError,
    DriverConfigurationError,
    GenericApiKeyDriver,
    GenericBasicDriver,
    GenericBearerDriver,
    GenericOAuthClientCredentialsDriver,
)
from mercury_tools.drivers.registry import UnknownDriverError, build_generic_registry

_MALFORMED_URLS = (
    "erp.example.test",
    "ftp://erp.example.test",
    "https:///missing-host",
    "https://client:opaque-url-secret@erp.example.test",
    "https://erp.example.test/path#fragment",
    "https://erp.example.test:not-a-port",
    "https://[::1",
    "https://exa mple.test/path",
    "https://erp.example.test/path\tsegment",
    "https://erp.example.test/path\x00segment",
    "https://good.example\\evil.example/path",
    "https://erp.example.test/%ZZ",
    "https://erp.example.test:",
)


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
    assert token not in repr(driver.__dict__)


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


@pytest.mark.parametrize(
    ("environments", "code"),
    [
        (None, "driver_environments_required"),
        ({}, "driver_environments_required"),
        ({"": "https://erp.example.test"}, "driver_environment_invalid"),
        ({" \t ": "https://erp.example.test"}, "driver_environment_invalid"),
        ({"production": ""}, "driver_environment_invalid"),
        ({"production": "\t"}, "driver_environment_invalid"),
        *[({"production": value}, "driver_url_invalid") for value in _MALFORMED_URLS],
    ],
)
def test_direct_driver_constructor_rejects_invalid_environment_maps_without_echoing_urls(
    environments: object,
    code: str,
) -> None:
    with pytest.raises(DriverConfigurationError, match=rf"^{code}$") as error:
        GenericBearerDriver(connector_id="custom", environments=environments)  # type: ignore[arg-type]

    assert "opaque-url-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("environments", "code"),
    [
        ({" \t ": "https://erp.example.test"}, "driver_environment_invalid"),
        *[({"production": value}, "driver_url_invalid") for value in _MALFORMED_URLS],
    ],
)
def test_factory_rejects_invalid_environment_maps_at_construction(
    environments: dict[str, str],
    code: str,
) -> None:
    registry = build_generic_registry()

    with pytest.raises(DriverConfigurationError, match=rf"^{code}$") as error:
        registry.create("bearer", connector_id="custom", environments=environments)

    assert "opaque-url-secret" not in str(error.value)


@pytest.mark.parametrize(
    "environment_url",
    [
        "http://127.0.0.1:8080/gateway",
        "https://[2001:db8::1]:8443/gateway%2Fv1?name=valid%20escape",
    ],
)
def test_direct_and_factory_drivers_allow_valid_gateway_and_ipv6_urls(
    environment_url: str,
) -> None:
    direct = GenericBearerDriver(connector_id="direct", environments={"local": environment_url})
    factory = build_generic_registry().create(
        "bearer",
        connector_id="factory",
        environments={"local": environment_url},
    )

    assert direct.resolve_base_url("local") == environment_url
    assert factory.resolve_base_url("local") == environment_url


@pytest.mark.parametrize(
    ("token_urls", "code"),
    [
        (None, "driver_environments_required"),
        ({}, "driver_environments_required"),
        ({"sandbox": "https://auth.example.test/token"}, "oauth_environment_mismatch"),
        (
            {"production": "https://client:opaque-url-secret@auth.example.test/token"},
            "driver_url_invalid",
        ),
        ({"production": "https://auth.example.test:not-a-port/token"}, "driver_url_invalid"),
        ({"production": "https://auth.example.test/token#fragment"}, "driver_url_invalid"),
        ({"production": "https://auth.example.test/%ZZ"}, "driver_url_invalid"),
        ({"production": "https://auth.example.test:"}, "driver_url_invalid"),
    ],
)
def test_oauth_constructor_validates_token_urls_and_requires_exact_environment_sets(
    token_urls: object,
    code: str,
) -> None:
    with pytest.raises(DriverConfigurationError, match=rf"^{code}$") as error:
        GenericOAuthClientCredentialsDriver(
            connector_id="custom",
            environments={"production": "https://erp.example.test"},
            token_urls=token_urls,  # type: ignore[arg-type]
        )

    assert "opaque-url-secret" not in str(error.value)


@pytest.mark.parametrize("token_url", _MALFORMED_URLS)
def test_oauth_factory_rejects_malformed_token_urls(token_url: str) -> None:
    with pytest.raises(DriverConfigurationError, match="^driver_url_invalid$") as error:
        build_generic_registry().create(
            "oauth_client_credentials",
            connector_id="custom",
            environments={"production": "https://erp.example.test"},
            token_urls={"production": token_url},
        )

    assert "opaque-url-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("client_id_name", "client_secret_name"),
    [
        ("application", "APPLICATION"),
        ("grant_type", "application_secret"),
        ("application_id", "GRANT_TYPE"),
        ("scope", "application_secret"),
        ("application_id", "SCOPE"),
    ],
)
def test_oauth_constructor_rejects_colliding_form_parameter_names(
    client_id_name: str,
    client_secret_name: str,
) -> None:
    with pytest.raises(DriverConfigurationError, match="^oauth_configuration_invalid$"):
        GenericOAuthClientCredentialsDriver(
            connector_id="custom",
            environments={"production": "https://erp.example.test"},
            token_urls={"production": "https://auth.example.test/token"},
            client_id_name=client_id_name,
            client_secret_name=client_secret_name,
        )


@pytest.mark.parametrize(
    ("client_id_name", "client_secret_name"),
    [
        ("application", "APPLICATION"),
        ("Grant_Type", "application_secret"),
        ("application_id", "Scope"),
    ],
)
def test_oauth_factory_rejects_colliding_form_parameter_names(
    client_id_name: str,
    client_secret_name: str,
) -> None:
    with pytest.raises(DriverConfigurationError, match="^oauth_configuration_invalid$"):
        build_generic_registry().create(
            "oauth_client_credentials",
            connector_id="custom",
            environments={"production": "https://erp.example.test"},
            token_urls={"production": "https://auth.example.test/token"},
            client_id_name=client_id_name,
            client_secret_name=client_secret_name,
        )


def test_api_key_factory_does_not_treat_an_explicit_blank_key_name_as_default() -> None:
    registry = build_generic_registry()
    environments = {"production": "https://erp.example.test"}

    with pytest.raises(DriverConfigurationError, match="^api_key_configuration_invalid$"):
        GenericApiKeyDriver(
            connector_id="custom",
            placement="header",
            key_name=" \t ",
            environments=environments,
        )
    with pytest.raises(DriverConfigurationError, match="^api_key_configuration_invalid$"):
        registry.create(
            "api_key_header",
            connector_id="custom",
            environments=environments,
            key_name="",
        )

    defaulted = registry.create(
        "api_key_header",
        connector_id="defaulted",
        environments=environments,
        key_name=None,
    )
    assert defaulted.key_name == "X-API-Key"


def test_api_key_header_driver_rejects_transport_managed_content_type() -> None:
    with pytest.raises(DriverConfigurationError, match="^api_key_configuration_invalid$"):
        GenericApiKeyDriver(
            connector_id="custom",
            placement="header",
            key_name="Content-Type",
            environments={"production": "https://erp.example.test"},
        )


@pytest.mark.asyncio
async def test_probe_url_construction_errors_are_returned_as_safe_probe_failures() -> None:
    class UrlConstructionFailure:
        async def get(self, *args: object, **kwargs: object) -> httpx.Response:
            raise ValueError("opaque-url-secret")

    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )
    probe = await driver.validate_credentials(
        environment="production",
        credentials={"token": "credential-secret"},
        client=UrlConstructionFailure(),  # type: ignore[arg-type]
    )

    assert probe.status == "failed"
    assert probe.details == {"error": "probe_request_failed"}
    assert "opaque-url-secret" not in json.dumps(probe.public_dict())


@pytest.mark.asyncio
async def test_oauth_token_url_construction_errors_are_returned_as_safe_auth_failures() -> None:
    class UrlConstructionFailure:
        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise ValueError("opaque-url-secret")

    driver = GenericOAuthClientCredentialsDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
        token_urls={"production": "https://auth.example.test/token"},
    )

    with pytest.raises(ConnectorAuthError, match="^oauth_token_failed$") as error:
        await driver.prepare_auth(
            environment="production",
            credentials={"client_id": "client", "client_secret": "credential-secret"},
            client=UrlConstructionFailure(),  # type: ignore[arg-type]
        )

    assert "opaque-url-secret" not in str(error.value)


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
@pytest.mark.parametrize(
    "access_token",
    [
        "client secret+/%",
        quote_plus("client secret+/%", safe=""),
        quote(quote_plus("client secret+/%", safe=""), safe=""),
    ],
)
async def test_oauth_rejects_tokens_equivalent_to_submitted_credentials_before_auth_context(
    access_token: str,
) -> None:
    calls: list[str] = []
    driver = GenericOAuthClientCredentialsDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
        token_urls={"production": "https://auth.example.test/token"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"access_token": access_token})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ConnectorAuthError, match="^oauth_token_failed$"):
            await driver.prepare_auth(
                environment="production",
                credentials={"client_id": "client-id", "client_secret": "client secret+/%"},
                client=client,
            )

    assert calls == ["https://auth.example.test/token"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_payload",
    [
        '{"access_token":"safe-token","expires_in":NaN}',
        '{"access_token":"safe-token","expires_in":Infinity}',
        '{"access_token":"safe-token","expires_in":-Infinity}',
        '{"access_token":"safe-token","expires_in":' + str(10**1000) + "}",
    ],
)
async def test_oauth_ignores_nonfinite_and_out_of_range_expiry_values(token_payload: str) -> None:
    driver = GenericOAuthClientCredentialsDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
        token_urls={"production": "https://auth.example.test/token"},
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=token_payload,
                headers={"Content-Type": "application/json"},
            )
        )
    ) as client:
        auth = await driver.prepare_auth(
            environment="production",
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            client=client,
        )

    assert auth.expires_at is None


@pytest.mark.asyncio
async def test_oauth_uses_environment_token_urls_and_configured_form_fields(
) -> None:
    driver = GenericOAuthClientCredentialsDriver(
        connector_id="custom",
        environments={
            "production": "https://erp.example.test/v1",
            "sandbox": "https://sandbox.erp.example.test/v1",
        },
        token_urls={
            "production": "https://auth.example.test/production/token",
            "sandbox": "https://auth.example.test/sandbox/token",
        },
        client_id_name="application_id",
        client_secret_name="application_secret",
        grant_type="client_credentials",
        scope="ledger.read ledger.write",
    )
    calls: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), dict(httpx.QueryParams(request.content.decode()))))
        return httpx.Response(200, json={"access_token": f"token-{len(calls)}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        production = await driver.prepare_auth(
            environment="production",
            credentials={"client_id": "client", "client_secret": "secret"},
            client=client,
        )
        sandbox = await driver.prepare_auth(
            environment="sandbox",
            credentials={"client_id": "client", "client_secret": "secret"},
            client=client,
        )

    assert production.headers == {"Authorization": "Bearer token-1"}
    assert sandbox.headers == {"Authorization": "Bearer token-2"}
    assert calls == [
        (
            "https://auth.example.test/production/token",
            {
                "application_id": "client",
                "application_secret": "secret",
                "grant_type": "client_credentials",
                "scope": "ledger.read ledger.write",
            },
        ),
        (
            "https://auth.example.test/sandbox/token",
            {
                "application_id": "client",
                "application_secret": "secret",
                "grant_type": "client_credentials",
                "scope": "ledger.read ledger.write",
            },
        ),
    ]


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

    assert probe.company_name == "[REDACTED]"
    assert probe.details == {"http_status": 200}
    assert secret not in json.dumps(probe.public_dict())


_ENCODED_PROBE_SECRET = "opaque+token space/%"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "company_name",
    [
        f"Acme {_ENCODED_PROBE_SECRET}",
        f"Acme {quote(_ENCODED_PROBE_SECRET, safe='')}",
        f"Acme {quote_plus(_ENCODED_PROBE_SECRET, safe='')}",
        f"Acme {quote(quote(_ENCODED_PROBE_SECRET, safe=''), safe='')}",
        f"Acme {quote(quote_plus(_ENCODED_PROBE_SECRET, safe=''), safe='')}",
        "Acme "
        + quote_plus(_ENCODED_PROBE_SECRET, safe="")
        .replace("%2B", "%2b")
        .replace("%2F", "%2f"),
    ],
)
async def test_probe_fully_redacts_literal_and_reversibly_encoded_credential_echoes(
    company_name: str,
) -> None:
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test/v1"},
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"company_name": company_name})
        )
    ) as client:
        probe = await driver.validate_credentials(
            environment="production",
            credentials={"token": _ENCODED_PROBE_SECRET},
            client=client,
        )

    assert probe.company_name == "[REDACTED]"
    assert _ENCODED_PROBE_SECRET not in json.dumps(probe.public_dict())


@pytest.mark.asyncio
async def test_probe_fully_redacts_reversibly_encoded_auth_value_echoes() -> None:
    driver = GenericBasicDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test/v1"},
    )
    credentials = {"username": "operator", "password": "opaque password"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"company_name": f"Acme {quote(request.headers['Authorization'], safe='')}"},
            )
        )
    ) as client:
        probe = await driver.validate_credentials(
            environment="production",
            credentials=credentials,
            client=client,
        )

    assert probe.company_name == "[REDACTED]"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("driver", "credentials", "auth_value"),
    [
        (
            GenericBearerDriver(
                connector_id="custom",
                environments={"production": "https://erp.example.test/v1"},
            ),
            {"token": "bearer-probe-token"},
            "Bearer bearer-probe-token",
        ),
        (
            GenericApiKeyDriver(
                connector_id="custom",
                placement="query",
                key_name="api_key",
                environments={"production": "https://erp.example.test/v1"},
            ),
            {"api_key": "query-probe-token"},
            "query-probe-token",
        ),
        (
            GenericBasicDriver(
                connector_id="custom",
                environments={"production": "https://erp.example.test/v1"},
            ),
            {"username": "basic-user", "password": "basic-probe-token"},
            "Basic " + b64encode(b"basic-user:basic-probe-token").decode(),
        ),
    ],
    ids=("bearer-header", "api-key-query", "basic-header"),
)
async def test_probe_redacts_base64_encoded_generic_auth_representations(
    driver: GenericBearerDriver | GenericApiKeyDriver | GenericBasicDriver,
    credentials: dict[str, str],
    auth_value: str,
) -> None:
    encoded_auth_value = b64encode(auth_value.encode()).decode()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"company_name": f"Acme {encoded_auth_value}"},
            )
        )
    ) as client:
        probe = await driver.validate_credentials(
            environment="production",
            credentials=credentials,
            client=client,
        )

    rendered = json.dumps(probe.public_dict()) + repr(probe)
    assert probe.company_name == "[REDACTED]"
    assert auth_value not in rendered
    assert encoded_auth_value not in rendered


def test_interpret_response_honors_body_error_rules_bounds_non_json_and_redacts_response(
    action_factory,
) -> None:
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )
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
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )
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
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )
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
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )

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
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )
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
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test"},
    )

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
