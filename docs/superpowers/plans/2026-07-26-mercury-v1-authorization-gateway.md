# Mercury V1 Authorization Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Mercury Finance `v1.0.0` as one hosted, OAuth-protected MCP that can connect one authorized workspace to FlowAccount or PEAK, run qualified invoice reads, and create qualified invoices only through immutable preview, explicit confirmation, provider dispatch, and sanitized audit.

**Architecture:** Keep the existing catalog, qualification, RAG, local execution, and reconciliation code as reusable foundations. Add a V1 hosted layer for Supabase OAuth identity, workspace bootstrap, encrypted provider credentials, tenant-isolated downstream Streamable HTTP MCP sessions, catalog-qualified public tools, and Supabase-backed preview/operation state. Preserve the current public demo behind feature flags until the V1 readiness gate passes; then switch the canonical `/mcp` business surface to required OAuth without introducing another repository or a second product-facing MCP.

**Tech Stack:** Python 3.11-3.13, MCP Python SDK/FastMCP 1.26, Starlette/Uvicorn, Pydantic 2, httpx, Supabase Auth/Postgres/PostgREST/RLS, PostgreSQL `pgcrypto`, PyJWT with asymmetric JWKS validation, `cryptography` AES-256-GCM, pytest/pytest-asyncio, Ruff, Render, MCP Apps HTML.

## Global Constraints

- The approved design is authoritative:
  `docs/superpowers/specs/2026-07-25-mercury-v1-authorization-gateway-design.md`.
- This is one V1 product. Do not create V2-V7 branches, a second hosted Mercury MCP, or another release-control repository.
- The Capability Catalog is the only execution authority. RAG and Skills can route or explain but can never authorize an endpoint, provider tool, schema version, or mutation.
- V1 enables only qualified reads and qualified `documents.{document_type}.create` capabilities. Update, patch, delete, void, payment, approval, email, share, attachment, master-data, and status-changing actions remain hidden.
- The release seed for each provider is exactly:
  `provider_profile.get`, `documents.invoice.list`,
  `documents.invoice.get`, and `documents.invoice.create`.
- Provider creates follow:
  `Draft -> Validate -> Thai HTML Preview -> Explicit confirmation -> Provider Create -> Sanitized Audit`.
- `run_accounting_skill` may run qualified reads and prepare previews; it must never dispatch a provider create.
- Provider credentials, OAuth tokens, raw provider payloads, personal identifiers, and tax identifiers must not enter Git, logs, model-visible MCP output, widget output, RAG, or general audit.
- Keep the current direct REST drivers and local SQLite execution tests during migration. They remain qualification/reference paths until the downstream MCP path passes certification; they are not the public V1 runtime.
- All schema and database changes are expand-first and backward compatible. Do not drop legacy token tables or public demo routes before the OAuth V1 switch and rollback proof.
- Every public MCP input and output is a closed JSON Schema. No tool may expose a bare or undocumented `object`.
- Every workspace-bound tool requires the explicit `workspace_id` returned by `get_mercury_context`.
- All monetary values crossing a public boundary are decimal strings, never binary floating-point values.
- A possible provider create dispatch is never retried blindly. Ambiguous results become `outcome_unknown` and require qualified reconciliation or manual review.
- Preview TTL is 30 minutes. Unconfirmed payloads are purged within 24 hours; confirmed operation payloads are retained for at most 30 days; sanitized audit is retained for one year.
- Do not bump package/plugin version to `1.0.0` or create the release tag until both providers pass all four seed capabilities and the owner-authorized production canary gate.
- Every task ends with focused tests and one commit. Do not combine unrelated tasks into a single commit.
- Before each commit run `git diff --check`; before release run the full verification matrix in Task 17.

## Execution Waves and Agent Allocation

The implementation remains one dependency graph. Parallel workers may operate only on the disjoint write sets below.

| Wave | Work | Recommended worker |
| --- | --- | --- |
| A | Tasks 1-3: configuration, OAuth identity, workspace bootstrap | Sol, high reasoning |
| B | Tasks 4-5: vault and downstream MCP runtime | Sol or Terra, high reasoning |
| C | Tasks 6-8: FlowAccount, PEAK, capability qualification | Terra, high reasoning |
| D | Tasks 9-11: public schemas, read tools, RAG/Skill boundaries | Terra, high reasoning |
| E | Tasks 12-14: hosted operations, dispatch/batch, Thai widget | Sol for state machine; Luna for widget after contracts freeze |
| F | Tasks 15-17: observability, plugin artifacts, certification/release | Luna for mechanical consistency; Sol for final gate |

Integration remains with the main agent. Workers must not modify another task's files, must report changed paths, and must not push or tag independently.

---

## Task 1: Freeze the Baseline and Add V1 Feature Configuration

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/mercury_tools/config.py`
- Create: `src/mercury_tools/v1/__init__.py`
- Create: `src/mercury_tools/v1/constants.py`
- Create: `tests/test_v1_config.py`
- Modify: `render.yaml`

The application stays on package version `0.3.1` during implementation. This task adds only dependencies and opt-in configuration.

- [ ] **Step 1: Write failing configuration tests**

Add tests proving:

```python
def test_v1_is_disabled_without_explicit_flag() -> None: ...
def test_v1_requires_canonical_https_resource_when_enabled() -> None: ...
def test_v1_rejects_missing_jwks_or_vault_key_configuration() -> None: ...
def test_provider_endpoint_overrides_are_server_only_https_urls() -> None: ...
def test_v1_preview_ttl_is_exactly_thirty_minutes() -> None: ...
```

Use environment isolation with `monkeypatch.delenv` and assert stable error codes instead of full secret-bearing messages.

- [ ] **Step 2: Run the focused test and confirm failure**

Run:

```bash
uv run pytest -q tests/test_v1_config.py
```

Expected: failure because the V1 settings and constants do not exist.

- [ ] **Step 3: Add dependencies and locked V1 settings**

Add dependency ranges and refresh `uv.lock`:

```toml
"PyJWT[crypto]>=2.10,<3",
"cryptography>=45,<47",
```

Add these fields to `Settings`:

```python
v1_enabled: bool
canonical_mcp_resource: str
supabase_auth_issuer: str
supabase_jwks_url: str
supabase_jwt_audience: str
vault_active_key: str
vault_active_key_version: str
vault_previous_key: str
vault_previous_key_version: str
flowaccount_mcp_sandbox_url: str
flowaccount_mcp_production_url: str
peak_mcp_uat_url: str
peak_mcp_production_url: str
provider_callback_base_url: str
```

Add fixed public constants:

```python
V1_VERSION = "1.0.0"
CANONICAL_MCP_RESOURCE = "https://mercury-tools-mcp.onrender.com/mcp"
PREVIEW_TTL_SECONDS = 1800
MAX_BATCH_DOCUMENTS = 25
```

`Settings.validate_v1()` must reject non-HTTPS hosted URLs, mismatched canonical resources, missing JWKS settings, invalid base64 32-byte AES keys, reused key versions, and provider URLs containing query strings or fragments.

- [ ] **Step 4: Add Render V1 environment declarations without enabling them**

Add secret-backed or explicit variables to `render.yaml`:

```yaml
MERCURY_V1_ENABLED: "false"
MERCURY_CANONICAL_MCP_RESOURCE: "https://mercury-tools-mcp.onrender.com/mcp"
SUPABASE_AUTH_ISSUER: "https://vbnlkqvauqwnjbxngkas.supabase.co/auth/v1"
SUPABASE_JWKS_URL: "https://vbnlkqvauqwnjbxngkas.supabase.co/auth/v1/.well-known/jwks.json"
SUPABASE_JWT_AUDIENCE: "https://mercury-tools-mcp.onrender.com/mcp"
MERCURY_VAULT_ACTIVE_KEY: sync false
MERCURY_VAULT_ACTIVE_KEY_VERSION: sync false
```

Provider resource URLs and previous-key rotation values remain `sync: false`. Keep `MERCURY_TOOLS_HTTP_REQUIRE_AUTH=false` until Task 16.

- [ ] **Step 5: Verify configuration and the existing baseline**

Run:

```bash
uv lock
uv run pytest -q tests/test_v1_config.py tests/test_http_app.py
uv run ruff check src/mercury_tools/config.py src/mercury_tools/v1 tests/test_v1_config.py
git diff --check
```

Expected: all pass; package version remains `0.3.1`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock render.yaml src/mercury_tools/config.py src/mercury_tools/v1 tests/test_v1_config.py
git commit -m "feat: add Mercury V1 runtime configuration"
```

---

## Task 2: Implement Mercury OAuth Principal Validation and Protected-Resource Metadata

**Files:**

- Create: `src/mercury_tools/auth/__init__.py`
- Create: `src/mercury_tools/auth/models.py`
- Create: `src/mercury_tools/auth/supabase_jwt.py`
- Create: `src/mercury_tools/auth/middleware.py`
- Create: `src/mercury_tools/auth/consent.py`
- Create: `src/mercury_tools/auth/templates/consent.html`
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/cloud/api.py`
- Create: `tests/test_auth_jwt.py`
- Create: `tests/test_protected_resource_routes.py`
- Create: `tests/test_mercury_consent.py`

- [ ] **Step 1: Write failing JWT and metadata tests**

Cover:

- valid asymmetric JWT with exact issuer, resource audience, expiry, subject, and `client_id`
- wrong issuer, audience, signature, expiry, or missing subject
- unknown `kid` triggers one bounded JWKS refresh
- missing/invalid bearer token returns `401` and an RFC 9728-compatible `WWW-Authenticate`
- authenticated but unauthorized request returns `403`
- root and path-compatible protected-resource metadata both name the canonical MCP resource and Supabase authorization server
- consent displays the requesting client, requested identity scopes, and Mercury workspace access
- consent rejects wildcard/non-HTTPS hosted redirect URIs and a mismatched OAuth transaction
- compatible third-party registration is discoverable through the Supabase authorization-server metadata
- health/legal/support routes remain public

The principal interface is:

```python
class MercuryPrincipal(BaseModel):
    subject: UUID
    client_id: str
    scopes: frozenset[str]
    token_id: str | None

class PrincipalResolver(Protocol):
    async def resolve(self, bearer_token: str) -> MercuryPrincipal: ...
```

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_auth_jwt.py tests/test_protected_resource_routes.py tests/test_mercury_consent.py
```

Expected: imports or assertions fail because V1 auth does not exist.

- [ ] **Step 3: Implement JWKS validation**

`SupabaseJwtValidator` must:

- cache JWKS by HTTP cache lifetime with a hard maximum
- permit only configured asymmetric algorithms
- validate `iss`, `aud`, `exp`, `nbf`, `sub`, and `client_id`
- never log or return a token
- raise closed exceptions carrying only:
  `mercury_auth_required`, `mercury_token_invalid`, or
  `mercury_scope_insufficient`

Do not reuse `create_client_token` or accept an `mc_` token in the V1 path.

- [ ] **Step 4: Add V1 authentication middleware**

Add `MercuryOAuthMiddleware` that stores `MercuryPrincipal` on
`request.state.mercury_principal`. It protects `/mcp` and V1 API/callback routes while exempting:

```text
/
/healthz
/readyz
/privacy
/terms
/support
/.well-known/oauth-protected-resource
/.well-known/oauth-protected-resource/mcp
```

Keep `BearerAuthMiddleware` for the legacy feature-flag path until Task 16.

- [ ] **Step 5: Publish protected-resource metadata**

The response must include:

```json
{
  "resource": "https://mercury-tools-mcp.onrender.com/mcp",
  "authorization_servers": [
    "https://vbnlkqvauqwnjbxngkas.supabase.co/auth/v1"
  ],
  "scopes_supported": ["openid", "email", "profile"],
  "bearer_methods_supported": ["header"]
}
```

Do not publish a client secret or copied bearer token.

- [ ] **Step 6: Add the first-party authorization and consent handoff**

Implement the Render-hosted authorization page configured for the Supabase OAuth
server. It displays the verified requesting client, `openid email profile`, the
Mercury resource, and workspace access before approval. It posts only the
opaque Supabase authorization transaction and the user's approve/deny choice.
It uses exact redirect URIs, `Cache-Control: no-store`,
`Referrer-Policy: no-referrer`, a narrow CSP, and no analytics or third-party
resources.

Supabase remains the token issuer and dynamic client registration endpoint.
Mercury must not implement a second token issuer or store host client secrets.

- [ ] **Step 7: Verify**

```bash
uv run pytest -q tests/test_auth_jwt.py tests/test_protected_resource_routes.py tests/test_mercury_consent.py tests/test_http_app.py
uv run ruff check src/mercury_tools/auth src/mercury_tools/mcp/server.py src/mercury_tools/cloud/api.py tests/test_auth_jwt.py tests/test_protected_resource_routes.py tests/test_mercury_consent.py
git diff --check
```

- [ ] **Step 8: Commit**

```bash
git add src/mercury_tools/auth src/mercury_tools/mcp/server.py src/mercury_tools/cloud/api.py tests/test_auth_jwt.py tests/test_protected_resource_routes.py tests/test_mercury_consent.py
git commit -m "feat: validate Mercury OAuth identities"
```

---

## Task 3: Add Idempotent Tenant and Workspace Bootstrap

**Files:**

- Create: `supabase/migrations/20260726100000_mercury_v1_identity.sql`
- Create: `src/mercury_tools/db/user_client.py`
- Create: `src/mercury_tools/workspaces/models.py`
- Create: `src/mercury_tools/workspaces/service.py`
- Modify: `src/mercury_tools/workspaces/__init__.py`
- Create: `src/mercury_tools/mcp/v1_schemas.py`
- Create: `src/mercury_tools/mcp/v1_tools.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Create: `tests/test_workspace_bootstrap.py`
- Create: `tests/integration/test_supabase_v1_workspace.py`

- [ ] **Step 1: Write failing service and migration contract tests**

Prove:

- first authenticated call creates one personal tenant, one default workspace, and one owner membership
- repeated and concurrent bootstrap returns the same workspace
- a user cannot request another tenant's workspace
- `get_mercury_context` returns no email, token, or provider credential
- all workspace-bound operations require an explicit UUID
- legacy rows remain readable and no legacy table is dropped

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_workspace_bootstrap.py
```

- [ ] **Step 3: Add the expand-only identity migration**

The migration must:

- create `mercury_tenants`
- add nullable `tenant_id` and `owner_auth_user_id` to `mercury_workspaces`
- add nullable `auth_user_id` and `tenant_id` to `mercury_workspace_members`
- make legacy `email` nullable only after adding `auth_user_id`
- add uniqueness for one personal tenant and one automatic default workspace per auth user
- add tenant/member indexes
- add RLS policies using `auth.uid()`
- add idempotent RPC `bootstrap_mercury_context()`
- preserve `mercury_client_tokens` and existing public-demo data

The RPC returns only UUIDs, display names, roles, and allowed next actions.

- [ ] **Step 4: Implement a user-scoped Supabase client**

`SupabaseUserClient` sends the end-user access token to PostgREST so RLS is active. The service-role client remains limited to migrations, publication, and explicitly reviewed server administration.

```python
class WorkspaceService:
    def bootstrap(self, principal: MercuryPrincipal, access_token: str) -> MercuryContext: ...
    def require_workspace(
        self,
        principal: MercuryPrincipal,
        access_token: str,
        workspace_id: UUID,
        required_role: WorkspaceRole,
    ) -> WorkspaceMembership: ...
```

The bearer token is request-scoped, `repr=False`, and never persisted.

- [ ] **Step 5: Add `get_mercury_context` behind the V1 flag**

Register a closed empty input schema and closed output schema. The output includes:

```text
status
active_workspace_id
memberships[]
next_allowed_actions[]
```

It excludes identity email and provider state.

- [ ] **Step 6: Verify unit and isolated database behavior**

```bash
uv run pytest -q tests/test_workspace_bootstrap.py tests/test_mcp_contract.py
uv run pytest -q tests/integration/test_supabase_v1_workspace.py -m integration
uv run ruff check src/mercury_tools/db/user_client.py src/mercury_tools/workspaces src/mercury_tools/mcp/v1_schemas.py src/mercury_tools/mcp/v1_tools.py src/mercury_tools/mcp/server.py tests/test_workspace_bootstrap.py
git diff --check
```

The integration test may run only against an isolated Supabase branch/test project. It must refuse the known production project reference.

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/20260726100000_mercury_v1_identity.sql src/mercury_tools/db/user_client.py src/mercury_tools/workspaces src/mercury_tools/mcp/v1_schemas.py src/mercury_tools/mcp/v1_tools.py src/mercury_tools/mcp/server.py tests/test_workspace_bootstrap.py tests/integration/test_supabase_v1_workspace.py
git commit -m "feat: bootstrap Mercury V1 workspaces"
```

---

## Task 4: Add Provider Connections and an Encrypted Credential Vault

**Files:**

- Create: `supabase/migrations/20260726101000_mercury_v1_provider_connections.sql`
- Create: `supabase/migrations/20260726102000_mercury_v1_credential_vault.sql`
- Create: `src/mercury_tools/credentials/__init__.py`
- Create: `src/mercury_tools/credentials/models.py`
- Create: `src/mercury_tools/credentials/vault.py`
- Create: `src/mercury_tools/providers/__init__.py`
- Create: `src/mercury_tools/providers/models.py`
- Create: `src/mercury_tools/providers/store.py`
- Create: `tests/test_credential_vault.py`
- Create: `tests/test_provider_connection_store.py`

- [ ] **Step 1: Write failing vault and tenancy tests**

Cover:

- AES-256-GCM encrypt/decrypt
- random nonce produces different ciphertext for identical plaintext
- additional authenticated data binds tenant, user, workspace, provider, company/merchant, environment, credential type, and key version
- cross-tenant, cross-provider, and cross-environment decrypt fails
- active and previous key rotation works; unknown key version fails closed
- `repr`, serialization, logs, errors, and audit never contain plaintext
- provider connection lookup is tenant/workspace-bound
- disconnect deletes usable envelope material idempotently

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_credential_vault.py tests/test_provider_connection_store.py
```

- [ ] **Step 3: Add provider-connection schema**

Create:

- `mercury_provider_connections`
- `mercury_provider_setup_attempts`
- `mercury_provider_oauth_states`

Connection rows store provider, environment, account/company display identity, authorization method, granted permissions, readiness, revision, validation timestamp, and encrypted-envelope reference. They never store raw credentials in JSON metadata.

Setup/OAuth state rows store only hashed random tokens, PKCE verifier ciphertext when required, same-user/workspace bindings, expiry, consumed timestamp, and non-secret callback state.

- [ ] **Step 4: Add the credential-envelope schema**

Create `mercury_provider_credential_envelopes` with:

```text
id
tenant_id
workspace_id
auth_user_id
connection_id
provider
environment
credential_type
key_version
nonce
ciphertext
aad_hash
created_at
rotated_at
revoked_at
```

Revoke access from `anon` and normal authenticated table access. Expose only narrowly scoped RPCs used after application membership checks. Add no plaintext shadow columns.

- [ ] **Step 5: Implement vault and store interfaces**

```python
class CredentialVault:
    def seal(self, binding: CredentialBinding, plaintext: bytes) -> CredentialEnvelope: ...
    def open(self, binding: CredentialBinding, envelope: CredentialEnvelope) -> bytearray: ...
    def rotate(self, binding: CredentialBinding, envelope: CredentialEnvelope) -> CredentialEnvelope: ...

class ProviderConnectionStore:
    def create_attempt(..., token_hash: str, expires_at: datetime) -> SetupAttempt: ...
    def consume_attempt(..., token_hash: str) -> SetupAttempt: ...
    def save_connection(..., envelopes: Sequence[CredentialEnvelope]) -> ProviderConnection: ...
    def list_for_workspace(...) -> tuple[ProviderConnectionSummary, ...]: ...
    def disconnect(...) -> DisconnectResult: ...
```

Plaintext remains request-scoped, is never persisted outside the encrypted
envelope, and mutable copies are cleared in `finally` on a best-effort basis.
Do not claim guaranteed Python-process memory zeroization.

- [ ] **Step 6: Verify**

```bash
uv run pytest -q tests/test_credential_vault.py tests/test_provider_connection_store.py tests/test_redaction.py
uv run ruff check src/mercury_tools/credentials src/mercury_tools/providers/models.py src/mercury_tools/providers/store.py tests/test_credential_vault.py tests/test_provider_connection_store.py
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/20260726101000_mercury_v1_provider_connections.sql supabase/migrations/20260726102000_mercury_v1_credential_vault.sql src/mercury_tools/credentials src/mercury_tools/providers/models.py src/mercury_tools/providers/store.py tests/test_credential_vault.py tests/test_provider_connection_store.py
git commit -m "feat: store encrypted provider connections"
```

---

## Task 5: Build the Tenant-Isolated Downstream MCP Runtime

**Files:**

- Create: `src/mercury_tools/providers/base.py`
- Create: `src/mercury_tools/providers/manifest.py`
- Create: `src/mercury_tools/providers/streamable_mcp.py`
- Create: `src/mercury_tools/providers/registry.py`
- Create: `catalog/global/flowaccount/driver.json`
- Create: `catalog/global/peak/driver.json`
- Create: `tests/test_provider_driver_manifest.py`
- Create: `tests/test_provider_mcp_runtime.py`

- [ ] **Step 1: Write failing manifest and transport tests**

Prove:

- manifests reject unknown keys, secrets, HTTP URLs, query/fragment URLs, model-supplied endpoints, unrecognized auth adapters, and unsupported MCP protocol versions
- resource URI is resolved from server config and bound to a hash
- runtime performs `initialize` before `tools/list` or `tools/call`
- each operation uses a distinct session scoped to one provider connection
- session headers are never reused across tenants/connections/environments
- discovery/read/create timeout classes are 30/30/60 seconds after a 5-second connect timeout
- create calls are never automatically retried after possible dispatch
- raw downstream errors, headers, tool names, and session IDs are sanitized

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_provider_driver_manifest.py tests/test_provider_mcp_runtime.py
```

- [ ] **Step 3: Define the hosted driver contract**

```python
class ProviderDriver(Protocol):
    provider: ProviderId

    async def discover(
        self,
        connection: ProviderConnection,
    ) -> ProviderDiscovery: ...

    async def validate_connection(
        self,
        connection: ProviderConnection,
    ) -> ProviderValidation: ...

    async def call(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id: UUID,
    ) -> ProviderCallResult: ...
```

The public result carries only normalized data, provider status class, sanitized provider identifier, and dispatch certainty.

- [ ] **Step 4: Implement Streamable HTTP session handling**

Use the MCP SDK's Streamable HTTP client and `ClientSession`. Capture and honor provider session headers only within the request-scoped context manager. Add explicit exception classes:

```text
provider_unavailable
provider_auth_required
provider_schema_changed
provider_timeout_pre_dispatch
provider_outcome_unknown
provider_response_invalid
```

- [ ] **Step 5: Add secretless driver manifests**

Each `driver.json` declares:

- provider
- supported environments
- server configuration key for resource URI
- `streamable_http`
- auth adapter
- allowed scopes/permissions
- timeout class
- discovered tool-to-normalized-capability mappings

Do not copy provider OAuth endpoints from RAG or model input into these files.

- [ ] **Step 6: Verify**

```bash
uv run pytest -q tests/test_provider_driver_manifest.py tests/test_provider_mcp_runtime.py tests/test_connector_driver_contract.py
uv run ruff check src/mercury_tools/providers tests/test_provider_driver_manifest.py tests/test_provider_mcp_runtime.py
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add src/mercury_tools/providers catalog/global/flowaccount/driver.json catalog/global/peak/driver.json tests/test_provider_driver_manifest.py tests/test_provider_mcp_runtime.py
git commit -m "feat: add downstream provider MCP runtime"
```

---

## Task 6: Implement FlowAccount Provider OAuth and Company Binding

**Files:**

- Create: `src/mercury_tools/providers/flowaccount.py`
- Create: `src/mercury_tools/providers/oauth.py`
- Modify: `src/mercury_tools/cloud/api.py`
- Modify: `src/mercury_tools/providers/registry.py`
- Create: `tests/test_flowaccount_provider_oauth.py`
- Create: `tests/test_flowaccount_provider_driver.py`

- [ ] **Step 1: Write failing OAuth lifecycle tests**

Cover:

- `start_provider_connection` creates a random ten-minute, single-use state and PKCE verifier bound to principal, tenant, workspace, provider, environment, and callback
- provider protected-resource and authorization-server metadata are discovered from the configured downstream MCP resource
- challenged scopes are intersected with the reviewed manifest allowlist
- callback rejects wrong user, workspace, environment, state, redirect URI, expiry, replay, or provider company mismatch
- tokens and dynamic client secret are encrypted
- transient verifier material is consumed and removed
- downstream discovery and `provider_profile.get` validation must pass before `ready`
- refresh occurs once before dispatch when supported

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_flowaccount_provider_oauth.py tests/test_flowaccount_provider_driver.py
```

- [ ] **Step 3: Implement OAuth primitives**

`ProviderOAuthService` performs:

```python
async def start(
    principal: MercuryPrincipal,
    workspace_id: UUID,
    provider: Literal["flowaccount"],
    environment: Literal["sandbox", "production"],
) -> ProviderAuthorizationStart: ...

async def complete(
    principal: MercuryPrincipal,
    callback: OAuthCallback,
) -> ProviderConnectionSummary: ...
```

Use 256-bit state, S256 PKCE, exact callback URI, no wildcard redirect, no token in query output, and no endpoint inference.

- [ ] **Step 4: Add the FlowAccount callback route**

Register:

```text
GET /auth/providers/flowaccount/callback
```

On success, show only provider, selected company display name, environment, readiness, and a safe return-to-host instruction. Add `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

- [ ] **Step 5: Implement FlowAccount MCP normalization**

Map qualified provider discovery and calls through `driver.json`. The existing direct REST `FlowAccountDriver` remains unchanged and is not called by the V1 hosted connection.

- [ ] **Step 6: Verify**

```bash
uv run pytest -q tests/test_flowaccount_provider_oauth.py tests/test_flowaccount_provider_driver.py tests/test_flowaccount_driver.py
uv run ruff check src/mercury_tools/providers/flowaccount.py src/mercury_tools/providers/oauth.py src/mercury_tools/cloud/api.py tests/test_flowaccount_provider_oauth.py tests/test_flowaccount_provider_driver.py
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add src/mercury_tools/providers/flowaccount.py src/mercury_tools/providers/oauth.py src/mercury_tools/cloud/api.py src/mercury_tools/providers/registry.py tests/test_flowaccount_provider_oauth.py tests/test_flowaccount_provider_driver.py
git commit -m "feat: connect FlowAccount through provider OAuth"
```

---

## Task 7: Implement PEAK Secure Credential Handoff

**Files:**

- Create: `src/mercury_tools/providers/peak.py`
- Create: `src/mercury_tools/providers/peak_setup.py`
- Create: `src/mercury_tools/providers/templates/peak-setup.html`
- Modify: `src/mercury_tools/cloud/api.py`
- Modify: `src/mercury_tools/providers/registry.py`
- Create: `tests/test_peak_secure_setup.py`
- Create: `tests/test_peak_provider_driver.py`

- [ ] **Step 1: Write failing handoff security tests**

Prove:

- setup URL carries its random token in the fragment only
- stored token is SHA-256 hash only and expires after ten minutes
- the page requires the same Mercury principal before rendering the form
- inline JavaScript immediately calls `history.replaceState`
- no local/session storage, analytics, third-party resource, or referrer leakage
- strict CSP, origin validation, CSRF, `no-store`, and `no-referrer` are present
- User Token, Connect ID, and Connect Key never appear in URL, log, exception, response, or audit
- replay, wrong user/workspace/provider/environment, and expired setup fail closed
- validation and encryption happen before the attempt is consumed in one transaction
- PEAK application code comes from Render configuration, not user input

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_peak_secure_setup.py tests/test_peak_provider_driver.py
```

- [ ] **Step 3: Implement the one-time setup page and POST**

Routes:

```text
GET  /auth/providers/peak/setup
POST /auth/providers/peak/setup
```

The GET reads the fragment through first-party inline JavaScript and exchanges it for a server-issued CSRF-bound form session. The POST accepts exactly:

```text
setup_session
csrf_token
user_token
connect_id
connect_key
```

Secret inputs use password fields and never repopulate.

- [ ] **Step 4: Implement the PEAK provider-key adapter**

Decrypt credentials only inside the request scope, derive required transport headers server-side, call the configured PEAK MCP, validate merchant binding using qualified `provider_profile.get`, then clear plaintext buffers.

Keep the direct REST `PeakDriver` unchanged as a qualification/reference implementation.

- [ ] **Step 5: Implement disconnect semantics**

Local disconnect always destroys the usable envelope. If provider revocation is unavailable, return:

```json
{
  "status": "provider_revocation_required",
  "local_credentials_deleted": true
}
```

with reviewed PEAK instructions and no secret.

- [ ] **Step 6: Verify**

```bash
uv run pytest -q tests/test_peak_secure_setup.py tests/test_peak_provider_driver.py tests/test_peak_driver.py
uv run ruff check src/mercury_tools/providers/peak.py src/mercury_tools/providers/peak_setup.py src/mercury_tools/cloud/api.py tests/test_peak_secure_setup.py tests/test_peak_provider_driver.py
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add src/mercury_tools/providers/peak.py src/mercury_tools/providers/peak_setup.py src/mercury_tools/providers/templates/peak-setup.html src/mercury_tools/cloud/api.py src/mercury_tools/providers/registry.py tests/test_peak_secure_setup.py tests/test_peak_provider_driver.py
git commit -m "feat: add secure PEAK provider setup"
```

---

## Task 8: Add Capability Qualification as the Sole Execution Gate

**Files:**

- Create: `supabase/migrations/20260726103000_mercury_v1_catalog_qualification.sql`
- Create: `src/mercury_tools/qualification/provider_mcp.py`
- Create: `src/mercury_tools/qualification/artifacts.py`
- Modify: `src/mercury_tools/catalog/models.py`
- Modify: `src/mercury_tools/db/catalog.py`
- Modify: `catalog/global/flowaccount/actions.json`
- Modify: `catalog/global/peak/actions.json`
- Create: `catalog/global/flowaccount/qualifications/.gitkeep`
- Create: `catalog/global/peak/qualifications/.gitkeep`
- Create: `scripts/qualify_provider_mcp.py`
- Create: `tests/test_v1_capability_catalog.py`
- Create: `tests/test_provider_mcp_qualification.py`

- [ ] **Step 1: Write failing lifecycle and authority tests**

Cover:

- state transitions are only:
  `discovered_unreviewed -> schema_validated -> nonproduction_qualified -> enabled -> disabled|superseded`
- a changed schema creates a new immutable version and never inherits `enabled`
- RAG, Skill text, provider discovery alone, or legacy capability observations cannot authorize execution
- only reads and document creates can become enabled in V1
- production requires non-production evidence plus owner-authorized canary
- provider/environment/action/version selection is exact
- missing evidence returns `insufficient_evidence` or `capability_unavailable`

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_v1_capability_catalog.py tests/test_provider_mcp_qualification.py
```

- [ ] **Step 3: Expand the catalog schema**

Add tables or columns for:

```text
provider_tool_name
normalized_capability
input_schema
output_schema
schema_hash
response_shape_hash
required_permissions
qualification_state
qualification_evidence_uri
evidence_expires_at
production_canary_at
disable_reason
```

Preserve immutable existing action versions.

- [ ] **Step 4: Normalize the four seed capabilities**

Add or alias exact source-controlled identities for both providers:

```text
provider_profile.get
documents.invoice.list
documents.invoice.get
documents.invoice.create
```

For FlowAccount, map the existing company/profile action to
`provider_profile.get`. For PEAK, add a normalized provider-profile action backed by the discovered and reviewed profile tool; do not infer it from the REST `/user` path at runtime.

- [ ] **Step 5: Generate sanitized qualification artifacts**

`qualify_provider_mcp.py` writes:

```text
catalog/global/flowaccount/qualifications/{capability_version_sha256}.json
catalog/global/peak/qualifications/{capability_version_sha256}.json
```

Each artifact contains provider/environment/company hash, capability/version, runner version, timestamps, schema hashes, response-shape hash, input hash, sanitized result identifier, checks, reviewer, expiry, and pass/fail. It contains no credential or raw accounting body.

- [ ] **Step 6: Verify**

```bash
uv run pytest -q tests/test_v1_capability_catalog.py tests/test_provider_mcp_qualification.py tests/test_catalog_publisher.py tests/test_catalog_sanitization.py
uv run ruff check src/mercury_tools/qualification/provider_mcp.py src/mercury_tools/qualification/artifacts.py scripts/qualify_provider_mcp.py tests/test_v1_capability_catalog.py tests/test_provider_mcp_qualification.py
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/20260726103000_mercury_v1_catalog_qualification.sql src/mercury_tools/qualification src/mercury_tools/catalog/models.py src/mercury_tools/db/catalog.py catalog/global scripts/qualify_provider_mcp.py tests/test_v1_capability_catalog.py tests/test_provider_mcp_qualification.py
git commit -m "feat: gate provider execution by qualification"
```

---

## Task 9: Publish Stable V1 Provider and Capability Tools

**Files:**

- Modify: `src/mercury_tools/mcp/v1_schemas.py`
- Modify: `src/mercury_tools/mcp/v1_tools.py`
- Create: `src/mercury_tools/mcp/v1_errors.py`
- Modify: `src/mercury_tools/mcp/contracts.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Create: `tests/test_v1_mcp_tool_contract.py`
- Modify: `tests/test_mcp_review_contract.py`

- [ ] **Step 1: Write failing MCP schema tests**

Require exact schemas for:

```text
get_mercury_context
list_accounting_providers
start_provider_connection
list_provider_connections
connector_status
list_provider_capabilities
get_capability_schema
disconnect_provider
```

Tests must reject:

- untyped objects
- unknown fields
- missing UUID formats
- a shared environment enum that accepts the wrong provider environment
- undocumented nullable fields
- inaccurate read/open-world/destructive/idempotent annotations

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_v1_mcp_tool_contract.py tests/test_mcp_review_contract.py
```

- [ ] **Step 3: Define closed models and response envelope**

Use discriminated provider/environment branches:

```python
class FlowAccountConnectionStart(BaseModel):
    workspace_id: UUID
    provider: Literal["flowaccount"]
    environment: Literal["sandbox", "production"]

class PeakConnectionStart(BaseModel):
    workspace_id: UUID
    provider: Literal["peak"]
    environment: Literal["uat", "production"]
```

The standard success envelope is closed and uses versioned `$ref` models. The closed error union contains every code from Section 16 of the approved spec.

- [ ] **Step 4: Register stable tools behind `MERCURY_V1_ENABLED`**

All tools resolve principal and explicit workspace membership before store or provider access. `connector_status` reports exact readiness and missing qualification without performing provider-wide calls.

- [ ] **Step 5: Keep legacy tools isolated**

When V1 is disabled, the current public demo contract remains unchanged. When V1 is enabled, legacy workspace creation/token/profile tools are not listed by `/mcp`, though temporary non-MCP compatibility routes may remain until Task 16.

- [ ] **Step 6: Verify**

```bash
uv run pytest -q tests/test_v1_mcp_tool_contract.py tests/test_mcp_contract.py tests/test_mcp_review_contract.py tests/test_connector_mcp_tools.py
uv run python scripts/review_mcp_contract.py
uv run ruff check src/mercury_tools/mcp tests/test_v1_mcp_tool_contract.py
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add src/mercury_tools/mcp tests/test_v1_mcp_tool_contract.py tests/test_mcp_review_contract.py
git commit -m "feat: publish stable Mercury V1 tools"
```

---

## Task 10: Execute Qualified Provider Reads and Generate Typed Wrappers

**Files:**

- Create: `src/mercury_tools/mcp/generated_tools.py`
- Create: `src/mercury_tools/execution/hosted/__init__.py`
- Create: `src/mercury_tools/execution/hosted/read_service.py`
- Modify: `src/mercury_tools/mcp/v1_tools.py`
- Modify: `src/mercury_tools/mcp/v1_schemas.py`
- Create: `tests/test_hosted_read_execution.py`
- Create: `tests/test_generated_provider_tools.py`

- [ ] **Step 1: Write failing read-execution tests**

Prove:

- exact enabled capability version is required
- connection provider, company/merchant, environment, and workspace must match
- generated wrappers have stable Mercury-owned names and closed input/output schemas
- wrappers never expose raw downstream tool names
- reads retry only with bounded backoff when safe
- schema drift disables only the affected version
- normalized output is sanitized and includes capability/version evidence
- `tools/list_changed` is emitted after branch publication/supersession

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_hosted_read_execution.py tests/test_generated_provider_tools.py
```

- [ ] **Step 3: Implement hosted read orchestration**

```python
class HostedReadService:
    async def execute(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        inputs: BaseModel,
    ) -> ProviderReadEnvelope: ...
```

Resolution order is workspace -> connection -> exact catalog version -> driver mapping -> downstream call -> response schema -> sanitizer -> audit.

- [ ] **Step 4: Generate exact wrappers**

Generate only enabled versions, including:

```text
mercury_flowaccount_provider_profile_get
mercury_flowaccount_invoice_list
mercury_flowaccount_invoice_get
mercury_peak_provider_profile_get
mercury_peak_invoice_list
mercury_peak_invoice_get
```

Create capabilities get `*_create_prepare` wrappers later in Task 13; no wrapper dispatches a create.

- [ ] **Step 5: Verify**

```bash
uv run pytest -q tests/test_hosted_read_execution.py tests/test_generated_provider_tools.py tests/test_v1_mcp_tool_contract.py
uv run ruff check src/mercury_tools/execution/hosted src/mercury_tools/mcp/generated_tools.py tests/test_hosted_read_execution.py tests/test_generated_provider_tools.py
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add src/mercury_tools/execution/hosted src/mercury_tools/mcp/generated_tools.py src/mercury_tools/mcp/v1_tools.py src/mercury_tools/mcp/v1_schemas.py tests/test_hosted_read_execution.py tests/test_generated_provider_tools.py
git commit -m "feat: execute qualified provider reads"
```

---

## Task 11: Enforce Workspace-Bound RAG and Published Skill Routing

**Files:**

- Create: `supabase/migrations/20260726104000_mercury_v1_workspace_knowledge_scope.sql`
- Modify: `src/mercury_tools/db/supabase.py`
- Modify: `src/mercury_tools/rag/routing.py`
- Modify: `src/mercury_tools/skills/catalog.py`
- Modify: `src/mercury_tools/skills/routing.py`
- Modify: `src/mercury_tools/mcp/v1_tools.py`
- Modify: `src/mercury_tools/mcp/v1_schemas.py`
- Create: `tests/test_v1_knowledge_scope.py`
- Create: `tests/test_v1_skill_routing.py`

- [ ] **Step 1: Write failing tenant and authority tests**

Cover:

- global reviewed content is visible to every authorized workspace
- published workspace content is visible only to its members
- draft, rejected, and another tenant's content is absent
- both RLS and application query enforce workspace visibility
- FTS is the required production path; hash embedding is test-only
- unknown knowledge filters are rejected
- Skills resolve exact published `skill_id` and `skill_version`
- Skills can require capabilities but cannot enable them
- missing fact, source, schema, or capability returns `insufficient_evidence`
- host-connected evidence can be passed as typed input without another service's OAuth token

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_v1_knowledge_scope.py tests/test_v1_skill_routing.py
```

- [ ] **Step 3: Expand knowledge and Skill publication schema**

Add workspace ownership/visibility and published-version identity without rewriting global seed documents. Apply RLS only after global backfill and indexes complete.

- [ ] **Step 4: Publish exact V1 knowledge and Skill tools**

`search_knowledge` and `retrieve_context_pack` require `workspace_id`. Their filter object accepts only:

```text
jurisdiction
provider
doc_type
review_status
effective_on
source_id
capability_version
```

`run_accounting_skill` is a generated discriminated union from published Skill schemas. It may call `HostedReadService` and may prepare a create preview after Task 13, but never calls provider create.

- [ ] **Step 5: Verify**

```bash
uv run pytest -q tests/test_v1_knowledge_scope.py tests/test_v1_skill_routing.py tests/test_knowledge_routing.py tests/test_skill_routing.py tests/test_mcp_rag_routing.py
uv run ruff check src/mercury_tools/db/supabase.py src/mercury_tools/rag src/mercury_tools/skills src/mercury_tools/mcp/v1_tools.py tests/test_v1_knowledge_scope.py tests/test_v1_skill_routing.py
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/20260726104000_mercury_v1_workspace_knowledge_scope.sql src/mercury_tools/db/supabase.py src/mercury_tools/rag src/mercury_tools/skills src/mercury_tools/mcp/v1_tools.py src/mercury_tools/mcp/v1_schemas.py tests/test_v1_knowledge_scope.py tests/test_v1_skill_routing.py
git commit -m "feat: scope Mercury knowledge and Skills"
```

---

## Task 12: Persist Hosted Immutable Previews and Operations

**Files:**

- Create: `supabase/migrations/20260726105000_mercury_v1_operations_previews.sql`
- Create: `src/mercury_tools/execution/hosted/models.py`
- Create: `src/mercury_tools/execution/hosted/store.py`
- Create: `src/mercury_tools/execution/hosted/preview_service.py`
- Modify: `src/mercury_tools/execution/models.py`
- Create: `tests/test_hosted_preview_store.py`
- Create: `tests/test_document_preview.py`

- [ ] **Step 1: Write failing state-machine tests**

Prove:

- preview binds tenant, user, workspace, connection, provider, company/merchant, environment, capability/version, connection revision, payload hash, expiry, and `state_version`
- only exact qualified document-create schemas are accepted
- total, VAT, discount, withholding, and currency cross-checks are deterministic decimal operations
- payload is immutable; edit creates a new preview
- stale state version, expired preview, changed connection/version, or changed hash fails
- one preview contains 1-25 documents with unique `client_item_id` and payload hash
- encrypted business payload is separate from sanitized preview summary
- unconfirmed expiry and retention metadata are correct

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_hosted_preview_store.py tests/test_document_preview.py
```

- [ ] **Step 3: Add preview/operation schema**

Create:

- `mercury_document_previews`
- `mercury_preview_items`
- `mercury_operations`
- `mercury_operation_items`
- `mercury_operation_events`

Use unique constraints for workspace/connection/payload hash and serialized state transitions. Store encrypted provider payloads and sanitized summaries separately. Add purge indexes and tenant RLS.

- [ ] **Step 4: Adapt reusable local primitives**

Reuse canonical hashing, catalog binding, risk policy, request validation, and state concepts from:

```text
execution/models.py
execution/policy.py
execution/request_builder.py
```

Do not import `RepositoryContext`, `LocalRequestStore`, local SQLite, or repository credentials into hosted code. Set hosted preview TTL to 30 minutes while preserving the local 15-minute contract for backward compatibility.

- [ ] **Step 5: Implement `prepare_document_create`**

The generated discriminated union supports:

```text
mode: single + exact document
mode: batch + 1..25 exact documents
```

The tool persists the immutable request, returns preview identity/summary/warnings/review points, and performs no provider call.

- [ ] **Step 6: Verify**

```bash
uv run pytest -q tests/test_hosted_preview_store.py tests/test_document_preview.py tests/test_request_store.py tests/test_execution_policy.py
uv run ruff check src/mercury_tools/execution/hosted src/mercury_tools/execution/models.py tests/test_hosted_preview_store.py tests/test_document_preview.py
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/20260726105000_mercury_v1_operations_previews.sql src/mercury_tools/execution/hosted src/mercury_tools/execution/models.py tests/test_hosted_preview_store.py tests/test_document_preview.py
git commit -m "feat: persist hosted document previews"
```

---

## Task 13: Confirm, Dispatch, Reconcile, and Batch Provider Creates

**Files:**

- Create: `src/mercury_tools/execution/hosted/operation_service.py`
- Create: `src/mercury_tools/execution/hosted/batch_service.py`
- Create: `src/mercury_tools/execution/hosted/reconciliation_service.py`
- Modify: `src/mercury_tools/mcp/v1_tools.py`
- Modify: `src/mercury_tools/mcp/v1_schemas.py`
- Modify: `src/mercury_tools/mcp/generated_tools.py`
- Create: `tests/test_document_operations.py`
- Create: `tests/test_document_batch.py`
- Create: `tests/test_hosted_outcome_reconciliation.py`

- [ ] **Step 1: Write failing confirmation and replay tests**

Cover:

- only `confirmation="CONFIRM_CREATE"` is accepted
- confirm input cannot replace provider payload
- latest `state_version` and every original binding are rechecked
- concurrent confirmation serializes to one operation
- repeated confirmation returns the existing result
- pre-dispatch failure may retry the same operation
- possible dispatch plus timeout/5xx/transport loss/malformed response becomes `outcome_unknown`
- unknown outcome is never replayed automatically
- qualified exact lookup reconciles one match to success, zero to unknown, and multiple to manual review
- audit insertion failure before dispatch blocks dispatch

- [ ] **Step 2: Write failing batch tests**

Cover:

- two-document success
- duplicate item ID or payload hash rejected before preview
- native batch is selected only with full exact batch qualification
- otherwise sequential dispatch is used
- deterministic rejection stops undispatched children
- ambiguous child stops later children and is not replayed
- result has one closed state per `client_item_id`
- no rollback claim for already-created provider documents

- [ ] **Step 3: Confirm tests fail**

```bash
uv run pytest -q tests/test_document_operations.py tests/test_document_batch.py tests/test_hosted_outcome_reconciliation.py
```

- [ ] **Step 4: Implement the operation state machine**

States:

```text
prepared
awaiting_confirmation
dispatching
succeeded
failed_pre_dispatch
provider_rejected
outcome_unknown
needs_manual_review
expired
cancelled
```

`OperationService.confirm_and_dispatch()` locks by workspace, connection, and payload hash, records confirmation, creates operation identities, writes `dispatching` before the call, and passes the Mercury operation UUID as provider idempotency key when the qualified capability supports it.

- [ ] **Step 5: Implement batch and reconciliation**

Sequential fallback dispatches in deterministic item order and stops according to the approved spec. Reconciliation may call only the exact lookup capability version recorded in qualification evidence.

- [ ] **Step 6: Register create tools**

Add:

```text
prepare_document_create
confirm_document_create
get_operation_status
mercury_flowaccount_invoice_create_prepare
mercury_peak_invoice_create_prepare
```

Create-prepare wrappers never dispatch. `confirm_document_create` is open-world and destructive in MCP annotations but idempotent at the Mercury operation boundary.

- [ ] **Step 7: Verify**

```bash
uv run pytest -q tests/test_document_operations.py tests/test_document_batch.py tests/test_hosted_outcome_reconciliation.py tests/test_executor.py tests/test_reconciliation.py tests/test_v1_mcp_tool_contract.py
uv run ruff check src/mercury_tools/execution/hosted src/mercury_tools/mcp tests/test_document_operations.py tests/test_document_batch.py tests/test_hosted_outcome_reconciliation.py
git diff --check
```

- [ ] **Step 8: Commit**

```bash
git add src/mercury_tools/execution/hosted src/mercury_tools/mcp tests/test_document_operations.py tests/test_document_batch.py tests/test_hosted_outcome_reconciliation.py
git commit -m "feat: confirm and dispatch document creates"
```

---

## Task 14: Build the Thai MCP Apps Preview Widget and Text Fallback

**Files:**

- Create: `src/mercury_tools/mcp/widgets/__init__.py`
- Create: `src/mercury_tools/mcp/widgets/mercury-document-preview-v1.html`
- Create: `src/mercury_tools/mcp/widget_tools.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/mcp/v1_tools.py`
- Create: `tests/test_preview_widget_contract.py`
- Create: `tests/test_preview_widget_accessibility.py`
- Create: `tests/browser/test_preview_widget.py`

- [ ] **Step 1: Write failing resource and surface tests**

Prove:

- resource URI is exactly `ui://widget/mercury-document-preview-v1.html`
- MIME type is `text/html;profile=mcp-app`
- `_meta.ui.*` and compatibility `openai/outputTemplate` match the approved spec
- `structuredContent`, `content`, and `_meta["mercury/preview"]` are closed, separate surfaces
- model-visible content does not receive the full provider payload
- non-widget fallback includes workspace, preview, state version, totals, warnings, and exact next action
- confirmation calls the same MCP tool with the displayed state version
- stale state rerenders rather than guessing

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_preview_widget_contract.py tests/test_preview_widget_accessibility.py
```

- [ ] **Step 3: Implement `render_document_preview`**

The tool loads the stored authorized preview and emits:

1. `mercury.preview.summary.v1` in `structuredContent`
2. concise Thai/English-safe narration in `content`
3. `mercury.preview.widget.v1` in `_meta["mercury/preview"]`

No provider call occurs.

- [ ] **Step 4: Build the self-contained widget**

Requirements:

- semantic Thai document table
- Sarabun/Thai-capable system fallback without remote fonts
- no Thai letter spacing
- tabular monetary numerals
- responsive desktop/mobile
- keyboard-visible focus
- A4 print stylesheet
- inline versioned CSS/JS only
- no browser storage
- bridge calls only `confirm_document_create`
- no direct FlowAccount/PEAK network call

- [ ] **Step 5: Run browser verification**

Run:

```bash
uv run pytest -q tests/test_preview_widget_contract.py tests/test_preview_widget_accessibility.py
uv run pytest -q tests/browser/test_preview_widget.py
```

Expected screenshots cover desktop, mobile, long Thai counterparty names, 25-item batch summary, warning state, expired state, and print layout. Confirm no overlap, horizontal clipping, or inaccessible focus.

- [ ] **Step 6: Verify lint and commit**

```bash
uv run ruff check src/mercury_tools/mcp tests/test_preview_widget_contract.py tests/test_preview_widget_accessibility.py
git diff --check
git add src/mercury_tools/mcp tests/test_preview_widget_contract.py tests/test_preview_widget_accessibility.py tests/browser/test_preview_widget.py
git commit -m "feat: add Thai document preview widget"
```

---

## Task 15: Add Readiness, Rate Limits, Retention, and Safe Observability

**Files:**

- Create: `supabase/migrations/20260726106000_mercury_v1_retention_and_rls.sql`
- Create: `src/mercury_tools/observability.py`
- Create: `src/mercury_tools/rate_limits.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/db/supabase.py`
- Create: `scripts/purge_v1_payloads.py`
- Create: `tests/test_v1_observability.py`
- Create: `tests/test_v1_rate_limits.py`
- Create: `tests/test_v1_retention.py`

- [ ] **Step 1: Write failing operational tests**

Cover:

- `/healthz` is process-only
- `/readyz` checks database, authorization metadata, active vault key, and manifest validity without provider-wide calls
- logs/audit contain correlation ID, tool/capability version, provider/environment, status class, latency, retry count, state transitions, and sanitized identifiers
- no raw input/provider body/credential appears
- rate limits apply to authorization, setup, discovery, prepare, and confirm
- purge deletes expired unconfirmed payloads within 24 hours and confirmed encrypted payloads after 30 days, preserving one-year sanitized audit

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_v1_observability.py tests/test_v1_rate_limits.py tests/test_v1_retention.py
```

- [ ] **Step 3: Implement safe metrics and readiness**

Use low-cardinality provider/environment/capability labels. Never place workspace ID, user ID, company name, operation payload, or provider document body in metric labels.

- [ ] **Step 4: Finish RLS only after backfill**

The final migration:

- verifies tenant/workspace backfill
- enables restrictive RLS on V1 tables
- installs retention indexes/functions
- revokes broad table access
- leaves legacy tables intact for rollback

- [ ] **Step 5: Verify**

```bash
uv run pytest -q tests/test_v1_observability.py tests/test_v1_rate_limits.py tests/test_v1_retention.py tests/test_audit.py tests/test_redaction.py
uv run ruff check src/mercury_tools/observability.py src/mercury_tools/rate_limits.py scripts/purge_v1_payloads.py tests/test_v1_observability.py tests/test_v1_rate_limits.py tests/test_v1_retention.py
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/20260726106000_mercury_v1_retention_and_rls.sql src/mercury_tools/observability.py src/mercury_tools/rate_limits.py src/mercury_tools/mcp/server.py src/mercury_tools/db/supabase.py scripts/purge_v1_payloads.py tests/test_v1_observability.py tests/test_v1_rate_limits.py tests/test_v1_retention.py
git commit -m "feat: harden Mercury V1 operations"
```

---

## Task 16: Prepare the One-Click OAuth Plugin and Release Gate

**Files:**

- Modify: `plugins/mercury-finance/.codex-plugin/plugin.json`
- Modify: `plugins/mercury-finance/.mcp.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `chatgpt-app-submission.json`
- Modify: `submission/openai-plugin/listing.json`
- Modify: `submission/openai-plugin/release-notes.md`
- Modify: `submission/openai-plugin/starter-prompts.json`
- Modify: `submission/openai-plugin/test-cases.json`
- Create: `deployment/mercury-oauth-clients.public.json`
- Create: `scripts/set_release_version.py`
- Modify: `render.yaml`
- Modify: `tests/test_plugin_distribution.py`
- Modify: `tests/test_openai_plugin_submission.py`
- Create: `tests/test_v1_artifact_consistency.py`
- Create: `scripts/smoke_clean_plugin_install.py`

- [ ] **Step 1: Write failing artifact-consistency tests**

Require:

- all public artifacts use one product name, canonical MCP URL, and the same package version
- no artifact says `authentication: none`, “stores no ERP credentials,” or “never calls an ERP”
- no copied bearer token, Supabase service key, Render secret, provider credential, or OAuth client secret
- public OAuth client record contains only client ID, exact redirect URI, authorization server, and MCP resource
- wildcard and non-HTTPS hosted redirects fail validation
- first protected call starts OAuth without manual token entry
- One-click install does not require local Python, Supabase credentials, Mercury token, or LLM API key

- [ ] **Step 2: Confirm tests fail**

```bash
uv run pytest -q tests/test_plugin_distribution.py tests/test_openai_plugin_submission.py tests/test_v1_artifact_consistency.py
```

- [ ] **Step 3: Update plugin and submission content**

Describe Mercury as one hosted accounting and ERP authorization gateway with cited knowledge, provider connection, qualified reads, and controlled document creates. Keep connector-neutral positioning while listing FlowAccount and PEAK as V1 providers.

Update tool annotations to reflect real effects. `confirm_document_create` is mutating/open-world/destructive and Mercury-idempotent. `disconnect_provider` is destructive and idempotent.

- [ ] **Step 4: Add the public OAuth client record**

Generate the file from the verified Supabase registration response, not by hand.
It contains the current package version, canonical MCP resource, Supabase
authorization server, and one entry per registered host with the actual
non-secret client ID and exact HTTPS redirect URIs. The generator refuses empty,
example, wildcard, localhost-for-public-host, or secret-bearing values.

The file must not be committed until tests prove no secret field is present. If
a host registration has not been issued, the release gate remains blocked
rather than inserting a fake value.

- [ ] **Step 5: Switch V1 authentication in Render preview**

Set:

```yaml
MERCURY_V1_ENABLED: "true"
MERCURY_TOOLS_HTTP_REQUIRE_AUTH: "true"
```

only in the Render preview/staging environment first. Production remains unchanged until Task 17 passes.

- [ ] **Step 6: Add a deterministic release-version updater and gate**

`scripts/set_release_version.py` updates the package, plugin, marketplace,
submission, public OAuth record, and release notes in one operation. Invoking
it with `1.0.0` must fail unless both providers have current passing artifacts
for all four seed capabilities and the certification document records an
owner-authorized canary. Do not invoke it in this task.

- [ ] **Step 7: Verify**

```bash
uv run pytest -q tests/test_plugin_distribution.py tests/test_openai_plugin_submission.py tests/test_v1_artifact_consistency.py
uv run python scripts/validate_plugin.py
uv run python scripts/smoke_clean_plugin_install.py
uv build --wheel --sdist --out-dir dist
git diff --check
```

- [ ] **Step 8: Commit**

```bash
git add plugins/mercury-finance .agents/plugins/marketplace.json chatgpt-app-submission.json submission/openai-plugin deployment/mercury-oauth-clients.public.json render.yaml tests/test_plugin_distribution.py tests/test_openai_plugin_submission.py tests/test_v1_artifact_consistency.py scripts/set_release_version.py scripts/smoke_clean_plugin_install.py
git commit -m "feat: prepare one-click OAuth plugin"
```

Package and public artifact versions remain `0.3.1` after this task. Do not tag.

---

## Task 17: Certify Hosted V1, Run Owner Canary, and Release

**Files:**

- Modify: `pyproject.toml`
- Modify: `src/mercury_tools/__init__.py`
- Modify: `plugins/mercury-finance/.codex-plugin/plugin.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `chatgpt-app-submission.json`
- Modify: `submission/openai-plugin/listing.json`
- Modify: `submission/openai-plugin/release-notes.md`
- Modify: `deployment/mercury-oauth-clients.public.json`
- Create: `tests/integration/test_v1_hosted_oauth.py`
- Create: `tests/integration/test_v1_flowaccount_certification.py`
- Create: `tests/integration/test_v1_peak_certification.py`
- Create: `tests/integration/test_v1_document_create.py`
- Create: `scripts/certify_mercury_v1.py`
- Create: `docs/releases/mercury-v1-certification.md`
- Modify: `README.md`
- Modify: `render.yaml`

- [ ] **Step 1: Add integration tests with explicit environment guards**

Tests must refuse production by default. Provider certification uses:

- FlowAccount sandbox
- PEAK UAT or a dedicated owner-authorized test merchant
- no live operating ledger

The production canary requires:

```text
MERCURY_V1_OWNER_CANARY=CONFIRM_OWNER_AUTHORIZED_CANARY
```

plus explicit provider, company/merchant hash, capability version, and a separately confirmed minimal document fixture.

- [ ] **Step 2: Apply migrations to an isolated Supabase branch**

Run the migration chain in timestamp order and verify:

- legacy data remains
- bootstrap and RLS pass
- vault tables expose no plaintext
- previews/operations and retention constraints pass
- rollback to the previous Render build remains possible

- [ ] **Step 3: Deploy and smoke the Render preview**

Verify:

```text
GET /healthz
GET /readyz
GET /.well-known/oauth-protected-resource
MCP initialize
MCP tools/list
first protected tool -> OAuth
get_mercury_context
```

No provider-wide call occurs in readiness.

- [ ] **Step 4: Certify FlowAccount**

Run the four seed capabilities in sandbox:

```text
provider_profile.get
documents.invoice.list
documents.invoice.get
documents.invoice.create
```

The create must pass prepare, Thai preview, explicit confirmation, provider result, operation status, audit, and duplicate-confirmation protection.

- [ ] **Step 5: Certify PEAK**

Run the same four seed capabilities in UAT or the dedicated test merchant. If PEAK cannot supply a safe non-production/dedicated tenant, the release remains blocked; do not silently mark the capability enabled.

- [ ] **Step 6: Run failure and batch certification**

Prove:

- revoked/expired auth
- wrong company/merchant
- schema drift isolation
- concurrent confirmation
- deterministic rejection
- timeout after possible dispatch
- malformed response
- partial batch
- ambiguous child outcome without replay
- reconciliation or manual review

- [ ] **Step 7: Run the full local verification matrix**

```bash
uv sync --extra dev --frozen
uv run ruff check .
uv run pytest -q --ignore=tests/integration
uv run python scripts/review_mcp_contract.py
uv run python scripts/validate_plugin.py
uv run python scripts/smoke_hosted_plugin.py --repo-root .
uv run python scripts/smoke_clean_plugin_install.py
uv build --wheel --sdist --out-dir dist
git diff --check
```

Expected: all commands pass.

- [ ] **Step 8: Scan source, history, build, and artifacts for secrets**

Run the repository's secret scanner plus explicit patterns over:

```text
Git history
working tree
dist/
CI logs/artifacts
qualification artifacts
plugin/submission bundles
```

Any provider credential, bearer token, Supabase service key, database password, Render secret, or OAuth client secret blocks release and triggers rotation before proceeding.

- [ ] **Step 9: Perform the owner-authorized production canary**

Run one minimal qualified read and one explicitly approved create for each provider owner account. Record only hashes, provider result identifiers, capability versions, timestamps, and sanitized outcomes in `docs/releases/mercury-v1-certification.md`.

If either provider lacks owner authorization or a safe create fixture, stop. Do not infer success from sandbox/UAT.

- [ ] **Step 10: Record certification and set every artifact to `1.0.0`**

Write the sanitized certification record, then run:

```bash
uv run python scripts/set_release_version.py 1.0.0
```

The command must update every public artifact atomically and refuse to run if
the certification gate is incomplete.

- [ ] **Step 11: Commit and verify the exact release candidate**

```bash
git add pyproject.toml uv.lock src/mercury_tools/__init__.py plugins/mercury-finance .agents/plugins/marketplace.json chatgpt-app-submission.json submission/openai-plugin deployment/mercury-oauth-clients.public.json tests/integration scripts/certify_mercury_v1.py docs/releases/mercury-v1-certification.md README.md render.yaml
git commit -m "release: Mercury Finance v1.0.0"
uv sync --extra dev --frozen
uv run ruff check .
uv run pytest -q --ignore=tests/integration
uv run python scripts/review_mcp_contract.py
uv run python scripts/validate_plugin.py
uv run python scripts/smoke_clean_plugin_install.py
uv build --wheel --sdist --out-dir dist
git diff --check
```

Re-run the secret scan from Step 8 against the exact commit and `dist/`.

- [ ] **Step 12: Enable production V1**

Set production:

```text
MERCURY_V1_ENABLED=true
MERCURY_TOOLS_HTTP_REQUIRE_AUTH=true
```

Push the exact release commit, deploy that commit, re-run hosted
OAuth/context/read/preview/confirm smoke tests, then verify legacy business
tools are no longer listed on the V1 MCP surface.

- [ ] **Step 13: Tag and publish only the deployed certified commit**

```bash
git tag -a v1.0.0 -m "Mercury Finance v1.0.0"
git push origin v1.0.0
```

Create the GitHub release from the exact tag and attach only the verified wheel/sdist and public plugin artifacts. Confirm Render production reports the same commit SHA and version.

---

## Final Acceptance Traceability

| Approved acceptance criterion | Primary tasks |
| --- | --- |
| One-click install without local runtime/secrets | 2, 16, 17 |
| Mercury OAuth on first protected use | 2, 3, 16, 17 |
| Idempotent default workspace | 3 |
| FlowAccount OAuth/company binding | 5, 6, 17 |
| PEAK secure setup/merchant binding | 4, 5, 7, 17 |
| Both providers expose seed reads | 8-10, 17 |
| Both providers expose seed create | 8, 12-14, 17 |
| Preview/confirm/provider result/audit | 12-15, 17 |
| No duplicate on repeated/concurrent confirmation | 12, 13, 17 |
| Ambiguous creates are not replayed | 13, 17 |
| Credentials absent from all public surfaces/artifacts | 2, 4-7, 14-17 |
| Schema drift isolates one version | 8-10, 13, 17 |
| Thai widget and non-widget fallback | 14, 17 |
| Production canary is owner-authorized | 8, 16, 17 |

## Completion Definition

This plan is complete only when all Task 17 gates pass and the deployed canonical MCP reports the released `v1.0.0` commit. A green unit suite without both provider certifications is not a releasable V1. A public Hosted MCP that cannot complete OAuth, workspace bootstrap, qualified reads, immutable preview, explicit confirmation, and one certified create per provider remains a preview build and must not be tagged `v1.0.0`.
