from __future__ import annotations

from typing import Any

import httpx

from mercury_tools.config import Settings
from mercury_tools.product import ConnectRequest, create_client_token


def make_client_token() -> str:
    return create_client_token(
        Settings(
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role",
            openai_api_key="",
            connect_signing_secret="signing-secret",
        ),
        ConnectRequest(
            email="owner@example.com",
            company="Demo Co",
            host_app="codex",
            invite_code="invite",
        ),
    )


def configure_product_env(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")


def assert_values_absent(payload: dict[str, Any], values: list[str]) -> None:
    serialized = str(payload)
    for value in values:
        assert value not in serialized


def assert_key_fragments_absent(payload: dict[str, Any], fragments: list[str]) -> None:
    keys: list[str] = []

    def collect_keys(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                keys.append(str(key))
                collect_keys(item)
        elif isinstance(value, list | tuple):
            for item in value:
                collect_keys(item)

    collect_keys(payload)
    for fragment in fragments:
        assert all(fragment not in key for key in keys)


def test_list_connectors_exposes_setup_targets_without_secrets() -> None:
    from mercury_tools.mcp.server import list_connectors

    payload = list_connectors()

    assert payload["status"] == "ok"
    assert {item["connector_id"] for item in payload["connectors"]} >= {
        "flowaccount",
        "peak",
        "express",
    }
    assert "super-secret" not in str(payload)
    assert "client_secret_value" not in str(payload)


def test_start_connector_setup_requires_valid_connector(monkeypatch) -> None:
    from mercury_tools.mcp.server import start_connector_setup

    configure_product_env(monkeypatch)

    invalid = start_connector_setup(
        client_token=make_client_token(),
        connector_id="unknown",
        environment="production",
    )

    assert invalid["status"] == "error"
    assert "Unknown connector" in invalid["message"]


def test_start_connector_setup_returns_redacted_profile(monkeypatch) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    class FakeStore:
        def start_connector_setup(
            self,
            *,
            token_payload: dict[str, Any],
            connector_id: str,
            environment: str,
            company_name: str | None = None,
        ) -> dict[str, Any]:
            assert token_payload["sub"] == "owner@example.com"
            return {
                "connector_id": connector_id,
                "environment": environment,
                "company_name": company_name,
                "status": "requires_credentials",
                "metadata": {"client_secret": "super-secret-value"},
            }

    monkeypatch.setattr(server, "_product_store", lambda settings: FakeStore())

    payload = server.start_connector_setup(
        client_token=make_client_token(),
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Co Books",
    )

    assert payload["status"] == "ok"
    assert payload["profile"]["connector_id"] == "flowaccount"
    assert payload["profile"]["company_name"] == "Demo Co Books"
    assert payload["profile"]["metadata"]["client_secret"] == "[REDACTED]"
    assert "super-secret-value" not in str(payload)


def test_submit_connector_credentials_reports_missing_required_fields(monkeypatch) -> None:
    from mercury_tools.mcp.server import submit_connector_credentials

    configure_product_env(monkeypatch)

    payload = submit_connector_credentials(
        client_token=make_client_token(),
        connector_id="flowaccount",
        environment="production",
        credentials={"client_id": "demo-client-id"},
    )

    assert payload["status"] == "awaiting_credentials"
    assert payload["missing_fields"] == ["client_secret"]
    assert "Required connector credentials are missing" in payload["message"]


def test_submit_connector_credentials_validates_token_before_missing_fields(monkeypatch) -> None:
    from mercury_tools.mcp.server import submit_connector_credentials

    configure_product_env(monkeypatch)

    payload = submit_connector_credentials(
        client_token="not-a-token",
        connector_id="flowaccount",
        environment="production",
        credentials={"client_id": "demo-client-id"},
    )

    assert payload["status"] == "error"
    assert "client token" in payload["message"]
    assert "missing_fields" not in payload
    assert "credential_fields" not in payload


def test_submit_connector_credentials_stores_only_field_names(monkeypatch) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    class FakeStore:
        def set_connector_credentials(
            self,
            *,
            token_payload: dict[str, Any],
            connector_id: str,
            environment: str,
            credentials: dict[str, str],
        ) -> dict[str, Any]:
            assert token_payload["sub"] == "owner@example.com"
            assert credentials == {
                "client_id": "demo-client-id",
                "client_secret": "super-secret-value",
            }
            return {
                "status": "credentials_configured",
                "connector_id": connector_id,
                "environment": environment,
                "credential_fields": sorted(credentials),
                "credential_fingerprints": {"client_secret": "abc123"},
                "ciphertext": "encrypted-secret-derived-value",
            }

    monkeypatch.setattr(server, "_product_store", lambda settings: FakeStore())

    payload = server.submit_connector_credentials(
        client_token=make_client_token(),
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )

    assert payload["status"] == "credentials_received"
    assert payload["connector_id"] == "flowaccount"
    assert payload["environment"] == "production"
    assert payload["credential_fields"] == ["client_id", "client_secret"]
    assert payload["setup_state"] == "credentials_configured"
    assert "result" not in payload
    assert "credential_fingerprints" not in str(payload)
    assert "abc123" not in str(payload)
    assert "ciphertext" not in str(payload)
    assert "encrypted-secret-derived-value" not in str(payload)
    assert "super-secret-value" not in str(payload)
    assert "demo-client-id" not in str(payload)


def test_validate_connector_connection_validates_flowaccount_read_only(monkeypatch) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    def fake_post(url, data=None, timeout=60):
        calls.append(("POST", url))
        assert data == {
            "grant_type": "client_credentials",
            "scope": "flowaccount-api",
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        }
        assert timeout == 60
        return FakeResponse(200, {"access_token": "secret-token", "token_type": "Bearer"})

    def fake_get(url, headers=None, timeout=60):
        calls.append(("GET", url))
        assert headers == {"Authorization": "Bearer secret-token"}
        assert timeout == 60
        return FakeResponse(200, {"companyName": "Demo Books"})

    class FakeStore:
        def __init__(self):
            self.profile_payloads: list[dict[str, Any]] = []

        def set_connector_profile(
            self,
            *,
            token_payload: dict[str, Any],
            connector_id: str,
            environment: str,
            company_name: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            assert token_payload["sub"] == "owner@example.com"
            payload = {
                "connector_id": connector_id,
                "environment": environment,
                "company_name": company_name,
                "metadata": metadata or {},
            }
            self.profile_payloads.append(payload)
            return payload

    store = FakeStore()
    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr(server, "_product_store", lambda settings: store)
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.validate_connector_connection(
        client_token=make_client_token(),
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )

    assert payload["status"] == "ready"
    assert payload["connector_id"] == "flowaccount"
    assert payload["environment"] == "production"
    assert payload["company_name"] == "Demo Books"
    assert payload["enabled_capabilities"] == [
        "company.info.read",
        "contacts.list",
        "products.list",
        "documents.invoice.list",
        "documents.invoice.get",
        "tax.vat_summary.read",
    ]
    assert payload["validation"] == {"token_status": 200, "company_info_status": 200}
    assert store.profile_payloads == [
        {
            "connector_id": "flowaccount",
            "environment": "production",
            "company_name": "Demo Books",
            "metadata": {
                "setup_state": "ready",
                "enabled_capabilities": payload["enabled_capabilities"],
                "validation": {"token_status": 200, "company_info_status": 200},
            },
        }
    ]
    assert calls == [
        ("POST", "https://openapi.flowaccount.com/token"),
        ("GET", "https://openapi.flowaccount.com/v1/company/info"),
    ]
    assert "super-secret-value" not in str(payload)
    assert "demo-client-id" not in str(payload)
    assert "secret-token" not in str(payload)


def test_validate_connector_connection_token_failure_sanitizes_provider_echoes(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any]):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, data=None, timeout=60):
        return FakeResponse(
            401,
            {
                "error": "invalid_client",
                "client_id": "demo-client-id",
                "client_secret": "super-secret-value",
                "detail": (
                    "FlowAccount echoed demo-client-id and super-secret-value "
                    "with echoed-access-token"
                ),
                "access_token": "echoed-access-token",
                "credential_fingerprints": {"client_secret": "fingerprint-leak"},
                "ciphertext": "ciphertext-leak",
            },
        )

    def fake_get(url, headers=None, timeout=60):
        raise AssertionError("company info should not be called after token failure")

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.validate_connector_connection(
        client_token=make_client_token(),
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )

    assert payload["status"] == "validation_failed"
    provider_response = payload["provider_response"]
    assert_key_fragments_absent(
        provider_response,
        [
            "client_id",
            "client_secret",
            "access_token",
            "credential_fingerprints",
            "ciphertext",
        ],
    )
    assert_values_absent(
        payload,
        [
            "demo-client-id",
            "super-secret-value",
            "echoed-access-token",
            "fingerprint-leak",
            "ciphertext-leak",
        ],
    )


def test_validate_connector_connection_company_info_failure_sanitizes_provider_echoes(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any]):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, data=None, timeout=60):
        return FakeResponse(
            200,
            {"access_token": "secret-token", "token_type": "Bearer"},
        )

    def fake_get(url, headers=None, timeout=60):
        assert headers == {"Authorization": "Bearer secret-token"}
        return FakeResponse(
            403,
            {
                "error": "forbidden",
                "client_id": "demo-client-id",
                "client_secret": "super-secret-value",
                "detail": (
                    "Company info echoed demo-client-id, super-secret-value, "
                    "and secret-token"
                ),
                "credential_fingerprints": {"client_secret": "fingerprint-leak"},
                "ciphertext": "ciphertext-leak",
            },
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.validate_connector_connection(
        client_token=make_client_token(),
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )

    assert payload["status"] == "validation_failed"
    provider_response = payload["provider_response"]
    assert_key_fragments_absent(
        provider_response,
        [
            "client_id",
            "client_secret",
            "credential_fingerprints",
            "ciphertext",
        ],
    )
    assert_values_absent(
        payload,
        [
            "demo-client-id",
            "super-secret-value",
            "secret-token",
            "fingerprint-leak",
            "ciphertext-leak",
        ],
    )


def test_validate_connector_connection_http_error_is_sanitized(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    def fake_post(url, data=None, timeout=60):
        raise httpx.ReadError(
            "read failed with demo-client-id and super-secret-value"
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.validate_connector_connection(
        client_token=make_client_token(),
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )

    assert payload["status"] == "validation_failed"
    assert payload["error_type"] == "ReadError"
    assert "Traceback" not in str(payload)
    assert_values_absent(payload, ["demo-client-id", "super-secret-value"])


def test_run_workspace_flow_requires_ready_connector(monkeypatch) -> None:
    from mercury_tools.mcp import server

    class FakeStore:
        def dashboard(self, token_payload):
            return {
                "workspace": {"name": "Demo Co"},
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "credentials_configured",
                        "metadata": {"setup_state": "credentials_received"},
                    }
                ],
            }

        def get_flow(self, token_payload, flow_id):
            raise AssertionError("blocked connector setup should not load the flow")

    configure_product_env(monkeypatch)
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_workspace_flow_tool(
        client_token=make_client_token(),
        flow_id="workspace-revenue",
        dry_run=False,
    )

    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]
