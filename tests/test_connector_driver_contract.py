from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest

from mercury_tools.drivers.base import (
    AuthContext,
    ConnectionProbe,
    ConnectorDriver,
    ConnectorResult,
    PreparedFile,
)
from mercury_tools.drivers.generic import GenericBearerDriver
from mercury_tools.drivers.models import CredentialField
from mercury_tools.drivers.registry import (
    DriverRegistry,
    DuplicateDriverError,
    UnknownDriverError,
    build_generic_registry,
)


def test_public_driver_models_are_frozen() -> None:
    auth = AuthContext(headers={"Authorization": "Bearer token"}, query={}, expires_at=None)
    probe = ConnectionProbe(
        status="connected",
        connector_id="custom",
        environment="production",
        company_name=None,
        details={"http_status": 200},
    )
    result = ConnectorResult(
        status="succeeded",
        http_status=200,
        data={"ok": True},
        summary="ok",
        dispatched=True,
    )
    prepared = PreparedFile(
        "document",
        Path("/tmp/document.pdf"),
        "document.pdf",
        "application/pdf",
    )

    for model in (auth, probe, result, prepared):
        with pytest.raises(FrozenInstanceError):
            model.status = "changed"  # type: ignore[misc,union-attr]


@pytest.mark.parametrize(
    ("label", "headers", "query", "secret"),
    [
        ("bearer", {"Authorization": "Bearer bearer-secret"}, {}, "bearer-secret"),
        ("api_key_header", {"X-API-Key": "header-secret"}, {}, "header-secret"),
        ("api_key_query", {}, {"api_key": "query-secret"}, "query-secret"),
        ("basic", {"Authorization": "Basic basic-secret"}, {}, "basic-secret"),
        ("oauth", {"Authorization": "Bearer oauth-secret"}, {}, "oauth-secret"),
    ],
)
def test_auth_context_repr_exposes_only_safe_metadata(
    label: str,
    headers: dict[str, str],
    query: dict[str, str],
    secret: str,
) -> None:
    rendered = repr(AuthContext(headers=headers, query=query, expires_at=None))

    assert secret not in rendered
    assert "header_names" in rendered
    assert label not in rendered
    for name in (*headers, *query):
        assert name in rendered


def test_probe_details_and_result_data_are_deeply_immutable_and_json_serializable() -> None:
    probe = ConnectionProbe(
        status="connected",
        connector_id="custom",
        environment="production",
        company_name="Example Co.",
        details={"meta": {"items": [{"status": "ok"}]}},
    )
    result = ConnectorResult(
        status="succeeded",
        http_status=200,
        data={"meta": {"items": [{"status": "ok"}]}},
        summary="json_response",
        dispatched=True,
    )

    assert probe.details == {"meta": {"items": ({"status": "ok"},)}}
    assert result.data == {"meta": {"items": ({"status": "ok"},)}}
    assert json.loads(json.dumps(probe.details, allow_nan=False)) == {
        "meta": {"items": [{"status": "ok"}]}
    }
    assert json.loads(json.dumps(result.data, allow_nan=False)) == {
        "meta": {"items": [{"status": "ok"}]}
    }
    with pytest.raises(TypeError, match="^immutable_mapping$"):
        probe.details["meta"]["items"][0]["status"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError, match="^immutable_mapping$"):
        result.data["meta"]["items"][0]["status"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    "value",
    [
        {1: "non-string-key"},
        float("nan"),
        float("inf"),
        float("-inf"),
        b"opaque-bytes",
        bytearray(b"opaque-bytearray"),
        memoryview(b"opaque-memoryview"),
        {"opaque-set"},
        object(),
    ],
)
def test_public_driver_response_models_reject_non_json_values_with_safe_stable_errors(
    value: object,
) -> None:
    with pytest.raises(TypeError, match="^public_data_invalid$") as probe_error:
        ConnectionProbe(
            status="connected",
            connector_id="custom",
            environment="production",
            company_name=None,
            details={"value": value},
        )
    with pytest.raises(TypeError, match="^public_data_invalid$") as result_error:
        ConnectorResult(
            status="succeeded",
            http_status=200,
            data=value,
            summary="json_response",
            dispatched=True,
        )

    assert "opaque" not in str(probe_error.value)
    assert "opaque" not in str(result_error.value)


@pytest.mark.parametrize("value", [None, True, 1, 1.5, "ok", ["one", {"two": 2.0}]])
def test_public_driver_response_models_accept_strict_json_data(value: object) -> None:
    result = ConnectorResult(
        status="succeeded",
        http_status=200,
        data=value,
        summary="json_response",
        dispatched=True,
    )

    json.dumps(result.data, allow_nan=False)


def test_registry_rejects_duplicates_with_stable_error_and_lists_immutable_summaries() -> None:
    registry = DriverRegistry()
    environments = {"production": "https://erp.example.test"}
    registry.register(GenericBearerDriver(connector_id="zeta", environments=environments))
    registry.register(GenericBearerDriver(connector_id="alpha", environments=environments))

    with pytest.raises(DuplicateDriverError, match="^duplicate_connector_driver$"):
        registry.register(GenericBearerDriver(connector_id="alpha", environments=environments))
    with pytest.raises(UnknownDriverError, match="^connector_driver_not_found$"):
        registry.get("missing")

    summaries = registry.summaries()

    assert [item["connector_id"] for item in summaries] == ["alpha", "zeta"]
    assert [item["entry_type"] for item in summaries] == ["connector", "connector"]
    assert summaries[0]["credential_fields"] == ("token",)
    assert "secret-token" not in json.dumps(summaries)
    with pytest.raises(TypeError):
        summaries[0]["connector_id"] = "changed"  # type: ignore[index]
    with pytest.raises(AttributeError):
        summaries[0]["credential_fields"].append("changed")  # type: ignore[union-attr]

    assert registry.summaries()[0]["connector_id"] == "alpha"


def test_registry_summaries_use_explicit_credential_schema_without_a_fake_environment() -> None:
    class SchemaOnlyDriver:
        driver_id = "schema_only"
        connector_id = "custom"
        credential_schema = (CredentialField("token", secret=True, label="Token"),)

        def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
            raise AssertionError(f"credential_fields must not receive {environment!r}")

    registry = DriverRegistry()
    registry.register(SchemaOnlyDriver())  # type: ignore[arg-type]

    assert registry.summaries() == (
        {
            "entry_type": "connector",
            "connector_id": "custom",
            "driver_id": "schema_only",
            "credential_fields": ("token",),
        },
    )


def test_connector_driver_protocol_does_not_require_credential_schema() -> None:
    assert "credential_schema" not in ConnectorDriver.__annotations__


def test_registry_summaries_accept_a_driver_with_only_the_planned_protocol() -> None:
    class PlannedProtocolDriver:
        driver_id = "planned"
        connector_id = "custom"

        def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
            raise AssertionError(f"credential_fields must not receive {environment!r}")

        def resolve_base_url(self, environment: str) -> str:
            return "https://erp.example.test"

        def safe_probe_action(self, environment: str) -> str:
            return "GET /"

        def prepare_files(self, **kwargs: object) -> tuple[PreparedFile, ...]:
            return ()

        async def prepare_auth(self, **kwargs: object) -> AuthContext:
            return AuthContext(headers={}, query={}, expires_at=None)

        async def validate_credentials(self, **kwargs: object) -> ConnectionProbe:
            return ConnectionProbe(
                status="connected",
                connector_id=self.connector_id,
                environment="production",
                company_name=None,
                details={},
            )

        def interpret_response(self, **kwargs: object) -> ConnectorResult:
            return ConnectorResult(
                status="succeeded",
                http_status=200,
                data=None,
                summary="json_response",
                dispatched=True,
            )

        def sanitize_response(self, action: object, value: object) -> object:
            return value

    registry = DriverRegistry()
    registry.register(PlannedProtocolDriver())  # type: ignore[arg-type]

    assert registry.summaries() == (
        {
            "entry_type": "connector",
            "connector_id": "custom",
            "driver_id": "planned",
            "credential_fields": (),
        },
    )


def test_registry_distinguishes_factory_recipes_from_connector_entries_with_same_name() -> None:
    registry = build_generic_registry()
    connector = GenericBearerDriver(
        connector_id="bearer",
        environments={"production": "https://erp.example.test"},
    )

    with pytest.raises(UnknownDriverError, match="^connector_driver_not_found$"):
        registry.get("bearer")
    registry.register(connector)

    summaries = registry.summaries()
    connector_summary = next(item for item in summaries if item["entry_type"] == "connector")
    factory_summary = next(
        item
        for item in summaries
        if item["entry_type"] == "factory" and item["driver_id"] == "bearer"
    )

    assert connector_summary == {
        "entry_type": "connector",
        "connector_id": "bearer",
        "driver_id": "bearer",
        "credential_fields": ("token",),
    }
    assert factory_summary["credential_fields"] == ("token",)
    assert "connector_id" not in factory_summary
    assert registry.get("bearer") is connector
    assert registry.get_factory("bearer").driver_id == "bearer"
    assert summaries == registry.summaries()


@pytest.mark.asyncio
async def test_probe_and_auth_exception_never_include_credential_values() -> None:
    secret = "unmistakable-secret-token"
    driver = GenericBearerDriver(
        connector_id="custom",
        environments={"production": "https://erp.example.test/v1"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return httpx.Response(401, json={"message": f"token={secret}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await driver.validate_credentials(
            environment="production",
            credentials={"token": secret},
            client=client,
        )

        with pytest.raises(Exception) as raised:
            await driver.prepare_auth(
                environment="production",
                credentials={},
                client=client,
            )

    assert probe.status == "failed"
    assert secret not in json.dumps(probe.details)
    assert secret not in str(raised.value)
    assert isinstance(probe.details, Mapping)
