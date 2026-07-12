# Mercury Public MCP Contest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current Mercury preview into a coherent public, token-free MCP contest product that installs from GitHub, routes knowledge by ERP, keeps public workspace state, and blocks production-changing ERP actions.

**Architecture:** Keep one public Streamable HTTP MCP server on Render and one `mercury-finance` GitHub marketplace plugin. Replace MCP-facing `client_token` parameters with opaque public `workspace_id` routing, while reusing the existing Supabase product tables and encrypted connector vault internally. Keep the host AI as the conversation/model runtime and keep Mercury free of dashboard or chat UX.

**Tech Stack:** Python 3.11, MCP Python SDK 1.26/FastMCP, Starlette, Supabase Postgres/PostgREST/pgvector, pytest, Ruff, Docker, Render, Codex GitHub marketplaces.

## Global Constraints

- Contest deployment is public and requires no OAuth, bearer token, login, or web setup page.
- `workspace_id` provides routing only and must never be described as authentication or private tenant isolation.
- Do not discard or overwrite unrelated dirty-worktree changes. Stage only files owned by the current task.
- Do not expose raw ERP credentials, access tokens, tax IDs, emails, customer records, or accounting payloads in MCP responses or audit events.
- Connector credential field names and public token endpoint URLs must remain visible as setup metadata.
- Production create, update, delete, payment, void, email, share, approval, and journal-posting operations stay blocked.
- Mercury does not call an LLM for final answers; Codex or another MCP host remains the model runtime.
- No Mercury dashboard, browser setup console, or standalone chat page.
- Keep the public MCP endpoint at `https://mercury-tools-mcp.onrender.com/mcp`.
- Keep marketplace source `natthaphonchop2-creator/mercury-tools`, ref `main`, sparse paths `.agents/plugins` and `plugins/mercury-finance`.

---

## File Responsibility Map

- `src/mercury_tools/workspaces/public.py`: validate/generate public workspace IDs and create internal compatibility payloads.
- `src/mercury_tools/db/product.py`: create and resolve public workspaces, load encrypted connector credentials, and reuse product persistence without exposing token semantics to MCP clients.
- `src/mercury_tools/rag/routing.py`: infer an ERP connector from explicit connector names in a query.
- `src/mercury_tools/mcp/server.py`: expose the public MCP contract and remove obsolete local-runtime behavior from remote tools.
- `src/mercury_tools/connectors/catalog.py`: provide explicit public connector metadata and capability policy.
- `src/mercury_tools/safety/redaction.py`: retain strict value redaction; do not use generic redaction to erase already-curated connector metadata.
- `plugins/mercury-finance/`: package skills and the public remote MCP configuration.
- `wiki/connectors/`: connector-specific endpoint dictionaries and setup knowledge.
- `supabase/migrations/0003_*.sql`, `0004_*.sql`: deterministic hybrid-search fixes.
- `tests/`: contract, persistence, routing, redaction, plugin-install, and remote smoke coverage.

---

### Task 1: Add Public Workspace Identity Primitives

**Files:**
- Create: `src/mercury_tools/workspaces/__init__.py`
- Create: `src/mercury_tools/workspaces/public.py`
- Create: `tests/test_public_workspace.py`

**Interfaces:**
- Produces: `new_public_workspace_id() -> str`
- Produces: `normalize_public_workspace_id(value: str) -> str`
- Produces: `public_workspace_token_payload(workspace_id: str) -> dict[str, Any]`
- Produces: `public_workspace_connect_request(workspace_id: str, company_name: str | None) -> ConnectRequest`

- [ ] **Step 1: Write failing format and payload tests**

```python
from mercury_tools.workspaces.public import (
    new_public_workspace_id,
    normalize_public_workspace_id,
    public_workspace_connect_request,
    public_workspace_token_payload,
)


def test_public_workspace_id_is_opaque_and_validated() -> None:
    workspace_id = new_public_workspace_id()
    assert workspace_id.startswith("mw_")
    assert len(workspace_id) >= 24
    assert normalize_public_workspace_id(workspace_id) == workspace_id


def test_public_workspace_payload_uses_workspace_id_as_internal_jti() -> None:
    workspace_id = "mw_abcdefghijklmnopqrstuvwxyz"
    payload = public_workspace_token_payload(workspace_id)
    request = public_workspace_connect_request(workspace_id, "Demo Company")
    assert payload["jti"] == workspace_id
    assert payload["scope"] == ["public:contest"]
    assert request.company == "Demo Company"
    assert request.host_app == "generic"
    assert workspace_id not in request.email
```

- [ ] **Step 2: Verify the tests fail because the module does not exist**

Run: `uv run pytest tests/test_public_workspace.py -q`

Expected: collection fails with `ModuleNotFoundError: mercury_tools.workspaces`.

- [ ] **Step 3: Implement the public workspace primitives**

```python
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any

from mercury_tools.product import ConnectRequest

PUBLIC_WORKSPACE_RE = re.compile(r"^mw_[A-Za-z0-9_-]{20,80}$")
PUBLIC_WORKSPACE_TTL_SECONDS = 60 * 60 * 24 * 365 * 10


def new_public_workspace_id() -> str:
    return "mw_" + secrets.token_urlsafe(18)


def normalize_public_workspace_id(value: str) -> str:
    normalized = value.strip()
    if not PUBLIC_WORKSPACE_RE.fullmatch(normalized):
        raise ValueError("Invalid Mercury public workspace ID.")
    return normalized


def public_workspace_token_payload(workspace_id: str) -> dict[str, Any]:
    normalized = normalize_public_workspace_id(workspace_id)
    now = int(time.time())
    subject_hash = hashlib.sha256(normalized.encode()).hexdigest()[:20]
    return {
        "sub": f"public-{subject_hash}@workspace.invalid",
        "company": "Mercury Public Workspace",
        "host_app": "generic",
        "iat": now,
        "exp": now + PUBLIC_WORKSPACE_TTL_SECONDS,
        "jti": normalized,
        "scope": ["public:contest"],
    }


def public_workspace_connect_request(
    workspace_id: str,
    company_name: str | None,
) -> ConnectRequest:
    payload = public_workspace_token_payload(workspace_id)
    company = (company_name or "Mercury Public Workspace").strip()
    return ConnectRequest(
        email=payload["sub"],
        company=company or "Mercury Public Workspace",
        host_app="generic",
        invite_code="",
    )
```

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_public_workspace.py -q`

Expected: all public workspace primitive tests pass.

- [ ] **Step 5: Commit only Task 1 files**

```bash
git add src/mercury_tools/workspaces tests/test_public_workspace.py
git commit -m "Add Mercury public workspace identifiers"
```

### Task 2: Persist And Resolve Public Workspaces

**Files:**
- Modify: `src/mercury_tools/db/product.py`
- Modify: `tests/test_product_fallback.py`
- Modify: `tests/test_connector_setup.py`

**Interfaces:**
- Consumes: `public_workspace_token_payload(workspace_id)` and `public_workspace_connect_request(workspace_id, company_name)`
- Produces: `SupabaseProductStore.create_public_workspace(company_name: str | None) -> dict[str, Any]`
- Produces: `SupabaseProductStore.public_dashboard(workspace_id: str) -> dict[str, Any]`
- Produces: `decrypt_connector_credentials(settings, workspace_key_value, vault_record) -> dict[str, str]`
- Produces: `SupabaseProductStore.get_connector_credentials(workspace_id, connector_id, environment) -> dict[str, str]`

- [ ] **Step 1: Write failing persistence tests**

Add tests that create a public workspace through an `AuditFallbackStore`, then resolve it using the returned `workspace_id`:

```python
def test_product_store_creates_public_workspace_and_resolves_dashboard() -> None:
    store = AuditFallbackStore()
    created = store.create_public_workspace("Public Demo Co")
    dashboard = store.public_dashboard(created["workspace_id"])
    assert created["workspace_id"].startswith("mw_")
    assert created["workspace"]["name"] == "Public Demo Co"
    assert dashboard["workspace"]["name"] == "Public Demo Co"
    assert dashboard["public_mode"] is True


def test_connector_vault_round_trip_never_returns_ciphertext() -> None:
    store = AuditFallbackStore()
    created = store.create_public_workspace("Public Demo Co")
    workspace_id = created["workspace_id"]
    payload = public_workspace_token_payload(workspace_id)
    store.set_connector_credentials(
        token_payload=payload,
        connector_id="flowaccount",
        environment="sandbox",
        credentials={"client_id": "demo-id", "client_secret": "demo-secret"},
    )
    credentials = store.get_connector_credentials(
        workspace_id=workspace_id,
        connector_id="flowaccount",
        environment="sandbox",
    )
    assert credentials == {"client_id": "demo-id", "client_secret": "demo-secret"}
    assert "ciphertext" not in str(store.public_dashboard(workspace_id))
```

- [ ] **Step 2: Run tests and confirm the methods are missing**

Run: `uv run pytest tests/test_product_fallback.py tests/test_connector_setup.py -q`

Expected: failures report missing public workspace and connector-vault methods.

- [ ] **Step 3: Implement workspace creation and dashboard adapters**

Use the existing product-table and audit-fallback paths rather than adding a parallel database model:

```python
def create_public_workspace(self, company_name: str | None = None) -> dict[str, Any]:
    workspace_id = new_public_workspace_id()
    request = public_workspace_connect_request(workspace_id, company_name)
    token_payload = public_workspace_token_payload(workspace_id)
    persisted = self.upsert_connection(request, token_payload)
    return {
        "status": "ok",
        "public_mode": True,
        "workspace_id": workspace_id,
        "workspace": persisted["workspace"],
    }


def public_dashboard(self, workspace_id: str) -> dict[str, Any]:
    payload = self.dashboard(public_workspace_token_payload(workspace_id))
    if payload.get("status") == "unregistered":
        return {"status": "not_found", "public_mode": True, "workspace_id": workspace_id}
    return {**payload, "public_mode": True, "workspace_id": workspace_id}
```

Keep the public ID in the existing `mercury_client_tokens.token_jti` field so the current product tables and connector/flow methods remain reusable.

- [ ] **Step 4: Implement connector credential decryption for server-side validation**

Use the existing Fernet key derivation and validate connector/environment fields before returning the internal dictionary. The method is server-only and must never be called from response serialization.

```python
def decrypt_connector_credentials(
    settings: Settings,
    *,
    workspace_key_value: str,
    vault_record: dict[str, Any],
) -> dict[str, str]:
    ciphertext = str(vault_record.get("ciphertext") or "")
    if not ciphertext:
        raise ValueError("Connector credential vault is empty.")
    plaintext = Fernet(vault_key(settings, workspace_key_value)).decrypt(
        ciphertext.encode("ascii")
    )
    decoded = json.loads(plaintext)
    if decoded.get("connector_id") != vault_record.get("connector_id"):
        raise ValueError("Connector credential vault does not match connector.")
    if decoded.get("environment") != vault_record.get("environment"):
        raise ValueError("Connector credential vault does not match environment.")
    credentials = decoded.get("credentials")
    if not isinstance(credentials, dict):
        raise ValueError("Connector credential vault payload is invalid.")
    return {str(key): str(value) for key, value in credentials.items()}
```

`get_connector_credentials` resolves `public_workspace_token_payload(workspace_id)`, fetches the private connector profile for that workspace/connector/environment, and passes its `server_vault` to this function. It raises a sanitized `ValueError` when the profile or vault is missing.

- [ ] **Step 5: Run product and connector persistence tests**

Run: `uv run pytest tests/test_product_fallback.py tests/test_connector_setup.py -q`

Expected: all focused tests pass and raw credentials remain absent from event serialization.

- [ ] **Step 6: Commit Task 2 files**

```bash
git add src/mercury_tools/db/product.py tests/test_product_fallback.py tests/test_connector_setup.py
git commit -m "Persist public Mercury workspaces"
```

### Task 3: Correct Public Connector Metadata

**Files:**
- Modify: `src/mercury_tools/connectors/catalog.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `tests/test_connector_catalog.py`
- Modify: `tests/test_connector_mcp_tools.py`

**Interfaces:**
- Produces: `ConnectorManifest.public_summary() -> dict[str, Any]`
- Produces: `list_connector_public_summaries() -> list[dict[str, Any]]`
- Produces: `connector_capabilities(connector_id: str) -> dict[str, Any]`
- Updates: `list_connectors()` returns curated metadata without applying generic key-name redaction.

- [ ] **Step 1: Add failing public metadata tests**

```python
def test_flowaccount_public_summary_keeps_setup_field_names_and_urls() -> None:
    manifest = connector_by_id("flowaccount")
    summary = manifest.public_summary()
    assert summary["required_secret_fields"] == ["client_id", "client_secret"]
    assert summary["preset"]["token_url"] == "https://openapi.flowaccount.com/v1/token"
    assert "credential_values" not in summary


def test_list_connectors_does_not_erase_public_setup_metadata() -> None:
    payload = server.list_connectors()
    flow = next(row for row in payload["connectors"] if row["connector_id"] == "flowaccount")
    assert flow["required_secret_fields"] == ["client_id", "client_secret"]
    assert flow["preset"]["token_url"].startswith("https://")
```

- [ ] **Step 2: Verify the existing output fails with `[REDACTED]`**

Run: `uv run pytest tests/test_connector_catalog.py tests/test_connector_mcp_tools.py -q`

Expected: the required field list and token URL assertions fail.

- [ ] **Step 3: Add an explicit safe connector serializer**

`public_summary()` should return only connector ID, display name, neutral status, environments, required field names, public presets, capability names, and validation probe metadata. Do not include credential values or runtime responses.

```python
def public_summary(self) -> dict[str, Any]:
    return {
        "connector_id": self.connector_id,
        "name": self.name,
        "status": self.status,
        "environments": list(self.environments),
        "required_secret_fields": list(self.required_secret_fields),
        "preset": dict(self.preset),
        "environment_presets": {
            key: dict(value) for key, value in self.environment_presets.items()
        },
        "capabilities": list(self.capabilities),
        "validation": {
            "method": self.validation.method,
            "token_url": self.validation.token_url,
            "healthcheck_endpoint": self.validation.healthcheck_endpoint,
            "safe_probe": self.validation.safe_probe,
        },
    }


def list_connector_public_summaries() -> list[dict[str, Any]]:
    return [manifest.public_summary() for manifest in CONNECTOR_CATALOG]
```

- [ ] **Step 4: Add connector capability lookup**

```python
@mcp.tool()
def connector_capabilities(connector_id: str) -> dict[str, Any]:
    manifest = connector_by_id(connector_id)
    if manifest is None:
        return {"status": "not_found", "connector_id": connector_id}
    return {
        "status": "ok",
        "connector_id": manifest.connector_id,
        "capabilities": manifest.capabilities,
        "public_policy": "read_only_validation",
    }
```

- [ ] **Step 5: Run connector tests**

Run: `uv run pytest tests/test_connector_catalog.py tests/test_connector_mcp_tools.py -q`

Expected: metadata remains visible and no credential value appears.

- [ ] **Step 6: Commit Task 3 files**

```bash
git add src/mercury_tools/connectors/catalog.py src/mercury_tools/mcp/server.py tests/test_connector_catalog.py tests/test_connector_mcp_tools.py
git commit -m "Expose safe connector setup metadata"
```

### Task 4: Replace Token-Scoped MCP Tools With Public Workspace Tools

**Files:**
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/flows/runner.py`
- Modify: `tests/test_mcp_contract.py`
- Modify: `tests/test_connector_mcp_tools.py`
- Modify: `tests/test_flows.py`
- Modify: `tests/test_http_app.py`

**Interfaces:**
- Produces MCP tools: `create_public_workspace`, `get_public_workspace`, `select_workspace_connector`
- Updates MCP tools: `connector_status`, `retrieve_workspace_context_pack`, `start_connector_setup`, `submit_connector_credentials`, `validate_connector_connection`, `list_workspace_flows`, `save_workspace_flow`, `run_workspace_flow`, `run_mercury_flow`
- Removes MCP-facing requirement: `client_token`

- [ ] **Step 1: Write a failing MCP schema contract test**

```python
async def test_public_mcp_tool_schemas_use_workspace_id() -> None:
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert "create_public_workspace" in tools
    for name in {
        "retrieve_workspace_context_pack",
        "start_connector_setup",
        "submit_connector_credentials",
        "validate_connector_connection",
        "list_workspace_flows",
        "run_workspace_flow",
        "save_workspace_flow",
    }:
        properties = tools[name].inputSchema["properties"]
        assert "workspace_id" in properties
        assert "client_token" not in properties


def test_public_workspace_audit_reference_does_not_store_raw_id() -> None:
    workspace_id = "mw_abcdefghijklmnopqrstuvwxyz"
    audit_ref = _public_workspace_audit_ref(workspace_id)
    assert workspace_id not in str(audit_ref)
    assert audit_ref["workspace_id_hash"]
```

- [ ] **Step 2: Run the contract test and confirm old schemas fail**

Run: `uv run pytest tests/test_mcp_contract.py -q`

Expected: tools still require `client_token` and public workspace tools are absent.

- [ ] **Step 3: Add public workspace MCP tools**

Implement `create_public_workspace(company_name=None)` and `get_public_workspace(workspace_id)` as thin adapters over `SupabaseProductStore`.

```python
@mcp.tool()
def create_public_workspace(company_name: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    if not settings.supabase_configured:
        return {"status": "unavailable", "message": "Supabase product state is unavailable."}
    payload = _product_store(settings).create_public_workspace(company_name)
    _audit("create_public_workspace", {"company_name": company_name}, {"status": payload["status"]})
    return redact_json(payload)


@mcp.tool()
def get_public_workspace(workspace_id: str) -> dict[str, Any]:
    settings = load_settings()
    payload = _product_store(settings).public_dashboard(workspace_id)
    _audit(
        "get_public_workspace",
        _public_workspace_audit_ref(workspace_id),
        {"status": payload["status"]},
    )
    return redact_json(payload)
```

- [ ] **Step 4: Replace MCP tool arguments and audit references**

Use `public_workspace_token_payload(workspace_id)` internally. Audit the workspace ID through a one-way hash and prefix, never as a client token. Keep HTTP `/api/*` token handlers separate until they are removed in a later private-product cleanup.

```python
def _public_workspace_audit_ref(workspace_id: str) -> dict[str, str]:
    normalized = normalize_public_workspace_id(workspace_id)
    return {
        "workspace_id_prefix": normalized[:6],
        "workspace_id_hash": sha256_text(normalized)[:16],
    }


def _public_workspace_payload(workspace_id: str) -> dict[str, Any]:
    return public_workspace_token_payload(normalize_public_workspace_id(workspace_id))
```

Each workspace-scoped MCP tool calls `_public_workspace_payload(workspace_id)` before existing product-store methods and uses `_public_workspace_audit_ref` in audit input.

- [ ] **Step 5: Replace obsolete connector status**

`connector_status()` with no workspace ID returns connector catalog state and `requires_workspace`. With a workspace ID it returns the public dashboard's active connector profile. Remove `/root/.mercury-agent`, local home paths, and the duplicate `workspace_connector_status` MCP registration.

```python
@mcp.tool()
def connector_status(workspace_id: str | None = None) -> dict[str, Any]:
    if workspace_id is None:
        return {
            "status": "requires_workspace",
            "next_tool": "create_public_workspace",
            "connectors": list_connector_public_summaries(),
        }
    dashboard = _product_store(load_settings()).public_dashboard(workspace_id)
    profiles = public_connector_profiles(dashboard.get("connector_profiles"))
    active = _active_workspace_connector_profile({"connector_profiles": profiles})
    return redact_json(
        {
            "status": "ok" if active else "requires_setup",
            "workspace": dashboard.get("workspace"),
            "active_connector": active,
            "connector_profiles": profiles,
        }
    )
```

- [ ] **Step 6: Update flows to pass workspace routing**

Rename optional runner plumbing from `client_token` to `workspace_id`. Preserve dry-run behavior and make connector-backed flow runs return `requires_workspace` when no ID is available.

```python
def run_mercury_flow(
    flow_yaml: str | None = None,
    flow_files: dict[str, str] | None = None,
    config_yaml: str | None = None,
    workspace_flow_id: str | None = None,
    workspace_id: str | None = None,
    dry_run: bool = True,
    env: dict[str, Any] | None = None,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    continue_on_failure: bool | None = None,
) -> dict[str, Any]:
    if workspace_flow_id and not workspace_id:
        return {
            "status": "requires_workspace",
            "next_tool": "create_public_workspace",
            "message": "workspace_id is required with workspace_flow_id.",
        }
    # Preserve the existing exactly-one-input-mode validation and dispatch.
```

Keep the existing full dispatch body below this guard; replace only token plumbing and public audit references.

- [ ] **Step 7: Run MCP, flow, and HTTP tests**

Run: `uv run pytest tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_flows.py tests/test_http_app.py -q`

Expected: all public MCP schema and behavior tests pass; legacy HTTP endpoints remain covered independently.

- [ ] **Step 8: Commit Task 4 files**

```bash
git add src/mercury_tools/mcp/server.py src/mercury_tools/flows/runner.py tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_flows.py tests/test_http_app.py
git commit -m "Expose token-free public workspace MCP tools"
```

### Task 5: Add Connector-Aware RAG Routing

**Files:**
- Create: `src/mercury_tools/rag/routing.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/rag/models.py`
- Modify: `tests/test_search_filters.py`
- Modify: `tests/test_wiki_content.py`
- Include: `wiki/connectors/flowaccount-endpoint-dictionary.md`
- Include: `supabase/migrations/0003_match_knowledge_chunks_null_embedding.sql`
- Include: `supabase/migrations/0004_match_knowledge_chunks_endpoint_terms.sql`

**Interfaces:**
- Produces: `infer_connector_id(query: str) -> str | None`
- Produces: `apply_connector_routing(query, filters) -> tuple[dict[str, Any], str | None]`
- Updates search payloads with `applied_filters`, `inferred_connector`, and per-result `metadata`.

- [ ] **Step 1: Write failing connector inference tests**

```python
def test_connector_inference_is_explicit_and_unambiguous() -> None:
    assert infer_connector_id("FlowAccount invoice endpoint") == "flowaccount"
    assert infer_connector_id("ดึงใบแจ้งหนี้จาก PEAK") == "peak"
    assert infer_connector_id("invoice endpoint") is None


def test_explicit_filter_wins_over_query_inference() -> None:
    filters, inferred = apply_connector_routing(
        "FlowAccount invoice endpoint",
        {"connector": "peak"},
    )
    assert filters["connector"] == "peak"
    assert inferred is None
```

- [ ] **Step 2: Run routing tests and confirm missing module failure**

Run: `uv run pytest tests/test_search_filters.py -q`

Expected: import or assertion failure for connector routing.

- [ ] **Step 3: Implement deterministic connector inference**

Recognize only explicit aliases: `flowaccount`, `flow account`, `peak`, `peak accounting`, and `express account`. Do not infer a connector from generic accounting nouns.

```python
from __future__ import annotations

import re
from typing import Any

CONNECTOR_PATTERNS = {
    "flowaccount": (r"\bflow\s*account\b", r"\bflowaccount\b"),
    "peak": (r"\bpeak\s*accounting\b", r"\bpeak\b"),
    "express": (r"\bexpress\s*account\b",),
}


def infer_connector_id(query: str) -> str | None:
    text = query.casefold()
    matches = {
        connector_id
        for connector_id, patterns in CONNECTOR_PATTERNS.items()
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def apply_connector_routing(
    query: str,
    filters: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    applied = dict(filters or {})
    if applied.get("connector"):
        return applied, None
    inferred = infer_connector_id(query)
    if inferred:
        applied["connector"] = inferred
    return applied, inferred
```

- [ ] **Step 4: Apply routing to global search and context tools**

If the caller supplies a connector filter, keep it. Otherwise apply an inferred connector and return the inference in the payload. Workspace context always uses the selected profile and does not use query inference.

```python
applied_filters, inferred_connector = apply_connector_routing(query, filters)
results = _service().search(
    query,
    filters=_filters(applied_filters),
    top_k=top_k,
    mode=mode,
)
payload = {
    "query": query,
    "applied_filters": applied_filters,
    "inferred_connector": inferred_connector,
    "results": serialize_search_results(results),
}
```

- [ ] **Step 5: Return RAG metadata in MCP results**

Add `metadata: result.metadata` to `search_knowledge` output. Verify citations retain source title, URI, URL/path, heading, and chunk ID.

```python
def serialize_search_results(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": result.chunk_id,
            "document_uri": result.document_uri,
            "score": result.score,
            "text": result.text,
            "citation": result.citation,
            "metadata": result.metadata,
            "source_title": result.source_title,
            "source_uri": result.source_uri,
            "source_url": result.source_url,
            "source_path": result.source_path,
        }
        for result in results
    ]
```

- [ ] **Step 6: Verify endpoint dictionary coverage locally**

Run:

```bash
uv run pytest tests/test_search_filters.py tests/test_wiki_content.py tests/test_peak_wiki_content.py -q
uv run mercury-tools ingest wiki --path ./wiki
uv run mercury-tools search "FlowAccount invoice endpoint" --json
```

Expected: FlowAccount query results contain only FlowAccount sources after inferred routing; PEAK tests continue to pass.

- [ ] **Step 7: Commit routing, migration, and wiki files**

```bash
git add src/mercury_tools/rag/routing.py src/mercury_tools/rag/models.py src/mercury_tools/mcp/server.py tests/test_search_filters.py tests/test_wiki_content.py wiki/connectors/flowaccount-endpoint-dictionary.md supabase/migrations/0003_match_knowledge_chunks_null_embedding.sql supabase/migrations/0004_match_knowledge_chunks_endpoint_terms.sql
git commit -m "Route Mercury knowledge by ERP connector"
```

### Task 6: Enforce The Contest Read-Only Boundary

**Files:**
- Modify: `src/mercury_tools/connectors/catalog.py`
- Modify: `src/mercury_tools/flows/models.py`
- Modify: `src/mercury_tools/flows/runner.py`
- Modify: `tests/test_connector_catalog.py`
- Modify: `tests/test_flows.py`

**Interfaces:**
- Produces: `ConnectorManifest.read_capabilities` and `ConnectorManifest.blocked_capabilities`
- Produces: `is_public_capability_allowed(capability: str) -> bool`
- Updates flow execution to return structured `blocked` before any connector mutation.

- [ ] **Step 1: Add failing policy tests**

```python
def test_public_policy_allows_reads_and_blocks_mutations() -> None:
    assert is_public_capability_allowed("documents.invoice.list") is True
    assert is_public_capability_allowed("documents.invoice.create") is False
    assert is_public_capability_allowed("documents.invoice.payment.create") is False
    assert is_public_capability_allowed("documents.email.send") is False
    assert is_public_capability_allowed("journal.approve.create") is False
```

- [ ] **Step 2: Run focused tests and confirm policy helper is absent**

Run: `uv run pytest tests/test_connector_catalog.py tests/test_flows.py -q`

Expected: missing helper or incorrect policy assertions.

- [ ] **Step 3: Implement explicit capability classification**

Allow suffixes `.read`, `.list`, and `.get`, plus setup authentication probes. Block create, update, delete, payment, void, send, share, approve, upload, attach, invite, and journal mutation capabilities. Connector manifests may further narrow allowed reads.

```python
PUBLIC_ALLOWED_EXACT = {"auth.token.create", "auth.client_token.create"}
PUBLIC_ALLOWED_SUFFIXES = (".read", ".list", ".get")
PUBLIC_BLOCKED_SEGMENTS = {
    "create", "update", "delete", "payment", "void", "send", "share",
    "approve", "upload", "attach", "invite", "post",
}


def is_public_capability_allowed(capability: str) -> bool:
    normalized = capability.strip().lower()
    if normalized in PUBLIC_ALLOWED_EXACT:
        return True
    if any(segment in PUBLIC_BLOCKED_SEGMENTS for segment in normalized.split(".")):
        return False
    return normalized.endswith(PUBLIC_ALLOWED_SUFFIXES)
```

- [ ] **Step 4: Add a pre-execution flow gate**

Any flow command that declares a connector capability must be checked before network execution. Return:

```json
{
  "status": "blocked",
  "reason": "public_preview_read_only",
  "capability": "documents.invoice.create"
}
```

```python
def public_capability_gate(capability: str) -> dict[str, Any] | None:
    if is_public_capability_allowed(capability):
        return None
    return {
        "status": "blocked",
        "reason": "public_preview_read_only",
        "capability": capability,
    }
```

Call this gate before connector command dispatch and return the structured result without invoking an HTTP client when it is non-null.

- [ ] **Step 5: Run policy tests**

Run: `uv run pytest tests/test_connector_catalog.py tests/test_flows.py -q`

Expected: read capability cases pass and all mutation cases are blocked.

- [ ] **Step 6: Commit Task 6 files**

```bash
git add src/mercury_tools/connectors/catalog.py src/mercury_tools/flows/models.py src/mercury_tools/flows/runner.py tests/test_connector_catalog.py tests/test_flows.py
git commit -m "Enforce public preview connector policy"
```

### Task 7: Align Plugin Skills And Contest Documentation

**Files:**
- Modify: `plugins/mercury-finance/.codex-plugin/plugin.json`
- Modify: `plugins/mercury-finance/skills/connector-setup-guide-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/company-health-check-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/mercury-flow-runner/SKILL.md`
- Include: `plugins/mercury-finance/skills/flowaccount-connector-setup-th/SKILL.md`
- Modify: `README.md`
- Modify: `docs/JUDGE_QUICKSTART.md`
- Modify: `docs/REMOTE_DEPLOYMENT.md`
- Modify: `tests/test_plugin_package.py`
- Modify: `tests/test_runtime_skills.py`

**Interfaces:**
- Plugin skills consume public `workspace_id` tools and never mention `client_token` or Mercury Connect.
- Plugin manifest accurately advertises `Interactive` and `Read` for contest v1.

- [ ] **Step 1: Write failing plugin-content tests**

```python
def test_contest_plugin_uses_public_workspace_contract() -> None:
    plugin_root = Path("plugins/mercury-finance")
    combined = "\n".join(path.read_text() for path in plugin_root.rglob("SKILL.md"))
    assert "workspace_id" in combined
    assert "client_token" not in combined
    assert "Mercury Connect" not in combined


def test_plugin_capabilities_match_public_read_only_runtime() -> None:
    manifest = json.loads(Path("plugins/mercury-finance/.codex-plugin/plugin.json").read_text())
    assert manifest["interface"]["capabilities"] == ["Interactive", "Read"]
```

- [ ] **Step 2: Run plugin tests and confirm stale content fails**

Run: `uv run pytest tests/test_plugin_package.py tests/test_runtime_skills.py -q`

Expected: stale token/setup wording or `Write` capability causes failures.

- [ ] **Step 3: Rewrite setup skills around the public sequence**

The gated sequence is: create/reuse workspace ID, choose connector, choose environment, show presets, request only missing credential values, submit once, validate read-only access, then retrieve connector-specific context. Stay on the current step when validation fails.

Each connector setup skill must contain this exact state contract:

```markdown
1. Call `connector_status` with the current `workspace_id`.
2. If status is `requires_workspace`, call `create_public_workspace` and retain its `workspace_id` in this task.
3. Call `start_connector_setup` with `workspace_id`, connector ID, and environment.
4. Ask only for fields returned in `missing_fields` or `required_secret_fields`.
5. Call `submit_connector_credentials` once with the collected values.
6. Call `validate_connector_connection` and do not continue until it returns `connected_read_only` or `ready`.
7. Call `retrieve_workspace_context_pack` for connector-specific knowledge.
```

- [ ] **Step 4: Update plugin manifest and cachebuster**

Run:

```bash
python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py plugins/mercury-finance
python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance
```

Expected: plugin validation passes and version cachebuster changes.

- [ ] **Step 5: Update judge and deployment docs**

Document one GitHub marketplace command, plugin installation, new-task pickup, public workspace behavior, three demo prompts, and the lack of private isolation. Remove token, OAuth, Mercury Connect, browser dashboard, and local Hermes CLI instructions from the contest path.

- [ ] **Step 6: Run plugin tests and validation**

Run:

```bash
uv run pytest tests/test_plugin_package.py tests/test_runtime_skills.py -q
python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance
```

Expected: tests and validator pass.

- [ ] **Step 7: Commit Task 7 files**

```bash
git add plugins/mercury-finance README.md docs/JUDGE_QUICKSTART.md docs/REMOTE_DEPLOYMENT.md tests/test_plugin_package.py tests/test_runtime_skills.py
git commit -m "Align Mercury plugin with public contest MCP"
```

### Task 8: Verify, Publish, Ingest, And Smoke-Test Production

**Files:**
- Modify only if verification reveals a defect: `.github/workflows/*`, `Dockerfile`, `render.yaml`, `src/mercury_tools/remote.py`, `tests/test_remote_verify.py`

**Interfaces:**
- Proves marketplace installation, MCP initialization, public workspace flow, connector-filtered RAG, skill loading, dry-run execution, Supabase ingestion, and Render health.

- [ ] **Step 1: Run the complete local quality gate**

```bash
uv run ruff check .
uv run pytest -q
python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance
```

Expected: Ruff clean, all non-live tests pass, and plugin validation passes.

- [ ] **Step 2: Test marketplace installation in an isolated Codex home**

Create a temporary `CODEX_HOME`, add the GitHub marketplace with both sparse paths, install `mercury-finance@mercury-tools`, then run `codex mcp list`.

Expected: plugin status is `installed, enabled`; `mercury-tools` points to the Render `/mcp` URL with no bearer-token environment variable.

- [ ] **Step 3: Push the implementation commits**

Run: `git push origin main`

Expected: GitHub `main` contains the public MCP contract, plugin version, endpoint dictionaries, migrations, tests, and docs.

- [ ] **Step 4: Apply Supabase search migrations in order**

Apply `0003_match_knowledge_chunks_null_embedding.sql`, then `0004_match_knowledge_chunks_endpoint_terms.sql` to project `vbnlkqvauqwnjbxngkas`.

Expected: `match_knowledge_chunks` accepts connector filters and returns keyword matches even when embeddings are null.

- [ ] **Step 5: Ingest the committed wiki into Supabase**

Run the repository ingestion workflow or execute:

```bash
uv run mercury-tools ingest wiki --path ./wiki
```

Expected: FlowAccount and PEAK endpoint dictionaries are inserted or updated; unchanged documents are skipped by SHA-256.

- [ ] **Step 6: Wait for Render deployment and verify health**

Run:

```bash
uv run mercury-tools remote verify --url https://mercury-tools-mcp.onrender.com
```

Expected: health is `ok`, Supabase and hash embeddings are configured, MCP path is `/mcp`, authentication is not required, and readiness is true.

- [ ] **Step 7: Run a real MCP protocol smoke suite**

Initialize a Streamable HTTP session and verify:

1. `tools/list` contains public workspace tools and no MCP schema requires `client_token`.
2. `create_public_workspace` returns an ID with prefix `mw_`.
3. `list_connectors` exposes `client_id` and `client_secret` as required field names without values.
4. `search_knowledge("FlowAccount invoice endpoint")` returns FlowAccount sources only.
5. `run_accounting_skill("company-health-check-th", {"question": "company health"}, false)` returns the skill package.
6. `run_mercury_flow(flow_yaml="name: Smoke\n---\n- emitReport:\n    title: Smoke", dry_run=true)` returns `planned`.
7. `connector_status(workspace_id)` never returns `/root/.mercury-agent`.

Expected: every assertion passes over the deployed Render endpoint.

- [ ] **Step 8: Record final evidence**

Add the exact commit SHA, Render health result, tool count, marketplace install result, RAG source titles, and test summary to `docs/JUDGE_QUICKSTART.md` only if the values are stable and useful to judges. Do not add secrets, workspace IDs, credentials, or raw provider payloads.

---

## Plan Self-Review Result

- Spec coverage: marketplace installation, public MCP transport, public workspace routing, connector setup, RAG routing, citations, read-only policy, audit redaction, skills, Supabase ingestion, Render deployment, and production smoke testing each map to a task above.
- Placeholder scan: the plan contains no unresolved implementation marker.
- Type consistency: public MCP surfaces use `workspace_id: str`; internal compatibility code uses `dict[str, Any]` token payloads only inside the product store; connector filters use `dict[str, Any]`; flow environment overrides use `dict[str, Any] | None`.
- Scope: OAuth, private tenant isolation, production ERP mutations, billing, public-directory submission, and standalone web UX remain outside this contest plan.
