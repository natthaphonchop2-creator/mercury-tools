from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from mercury_tools.config import Settings
from mercury_tools.providers.manifest import (
    ProviderManifestError,
    TimeoutClass,
    TimeoutPolicy,
    load_provider_manifest,
    resolve_provider_resource,
)
from mercury_tools.providers.registry import (
    ProviderDriverRegistry,
    ProviderRegistryError,
    build_provider_registry,
)

ROOT = Path(__file__).resolve().parents[1]
FLOWACCOUNT_MANIFEST = ROOT / "catalog/global/flowaccount/driver.json"
PEAK_MANIFEST = ROOT / "catalog/global/peak/driver.json"


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "supabase_url": "",
        "supabase_service_role_key": "",
        "openai_api_key": "",
        "flowaccount_mcp_sandbox_url": "https://flowaccount-sandbox.example/mcp",
        "flowaccount_mcp_production_url": "https://flowaccount.example/mcp",
        "peak_mcp_uat_url": "https://peak-uat.example/mcp",
        "peak_mcp_production_url": "https://peak.example/mcp",
    }
    values.update(updates)
    return Settings(**values)


def _write_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "driver.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _flowaccount_payload() -> dict[str, object]:
    return json.loads(FLOWACCOUNT_MANIFEST.read_text(encoding="utf-8"))


def test_seed_manifests_are_closed_secretless_transport_descriptions() -> None:
    flowaccount = load_provider_manifest(FLOWACCOUNT_MANIFEST)
    peak = load_provider_manifest(PEAK_MANIFEST)

    assert flowaccount.model_dump(mode="json") == {
        "manifest_version": "1",
        "provider": "flowaccount",
        "environments": {
            "production": "flowaccount_mcp_production_url",
            "sandbox": "flowaccount_mcp_sandbox_url",
        },
        "transport": "streamable_http",
        "protocol_version": "2025-11-25",
        "auth_adapter": "oauth2_pkce",
        "allowed_permissions": [
            "documents.create",
            "documents.read",
            "profile.read",
        ],
        "timeout_classes": {
            "discovery": {"connect_seconds": 5, "operation_seconds": 30},
            "read": {"connect_seconds": 5, "operation_seconds": 30},
            "create": {"connect_seconds": 5, "operation_seconds": 60},
        },
        "discovery_mappings": [
            {
                "provider_tool": "get_provider_profile",
                "normalized_capability": "provider_profile.get",
                "timeout_class": "read",
            },
            {
                "provider_tool": "list_invoices",
                "normalized_capability": "documents.invoice.list",
                "timeout_class": "read",
            },
            {
                "provider_tool": "get_invoice",
                "normalized_capability": "documents.invoice.get",
                "timeout_class": "read",
            },
            {
                "provider_tool": "create_invoice",
                "normalized_capability": "documents.invoice.create",
                "timeout_class": "create",
            },
        ],
    }
    assert peak.model_dump(mode="json") == {
        **flowaccount.model_dump(mode="json"),
        "provider": "peak",
        "environments": {
            "production": "peak_mcp_production_url",
            "uat": "peak_mcp_uat_url",
        },
        "auth_adapter": "provider_credentials",
    }

    for path in (FLOWACCOUNT_MANIFEST, PEAK_MANIFEST):
        serialized = path.read_text(encoding="utf-8").casefold()
        assert "https://" not in serialized
        assert "http://" not in serialized
        assert "authorization" not in serialized
        assert "access_token" not in serialized
        assert "refresh_token" not in serialized
        assert "client_secret" not in serialized
        assert "session_id" not in serialized


@pytest.mark.parametrize(
    ("mutator", "private_sentinel"),
    [
        (
            lambda payload: payload.update({"unexpected": "PRIVATE_MANIFEST_SENTINEL"}),
            "PRIVATE_MANIFEST_SENTINEL",
        ),
        (
            lambda payload: payload.update({"endpoint": "https://model-supplied.example/mcp"}),
            "model-supplied.example",
        ),
        (
            lambda payload: payload["environments"].update(  # type: ignore[union-attr]
                {"sandbox": "http://provider.example/mcp"}
            ),
            "provider.example",
        ),
        (
            lambda payload: payload["environments"].update(  # type: ignore[union-attr]
                {"sandbox": "https://provider.example/mcp?workspace=private"}
            ),
            "workspace=private",
        ),
        (
            lambda payload: payload["environments"].update(  # type: ignore[union-attr]
                {"sandbox": "https://provider.example/mcp#private"}
            ),
            "#private",
        ),
        (
            lambda payload: payload.update({"transport": "custom_http"}),
            "custom_http",
        ),
        (
            lambda payload: payload.update({"auth_adapter": "unreviewed_adapter"}),
            "unreviewed_adapter",
        ),
        (
            lambda payload: payload.update({"protocol_version": "2099-01-01"}),
            "2099-01-01",
        ),
        (
            lambda payload: payload["allowed_permissions"].append(  # type: ignore[union-attr]
                "Bearer [REDACTED]"
            ),
            "Bearer",
        ),
        (
            lambda payload: payload["discovery_mappings"][0].update(  # type: ignore[index,union-attr]
                {"headers": {"X-Private": "[REDACTED]"}}
            ),
            "X-Private",
        ),
    ],
)
def test_manifest_loader_rejects_untrusted_transport_and_secret_shapes(
    tmp_path: Path,
    mutator: object,
    private_sentinel: str,
) -> None:
    payload = deepcopy(_flowaccount_payload())
    assert callable(mutator)
    mutator(payload)

    with pytest.raises(ProviderManifestError, match="^provider_manifest_invalid$") as error:
        load_provider_manifest(_write_manifest(tmp_path, payload))

    assert private_sentinel not in str(error.value)
    assert private_sentinel not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_manifest_requires_exact_provider_environment_configuration_bindings(
    tmp_path: Path,
) -> None:
    payload = _flowaccount_payload()
    payload["environments"] = {
        "sandbox": "flowaccount_mcp_production_url",
        "production": "flowaccount_mcp_sandbox_url",
    }

    with pytest.raises(ProviderManifestError, match="^provider_manifest_invalid$"):
        load_provider_manifest(_write_manifest(tmp_path, payload))


def test_resource_is_resolved_only_from_settings_and_bound_to_uri_hash() -> None:
    manifest = load_provider_manifest(FLOWACCOUNT_MANIFEST)
    resource = resolve_provider_resource(
        settings=_settings(),
        manifest=manifest,
        environment="sandbox",
    )

    assert resource.provider.value == "flowaccount"
    assert resource.environment == "sandbox"
    assert resource.uri == "https://flowaccount-sandbox.example/mcp"
    assert (
        resource.uri_sha256
        == hashlib.sha256(b"https://flowaccount-sandbox.example/mcp").hexdigest()
    )
    assert "flowaccount-sandbox.example" not in repr(resource)

    changed = resolve_provider_resource(
        settings=_settings(flowaccount_mcp_sandbox_url="https://flowaccount-staged.example/mcp"),
        manifest=manifest,
        environment="sandbox",
    )
    assert changed.uri_sha256 != resource.uri_sha256


def test_resource_resolution_rejects_caller_endpoint_and_invalid_server_config() -> None:
    manifest = load_provider_manifest(FLOWACCOUNT_MANIFEST)

    with pytest.raises(TypeError) as endpoint_error:
        resolve_provider_resource(  # type: ignore[call-arg]
            settings=_settings(),
            manifest=manifest,
            environment="sandbox",
            endpoint="https://model-supplied.example/mcp",
        )
    assert "model-supplied.example" not in str(endpoint_error.value)

    with pytest.raises(ProviderManifestError, match="^provider_resource_unavailable$") as error:
        resolve_provider_resource(
            settings=_settings(flowaccount_mcp_sandbox_url="https://provider.example/mcp?private"),
            manifest=manifest,
            environment="sandbox",
        )
    assert "provider.example" not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_manifest_nested_collections_are_deeply_immutable() -> None:
    manifest = load_provider_manifest(FLOWACCOUNT_MANIFEST)

    assert isinstance(manifest.environments, Mapping)
    assert not isinstance(manifest.environments, dict)
    assert isinstance(manifest.timeout_classes, Mapping)
    assert not isinstance(manifest.timeout_classes, dict)

    with pytest.raises(TypeError):
        manifest.environments["sandbox"] = "flowaccount_mcp_production_url"  # type: ignore[index]
    with pytest.raises(AttributeError):
        manifest.environments.update(  # type: ignore[attr-defined]
            {"sandbox": "flowaccount_mcp_production_url"}
        )
    with pytest.raises(TypeError):
        manifest.timeout_classes[TimeoutClass.CREATE] = TimeoutPolicy(
            connect_seconds=5,
            operation_seconds=60,
        )
    with pytest.raises(AttributeError):
        manifest.timeout_classes.clear()  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        manifest.allowed_permissions.append("injected")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        manifest.discovery_mappings.append("injected")  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        manifest.timeout_classes[TimeoutClass.CREATE].operation_seconds = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        manifest.discovery_mappings[0].provider_tool = "injected"  # type: ignore[misc]


def test_registry_loads_only_known_provider_manifests_from_server_catalog() -> None:
    registry = build_provider_registry(
        settings=_settings(),
        manifest_root=ROOT / "catalog/global",
    )

    assert isinstance(registry, ProviderDriverRegistry)
    assert registry.providers() == ("flowaccount", "peak")
    assert registry.get("flowaccount").provider.value == "flowaccount"
    with pytest.raises(ProviderRegistryError, match="^provider_driver_not_found$"):
        registry.get("unknown")
