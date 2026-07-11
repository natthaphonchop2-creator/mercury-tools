import inspect
import json

import httpx
import pytest

from mercury_tools.config import Settings
from mercury_tools.connectors.catalog import connector_by_id
from mercury_tools.connectors.setup import (
    CONNECTOR_SETUP_STATES,
    next_setup_state,
    required_missing_fields,
    resolve_setup_state,
    validate_connector_connection_healthcheck,
    validate_connector_connection_healthcheck_async,
)
from mercury_tools.db.product import SupabaseProductStore


def assert_values_absent(payload: dict, values: list[str]) -> None:
    serialized = str(payload)
    for value in values:
        assert value not in serialized


def assert_key_fragments_absent(payload: dict, fragments: list[str]) -> None:
    keys: list[str] = []

    def collect_keys(value) -> None:
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


def legacy_mock_transport(post=None, get=None) -> httpx.MockTransport:
    """Adapt existing setup assertions to the provider-driver mock transport seam."""

    def handler(request: httpx.Request) -> httpx.Response:
        callback = post if request.method == "POST" else get
        assert callback is not None
        parameter_names = inspect.signature(callback).parameters
        kwargs: dict[str, object] = {}
        if "data" in parameter_names:
            pairs = httpx.QueryParams(request.content.decode())
            kwargs["data"] = {key: pairs.get(key) for key in pairs}
        if "json" in parameter_names:
            kwargs["json"] = json.loads(request.content)
        if "headers" in parameter_names:
            kwargs["headers"] = request.headers
        if "timeout" in parameter_names:
            kwargs["timeout"] = 60
        response = callback(str(request.url), **kwargs)
        try:
            return httpx.Response(response.status_code, json=response.json())
        except ValueError:
            return httpx.Response(response.status_code, content=b"{")

    return httpx.MockTransport(handler)


def test_setup_states_are_ordered_and_explicit() -> None:
    assert CONNECTOR_SETUP_STATES == [
        "not_started",
        "program_selected",
        "environment_selected",
        "awaiting_credentials",
        "credentials_received",
        "validation_failed",
        "connected",
        "ready",
    ]


def test_required_missing_fields_uses_manifest() -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    assert required_missing_fields(manifest, {}) == ["client_id", "client_secret"]
    assert required_missing_fields(manifest, {"client_id": "abc"}) == ["client_secret"]
    assert required_missing_fields(
        manifest,
        {"client_id": "abc", "client_secret": "def"},
    ) == []


def test_validate_flowaccount_uses_token_and_company_info(monkeypatch) -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None
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
        assert data["grant_type"] == "client_credentials"
        assert data["scope"] == "flowaccount-api"
        assert data["client_id"] == "cid"
        assert data["client_secret"] == "csecret"
        assert timeout == 60
        return FakeResponse(200, {"access_token": "secret-token", "token_type": "Bearer"})

    def fake_get(url, headers=None, timeout=60):
        calls.append(("GET", url))
        assert headers["Authorization"] == "Bearer secret-token"
        assert timeout == 60
        return FakeResponse(200, {"companyName": "Demo Books"})

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials={"client_id": "cid", "client_secret": "csecret"},
        environment="production",
        transport=legacy_mock_transport(post=fake_post, get=fake_get),
    )

    assert result["status"] == "connected"
    assert result["company_name"] == "Demo Books"
    assert result["enabled_capabilities"] == manifest.capabilities
    assert "secret-token" not in str(result)
    assert "csecret" not in str(result)
    assert calls == [
        ("POST", "https://openapi.flowaccount.com/v1/token"),
        ("GET", "https://openapi.flowaccount.com/v1/company/info"),
    ]


def test_validate_flowaccount_uses_sandbox_token_and_company_info_urls(
    monkeypatch,
) -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None
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
        return FakeResponse(200, {"access_token": "secret-token", "token_type": "Bearer"})

    def fake_get(url, headers=None, timeout=60):
        calls.append(("GET", url))
        return FakeResponse(200, {"companyName": "Sandbox Books"})

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials={"client_id": "cid", "client_secret": "csecret"},
        environment="sandbox",
        transport=legacy_mock_transport(post=fake_post, get=fake_get),
    )

    assert result["status"] == "connected"
    assert result["company_name"] == "Sandbox Books"
    assert calls == [
        ("POST", "https://openapi.flowaccount.com/test/token"),
        ("GET", "https://openapi.flowaccount.com/test/company/info"),
    ]


def test_validate_peak_uses_hmac_client_token_and_user_read(monkeypatch) -> None:
    manifest = connector_by_id("peak")
    assert manifest is not None
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    credentials = {
        "connect_id": "peak-connect-id",
        "connect_key": "peak-connect-key",
        "application_code": "app-code",
        "user_token": "user-token-value",
    }

    def assert_peak_headers(headers: dict) -> None:
        assert headers["User-Token"] == "user-token-value"
        assert headers["Time-Stamp"].isdigit()
        assert len(headers["Time-Stamp"]) == 14
        assert len(headers["Time-Signature"]) == 40

    def fake_post(url, headers=None, json=None, timeout=60):
        calls.append(("POST", url))
        assert url == "https://peakengineapidev.azurewebsites.net/api/v1/clienttoken"
        assert json == {
            "PeakClientToken": {
                "connectId": "peak-connect-id",
                "password": "peak-connect-key",
            }
        }
        assert_peak_headers(headers)
        assert headers["Client-Token"] == ""
        assert timeout == 60
        return FakeResponse(
            200,
            {
                "PeakClientToken": {
                    "token": "peak-client-token",
                    "resCode": "200",
                    "resDesc": "Token Authorized",
                }
            },
        )

    def fake_get(url, headers=None, timeout=60):
        calls.append(("GET", url))
        assert url == "https://peakengineapidev.azurewebsites.net/api/v1/user"
        assert_peak_headers(headers)
        assert headers["Client-Token"] == "peak-client-token"
        assert timeout == 60
        return FakeResponse(
            200,
            {
                "PeakUser": {
                    "resCode": "200",
                    "resDesc": "PeakUser have Completed",
                    "package": "ProPlus",
                    "isUserTokenEnable": True,
                }
            },
        )

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials=credentials,
        environment="uat",
        transport=legacy_mock_transport(post=fake_post, get=fake_get),
    )

    assert result["status"] == "connected"
    assert result["connector_id"] == "peak"
    assert result["enabled_capabilities"] == manifest.capabilities
    assert result["validation"] == {
        "clienttoken_status": 200,
        "user_status": 200,
        "user_res_code": "200",
    }
    assert_values_absent(
        result,
        [
            "peak-connect-id",
            "peak-connect-key",
            "app-code",
            "user-token-value",
            "peak-client-token",
        ],
    )
    assert calls == [
        ("POST", "https://peakengineapidev.azurewebsites.net/api/v1/clienttoken"),
        ("GET", "https://peakengineapidev.azurewebsites.net/api/v1/user"),
    ]


def test_validate_peak_token_failure_sanitizes_provider_echoes(monkeypatch) -> None:
    manifest = connector_by_id("peak")
    assert manifest is not None

    class FakeResponse:
        status_code = 200
        text = "provider echoed peak-connect-id and peak-connect-key"

        def json(self):
            return {
                "PeakClientToken": {
                    "resCode": "500",
                    "resDesc": (
                        "PEAK internal server error for peak-connect-id "
                        "and peak-connect-key"
                    ),
                    "user_token": "user-token-value",
                    "token": "",
                }
            }

    def fake_post(url, headers=None, json=None, timeout=60):
        return FakeResponse()

    def fake_get(url, headers=None, timeout=60):
        raise AssertionError("read endpoint must not run after token failure")

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials={
            "connect_id": "peak-connect-id",
            "connect_key": "peak-connect-key",
            "application_code": "app-code",
            "user_token": "user-token-value",
        },
        environment="uat",
        transport=legacy_mock_transport(post=fake_post, get=fake_get),
    )

    assert result["status"] == "validation_failed"
    assert result["message"] == "PEAK ClientToken request failed."
    assert_values_absent(
        result,
        [
            "peak-connect-id",
            "peak-connect-key",
            "app-code",
            "user-token-value",
        ],
    )
    assert_key_fragments_absent(result, ["user_token", "token"])


def test_validate_flowaccount_http_error_returns_sanitized_validation_failed(
    monkeypatch,
) -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    def fake_post(url, data=None, timeout=60):
        raise httpx.ConnectError(
            "network failed for client-id-leak and client-secret-leak"
        )

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials={
            "client_id": "client-id-leak",
            "client_secret": "client-secret-leak",
        },
        environment="production",
        transport=legacy_mock_transport(post=fake_post),
    )

    assert result["status"] == "validation_failed"
    assert result["error_type"] == "ConnectError"
    assert "Traceback" not in str(result)
    assert_values_absent(result, ["client-id-leak", "client-secret-leak"])


def test_validate_flowaccount_invalid_json_returns_sanitized_validation_failed(
    monkeypatch,
) -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    class FakeResponse:
        status_code = 200

        def json(self):
            raise ValueError(
                "invalid JSON for client-id-leak and client-secret-leak"
            )

    def fake_post(url, data=None, timeout=60):
        return FakeResponse()

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials={
            "client_id": "client-id-leak",
            "client_secret": "client-secret-leak",
        },
        environment="production",
        transport=legacy_mock_transport(post=fake_post),
    )

    assert result["status"] == "validation_failed"
    assert result["http_status"] == 200
    assert result["error_type"] == "JSONDecodeError"
    assert "Traceback" not in str(result)
    assert_values_absent(result, ["client-id-leak", "client-secret-leak"])


@pytest.mark.asyncio
async def test_async_healthcheck_preserves_compatibility_result_shape() -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "access-token"})
        return httpx.Response(200, json={"companyName": "Async Books"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await validate_connector_connection_healthcheck_async(
            manifest,
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            environment="production",
            client=client,
        )

    assert result == {
        "status": "connected",
        "connector_id": "flowaccount",
        "environment": "production",
        "company_name": "Async Books",
        "enabled_capabilities": manifest.capabilities,
        "validation": {"token_status": 200, "company_info_status": 200},
    }


@pytest.mark.asyncio
async def test_sync_healthcheck_in_active_loop_requires_async_path() -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    with pytest.raises(RuntimeError, match="^connector_healthcheck_async_required$"):
        validate_connector_connection_healthcheck(
            manifest,
            credentials={"client_id": "client-id", "client_secret": "client-secret"},
            environment="production",
            transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        )


def test_validate_flowaccount_token_failure_sanitizes_provider_response_keys(
    monkeypatch,
) -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    class FakeResponse:
        status_code = 401

        def json(self):
            return {
                "error": "invalid_client",
                "client_id": "demo-client-id",
                "client_secret": "super-secret-value",
                "access_token": "echoed-access-token",
                "demo-client-id": "invalid client",
                "prefix-super-secret-value-suffix": "invalid secret",
                "echoed-access-token": "invalid token",
                "ciphertext": "ciphertext-secret-value",
                "credential_fingerprints": {
                    "client_secret": "fingerprint-secret-value"
                },
                "ciphertext-secret-value": "invalid ciphertext",
                "fingerprint-secret-value": "invalid fingerprint",
            }

    def fake_post(url, data=None, timeout=60):
        return FakeResponse()

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
        environment="production",
        transport=legacy_mock_transport(post=fake_post),
    )

    assert result["status"] == "validation_failed"
    assert_key_fragments_absent(
        result,
        [
            "client_id",
            "client_secret",
            "access_token",
            "ciphertext",
            "credential_fingerprints",
            "demo-client-id",
            "super-secret-value",
            "echoed-access-token",
            "ciphertext-secret-value",
            "fingerprint-secret-value",
        ],
    )
    assert_values_absent(
        result,
        [
            "demo-client-id",
            "super-secret-value",
            "echoed-access-token",
            "ciphertext-secret-value",
            "fingerprint-secret-value",
        ],
    )


@pytest.mark.parametrize(
    "body_failure",
    [
        pytest.param({"status": False}, id="status-false"),
        pytest.param({"success": False}, id="success-false"),
        pytest.param({"code": 12}, id="nonzero-code"),
        pytest.param({"resCode": "600"}, id="nonzero-res-code"),
        pytest.param(
            {"error": "provider echoed demo-client-id and super-secret-value"},
            id="truthy-error",
        ),
    ],
)
def test_validate_flowaccount_company_info_body_failure_returns_sanitized_validation_failed(
    monkeypatch,
    body_failure: dict,
) -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
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
        return FakeResponse(
            200,
            {
                **body_failure,
                "companyName": "Demo Books",
                "client_id": "demo-client-id",
                "client_secret": "super-secret-value",
                "detail": "body echoed demo-client-id super-secret-value secret-token",
                "access_token": "secret-token",
            },
        )

    result = validate_connector_connection_healthcheck(
        manifest,
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
        environment="production",
        transport=legacy_mock_transport(post=fake_post, get=fake_get),
    )

    assert result["status"] == "validation_failed"
    assert result["http_status"] == 200
    assert result["message"].startswith("Company info request failed.")
    assert "provider_response" not in result
    assert_values_absent(
        result,
        ["demo-client-id", "super-secret-value", "secret-token"],
    )


def test_next_setup_state_does_not_skip_credentials() -> None:
    assert (
        next_setup_state(has_environment=False, missing_fields=["client_id"])
        == "program_selected"
    )
    assert (
        next_setup_state(has_environment=True, missing_fields=["client_id"])
        == "awaiting_credentials"
    )
    assert next_setup_state(has_environment=True, missing_fields=[]) == "credentials_received"


def test_resolve_setup_state_covers_declared_states() -> None:
    assert (
        resolve_setup_state(
            has_program=False,
            has_environment=False,
            missing_fields=["client_id"],
        )
        == "not_started"
    )
    assert (
        resolve_setup_state(
            has_program=True,
            has_environment=False,
            missing_fields=["client_id"],
        )
        == "program_selected"
    )
    assert (
        resolve_setup_state(
            has_program=True,
            has_environment=True,
            missing_fields=[],
        )
        == "environment_selected"
    )
    assert (
        resolve_setup_state(
            has_program=True,
            has_environment=True,
            missing_fields=["client_id"],
        )
        == "awaiting_credentials"
    )
    assert (
        resolve_setup_state(
            has_program=True,
            has_environment=True,
            missing_fields=[],
            credentials_received=True,
        )
        == "credentials_received"
    )
    assert (
        resolve_setup_state(
            has_program=True,
            has_environment=True,
            missing_fields=[],
            credentials_received=True,
            validation_status="failed",
        )
        == "validation_failed"
    )
    assert (
        resolve_setup_state(
            has_program=True,
            has_environment=True,
            missing_fields=[],
            credentials_received=True,
            validation_status="valid",
        )
        == "connected"
    )
    assert (
        resolve_setup_state(
            has_program=True,
            has_environment=True,
            missing_fields=[],
            credentials_received=True,
            validation_status="valid",
            validated_capability_count=1,
        )
        == "ready"
    )


class StoreForSetup(SupabaseProductStore):
    def __init__(self):
        super().__init__(
            Settings(
                supabase_url="https://example.supabase.co",
                supabase_service_role_key="service-role",
                openai_api_key="",
                connect_signing_secret="signing-secret",
            )
        )
        self.rows: list[dict] = []
        self.profiles: dict[tuple[str, str, str], dict] = {}
        self.profile_payloads: list[dict] = []
        self.profile_patches: list[dict] = []
        self.audit_events: list[dict] = []

    def _request(self, method: str, path: str, **kwargs):
        if path == "mercury_client_tokens" and method == "GET":
            return [
                {
                    "id": "token-1",
                    "status": "active",
                    "workspace_id": "ws-1",
                    "member_id": "member-1",
                    "host_app": "codex",
                    "expires_at": "2026-07-09T00:00:00+00:00",
                }
            ]
        if path == "mercury_workspaces" and method == "GET":
            return [
                {
                    "id": "ws-1",
                    "workspace_key": "workspace-demo",
                    "name": "Demo Co",
                    "plan": "invite-preview",
                    "status": "active",
                    "metadata": {},
                    "created_at": "2026-07-09T00:00:00+00:00",
                    "updated_at": "2026-07-09T00:00:00+00:00",
                }
            ]
        if path == "mercury_workspace_members" and method == "GET":
            return [
                {
                    "id": "member-1",
                    "email": "owner@example.com",
                    "role": "owner",
                    "host_app": "codex",
                    "status": "active",
                    "created_at": "2026-07-09T00:00:00+00:00",
                    "last_seen_at": "2026-07-09T00:00:00+00:00",
                }
            ]
        if path == "mercury_connector_profiles" and method == "POST":
            payload = kwargs["json"][0]
            self.profile_payloads.append(payload)
            key = (
                payload["workspace_id"],
                payload["connector_id"],
                payload["environment"],
            )
            existing = self.profiles.get(key)
            row = {
                **(existing or {}),
                **payload,
                "id": (existing or {}).get("id") or "profile-1",
                "created_at": (existing or {}).get("created_at")
                or "2026-07-09T00:00:00+00:00",
                "updated_at": "2026-07-09T00:00:00+00:00",
            }
            self.profiles[key] = row
            self.rows.append(row)
            return [row]
        if path == "mercury_connector_profiles" and method == "GET":
            params = kwargs.get("params") or {}
            workspace_id = str(params.get("workspace_id") or "").removeprefix("eq.")
            connector_id = str(params.get("connector_id") or "").removeprefix("eq.")
            environment = str(params.get("environment") or "").removeprefix("eq.")
            return [
                profile
                for key, profile in self.profiles.items()
                if key == (workspace_id, connector_id, environment)
            ]
        if path == "mercury_connector_profiles" and method == "PATCH":
            params = kwargs.get("params") or {}
            profile_id = str(params.get("id") or "").removeprefix("eq.")
            patch = kwargs["json"]
            self.profile_patches.append(patch)
            for key, profile in self.profiles.items():
                if profile["id"] != profile_id:
                    continue
                row = {**profile, **patch}
                self.profiles[key] = row
                self.rows.append(row)
                return [row]
            raise RuntimeError(f"unknown connector profile id {profile_id}")
        if path == "mercury_product_events" and method == "POST":
            row = {
                **kwargs["json"][0],
                "id": f"event-{len(self.rows) + 1}",
                "created_at": "2026-07-09T00:00:00+00:00",
            }
            self.rows.append(row)
            return [row]
        if path == "mcp_audit_events" and method == "POST":
            row = {
                **kwargs["json"][0],
                "id": f"audit-{len(self.audit_events) + 1}",
                "created_at": "2026-07-09T00:00:00+00:00",
            }
            self.audit_events.append(row)
            return [row]
        if path == "mcp_audit_events" and method == "GET":
            return list(self.audit_events)
        raise RuntimeError(f"unexpected request {method} {path}")


def test_start_connector_setup_stores_setup_metadata() -> None:
    store = StoreForSetup()
    profile = store.start_connector_setup(
        token_payload={
            "sub": "owner@example.com",
            "company": "Demo Co",
            "host_app": "codex",
            "iat": 0,
            "exp": 99999,
            "jti": "token-jti",
        },
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Co Books",
    )
    assert profile["connector_id"] == "flowaccount"
    assert profile["environment"] == "production"
    assert profile["status"] == "requires_credentials"
    assert profile["metadata"]["setup_state"] == "awaiting_credentials"
    assert profile["metadata"]["required_secret_fields"] == ["client_id", "client_secret"]
    assert profile["metadata"]["preset"]["grant_type"] == "client_credentials"
    assert profile["metadata"]["preset"]["api_base_url"] == (
        "https://openapi.flowaccount.com/v1"
    )
    assert profile["metadata"]["preset"]["token_url"] == (
        "https://openapi.flowaccount.com/v1/token"
    )
    assert profile["metadata"]["capabilities"] == connector_by_id("flowaccount").capabilities
    assert "documents.invoice.create" in profile["metadata"]["capabilities"]
    assert "documents.expense.create" in profile["metadata"]["capabilities"]


def test_start_connector_setup_stores_environment_specific_preset() -> None:
    store = StoreForSetup()
    profile = store.start_connector_setup(
        token_payload={
            "sub": "owner@example.com",
            "company": "Demo Co",
            "host_app": "codex",
            "iat": 0,
            "exp": 99999,
            "jti": "token-jti",
        },
        connector_id="flowaccount",
        environment="sandbox",
    )

    assert profile["metadata"]["preset"]["api_base_url"] == (
        "https://openapi.flowaccount.com/test"
    )
    assert profile["metadata"]["preset"]["token_url"] == (
        "https://openapi.flowaccount.com/test/token"
    )


def test_start_connector_setup_supports_custom_erp_setup_target() -> None:
    store = StoreForSetup()
    profile = store.start_connector_setup(
        token_payload={
            "sub": "owner@example.com",
            "company": "Demo Co",
            "host_app": "codex",
            "iat": 0,
            "exp": 99999,
            "jti": "token-jti",
        },
        connector_id="custom",
        environment="gateway",
        company_name="Demo Co ERP",
    )

    assert profile["connector_id"] == "custom"
    assert profile["environment"] == "gateway"
    assert profile["display_name"] == "Custom ERP"
    assert profile["company_name"] == "Demo Co ERP"
    assert profile["status"] == "requires_credentials"
    assert profile["metadata"]["setup_state"] == "awaiting_credentials"
    assert profile["metadata"]["required_secret_fields"] == ["base_url", "api_key"]
    assert profile["metadata"]["capabilities"] == []


def test_ready_connector_profile_metadata_sets_connected_status() -> None:
    store = StoreForSetup()
    profile = store.set_connector_profile(
        token_payload={
            "sub": "owner@example.com",
            "company": "Demo Co",
            "host_app": "codex",
            "iat": 0,
            "exp": 99999,
            "jti": "token-jti",
        },
        connector_id="flowaccount",
        environment="production",
        metadata={
            "setup_state": "ready",
            "enabled_capabilities": ["company.info.read"],
        },
    )

    assert profile["status"] == "connected"
    assert store.profile_payloads[-1]["status"] == "connected"


def test_product_table_credentials_store_server_vault_on_profile_not_audit() -> None:
    from mercury_tools.db.product import public_connector_profile

    store = StoreForSetup()
    token_payload = {
        "sub": "owner@example.com",
        "company": "Demo Co",
        "host_app": "codex",
        "iat": 0,
        "exp": 99999,
        "jti": "token-jti",
    }
    store.start_connector_setup(
        token_payload=token_payload,
        connector_id="flowaccount",
        environment="production",
    )

    result = store.set_connector_credentials(
        token_payload=token_payload,
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )
    patched_metadata = store.profile_patches[-1]["metadata"]
    server_vault = patched_metadata["server_vault"]
    public_profile = public_connector_profile(next(iter(store.profiles.values())))

    assert result["status"] == "credentials_configured"
    assert server_vault["fields"] == ["client_id", "client_secret"]
    assert "ciphertext" in server_vault
    assert "super-secret-value" not in str(server_vault)
    assert "demo-client-id" not in str(server_vault)
    assert "'server_vault':" not in str(store.audit_events)
    assert "'vault_record':" not in str(store.audit_events)
    assert server_vault["ciphertext"] not in str(store.audit_events)
    assert "'server_vault':" not in str(public_profile)
    assert server_vault["ciphertext"] not in str(public_profile)


def test_product_table_validation_preserves_private_credential_vault_metadata() -> None:
    from mercury_tools.db.product import public_connector_profile

    store = StoreForSetup()
    token_payload = {
        "sub": "owner@example.com",
        "company": "Demo Co",
        "host_app": "codex",
        "iat": 0,
        "exp": 99999,
        "jti": "token-jti",
    }
    store.start_connector_setup(
        token_payload=token_payload,
        connector_id="flowaccount",
        environment="production",
    )
    store.set_connector_credentials(
        token_payload=token_payload,
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )
    credential_metadata = store.profile_patches[-1]["metadata"]
    server_vault = credential_metadata["server_vault"]

    ready_profile = store.set_connector_profile(
        token_payload=token_payload,
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Books",
        metadata={
            "setup_state": "ready",
            "enabled_capabilities": ["company.info.read"],
            "validation": {"token_status": 200, "company_info_status": 200},
        },
    )
    stored_profile = next(iter(store.profiles.values()))
    stored_metadata = stored_profile["metadata"]
    public_profile = public_connector_profile(stored_profile)

    assert ready_profile["status"] == "connected"
    assert stored_profile["status"] == "connected"
    assert stored_metadata["server_vault"]["ciphertext"] == server_vault["ciphertext"]
    assert stored_metadata["credential_storage"] == "encrypted_server_vault"
    assert stored_metadata["credential_fields"] == ["client_id", "client_secret"]
    assert stored_metadata["credential_fingerprints"] == credential_metadata[
        "credential_fingerprints"
    ]
    assert stored_metadata["credentials_configured"] is True
    assert (
        stored_metadata["credentials_configured_at"]
        == credential_metadata["credentials_configured_at"]
    )
    assert stored_metadata["setup_state"] == "ready"
    assert stored_metadata["validation"] == {
        "token_status": 200,
        "company_info_status": 200,
    }
    assert "'server_vault':" not in str(ready_profile)
    assert "'server_vault':" not in str(public_profile)
    assert server_vault["ciphertext"] not in str(ready_profile)
    assert server_vault["ciphertext"] not in str(public_profile)
    assert "super-secret-value" not in str(ready_profile)
    assert "demo-client-id" not in str(public_profile)
    assert "'server_vault':" not in str(store.audit_events)
    assert server_vault["ciphertext"] not in str(store.audit_events)


def test_start_connector_setup_resume_without_company_name_preserves_label() -> None:
    store = StoreForSetup()
    token_payload = {
        "sub": "owner@example.com",
        "company": "Demo Co",
        "host_app": "codex",
        "iat": 0,
        "exp": 99999,
        "jti": "token-jti",
    }
    first_profile = store.start_connector_setup(
        token_payload=token_payload,
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Co Books",
    )
    resumed_profile = store.start_connector_setup(
        token_payload=token_payload,
        connector_id="flowaccount",
        environment="production",
    )

    assert first_profile["company_name"] == "Demo Co Books"
    assert resumed_profile["company_name"] == "Demo Co Books"
    assert "company_name" not in store.profile_payloads[-1]


def test_start_connector_setup_rejects_unknown_connector() -> None:
    store = StoreForSetup()
    with pytest.raises(ValueError, match="Unknown connector"):
        store.start_connector_setup(
            token_payload={
                "sub": "owner@example.com",
                "company": "Demo Co",
                "host_app": "codex",
                "iat": 0,
                "exp": 99999,
                "jti": "token-jti",
            },
            connector_id="unknown-connector",
            environment="production",
            company_name="Demo Co Books",
        )


def test_start_connector_setup_rejects_invalid_environment() -> None:
    store = StoreForSetup()
    with pytest.raises(ValueError, match="Unsupported environment"):
        store.start_connector_setup(
            token_payload={
                "sub": "owner@example.com",
                "company": "Demo Co",
                "host_app": "codex",
                "iat": 0,
                "exp": 99999,
                "jti": "token-jti",
            },
            connector_id="flowaccount",
            environment="invalid-env",
            company_name="Demo Co Books",
        )
