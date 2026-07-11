import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mercury_tools.catalog.models import CatalogSource


def _catalog_module():
    return importlib.import_module("mercury_tools.db.catalog")


class FakeResponse:
    def __init__(self, status_code: int = 201, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = "" if body is None else json.dumps(body)

    def json(self) -> Any:
        return self._body


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="test-service-role-key",
        supabase_configured=True,
    )


def test_publish_same_version_twice_is_idempotent_and_never_updates_versions(
    monkeypatch: pytest.MonkeyPatch,
    catalog_source,
    catalog_action,
) -> None:
    catalog = _catalog_module()
    calls: list[dict[str, Any]] = []
    inserted_versions: set[tuple[str, str]] = set()

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/erp_action_versions"):
            rows = []
            for row in kwargs["json"]:
                identity = (row["action_id"], row["version_id"])
                if identity not in inserted_versions:
                    inserted_versions.add(identity)
                    rows.append(row)
            return FakeResponse(body=rows)
        return FakeResponse(body=kwargs.get("json", []))

    monkeypatch.setattr(catalog.httpx, "request", request)
    store = catalog.SupabaseCatalogStore(_settings())

    first = store.publish(catalog_source, [catalog_action])
    second = store.publish(catalog_source, [catalog_action])

    assert first.created_versions == 1
    assert first.activated_actions == 1
    assert second.created_versions == 0
    assert second.activated_actions == 1
    version_calls = [call for call in calls if call["url"].endswith("/erp_action_versions")]
    assert len(version_calls) == 2
    assert all(call["method"] == "POST" for call in version_calls)
    assert all(call["params"] == {"on_conflict": "action_id,version_id"} for call in version_calls)
    assert all(
        "resolution=ignore-duplicates" in call["headers"]["Prefer"]
        for call in version_calls
    )
    assert not any(call["method"] in {"PATCH", "PUT", "DELETE"} for call in version_calls)


def test_publish_rejects_unsafe_source_before_any_network_call(
    monkeypatch: pytest.MonkeyPatch,
    catalog_source,
    catalog_action,
) -> None:
    catalog = _catalog_module()
    calls: list[dict[str, Any]] = []
    unsafe_value = "synthetic-service-role-like-value"
    unsafe_source = catalog_source.model_copy(
        update={"driver_suggestion": {"client_secret": unsafe_value}}
    )

    monkeypatch.setattr(
        catalog.httpx,
        "request",
        lambda *args, **kwargs: calls.append({"args": args, **kwargs}),
    )

    with pytest.raises(ValueError, match="catalog_source_invalid") as raised:
        catalog.SupabaseCatalogStore(_settings()).publish(unsafe_source, [catalog_action])

    assert unsafe_value not in str(raised.value)
    assert calls == []


def test_list_active_actions_serializes_filters_deterministically_and_round_trips(
    monkeypatch: pytest.MonkeyPatch,
    catalog_action,
) -> None:
    catalog = _catalog_module()
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(
            body=[
                {
                    "action_id": catalog_action.action_id,
                    "active_version_id": catalog_action.version_id,
                    "erp_action_versions": {"definition": catalog_action.model_dump(mode="json")},
                }
            ]
        )

    monkeypatch.setattr(catalog.httpx, "request", request)

    actions = catalog.SupabaseCatalogStore(_settings()).list_active_actions(
        {"method": "POST", "connector_id": "flowaccount", "capability": "documents.invoice.create"}
    )

    assert actions == [catalog_action]
    assert calls[0]["method"] == "GET"
    assert list(calls[0]["params"]) == [
        "capability",
        "connector_id",
        "erp_action_versions.method",
        "select",
    ]
    assert calls[0]["params"]["capability"] == "eq.documents.invoice.create"
    assert calls[0]["params"]["connector_id"] == "eq.flowaccount"
    assert calls[0]["params"]["erp_action_versions.method"] == "eq.POST"
    assert "definition" in calls[0]["params"]["select"]


def test_list_active_actions_rejects_credential_bearing_filter_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog_module()
    calls: list[dict[str, Any]] = []
    secret = "Bearer synthetic-secret"
    monkeypatch.setattr(
        catalog.httpx,
        "request",
        lambda *args, **kwargs: calls.append({"args": args, **kwargs}),
    )

    with pytest.raises(ValueError, match="catalog_filter_invalid") as raised:
        catalog.SupabaseCatalogStore(_settings()).list_active_actions({"connector_id": secret})

    assert secret not in str(raised.value)
    assert calls == []


def test_publisher_discovers_artifacts_in_path_order_and_validates_canonical_models(
    tmp_path: Path,
    catalog_source,
    catalog_action,
) -> None:
    module_path = Path("scripts/publish_catalog.py")
    spec = importlib.util.spec_from_file_location("publish_catalog", module_path)
    assert spec and spec.loader
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    first = tmp_path / "a-flow"
    second = tmp_path / "z-flow"
    first.mkdir()
    second.mkdir()
    second_source = CatalogSource.from_document(
        uri="https://example.test/z.json",
        connector_id="flowaccount",
        document={"openapi": "3.0.0", "info": {"version": "2026-07"}},
        report={"status": "imported"},
    )
    for directory, source in ((second, second_source), (first, catalog_source)):
        (directory / "source.json").write_text(source.model_dump_json())
        (directory / "actions.json").write_text(
            json.dumps([catalog_action.model_dump(mode="json")])
        )

    discovered = publisher.discover_catalog(tmp_path)

    assert [source.source_id for source, _ in discovered] == [
        catalog_source.source_id,
        second_source.source_id,
    ]


def test_publisher_cli_and_workflow_use_secret_hygiene(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_path = Path("scripts/publish_catalog.py")
    spec = importlib.util.spec_from_file_location("publish_catalog", module_path)
    assert spec and spec.loader
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    assert publisher.main(["--path", str(tmp_path / "missing")]) != 0
    workflow = Path(".github/workflows/publish-catalog.yml").read_text()
    assert "SUPABASE_URL: ${{ secrets.SUPABASE_URL }}" in workflow
    assert "SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}" in workflow
    assert "uv run python scripts/publish_catalog.py --path catalog/global" in workflow
    assert "test-service-role-key" not in workflow
