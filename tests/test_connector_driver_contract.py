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
    ConnectorResult,
    PreparedFile,
)
from mercury_tools.drivers.generic import GenericBearerDriver
from mercury_tools.drivers.models import CredentialField
from mercury_tools.drivers.registry import DriverRegistry, DuplicateDriverError, UnknownDriverError


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
    assert json.loads(json.dumps(probe.details)) == {"meta": {"items": [{"status": "ok"}]}}
    assert json.loads(json.dumps(result.data)) == {"meta": {"items": [{"status": "ok"}]}}
    with pytest.raises(TypeError, match="^immutable_mapping$"):
        probe.details["meta"]["items"][0]["status"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError, match="^immutable_mapping$"):
        result.data["meta"]["items"][0]["status"] = "changed"  # type: ignore[index]


def test_registry_rejects_duplicates_with_stable_error_and_lists_immutable_summaries() -> None:
    registry = DriverRegistry()
    registry.register(GenericBearerDriver(connector_id="zeta", environments={}))
    registry.register(GenericBearerDriver(connector_id="alpha", environments={}))

    with pytest.raises(DuplicateDriverError, match="^duplicate_connector_driver$"):
        registry.register(GenericBearerDriver(connector_id="alpha", environments={}))
    with pytest.raises(UnknownDriverError, match="^connector_driver_not_found$"):
        registry.get("missing")

    summaries = registry.summaries()

    assert [item["connector_id"] for item in summaries] == ["alpha", "zeta"]
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
            "connector_id": "custom",
            "driver_id": "schema_only",
            "credential_fields": ("token",),
        },
    )


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
