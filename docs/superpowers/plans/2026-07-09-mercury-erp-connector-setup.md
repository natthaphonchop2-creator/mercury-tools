# Mercury ERP Connector Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Mercury product path where a user connects an accounting or ERP system through a gated credential setup skill, then Mercury routes MCP tools to the correct ERP docs, connector capabilities, and workspace flows.

**Architecture:** Mercury remains an online MCP product. The Codex plugin exposes skills and starter prompts, while the Render-hosted MCP server owns connector setup state, credential validation, RAG routing, and safe flow execution. ERP documents live in Mercury Wiki with connector metadata; API keys and client secrets live only in encrypted server-side connector profiles.

**Tech Stack:** Python 3.11, FastMCP `mcp==1.26.0`, Supabase/PostgREST, pgvector-backed RAG, `httpx`, `cryptography.Fernet`, `pytest`, Codex plugin marketplace metadata.

## Global Constraints

- Mercury v1 is an online MCP product, not a local-only CLI, local LLM, or web app-first product.
- The host AI app, such as Codex, Cursor, or Claude, remains the chat surface and model runtime.
- The plugin must not contain Supabase service role keys, server bearer tokens, accounting connector credentials, or user secrets.
- API documentation goes to Mercury Wiki; API credentials go to encrypted connector profiles.
- MCP outputs expose only sanitized connector status, capability names, citations, and summaries.
- Production writes are blocked unless a future approval workflow explicitly enables them.
- No connector-backed accounting workflow can run until the selected connector has valid credentials, a selected environment, and at least one validated read-only capability.
- FlowAccount is the first complete connector; PEAK Accounting, Express Account, and custom ERP remain setup targets until their manifests and validation adapters are complete.
- Git commits should be small and frequent, one independently testable task at a time.

---

## Space Summary

Mercury space has four parts:

1. **Codex Plugin:** `plugins/mercury-finance` makes Mercury visible as a finance/accounting plugin in Codex. It contains branding, starter prompts, skills, and remote MCP config.
2. **Online MCP Server:** `src/mercury_tools/mcp/server.py` exposes tools that Codex can call. It should expose knowledge search, connector setup, connector status, skill execution packages, and flow execution.
3. **Supabase Product Layer:** `src/mercury_tools/db/product.py` stores workspace, member, token, connector profile, skill, flow, and audit state. Credentials must be encrypted before storage.
4. **RAG / LLM Wiki:** `wiki/` and `src/mercury_tools/rag/` store and retrieve connector-aware knowledge by `connector_id`, `doc_type`, `environment`, `jurisdiction`, and `review_status`.

The user experience should be:

```text
Install Mercury Finance plugin
→ open Mercury Connect
→ choose FlowAccount / PEAK / Express / Custom ERP
→ enter only required credentials through a secure path
→ Mercury validates read-only API access
→ Mercury unlocks relevant skills and flows
→ user asks finance/accounting questions naturally in Codex
```

---

## File Structure

- Create: `src/mercury_tools/connectors/__init__.py`
  - Exports connector catalog and setup primitives.
- Create: `src/mercury_tools/connectors/catalog.py`
  - Owns connector manifests, required credential fields, preset auth values, capabilities, and endpoint metadata.
- Create: `src/mercury_tools/connectors/setup.py`
  - Owns setup state machine, credential field validation, and read-only connector validation adapters.
- Modify: `src/mercury_tools/db/product.py`
  - Moves connector catalog definitions to `connectors/catalog.py` and adds setup-state helpers.
- Modify: `src/mercury_tools/mcp/server.py`
  - Adds connector setup MCP tools and gates connector-backed flows.
- Create: `tests/test_connector_catalog.py`
  - Tests connector manifests and preset values.
- Create: `tests/test_connector_setup.py`
  - Tests setup state transitions and secret redaction.
- Create: `tests/test_connector_mcp_tools.py`
  - Tests MCP tools for setup, submit credentials, validation, and flow gating.
- Modify: `supabase/migrations/0002_mercury_product_layer.sql`
  - Adds connector profile metadata conventions through comments only if no schema change is required.
- Create: `.agents/plugins/marketplace.json`
  - Codex repository marketplace entry.
- Create: `plugins/mercury-finance/.codex-plugin/plugin.json`
  - Codex plugin presentation and starter prompts.
- Create: `plugins/mercury-finance/.mcp.json`
  - Remote Mercury MCP configuration.
- Create: `plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md`
  - Gated setup skill for API keys and connector validation.
- Create: `plugins/mercury-finance/skills/company-health-check-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/vat-summary-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/invoice-review-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/management-report-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/connector-setup-guide-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/mercury-flow-runner/SKILL.md`
- Create: `docs/JUDGE_QUICKSTART.md`
  - Screenshot-friendly install and demo flow.

---

### Task 1: Connector Catalog Module

**Files:**
- Create: `src/mercury_tools/connectors/__init__.py`
- Create: `src/mercury_tools/connectors/catalog.py`
- Modify: `src/mercury_tools/db/product.py`
- Test: `tests/test_connector_catalog.py`

**Interfaces:**
- Produces: `ConnectorManifest`, `CONNECTOR_CATALOG`, `connector_by_id(connector_id: str) -> ConnectorManifest | None`
- Produces: `list_connector_summaries() -> list[dict[str, Any]]`
- Consumes: existing connector ids `flowaccount`, `peak`, `express`

- [ ] **Step 1: Write the failing catalog tests**

```python
# tests/test_connector_catalog.py
from mercury_tools.connectors.catalog import connector_by_id, list_connector_summaries


def test_flowaccount_manifest_has_presets_and_capabilities() -> None:
    manifest = connector_by_id("flowaccount")

    assert manifest is not None
    assert manifest.connector_id == "flowaccount"
    assert manifest.status == "available"
    assert manifest.required_secret_fields == ["client_id", "client_secret"]
    assert manifest.preset["grant_type"] == "client_credentials"
    assert manifest.preset["scope"] == "flowaccount-api"
    assert manifest.preset["api_base_url"] == "https://openapi.flowaccount.com/v1"
    assert manifest.preset["token_url"] == "https://openapi.flowaccount.com/v1/token"
    assert "company.info.read" in manifest.capabilities
    assert "documents.invoice.list" in manifest.capabilities
    assert manifest.validation.read_only is True


def test_setup_target_manifests_are_visible_but_not_live() -> None:
    peak = connector_by_id("peak")
    express = connector_by_id("express")

    assert peak is not None
    assert peak.status == "setup_target"
    assert express is not None
    assert express.status == "setup_target"


def test_connector_summaries_do_not_include_secrets() -> None:
    summaries = list_connector_summaries()
    serialized = str(summaries).lower()

    assert {item["connector_id"] for item in summaries} >= {"flowaccount", "peak", "express"}
    assert "client_secret" in serialized
    assert "super-secret" not in serialized
    assert "bearer" not in serialized
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_connector_catalog.py -v
```

Expected:

```text
ModuleNotFoundError: No module named 'mercury_tools.connectors'
```

- [ ] **Step 3: Create connector catalog implementation**

```python
# src/mercury_tools/connectors/__init__.py
from mercury_tools.connectors.catalog import (
    CONNECTOR_CATALOG,
    ConnectorManifest,
    ConnectorValidation,
    connector_by_id,
    list_connector_summaries,
)

__all__ = [
    "CONNECTOR_CATALOG",
    "ConnectorManifest",
    "ConnectorValidation",
    "connector_by_id",
    "list_connector_summaries",
]
```

```python
# src/mercury_tools/connectors/catalog.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConnectorValidation:
    method: str
    token_url: str = ""
    read_only_endpoint: str = ""
    read_only: bool = True


@dataclass(frozen=True)
class ConnectorManifest:
    connector_id: str
    name: str
    status: str
    environments: list[str]
    required_secret_fields: list[str]
    preset: dict[str, str] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    validation: ConnectorValidation = field(
        default_factory=lambda: ConnectorValidation(method="manual")
    )

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_secret_fields"] = list(self.required_secret_fields)
        data["capabilities"] = list(self.capabilities)
        return data


CONNECTOR_CATALOG: list[ConnectorManifest] = [
    ConnectorManifest(
        connector_id="flowaccount",
        name="FlowAccount",
        status="available",
        environments=["production", "sandbox"],
        required_secret_fields=["client_id", "client_secret"],
        preset={
            "grant_type": "client_credentials",
            "scope": "flowaccount-api",
            "api_base_url": "https://openapi.flowaccount.com/v1",
            "token_url": "https://openapi.flowaccount.com/v1/token",
        },
        capabilities=[
            "company.info.read",
            "contacts.list",
            "products.list",
            "documents.invoice.list",
            "documents.invoice.get",
            "tax.vat_summary.read",
        ],
        validation=ConnectorValidation(
            method="oauth_client_credentials",
            token_url="https://openapi.flowaccount.com/v1/token",
            read_only_endpoint="/company/info",
            read_only=True,
        ),
    ),
    ConnectorManifest(
        connector_id="peak",
        name="PEAK Accounting",
        status="setup_target",
        environments=["production", "sandbox"],
        required_secret_fields=["client_id", "client_secret"],
        capabilities=[],
    ),
    ConnectorManifest(
        connector_id="express",
        name="Express Account",
        status="setup_target",
        environments=["local", "gateway"],
        required_secret_fields=["gateway_url", "api_key"],
        capabilities=[],
    ),
]


def connector_by_id(connector_id: str) -> ConnectorManifest | None:
    clean = connector_id.strip().lower()
    return next((item for item in CONNECTOR_CATALOG if item.connector_id == clean), None)


def list_connector_summaries() -> list[dict[str, Any]]:
    return [item.summary() for item in CONNECTOR_CATALOG]
```

- [ ] **Step 4: Replace product catalog source**

In `src/mercury_tools/db/product.py`, replace the local `CONNECTOR_CATALOG` and `connector_by_id` definitions with imports:

```python
from mercury_tools.connectors.catalog import CONNECTOR_CATALOG, connector_by_id
```

When existing code expects dictionaries, convert manifests with:

```python
"connectors": [connector.summary() for connector in CONNECTOR_CATALOG],
```

- [ ] **Step 5: Run tests to verify catalog and existing product tests pass**

Run:

```bash
pytest tests/test_connector_catalog.py tests/test_product_fallback.py -v
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```bash
git add src/mercury_tools/connectors tests/test_connector_catalog.py src/mercury_tools/db/product.py
git commit -m "Add connector catalog manifests"
```

---

### Task 2: Gated Connector Setup State Machine

**Files:**
- Create: `src/mercury_tools/connectors/setup.py`
- Modify: `src/mercury_tools/db/product.py`
- Test: `tests/test_connector_setup.py`

**Interfaces:**
- Produces: `ConnectorSetupStatus` literal values `not_started`, `program_selected`, `environment_selected`, `awaiting_credentials`, `credentials_received`, `validation_failed`, `connected_read_only`, `ready`
- Produces: `setup_connector_profile(...) -> dict[str, Any]`
- Produces: `required_missing_fields(manifest, credentials) -> list[str]`
- Consumes: `SupabaseProductStore.set_connector_profile(...)` and `SupabaseProductStore.set_connector_credentials(...)`

- [ ] **Step 1: Write failing state machine tests**

```python
# tests/test_connector_setup.py
from mercury_tools.config import Settings
from mercury_tools.connectors.catalog import connector_by_id
from mercury_tools.connectors.setup import (
    CONNECTOR_SETUP_STATES,
    next_setup_state,
    required_missing_fields,
)
from mercury_tools.db.product import SupabaseProductStore


def test_setup_states_are_ordered_and_explicit() -> None:
    assert CONNECTOR_SETUP_STATES == [
        "not_started",
        "program_selected",
        "environment_selected",
        "awaiting_credentials",
        "credentials_received",
        "validation_failed",
        "connected_read_only",
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


def test_next_setup_state_does_not_skip_credentials() -> None:
    assert next_setup_state(has_environment=False, missing_fields=["client_id"]) == "program_selected"
    assert next_setup_state(has_environment=True, missing_fields=["client_id"]) == "awaiting_credentials"
    assert next_setup_state(has_environment=True, missing_fields=[]) == "credentials_received"


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

    def _request(self, method: str, path: str, **kwargs):
        if path == "mercury_connector_profiles" and method == "POST":
            row = {
                **kwargs["json"][0],
                "id": "profile-1",
                "created_at": "2026-07-09T00:00:00+00:00",
                "updated_at": "2026-07-09T00:00:00+00:00",
            }
            self.rows.append(row)
            return [row]
        raise RuntimeError(f"unexpected request {method} {path}")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_connector_setup.py -v
```

Expected:

```text
ModuleNotFoundError or ImportError for mercury_tools.connectors.setup
```

- [ ] **Step 3: Implement setup primitives**

```python
# src/mercury_tools/connectors/setup.py
from __future__ import annotations

from typing import Any, Literal

from mercury_tools.connectors.catalog import ConnectorManifest

ConnectorSetupStatus = Literal[
    "not_started",
    "program_selected",
    "environment_selected",
    "awaiting_credentials",
    "credentials_received",
    "validation_failed",
    "connected_read_only",
    "ready",
]

CONNECTOR_SETUP_STATES: list[ConnectorSetupStatus] = [
    "not_started",
    "program_selected",
    "environment_selected",
    "awaiting_credentials",
    "credentials_received",
    "validation_failed",
    "connected_read_only",
    "ready",
]


def required_missing_fields(
    manifest: ConnectorManifest,
    credentials: dict[str, Any],
) -> list[str]:
    return [
        field
        for field in manifest.required_secret_fields
        if not str(credentials.get(field) or "").strip()
    ]


def next_setup_state(
    *,
    has_environment: bool,
    missing_fields: list[str],
) -> ConnectorSetupStatus:
    if not has_environment:
        return "program_selected"
    if missing_fields:
        return "awaiting_credentials"
    return "credentials_received"
```

- [ ] **Step 4: Add product store helper for setup status**

Add this method to `SupabaseProductStore` in `src/mercury_tools/db/product.py`:

```python
def start_connector_setup(
    self,
    *,
    token_payload: dict[str, Any],
    connector_id: str,
    environment: str,
    company_name: str | None = None,
) -> dict[str, Any]:
    manifest = connector_by_id(connector_id)
    if not manifest:
        raise ValueError(f"Unknown connector: {connector_id}")
    if environment not in manifest.environments:
        raise ValueError(f"Unsupported environment for {connector_id}: {environment}")
    return self.set_connector_profile(
        token_payload=token_payload,
        connector_id=manifest.connector_id,
        environment=environment,
        company_name=company_name,
        metadata={
            "setup_state": "awaiting_credentials",
            "required_secret_fields": manifest.required_secret_fields,
            "preset": manifest.preset,
            "capabilities": manifest.capabilities,
        },
    )
```

If `set_connector_profile` does not accept `metadata`, extend its signature to:

```python
def set_connector_profile(
    self,
    *,
    token_payload: dict[str, Any],
    connector_id: str,
    environment: str,
    company_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

- [ ] **Step 5: Run setup tests**

Run:

```bash
pytest tests/test_connector_setup.py tests/test_product_fallback.py -v
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```bash
git add src/mercury_tools/connectors/setup.py src/mercury_tools/db/product.py tests/test_connector_setup.py
git commit -m "Add gated connector setup state"
```

---

### Task 3: Connector Setup MCP Tools

**Files:**
- Modify: `src/mercury_tools/mcp/server.py`
- Test: `tests/test_connector_mcp_tools.py`

**Interfaces:**
- Produces MCP tool: `list_connectors() -> dict[str, Any]`
- Produces MCP tool: `start_connector_setup(client_token: str, connector_id: str, environment: str, company_name: str | None = None) -> dict[str, Any]`
- Produces MCP tool: `submit_connector_credentials(client_token: str, connector_id: str, environment: str, credentials: dict[str, Any]) -> dict[str, Any]`
- Produces MCP tool: `validate_connector_connection(client_token: str, connector_id: str, environment: str, credentials: dict[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: Write failing MCP tool tests**

```python
# tests/test_connector_mcp_tools.py
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


def test_start_connector_setup_requires_valid_connector(monkeypatch) -> None:
    from mercury_tools.mcp.server import start_connector_setup

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    invalid = start_connector_setup(
        client_token=make_client_token(),
        connector_id="unknown",
        environment="production",
    )

    assert invalid["status"] == "error"
    assert "Unknown connector" in invalid["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_connector_mcp_tools.py -v
```

Expected:

```text
ImportError: cannot import name 'list_connectors'
```

- [ ] **Step 3: Add MCP tools**

Add imports to `src/mercury_tools/mcp/server.py`:

```python
from mercury_tools.connectors.catalog import connector_by_id, list_connector_summaries
from mercury_tools.connectors.setup import required_missing_fields
```

Add tools:

```python
@mcp.tool()
def list_connectors() -> dict[str, Any]:
    """List Mercury accounting and ERP connector options without secrets."""
    payload = redact_json({"status": "ok", "connectors": list_connector_summaries()})
    _audit("list_connectors", {}, {"count": len(payload["connectors"])})
    return payload


@mcp.tool()
def start_connector_setup(
    client_token: str,
    connector_id: str,
    environment: str,
    company_name: str | None = None,
) -> dict[str, Any]:
    """Start gated connector setup for one workspace."""
    try:
        settings = load_settings()
        token_payload = _client_token_payload_from_value(client_token)
        profile = _product_store(settings).start_connector_setup(
            token_payload=token_payload,
            connector_id=connector_id,
            environment=environment,
            company_name=company_name,
        )
        payload = redact_json({"status": "ok", "profile": profile})
        _audit(
            "start_connector_setup",
            {**_client_token_audit_ref(client_token), "connector_id": connector_id},
            {"status": "ok", "connector_id": connector_id, "environment": environment},
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "start_connector_setup",
            {**_client_token_audit_ref(client_token), "connector_id": connector_id},
            payload,
        )
        return payload


@mcp.tool()
def submit_connector_credentials(
    client_token: str,
    connector_id: str,
    environment: str,
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Store connector credentials server-side after checking required fields."""
    try:
        manifest = connector_by_id(connector_id)
        if not manifest:
            raise ValueError(f"Unknown connector: {connector_id}")
        missing = required_missing_fields(manifest, credentials)
        if missing:
            return {
                "status": "awaiting_credentials",
                "missing_fields": missing,
                "message": "Required connector credentials are missing.",
            }
        settings = load_settings()
        token_payload = _client_token_payload_from_value(client_token)
        result = _product_store(settings).set_connector_credentials(
            token_payload=token_payload,
            connector_id=connector_id,
            environment=environment,
            credentials={key: str(value) for key, value in credentials.items()},
        )
        payload = redact_json({"status": "credentials_received", "result": result})
        _audit(
            "submit_connector_credentials",
            {**_client_token_audit_ref(client_token), "connector_id": connector_id},
            {"status": "credentials_received", "credential_fields": result["credential_fields"]},
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "submit_connector_credentials",
            {**_client_token_audit_ref(client_token), "connector_id": connector_id},
            payload,
        )
        return payload
```

- [ ] **Step 4: Run MCP connector tests**

Run:

```bash
pytest tests/test_connector_mcp_tools.py -v
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```bash
git add src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py
git commit -m "Expose connector setup MCP tools"
```

---

### Task 4: FlowAccount Read-Only Validation Adapter

**Files:**
- Modify: `src/mercury_tools/connectors/setup.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Test: `tests/test_connector_setup.py`
- Test: `tests/test_connector_mcp_tools.py`

**Interfaces:**
- Produces: `validate_connector_read_only(manifest, credentials, environment) -> dict[str, Any]`
- Consumes: FlowAccount preset `token_url`, `api_base_url`, `grant_type`, `scope`

- [ ] **Step 1: Add failing validation tests with monkeypatched HTTP**

```python
def test_validate_flowaccount_uses_token_and_company_info(monkeypatch) -> None:
    from mercury_tools.connectors.catalog import connector_by_id
    from mercury_tools.connectors.setup import validate_connector_read_only

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
        return FakeResponse(200, {"access_token": "secret-token", "token_type": "Bearer"})

    def fake_get(url, headers=None, timeout=60):
        calls.append(("GET", url))
        assert headers["Authorization"] == "Bearer secret-token"
        return FakeResponse(200, {"companyName": "Demo Books"})

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    manifest = connector_by_id("flowaccount")
    assert manifest is not None
    result = validate_connector_read_only(
        manifest,
        credentials={"client_id": "cid", "client_secret": "csecret"},
        environment="production",
    )

    assert result["status"] == "connected_read_only"
    assert result["company_name"] == "Demo Books"
    assert result["enabled_capabilities"] == manifest.capabilities
    assert "secret-token" not in str(result)
    assert calls == [
        ("POST", "https://openapi.flowaccount.com/v1/token"),
        ("GET", "https://openapi.flowaccount.com/v1/company/info"),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_connector_setup.py::test_validate_flowaccount_uses_token_and_company_info -v
```

Expected:

```text
ImportError: cannot import name 'validate_connector_read_only'
```

- [ ] **Step 3: Implement read-only validation**

```python
# Add to src/mercury_tools/connectors/setup.py
import httpx

from mercury_tools.safety.redaction import redact_json


def _flowaccount_company_name(payload: dict[str, Any]) -> str | None:
    for key in ("companyName", "company_name", "name"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def validate_connector_read_only(
    manifest: ConnectorManifest,
    *,
    credentials: dict[str, Any],
    environment: str,
) -> dict[str, Any]:
    if manifest.connector_id != "flowaccount":
        return {
            "status": "validation_failed",
            "message": f"Read-only validation adapter is not available for {manifest.connector_id}.",
        }
    missing = required_missing_fields(manifest, credentials)
    if missing:
        return {"status": "awaiting_credentials", "missing_fields": missing}

    token_response = httpx.post(
        manifest.preset["token_url"],
        data={
            "grant_type": manifest.preset["grant_type"],
            "scope": manifest.preset["scope"],
            "client_id": str(credentials["client_id"]),
            "client_secret": str(credentials["client_secret"]),
        },
        timeout=60,
    )
    token_payload = token_response.json()
    access_token = str(token_payload.get("access_token") or "")
    if token_response.status_code >= 300 or not access_token:
        return redact_json(
            {
                "status": "validation_failed",
                "http_status": token_response.status_code,
                "message": "Token request failed.",
                "provider_response": token_payload,
            }
        )

    info_url = f"{manifest.preset['api_base_url'].rstrip('/')}/company/info"
    info_response = httpx.get(
        info_url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60,
    )
    info_payload = info_response.json()
    if info_response.status_code >= 300:
        return redact_json(
            {
                "status": "validation_failed",
                "http_status": info_response.status_code,
                "message": "Company info request failed.",
                "provider_response": info_payload,
            }
        )

    return redact_json(
        {
            "status": "connected_read_only",
            "connector_id": manifest.connector_id,
            "environment": environment,
            "company_name": _flowaccount_company_name(info_payload),
            "enabled_capabilities": manifest.capabilities,
            "validation": {
                "token_status": token_response.status_code,
                "company_info_status": info_response.status_code,
            },
        }
    )
```

- [ ] **Step 4: Wire `validate_connector_connection` MCP tool**

Add to `src/mercury_tools/mcp/server.py`:

```python
from mercury_tools.connectors.setup import validate_connector_read_only


@mcp.tool()
def validate_connector_connection(
    client_token: str,
    connector_id: str,
    environment: str,
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Validate one connector through read-only API calls and return sanitized status."""
    try:
        manifest = connector_by_id(connector_id)
        if not manifest:
            raise ValueError(f"Unknown connector: {connector_id}")
        token_payload = _client_token_payload_from_value(client_token)
        result = validate_connector_read_only(
            manifest,
            credentials=credentials,
            environment=environment,
        )
        if result["status"] == "connected_read_only":
            _product_store(load_settings()).set_connector_profile(
                token_payload=token_payload,
                connector_id=connector_id,
                environment=environment,
                company_name=result.get("company_name"),
                metadata={
                    "setup_state": "ready",
                    "enabled_capabilities": result.get("enabled_capabilities") or [],
                    "validation": result.get("validation") or {},
                },
            )
            result["status"] = "ready"
        payload = redact_json(result)
        _audit(
            "validate_connector_connection",
            {**_client_token_audit_ref(client_token), "connector_id": connector_id},
            {"status": payload["status"], "connector_id": connector_id},
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "validate_connector_connection",
            {**_client_token_audit_ref(client_token), "connector_id": connector_id},
            payload,
        )
        return payload
```

- [ ] **Step 5: Run validation tests**

Run:

```bash
pytest tests/test_connector_setup.py tests/test_connector_mcp_tools.py tests/test_redaction.py -v
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```bash
git add src/mercury_tools/connectors/setup.py src/mercury_tools/mcp/server.py tests/test_connector_setup.py tests/test_connector_mcp_tools.py
git commit -m "Validate FlowAccount connector read-only"
```

---

### Task 5: Gate Connector-Backed Skills And Workspace Flows

**Files:**
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/db/product.py`
- Test: `tests/test_connector_mcp_tools.py`

**Interfaces:**
- Produces: `workspace_connector_ready(dashboard_payload: dict[str, Any]) -> bool`
- Consumes: connector profile `status`, `metadata.setup_state`, `metadata.enabled_capabilities`

- [ ] **Step 1: Write failing gating test**

```python
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
            return {"flow_id": flow_id, "title": "Revenue", "yaml": "name: Revenue\n---\n- connectorStatus: {}"}

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_workspace_flow_tool(
        client_token=make_client_token(),
        flow_id="workspace-revenue",
        dry_run=False,
    )

    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_connector_mcp_tools.py::test_run_workspace_flow_requires_ready_connector -v
```

Expected:

```text
AssertionError because run_workspace_flow currently does not block on setup_state
```

- [ ] **Step 3: Add connector readiness helper**

Add to `src/mercury_tools/mcp/server.py`:

```python
def _workspace_connector_ready(dashboard_payload: dict[str, Any]) -> bool:
    profiles = dashboard_payload.get("connector_profiles") or []
    for profile in profiles:
        metadata = profile.get("metadata") or {}
        if metadata.get("setup_state") == "ready":
            return bool(metadata.get("enabled_capabilities") or [])
        if profile.get("status") in {"ready", "connected_read_only"}:
            return True
    return False


def _connector_setup_block_payload() -> dict[str, Any]:
    return {
        "status": "blocked",
        "message": (
            "Connector credential setup is required before running connector-backed "
            "accounting workflows."
        ),
        "next_tool": "start_connector_setup",
        "next_skill": "connector-credential-setup-th",
    }
```

- [ ] **Step 4: Gate `run_workspace_flow_tool`**

In `run_workspace_flow_tool`, after creating `store` and before loading the flow:

```python
dashboard_payload = store.dashboard(token_payload)
if not _workspace_connector_ready(dashboard_payload):
    payload = _connector_setup_block_payload()
    _audit(
        "run_workspace_flow",
        {
            **_client_token_audit_ref(client_token),
            "flow_id": flow_id,
            "dry_run": dry_run,
            "env_keys": _env_keys(env_overrides),
        },
        payload,
    )
    return payload
```

- [ ] **Step 5: Run gating tests**

Run:

```bash
pytest tests/test_connector_mcp_tools.py tests/test_mcp_contract.py -v
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```bash
git add src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py
git commit -m "Gate workspace flows on connector readiness"
```

---

### Task 6: Connector-Aware RAG Routing

**Files:**
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/rag/models.py`
- Test: `tests/test_search_filters.py`
- Test: `tests/test_connector_mcp_tools.py`

**Interfaces:**
- Produces MCP tool: `retrieve_workspace_context_pack(client_token: str, query: str, task: str | None = None, max_chunks: int = 12) -> dict[str, Any]`
- Consumes: existing `retrieve_context_pack` behavior and `SearchFilters`

- [ ] **Step 1: Write failing workspace context test**

```python
def test_retrieve_workspace_context_pack_uses_active_connector(monkeypatch) -> None:
    from mercury_tools.mcp import server

    captured = {}

    class FakeStore:
        def dashboard(self, token_payload):
            return {
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "ready",
                        "metadata": {"setup_state": "ready", "enabled_capabilities": ["documents.invoice.list"]},
                    }
                ]
            }

    class FakeService:
        def context_pack(self, query, task=None, filters=None, max_chunks=12):
            captured["filters"] = filters
            return type(
                "Pack",
                (),
                {
                    "results": [],
                    "as_dict": lambda self: {
                        "query": query,
                        "task": task,
                        "results": [],
                        "connector_context": "flowaccount",
                    },
                },
            )()

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())
    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    payload = server.retrieve_workspace_context_pack(
        client_token=make_client_token(),
        query="สรุปรายได้อาทิตย์นี้",
    )

    assert payload["status"] == "ok"
    assert captured["filters"].connector == "flowaccount"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_connector_mcp_tools.py::test_retrieve_workspace_context_pack_uses_active_connector -v
```

Expected:

```text
ImportError: cannot import name 'retrieve_workspace_context_pack'
```

- [ ] **Step 3: Add workspace context tool**

Add to `src/mercury_tools/mcp/server.py`:

```python
def _active_connector_filter(dashboard_payload: dict[str, Any]) -> dict[str, Any]:
    profiles = dashboard_payload.get("connector_profiles") or []
    for profile in profiles:
        metadata = profile.get("metadata") or {}
        if metadata.get("setup_state") == "ready" or profile.get("status") in {"ready", "connected_read_only"}:
            return {
                "connector": profile.get("connector_id"),
                "environment": profile.get("environment"),
            }
    return {}


@mcp.tool()
def retrieve_workspace_context_pack(
    client_token: str,
    query: str,
    task: str | None = None,
    max_chunks: int = 12,
) -> dict[str, Any]:
    """Retrieve a cited context pack filtered to the workspace connector."""
    try:
        token_payload = _client_token_payload_from_value(client_token)
        dashboard_payload = _product_store(load_settings()).dashboard(token_payload)
        connector_filter = _active_connector_filter(dashboard_payload)
        if not connector_filter:
            return {
                "status": "requires_setup",
                "message": "Select and validate an accounting or ERP connector first.",
                "next_skill": "connector-credential-setup-th",
            }
        pack = _service().context_pack(
            query,
            task=task,
            filters=_filters(
                {
                    "connector": connector_filter["connector"],
                    "review_status": "reviewed",
                }
            ),
            max_chunks=max_chunks,
        )
        payload = redact_json(
            {
                "status": "ok",
                "connector": connector_filter,
                **pack.as_dict(),
            }
        )
        _audit(
            "retrieve_workspace_context_pack",
            {**_client_token_audit_ref(client_token), "query": query},
            {"status": "ok", "connector": connector_filter.get("connector")},
        )
        return payload
    except (PermissionError, RuntimeError, ValueError) as exc:
        payload = {"status": "error", "message": str(exc)}
        _audit(
            "retrieve_workspace_context_pack",
            {**_client_token_audit_ref(client_token), "query": query},
            payload,
        )
        return payload
```

- [ ] **Step 4: Run workspace RAG tests**

Run:

```bash
pytest tests/test_connector_mcp_tools.py tests/test_search_filters.py -v
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit**

```bash
git add src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py tests/test_search_filters.py
git commit -m "Route context packs by workspace connector"
```

---

### Task 7: Codex Plugin Package

**Files:**
- Create: `.agents/plugins/marketplace.json`
- Create: `plugins/mercury-finance/.codex-plugin/plugin.json`
- Create: `plugins/mercury-finance/.mcp.json`
- Create: `plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/company-health-check-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/vat-summary-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/invoice-review-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/management-report-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/connector-setup-guide-th/SKILL.md`
- Create: `plugins/mercury-finance/skills/mercury-flow-runner/SKILL.md`
- Test: `tests/test_plugin_package.py`

**Interfaces:**
- Produces Codex marketplace entry with `source.path` equal to `./plugins/mercury-finance`
- Produces plugin MCP config pointing to `https://mercury-tools-mcp.onrender.com/mcp`
- Produces skill docs that tell the host AI which MCP tools to use

- [ ] **Step 1: Write failing plugin package tests**

```python
# tests/test_plugin_package.py
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_marketplace_points_to_plugin_folder() -> None:
    data = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    plugins = data["plugins"]
    mercury = next(item for item in plugins if item["id"] == "mercury-finance")

    assert mercury["source"]["path"] == "./plugins/mercury-finance"
    assert mercury["name"] == "Mercury Finance"


def test_plugin_declares_remote_mcp_without_secret_values() -> None:
    plugin = json.loads((ROOT / "plugins/mercury-finance/.codex-plugin/plugin.json").read_text())
    mcp = json.loads((ROOT / "plugins/mercury-finance/.mcp.json").read_text())
    serialized = json.dumps({"plugin": plugin, "mcp": mcp})

    assert plugin["name"] == "mercury-finance"
    assert "Mercury Finance" in plugin["display_name"]
    assert "https://mercury-tools-mcp.onrender.com/mcp" in serialized
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized
    assert "client_secret" not in serialized


def test_connector_credential_skill_is_gated() -> None:
    skill = (
        ROOT
        / "plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md"
    ).read_text()

    assert "Use when" in skill
    assert "Do not ask the user to paste API keys" in skill
    assert "Do not proceed" in skill
    assert "validate_connector_connection" in skill
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_plugin_package.py -v
```

Expected:

```text
FileNotFoundError for .agents/plugins/marketplace.json
```

- [ ] **Step 3: Create marketplace and plugin files**

File: `.agents/plugins/marketplace.json`

```json
{
  "plugins": [
    {
      "id": "mercury-finance",
      "name": "Mercury Finance",
      "description": "Accounting AI for ERP connectors, VAT, reports, audit context, and Mercury Flows.",
      "source": {
        "type": "path",
        "path": "./plugins/mercury-finance"
      }
    }
  ]
}
```

File: `plugins/mercury-finance/.codex-plugin/plugin.json`

```json
{
  "name": "mercury-finance",
  "display_name": "Mercury Finance",
  "description": "Accounting AI for ERP connectors, VAT, reports, audit context, and Mercury Flows.",
  "version": "0.1.0",
  "category": "Finance",
  "capabilities": ["Interactive", "Read"],
  "starter_prompts": [
    "Prepare a Thai VAT context pack",
    "Connect FlowAccount and validate read-only access",
    "Run a company health check flow"
  ]
}
```

File: `plugins/mercury-finance/.mcp.json`

```json
{
  "mcpServers": {
    "mercury-tools": {
      "type": "http",
      "url": "https://mercury-tools-mcp.onrender.com/mcp",
      "bearer_token_env_var": "MERCURY_TOOLS_MCP_TOKEN"
    }
  }
}
```

- [ ] **Step 4: Create the gated connector skill**

```markdown
---
name: connector-credential-setup-th
description: Use when a user needs to connect FlowAccount, PEAK Accounting, Express Account, or another ERP/API system before running accounting workflows
---

# Connector Credential Setup TH

## Rule

Do not proceed to the next setup step until the current step is complete and validated.

Do not ask the user to paste API keys, client secrets, bearer tokens, or refresh tokens into normal chat. Use Mercury Connect or the host app's secure MCP credential path.

## Steps

1. Call `list_connectors`.
2. Ask the user to choose one connector if none is selected.
3. Call `start_connector_setup` with connector id and environment.
4. Show preset values that Mercury already knows.
5. Ask only for required missing credential fields through a secure input path.
6. Call `submit_connector_credentials`.
7. Call `validate_connector_connection`.
8. If validation returns `ready`, continue to the requested accounting workflow.
9. If validation fails, stay on the failed step and ask for only the missing correction.

## Output

Answer in Thai. Show program name, company name when available, environment, enabled capabilities, and next safe command. Never show raw credentials.
```

- [ ] **Step 5: Create compact supporting skills**

Each supporting skill should be under 80 lines and route to MCP tools:

```markdown
---
name: company-health-check-th
description: Use when the user asks for company health, revenue, VAT, cash flow, or accounting status summaries
---

# Company Health Check TH

Use `connector_status` first. If connector setup is incomplete, route to `connector-credential-setup-th`.

Use `retrieve_workspace_context_pack` for accounting context and `run_mercury_flow` for approved health-check flows. Answer in Thai for management, keep evidence concise, and mention accountant review points without dumping raw audit paths unless asked.
```

Create equivalent concise files for:

```text
vat-summary-th
invoice-review-th
management-report-th
connector-setup-guide-th
mercury-flow-runner
```

- [ ] **Step 6: Run plugin tests**

Run:

```bash
pytest tests/test_plugin_package.py -v
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit**

```bash
git add .agents/plugins plugins/mercury-finance tests/test_plugin_package.py
git commit -m "Add Mercury Finance Codex plugin package"
```

---

### Task 8: Judge Quickstart And Final Verification

**Files:**
- Create: `docs/JUDGE_QUICKSTART.md`
- Modify: `README.md`
- Test: `tests/test_plugin_package.py`

**Interfaces:**
- Produces: contest-ready install instructions
- Consumes: plugin package and remote MCP endpoint

- [ ] **Step 1: Write failing docs smoke test**

```python
def test_judge_quickstart_mentions_plugin_and_no_secrets() -> None:
    text = (ROOT / "docs/JUDGE_QUICKSTART.md").read_text()

    assert "Mercury Finance" in text
    assert "codex plugin marketplace add" in text
    assert "https://mercury-tools-mcp.onrender.com/mcp" in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert "client_secret =" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_plugin_package.py::test_judge_quickstart_mentions_plugin_and_no_secrets -v
```

Expected:

```text
FileNotFoundError for docs/JUDGE_QUICKSTART.md
```

- [ ] **Step 3: Create quickstart**

```markdown
# Mercury Finance Judge Quickstart

Mercury Finance is an online MCP accounting agent layer for ERP/API connectors, RAG context, audit-safe workflows, and Thai finance reporting.

## Install In Codex

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref main \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
```

Install `Mercury Finance` from the plugin list.

## Connect MCP

Endpoint:

```text
https://mercury-tools-mcp.onrender.com/mcp
```

If Codex asks for authentication, open Mercury Connect and use the issued client token through Codex's secure MCP auth path.

## Demo Prompts

```text
Connect FlowAccount and validate read-only access
Prepare a Thai VAT context pack
Run a company health check flow
```

## Safety

Do not paste API keys or client secrets into normal chat. Mercury stores connector credentials server-side and only returns sanitized status to the host AI.
```

- [ ] **Step 4: Add README pointer**

Append to `README.md`:

```markdown
## Mercury Finance Codex Plugin

See `docs/JUDGE_QUICKSTART.md` for the contest install flow. The plugin connects Codex to the hosted Mercury Tools MCP server and keeps connector credentials out of Git.
```

- [ ] **Step 5: Run full local verification**

Run:

```bash
pytest -q
ruff check .
git status --short
```

Expected:

```text
pytest exits 0
ruff exits 0
git status shows only intended docs changes before commit
```

- [ ] **Step 6: Commit**

```bash
git add docs/JUDGE_QUICKSTART.md README.md tests/test_plugin_package.py
git commit -m "Document Mercury Finance judge quickstart"
```

---

## Self-Review

Spec coverage:

- Online MCP product boundary: covered by Global Constraints, Task 7, Task 8.
- ERP connector positioning: covered by Tasks 1, 3, 4, 6.
- Knowledge separated from credentials: covered by Tasks 2, 3, 4, 6, 7.
- Gated credential setup skill: covered by Tasks 2, 3, 4, 5, 7.
- Codex plugin package: covered by Task 7.
- Judge quickstart: covered by Task 8.
- No production writes: covered by Global Constraints, Task 4 validation adapter, Task 5 gating.

Placeholder scan:

- This plan contains no forbidden placeholder markers.
- Future connector tool names are scoped to concrete tasks or explicitly excluded from v1 implementation.

Type consistency:

- `connector_id`, `environment`, `client_token`, `credentials`, and `metadata.setup_state` are used consistently across catalog, setup, product store, and MCP tools.
- Setup statuses match the spec: `not_started`, `program_selected`, `environment_selected`, `awaiting_credentials`, `credentials_received`, `validation_failed`, `connected_read_only`, `ready`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-mercury-erp-connector-setup.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
