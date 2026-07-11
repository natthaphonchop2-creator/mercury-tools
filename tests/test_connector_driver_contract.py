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
