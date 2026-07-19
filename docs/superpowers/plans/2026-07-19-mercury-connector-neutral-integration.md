# Mercury Connector-Neutral Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Mercury v0.3.0 as a connector-neutral Accounting and ERP platform whose public plugin installs one hosted Mercury MCP, routes portable accounting Skills by normalized capability, and retains a separate advanced local path for reviewed ERP reads and approval-gated mutations.

**Architecture:** The hosted Mercury MCP owns connector discovery, sanitized workspace profiles, capability routing, cited accounting knowledge, Skills, and flow planning. Native provider MCP/OAuth sessions remain owned by the AI host; reviewed API drivers and Local Bridge integrations execute only in the local Mercury runtime. Connector manifests describe connection modes and evidence-backed capability states, while static MCP annotations accurately distinguish reads, additive writes, destructive writes, and external effects.

**Tech Stack:** Python 3.11+, FastMCP/MCP 1.26, Pydantic 2.13, httpx, SQLite, Supabase/Postgres, pytest, uv, Codex plugin metadata, Render Streamable HTTP.

## Global Constraints

- Do not put ERP credentials, OAuth tokens, bearer tokens, API keys, personal identifiers, or usable secret examples in Git, Supabase, RAG, audit output, Skills, MCP arguments, or test snapshots.
- Keep one public Mercury MCP entry in the plugin. Do not bundle FlowAccount, PEAK, or another provider MCP into the core plugin.
- Native provider MCP calls are host-level handoffs. Mercury returns ordered plans and capability requirements; it does not claim to call another MCP server through the host.
- Public tools may store only sanitized connector profile metadata and evidence references. API-driver credentials stay in repository-local secure state.
- Keep exact connector, company, environment, action version, payload hash, trusted-host, SSRF, redirect, replay, idempotency, and `outcome_unknown` controls.
- A provider's read-only limitation blocks only that provider mode and capability. It must not create a Mercury-wide production-write prohibition.
- Ordinary create/update operations receive one host approval. Sensitive operations receive one elevated host approval. No mutation bypasses immutable preview and payload-hash binding.
- Tool annotations describe business behavior. Append-only operational audit logging does not turn a read into a user-visible mutation.
- Keep compatibility Python helpers for superseded public tool names through v0.3.x, but do not register ambiguous compatibility helpers as MCP tools.
- All tests are networkless by default. Live provider and Render checks run only in their explicitly gated integration stages.

---

### Task 1: Replace the vendor-wide read gate with connection-mode capability manifests

**Files:**
- Modify: `src/mercury_tools/connectors/catalog.py`
- Modify: `src/mercury_tools/mcp/schemas.py`
- Modify: `tests/test_connector_catalog.py`
- Modify: `tests/test_connector_mcp_tools.py`

- [ ] **Step 1: Add failing catalog tests for connection modes and factual provider readiness**

Add tests that assert:

```python
def test_connector_catalog_is_mode_aware_and_connector_neutral() -> None:
    flow = connector_by_id("flowaccount")
    peak = connector_by_id("peak")
    express = connector_by_id("express")
    custom = connector_by_id("custom")
    generic = connector_by_id("generic_mcp")

    assert set(flow.connection_mode_ids) == {"native_mcp", "api_driver"}
    assert peak.connection_mode_ids == ["api_driver"]
    assert express.connection_mode_ids == ["local_bridge"]
    assert custom.connection_mode_ids == ["api_driver"]
    assert generic.connection_mode_ids == ["native_mcp"]


def test_flowaccount_native_mcp_read_only_does_not_block_api_driver_writes() -> None:
    flow = connector_by_id("flowaccount")

    assert flow.capability_state("native_mcp", "documents.invoice.list") == "declared"
    assert flow.capability_state("native_mcp", "documents.invoice.create") == "provider_unavailable"
    assert flow.capability_state("api_driver", "documents.invoice.create") == "not_validated"
```

Also assert every public catalog row contains `display_name`, `connection_modes`, `auth_modes`, `supported_environments`, `capability_source`, `provider_capability_status`, `setup_defaults`, `local_bridge_requirement`, and `last_reviewed_at`. Assert no connector is selected by default.

- [ ] **Step 2: Run the focused tests and confirm the old flat manifest fails**

Run:

```bash
uv run pytest -q tests/test_connector_catalog.py tests/test_connector_mcp_tools.py -k 'mode_aware or native_mcp_read_only or list_connectors'
```

Expected: failures because `connection_mode_ids`, `capability_state`, and `generic_mcp` do not exist and the old catalog still exposes `blocked_capabilities` from a global read-only policy.

- [ ] **Step 3: Introduce exact connection and capability types**

In `src/mercury_tools/connectors/catalog.py`, add:

```python
class ConnectionMode(StrEnum):
    NATIVE_MCP = "native_mcp"
    API_DRIVER = "api_driver"
    LOCAL_BRIDGE = "local_bridge"


class CapabilityState(StrEnum):
    DECLARED = "declared"
    OBSERVED = "observed"
    ENABLED = "enabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NOT_AUTHORIZED = "not_authorized"
    NOT_VALIDATED = "not_validated"
    VALIDATION_FAILED = "validation_failed"
    POLICY_CONFIRMATION_REQUIRED = "policy_confirmation_required"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    LOCAL_BRIDGE_REQUIRED = "local_bridge_required"


class CapabilityClass(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class ConnectorModeManifest:
    mode: ConnectionMode
    status: str
    auth_modes: tuple[str, ...]
    supported_environments: tuple[str, ...]
    capability_source: str
    provider_capability_status: Mapping[str, CapabilityState]
    capability_aliases: Mapping[str, tuple[str, ...]]
    setup_defaults: Mapping[str, str] = field(default_factory=dict)
    official_mcp_url: str | None = None
    provider_setup_url: str | None = None
    local_bridge_requirement: str | None = None
```

In `__post_init__`, defensively copy and freeze `provider_capability_status`,
`capability_aliases`, and `setup_defaults` before storing them. A frozen dataclass
must not expose mutable dictionaries through its `Mapping` fields. Add tests that
both direct mutation and mutation of the caller-owned source dictionaries cannot
change a catalog manifest after construction.

Change `ConnectorManifest` to store `display_name`, `connection_modes`, and `last_reviewed_at`. Add `connection_mode(mode)`, `connection_mode_ids`, `capability_state(mode, capability)`, `provider_capabilities(mode, normalized_capability)`, and a connector-neutral `public_summary()`.

`capability_aliases` maps a normalized Mercury capability to one or more provider actions. For example, FlowAccount maps `company.read -> company.info.read` and `documents.invoice.read -> documents.invoice.get`; PEAK maps the same normalized names to its reviewed catalog actions. Reject aliases whose provider action is absent from that mode's declared catalog.

Keep `name`, `environments`, `preset`, and `required_secret_fields` as deprecated Python properties for v0.3.x callers. Task 1 is model-layer only: retain `PUBLIC_ALLOWED_SUFFIXES`, `PUBLIC_BLOCKED_SEGMENTS`, `is_public_capability_allowed`, and `public_capability_gate` temporarily as deprecated Python compatibility helpers, and do not add new routing dependencies on them. Task 3 must remove every runtime routing dependency from `server.py` and `flows/runner.py` after profile persistence is available.

- [ ] **Step 4: Populate the initial catalog without overstating readiness**

Use these factual mode states:

| Connector | Mode | Status | Initial capability policy |
| --- | --- | --- | --- |
| FlowAccount | `native_mcp` | `available` | documented reads `declared`; provider-unpublished writes `provider_unavailable` |
| FlowAccount | `api_driver` | `reviewed` | reads and writes `not_validated` until environment evidence is present |
| PEAK | `api_driver` | `reviewed` | catalog actions `not_validated` until environment evidence is present |
| Express | `local_bridge` | `needs_validation` | declared actions `local_bridge_required` |
| Custom ERP | `api_driver` | `draft` | imported actions `not_validated` |
| Generic MCP | `native_mcp` | `user_supplied` | discovered tools `declared` until observed |

For FlowAccount native MCP, set `provider_setup_url` to `https://flowaccount.com/en/help-center/category/ai-connector-mcp` and `official_mcp_url` to the provider-published `https://mcp.flowaccount.com/mcp`. Keep its documented capabilities read-only. Preserve the reviewed API-driver token URLs separately in that mode's setup defaults, and add an exact URL assertion so the catalog cannot silently regress to an invented or stale endpoint.

- [ ] **Step 5: Update public connector IDs and assertions**

Extend `ConnectorId` in `src/mercury_tools/mcp/schemas.py` with `generic_mcp`.
The public `ConnectorEnvironment` literal must cover every environment declared
by the reviewed catalog, including Generic MCP's `user_supplied`; a catalog
entry must never be advertised if its lifecycle schema cannot select it. Update
catalog tests so public summaries contain mode-specific capability states and
never return a single vendor-wide `blocked_capabilities` list.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_connector_catalog.py tests/test_connector_mcp_tools.py
```

Expected: all connector catalog and public summary tests pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/mercury_tools/connectors/catalog.py src/mercury_tools/mcp/schemas.py tests/test_connector_catalog.py tests/test_connector_mcp_tools.py
git commit -m "refactor: model connector modes and capability states"
```

---

### Task 2: Persist sanitized connector profiles and portable Skill requirements

**Files:**
- Create: `supabase/migrations/20260719120000_connector_neutral_profiles.sql`
- Create: `tests/test_connector_neutral_profile_migration.py`
- Modify: `src/mercury_tools/db/product.py`
- Modify: `src/mercury_tools/cloud/models.py`
- Modify: `src/mercury_tools/cloud/api.py`
- Modify: `src/mercury_tools/cloud/client.py`
- Modify: `src/mercury_tools/mcp/schemas.py`
- Modify: `tests/test_product_fallback.py`
- Modify: `tests/test_connector_setup.py`
- Modify: `tests/test_cloud_secret_removal.py`
- Modify: `tests/test_cloud_api.py`
- Modify: `tests/test_cloud_client.py`
- Modify: `tests/test_plugin_package.py`

- [ ] **Step 1: Write migration tests before SQL**

Assert the new migration:

```python
assert "connection_mode text" in sql
assert "company_ref text" in sql
assert "external_server_name text" in sql
assert "capability_states jsonb" in sql
assert "evidence_source text" in sql
assert "validated_at timestamptz" in sql
assert "required_capabilities jsonb" in sql
assert "revoke all" in sql
assert "grant all" in sql
```

Also assert check constraints allow only `native_mcp`, `api_driver`, and `local_bridge`; capability state JSON must be an object; and anonymous/authenticated roles receive no direct table access.
Assert the old three-column uniqueness constraint is replaced by `(workspace_id, connector_id, connection_mode, environment)` so native MCP and API-driver profiles cannot overwrite one another.

- [ ] **Step 2: Run the migration test and confirm it fails because the migration is absent**

```bash
uv run pytest -q tests/test_connector_neutral_profile_migration.py
```

Expected: failure for missing migration file.

- [ ] **Step 3: Add the forward-only Supabase migration**

The migration must:

```sql
alter table public.mercury_connector_profiles
  add column if not exists connection_mode text not null default 'api_driver',
  add column if not exists company_ref text,
  add column if not exists external_server_name text,
  add column if not exists capability_states jsonb not null default '{}'::jsonb,
  add column if not exists evidence_source text,
  add column if not exists validated_at timestamptz;

alter table public.mercury_skill_catalog
  add column if not exists required_capabilities jsonb not null default '[]'::jsonb;
```

Add constraints for allowed connection modes, JSON object/array shape, bounded safe text, and no obvious secret-bearing keys in `capability_states`. Backfill existing profiles as `api_driver`, retain their environment and display name, and derive only neutral status from existing non-secret metadata. Do not migrate any credential value.

The migration must be rerun-safe. Let the new column default backfill legacy
rows only when the column is first added; never run an unconditional update that
rewrites an existing `native_mcp` or `local_bridge` profile to `api_driver`.
Translate only explicit legacy status `requires_credentials` to
`needs_validation`, and never reset a new neutral status on rerun.

Before adding constraints, replace legacy profile `metadata` with a strictly
allowlisted non-secret projection (or `{}` when it cannot be proven safe). Add a
database-level capability-state validator that checks every key and value, not
only exact top-level key membership. It must reject embedded secret-bearing names
such as `provider_access_token`, credential/bearer/API-key variants, tax-ID or
email fields, and response-body/payload fields. Capability-state values must be
limited to the reviewed state enum. Define the validator with a fixed
`search_path = pg_catalog`, revoke its default execution privilege from
`PUBLIC`, and grant execution only to `service_role`.

Drop `mercury_connector_profiles_workspace_id_connector_id_environment_key` when present and add a named unique constraint over `(workspace_id, connector_id, connection_mode, environment)`. Update PostgREST upsert conflict targets and fallback profile keys to use all four fields.

- [ ] **Step 4: Define typed validation evidence**

In `src/mercury_tools/mcp/schemas.py`, add strict models:

```python
class CapabilityObservation(StrictMcpInput):
    capability: str = Field(pattern=CAPABILITY_PATTERN, max_length=200)
    state: Literal[
        "observed",
        "provider_unavailable",
        "not_authorized",
        "validation_failed",
        "environment_mismatch",
    ]


class ConnectorValidationEvidence(StrictMcpInput):
    source: Literal[
        "native_mcp_safe_read",
        "api_driver_safe_probe",
        "local_bridge_safe_probe",
    ]
    status: Literal["succeeded", "failed"]
    observed_at: datetime
    evidence_ref: str = Field(pattern=r"^evidence_[0-9a-z_-]{8,128}$")
    provider_tool_name: str | None = Field(default=None, max_length=200)
    capabilities: list[CapabilityObservation] = Field(min_length=1, max_length=500)
```

The evidence model contains no response body, URL credential, token, tax ID, email, or arbitrary metadata object.

- [ ] **Step 5: Make profile status mode- and evidence-aware**

Replace `connector_profile_status_from_metadata()` with:

```python
def connector_profile_status(
    connector_id: str,
    connection_mode: str,
    capability_states: Mapping[str, str],
    *,
    evidence_source: str | None,
    validated_at: str | None,
) -> str:
    ...
```

Return only `requires_authorization`, `requires_local_setup`, `needs_validation`, `ready_read_only`, or `ready_read_write`. Require the exact evidence source for the selected mode (`native_mcp_safe_read`, `api_driver_safe_probe`, or `local_bridge_safe_probe`) before any ready state. Every observed capability used to produce either ready state must resolve to an action declared in the selected connector and connection-mode catalog; unknown capabilities and draft/custom modes with no reviewed catalog remain `needs_validation`. A native profile with matching catalog-declared read evidence becomes `ready_read_only`. Return `ready_read_write` only for an observed mutation capability that exists in the selected connector mode's reviewed catalog and only when that mode is eligible for API-driver writes. A native safe-read observation must never create write readiness. Failed, mismatched, missing, or unknown evidence never becomes ready.

Extend `PUBLIC_CONNECTOR_METADATA_KEYS` only with non-secret setup fields. Return normalized profile columns at the top level and redact all unrecognized metadata recursively.

- [ ] **Step 6: Make Skill rows capability-driven**

Add `required_capabilities` to `SKILL_CATALOG_SEED`, cloud models, API allowlists, fallback state, plugin contract fixtures, and Supabase row selection. Generic Skills must set `required_connectors=[]`; provider-specific setup Skills may retain `required_connectors=["flowaccount"]` or `["peak"]`. Cloud clients reading an older server response may default a missing `required_capabilities` field to `[]`, while current server and plugin contract responses must emit the field explicitly.

- [ ] **Step 7: Run storage and migration tests**

```bash
uv run pytest -q \
  tests/test_connector_neutral_profile_migration.py \
  tests/test_product_fallback.py \
  tests/test_connector_setup.py \
  tests/test_cloud_secret_removal.py \
  tests/test_cloud_api.py \
  tests/test_cloud_client.py \
  tests/test_plugin_package.py
```

Expected: all pass and serialized profiles contain no legacy vault key or credential value.

- [ ] **Step 8: Commit Task 2**

```bash
git add supabase/migrations/20260719120000_connector_neutral_profiles.sql tests/test_connector_neutral_profile_migration.py src/mercury_tools/db/product.py src/mercury_tools/cloud/models.py src/mercury_tools/cloud/api.py src/mercury_tools/cloud/client.py src/mercury_tools/mcp/schemas.py tests/test_product_fallback.py tests/test_connector_setup.py tests/test_cloud_secret_removal.py tests/test_cloud_api.py tests/test_cloud_client.py tests/test_plugin_package.py
git commit -m "feat: persist connector modes and capability evidence"
```

---

### Task 3: Replace setup stubs with an explicit public connector lifecycle

**Files:**
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/mcp/schemas.py`
- Modify: `src/mercury_tools/db/product.py`
- Modify: `src/mercury_tools/flows/runner.py`
- Modify: `tests/test_connector_mcp_tools.py`
- Modify: `tests/test_mcp_contract.py`
- Modify: `tests/test_http_app.py`
- Modify: `tests/test_flows.py`

- [ ] **Step 1: Add failing public lifecycle contract tests**

The public connector surface must expose:

```python
EXPECTED_CONNECTOR_TOOLS = {
    "list_connectors",
    "get_connector_setup",
    "link_connector_profile",
    "validate_connector_connection",
    "connector_status",
    "connector_capabilities",
    "unlink_connector_profile",
}
```

Test these exact signatures:

```python
get_connector_setup(connector_id, connection_mode=None)
link_connector_profile(
    workspace_id,
    connector_id,
    connection_mode,
    environment,
    company_ref=None,
    company_name=None,
    external_server_name=None,
)
validate_connector_connection(
    workspace_id,
    connector_id,
    connection_mode,
    environment,
    evidence,
)
connector_status(workspace_id, connector_id=None)
connector_capabilities(workspace_id, connector_id, connection_mode, environment)
unlink_connector_profile(
    workspace_id,
    connector_id,
    connection_mode,
    environment,
    confirm="unlink",
)
```

Assert `workspace_id` is required for every workspace-scoped tool. Assert no tool schema contains `client_id`, `client_secret`, `api_key`, `access_token`, `authorization`, or an unconstrained object.

Add negative lifecycle tests proving that failed evidence cannot produce a ready
profile, validation cannot create a profile before the exact profile is linked,
unlink followed by relink cannot resurrect historical evidence, and the legacy
HTTP setup route rejects extra/secret/provider-body/LAN fields and requires an
explicit connector mode. Assert safe reviewed setup defaults are returned,
non-ready resolver reasons are preserved, and the unlink MCP schema constrains
`confirm` to the literal `unlink`. A rejected legacy HTTP body must never echo
the submitted input value, including an unknown credential field. Every legacy
HTTP response must include `deprecated_tool="start_connector_setup"` and
`replacement_tool="link_connector_profile"`.

Add runtime regression tests proving capability routing is selected by
`connector_id + connection_mode + environment + persisted evidence`, not by a
capability-name suffix. FlowAccount `native_mcp` invoice creation must return
`provider_unavailable`; FlowAccount `api_driver` invoice creation must return
`not_validated` before evidence and become eligible for the downstream mutation
workflow only after matching validation evidence. Assert the registered
`connector_capabilities` tool requires all four profile coordinates and does not
return the legacy `read_capabilities`, `blocked_capabilities`, or
`read_only_validation` fields.

- [ ] **Step 2: Run contract tests and observe missing tools**

```bash
uv run pytest -q tests/test_connector_mcp_tools.py tests/test_mcp_contract.py -k 'connector or workspace_scoped'
```

Expected: failures because the server still exposes `start_connector_setup` and catalog-only `connector_capabilities(connector_id)`.

- [ ] **Step 3: Implement catalog setup guidance**

`get_connector_setup` returns one mode or all modes with:

```json
{
  "status": "ok",
  "connector_id": "flowaccount",
  "connection_modes": [{
    "mode": "native_mcp",
    "next_action": "connect_provider_mcp",
    "provider_setup_url": "https://flowaccount.com/en/help-center/category/ai-connector-mcp",
    "required_user_values": [],
    "setup_defaults": {},
    "capability_summary": {"read": "declared", "write": "provider_unavailable"}
  }]
}
```

API-driver guidance lists only required secret field names and a secure local command. It never asks for values in chat. Return a sanitized copy of the selected mode's reviewed non-secret `setup_defaults` so fixed grant type, scope, API base URL, and token URL are not asked from the user again. Local Bridge guidance returns `local_bridge_required` and a safe discovery handoff.

- [ ] **Step 4: Implement sanitized profile linking and evidence validation**

`link_connector_profile` validates the selected mode/environment against the manifest and stores only safe profile fields. Native MCP requires `external_server_name`; API driver stores a local driver identity; Local Bridge stores no LAN address in the hosted profile.

`validate_connector_connection` validates each observed capability against the explicitly selected connector mode and environment, stores the evidence reference and timestamp, and computes the neutral profile status. For `native_mcp_safe_read`, the tool records a host-observed provider result; it does not claim the Mercury server called the provider. For API-driver and Local Bridge evidence, accept only sanitized evidence produced by their local validation path.

Normalize every accepted capability alias to the selected mode's declared
provider-action key before persistence. Reject duplicate or conflicting
observations after alias expansion so two names cannot overwrite one canonical
capability state.

For a `discovered_tools` Generic MCP mode, host-observed evidence may persist the
exact discovered provider capability even though it is absent from a static
vendor catalog. Do not grant or deny that evidence by parsing capability-name
segments. Recording evidence does not execute the provider action; the provider
MCP tool's own annotations and the later approval policy classify execution
risk. Fixed-catalog modes must remain catalog-bound.

Validation requires an already linked profile with the exact workspace,
connector, mode, and environment identity; it must never upsert a new profile on
its own. A failed evidence envelope must either be rejected as inconsistent or
persist only non-ready capability states. It can never persist observed evidence
that produces `ready_read_only` or `ready_read_write`.

Catch typed evidence validation failures separately and return a fixed sanitized
message. Never serialize Pydantic's rejected `input_value`, a provider body, or
another unknown evidence field into the MCP result or audit payload.
This guarantee applies at the real FastMCP `call_tool` boundary, not only direct
Python calls. Keep the generated evidence JSON Schema explicit while deferring
the actual model validation to the sanitized handler boundary so framework
pre-validation cannot reflect rejected inputs before the handler runs.

- [ ] **Step 5: Implement status, capability reasons, and unlink**

`connector_capabilities(workspace_id, connector_id, connection_mode, environment)` returns declared and observed states for one unambiguous profile plus exact non-ready reasons such as `provider_unavailable`, `not_authorized`, `not_validated`, `environment_mismatch`, or `local_bridge_required`.

`connector_status` and `connector_capabilities` must preserve the resolver's exact
non-ready reason. A linked but unvalidated profile returns `not_validated`, not a
null reason or a generic setup status. A supported connector/mode with the wrong
environment returns `environment_mismatch`, not `not_found`.

`unlink_connector_profile` requires connector, mode, environment, and the exact literal `confirm="unlink"`; encode the literal in the generated MCP schema, not only in runtime branching. It deletes only the selected Mercury profile metadata, does not revoke provider OAuth, and returns `provider_disconnect_required=true` for native MCP profiles. Fallback state reconstruction must honor unlink tombstones so a later relink starts unvalidated and cannot recover evidence from an older configured event. Query fallback events by workspace and paginate through the complete ordered state stream; never reconstruct from only the oldest fixed-size prefix. Calling `link_connector_profile` again always starts a fresh unvalidated link and clears prior capability evidence, even when the four-part profile identity is unchanged.

- [ ] **Step 6: Replace the legacy global runtime gate with profile-aware routing**

Remove imports and calls to `public_capability_gate` from `mcp/server.py` and
`flows/runner.py`. Runtime decisions must resolve the selected connector manifest,
connection mode, environment, sanitized profile, and matching capability evidence.
Capability class (`read`, `create`, `update`, or `sensitive`) describes approval
behavior; it must not by itself grant or deny a provider action.

Keep the old gate functions only as deprecated non-routing Python compatibility
helpers through v0.3.x. Add regression coverage that fails if `server.py` or the
flow runner routes through the global suffix/segment gate again.

- [ ] **Step 7: Keep a non-MCP compatibility helper**

Retain `start_connector_setup(...)` as a plain Python wrapper to `link_connector_profile(...)` with the manifest's default mode. Remove its `@mcp.tool` decorator and mark its response with `deprecated_tool="start_connector_setup"` and `replacement_tool="link_connector_profile"`.

When the opt-in legacy HTTP API is enabled, keep `/api/connectors/setup` only as
a strict compatibility route. Validate its body with an extra-forbidden typed
model requiring `connector_id`, `connection_mode`, and `environment`; allow only
the same safe profile fields as `link_connector_profile`, and route through the
same lifecycle storage behavior. Never silently default a mode or ignore unknown,
secret, provider-body, or LAN-address fields. The compatibility wrapper must add
its deprecation and replacement fields on success and error responses alike.
Handle typed-body validation without serializing Pydantic's rejected
`input_value` back to the client.

- [ ] **Step 8: Run lifecycle tests**

```bash
uv run pytest -q tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_http_app.py tests/test_flows.py
```

Expected: all lifecycle, schema, redaction, fallback-store, and HTTP contract tests pass.

- [ ] **Step 9: Commit Task 3**

```bash
git add src/mercury_tools/mcp/server.py src/mercury_tools/mcp/schemas.py src/mercury_tools/db/product.py src/mercury_tools/flows/runner.py tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_http_app.py tests/test_flows.py
git commit -m "feat: expose connector-neutral lifecycle tools"
```

---

### Task 4: Correct public MCP annotations by business behavior

**Files:**
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `tests/test_mcp_contract.py`
- Modify: `tests/test_openai_plugin_submission.py`

- [ ] **Step 1: Replace the blanket-annotation test with a behavior matrix**

Add an exact expected mapping. The core entries are:

```python
EXPECTED_ANNOTATIONS = {
    "search_knowledge": (True, False, None, False),
    "retrieve_context_pack": (True, False, None, False),
    "get_document": (True, False, None, False),
    "list_connectors": (True, False, None, False),
    "get_connector_setup": (True, False, None, False),
    "connector_status": (True, False, None, False),
    "connector_capabilities": (True, False, None, False),
    "create_public_workspace": (False, False, False, False),
    "link_connector_profile": (False, False, False, False),
    "validate_connector_connection": (False, False, True, False),
    "unlink_connector_profile": (False, True, True, False),
    "save_workspace_flow": (False, False, True, False),
}
```

Tuple order is `(readOnlyHint, destructiveHint, idempotentHint, openWorldHint)`. Read tools use `idempotentHint=None`, not `False`, because the hint applies to mutation tools.

Complete the same exact matrix for every tool registered at the end of Task 3.
Do not register Task 5's future `run_inline_flow` tool or remove compatibility
flow tools in this task. The currently registered planning tools, including
`run_flow`, `run_flow_files`, and `run_mercury_flow`, are closed reads until Task
5 replaces the ambiguous public names. Other currently registered closed reads
include `retrieve_workspace_context_pack`, `get_public_workspace`,
`run_accounting_skill`, `flow_cheat_sheet`, `check_flow_syntax`,
`inspect_flow_files`, `list_workspace_flows`, and `run_workspace_flow`. Do not
register Task 6's future `list_accounting_skills` or
`get_accounting_skill_schema` tools in this task. Task 6 must add them to the
exact annotation matrix when it registers them.
`create_public_workspace` and `link_connector_profile` are closed non-idempotent
creates; `validate_connector_connection` and `save_workspace_flow` are closed
idempotent writes; `unlink_connector_profile` is a closed destructive
idempotent write. No hosted core tool sets `openWorldHint=true` because provider
MCP calls and local-driver network calls occur outside this server. Task 5 must
update the exact matrix when it changes the registered flow tool names.

- [ ] **Step 2: Run the annotation test and confirm `_AUDITED_PRIVATE` fails**

```bash
uv run pytest -q tests/test_mcp_contract.py -k annotations
```

Expected: failures for every read tool and unlink/save semantics.

- [ ] **Step 3: Add behavior-specific constants**

Replace `_AUDITED_PRIVATE` with:

```python
_CLOSED_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)
_CLOSED_CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_CLOSED_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_CLOSED_DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
```

Apply the constants individually. Treat append-only `_audit()` calls as observability and keep business reads annotated read-only.

- [ ] **Step 4: Verify annotations and schema serialization**

```bash
uv run pytest -q tests/test_mcp_contract.py tests/test_openai_plugin_submission.py -k 'annotations or submission'
```

Expected: all pass; read tools are no longer presented as writes by the host.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/mercury_tools/mcp/server.py tests/test_mcp_contract.py tests/test_openai_plugin_submission.py
git commit -m "fix: annotate public tools by business behavior"
```

---

### Task 5: Split ambiguous flow sources into explicit public tools

**Files:**
- Modify: `src/mercury_tools/mcp/schemas.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `tests/test_mcp_contract.py`
- Modify: `tests/test_connector_mcp_tools.py`
- Modify: `tests/test_plugin_package.py`
- Modify: `tests/test_http_app.py`

- [ ] **Step 1: Add failing schema tests for the revised flow surface**

The MCP tool set must include `inspect_flow_files`, `run_inline_flow`, `run_flow_files`, `run_workspace_flow`, and `save_workspace_flow`. It must not register `run_flow` or `run_mercury_flow`.

Add strict environment values:

```python
class FlowEnvironmentValue(StrictMcpInput):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
    value: str = Field(max_length=10_000)
```

Assert `flow_files` is always `list[FlowFileInput]`, tags are bounded string arrays, metadata references `WorkspaceFlowMetadata`, and public tools expose no `dict[str, Any]` input schema.

Hosted flow environment values are non-secret runtime parameters only. Reject
secret-bearing names such as `api_key`, `token`, `password`, `authorization`,
or `client_secret`, and reject values detected by the shared redaction boundary
before audit persistence or flow parsing. ERP and provider credentials remain
local-only and never enter the hosted MCP arguments.

- [ ] **Step 2: Run the schema tests and confirm the old generic source fails**

```bash
uv run pytest -q tests/test_mcp_contract.py tests/test_plugin_package.py -k 'flow and schema'
```

Expected: failures because `run_flow` accepts untyped `env` and `run_mercury_flow` still exposes a multi-source contract.

- [ ] **Step 3: Register explicit flow tools**

Use these signatures:

```python
run_inline_flow(
    workspace_id: str,
    flow_yaml: str,
    environment: list[FlowEnvironmentValue] = [],
    dry_run: bool = True,
)

run_flow_files(
    workspace_id: str,
    flow_files: list[FlowFileInput],
    config_yaml: str | None = None,
    environment: list[FlowEnvironmentValue] = [],
    include_tags: list[str] = [],
    exclude_tags: list[str] = [],
    continue_on_failure: bool = True,
    dry_run: bool = True,
)
```

Use `Field(default_factory=list)` in Pydantic models rather than mutable Python defaults in implementation. Require `workspace_id` even for non-connector hosted runs so workspace audit and saved-flow behavior are never implicit.
Reject blank, whitespace-only, and malformed workspace identifiers at the real
FastMCP boundary before environment handling or flow parsing. Add
`mcp.call_tool` regressions for both inline and file-based run tools and verify
that no plan or audit event is produced for rejected identifiers.

- [ ] **Step 4: Preserve compatibility outside the MCP registry**

Keep plain Python functions `run_flow(...)` and `run_mercury_flow(...)` for v0.3.x unit/API callers. Route them to the explicit implementations after normalizing their legacy payload. Do not decorate or advertise them as public MCP tools.

- [ ] **Step 5: Apply closed-read annotations to planning tools**

`inspect_flow_files`, `run_inline_flow`, `run_flow_files`, and `run_workspace_flow` are closed reads because hosted Mercury produces plans/artifacts and audit events but does not contact or mutate an ERP. `save_workspace_flow` remains an idempotent closed write keyed by content version.

- [ ] **Step 6: Run flow tests**

```bash
uv run pytest -q tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py -k flow
```

Expected: all flow schemas are explicit and no compatibility helper appears in `mcp.list_tools()`.
The HTTP `/api/status` flow-tool list must advertise the same revised public
names and must not retain `run_flow` or `run_mercury_flow`.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/mercury_tools/mcp/schemas.py src/mercury_tools/mcp/server.py tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py tests/test_http_app.py
git commit -m "refactor: split public Mercury flow sources"
```

---

### Task 6: Route accounting Skills by normalized capability instead of vendor name

**Files:**
- Create: `src/mercury_tools/skills/__init__.py`
- Create: `src/mercury_tools/skills/catalog.py`
- Create: `src/mercury_tools/skills/routing.py`
- Create: `tests/test_skill_routing.py`
- Modify: `src/mercury_tools/db/product.py`
- Modify: `src/mercury_tools/mercury_runtime.py`
- Modify: `src/mercury_tools/mcp/server.py`
- Modify: `src/mercury_tools/mcp/schemas.py`
- Modify: `tests/test_connector_mcp_tools.py`
- Modify: `tests/test_plugin_package.py`
- Modify: `plugins/mercury-finance/skills/company-health-check-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/invoice-review-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/vat-summary-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/management-report-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/accounts-payable-reconciliation-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/accounts-receivable-reconciliation-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/bank-settlement-reconciliation-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/marketplace-settlement-review-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/month-end-evidence-gathering-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/mercury-flow-runner/SKILL.md`

- [ ] **Step 1: Add routing tests for two providers and one unavailable capability**

Test the same `company-health-check-th` Skill against ready FlowAccount and PEAK profiles. Both must resolve through normalized capabilities without changing the Skill ID. Test a native read-only profile requesting `documents.invoice.create`; it must return `provider_capability_unavailable` while an unrelated read remains runnable.

Test exact public tools:

```python
list_accounting_skills()
get_accounting_skill_schema(skill_id)
run_accounting_skill(workspace_id, skill_id, inputs, evidence_mode=False)
```

Catalog listing and schema discovery are workspace-independent closed reads.
`run_accounting_skill` is workspace-scoped and requires `workspace_id` as an
explicit top-level MCP argument; do not hide it as an optional field inside the
skill-specific input payload. Add the two discovery tools and the revised run
tool to Task 4's exact annotation matrix as closed reads.

- [ ] **Step 2: Run the routing tests and confirm generic Skills are FlowAccount-bound**

```bash
uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py -k skill
```

Expected: failures because the canonical Skill catalog, deterministic routing,
and public list/schema tools do not exist yet. Task 2 has already removed vendor
requirements from the current generic seed rows; do not reintroduce them merely
to create a failing test.

- [ ] **Step 3: Create one canonical Skill catalog**

Define:

```python
@dataclass(frozen=True, slots=True)
class AccountingSkillDefinition:
    skill_id: str
    title: str
    category: str
    summary: str
    required_capabilities: tuple[str, ...]
    optional_capabilities: tuple[str, ...]
    required_connectors: tuple[str, ...]
    input_schema: type[BaseModel]
    output_schema_name: str
```

`SKILL_CATALOG_SEED` and public MCP Skill schemas must be generated from this
catalog. Generic Markdown Skills consume their contract by Skill ID through
`get_accounting_skill_schema`; they must not duplicate provider mappings or a
second independently maintained capability list. Plugin tests must verify each
generic Markdown Skill references its own Skill ID, calls the schema/routing
surface, and contains no conflicting vendor requirement.

Use these minimum portable requirements:

| Skill | Required capabilities | Optional capabilities |
| --- | --- | --- |
| Company health | `company.read` | `documents.invoice.list`, `tax.vat.summary.read` |
| Invoice review | `documents.invoice.list`, `documents.invoice.read` | `contacts.list` |
| VAT summary | `documents.invoice.list` | `tax.vat.summary.read` |
| Management report | `company.read`, `documents.invoice.list` | `payments.read`, `journal.read` |
| AP reconciliation | `documents.expense.list` | `payments.read` |
| AR reconciliation | `documents.invoice.list` | `payments.read` |

Provider-specific setup Skills may retain connector requirements. Normalized capability aliases belong in connector manifests, not in generic Skill Markdown.

- [ ] **Step 4: Implement deterministic profile resolution**

`resolve_skill_route(skill, profiles, requested_connector_id=None)` returns:

```json
{
  "status": "ready",
  "selected_profile": {
    "connector_id": "peak",
    "connection_mode": "api_driver",
    "environment": "production"
  },
  "capability_resolution": [],
  "ordered_steps": [],
  "host_tool_requirements": []
}
```

Pass explicit `inputs.connector_id` into `requested_connector_id` and resolve it
first. Otherwise select the single ready profile satisfying all required
capabilities. If multiple profiles qualify, return
`connector_selection_required` with sanitized choices rather than choosing a
preferred vendor.

For native MCP profiles, ordered steps name provider capabilities and tell the host to invoke the already-connected provider tools. For API drivers, return the advanced local Mercury handoff. For Local Bridge, return `local_bridge_required`.

- [ ] **Step 5: Publish and enforce machine-readable Skill schemas**

`get_accounting_skill_schema` returns the exact Pydantic JSON Schema. `run_accounting_skill` validates the typed common envelope plus named `SkillInputParameter` values against that schema before creating a route. Unknown, duplicate, missing, or secret-looking parameters fail before audit persistence.

Keep the generated common-envelope schema explicit while ensuring invalid or
secret-bearing nested values are validated inside a sanitized handler boundary.
Add a real FastMCP `call_tool` regression proving rejected values and Pydantic
`input_value` never appear in the MCP result or audit payload.

- [ ] **Step 6: Rewrite generic Skill Markdown around capabilities and host orchestration**

Each generic Skill must:

- inspect workspace connector status;
- resolve normalized capabilities;
- request connector selection only when ambiguous;
- return an ordered host plan for provider MCP/API-driver/Local Bridge mode;
- preserve citations, evidence requirements, accountant review points, and result schema; and
- never claim Mercury owns Google, ecommerce, bank, or provider OAuth tokens.

- [ ] **Step 7: Run Skill tests**

```bash
uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py
```

Expected: one generic Skill routes across at least FlowAccount and PEAK test profiles; missing capabilities return exact reasons; Markdown and seed metadata agree.

- [ ] **Step 8: Commit Task 6**

```bash
git add src/mercury_tools/skills src/mercury_tools/db/product.py src/mercury_tools/mercury_runtime.py src/mercury_tools/mcp/server.py src/mercury_tools/mcp/schemas.py tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py plugins/mercury-finance/skills
git commit -m "feat: route accounting skills by capability"
```

---

### Task 7: Reduce ERP mutations to one immutable approval

**Files:**
- Modify: `src/mercury_tools/execution/policy.py`
- Modify: `src/mercury_tools/execution/models.py`
- Modify: `src/mercury_tools/execution/store.py`
- Modify: `src/mercury_tools/execution/executor.py`
- Modify: `src/mercury_tools/execution/request_builder.py`
- Modify: `src/mercury_tools/local/audit.py`
- Modify: `tests/test_execution_policy.py`
- Modify: `tests/test_request_store.py`
- Modify: `tests/test_executor.py`

- [ ] **Step 1: Add failing one-approval tests**

Cover:

```python
def test_standard_create_requires_one_standard_approval(): ...
def test_update_requires_one_standard_approval(): ...
def test_delete_requires_one_elevated_approval(): ...
def test_payment_post_void_email_and_share_are_sensitive(): ...
def test_first_valid_approval_moves_high_risk_request_to_ready(): ...
def test_payload_change_invalidates_approval(): ...
def test_outcome_unknown_still_blocks_replay(): ...
```

Assert there is no newly-created `awaiting_final_confirmation` state.

- [ ] **Step 2: Run focused tests and confirm high-risk requests still require two confirmations**

```bash
uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py tests/test_executor.py -k 'approval or confirmation or outcome_unknown'
```

Expected: high-risk tests expose the current duplicate-confirmation behavior.

- [ ] **Step 3: Define static mutation class and approval level**

In `execution/policy.py` add:

```python
class ApprovalLevel(StrEnum):
    STANDARD = "standard"
    ELEVATED = "elevated"


class MutationClass(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    tier: RiskTier
    approval_level: ApprovalLevel
    mutation_class: MutationClass
    reasons: tuple[str, ...]
```

Classification rules:

- `POST` is `create` unless the action has a sensitive side effect;
- `PUT` and `PATCH` are `update` unless the action has a sensitive side effect;
- `DELETE` is always `sensitive`;
- payment, approve, void, post, finalize, email, share, invite, and delete effects are `sensitive`;
- inferred and unobserved mutations are `sensitive`.

Keep `CatalogAction.required_confirmations` as v0.2 catalog compatibility data only. Do not interpret it as the number of user prompts in v0.3.0.

- [ ] **Step 4: Change prepared requests to one approval record**

Replace `required_confirmations`/`confirmation_count` in new request JSON with:

```python
approval_level: ApprovalLevel
mutation_class: MutationClass
approval_count: Literal[0, 1] = 0
```

Remove `AWAITING_FINAL_CONFIRMATION` from newly-created states. Include approval level and mutation class in the canonical payload binding so changing either invalidates the hash.

Because previews expire after 15 minutes, set SQLite `PRAGMA user_version=2`. On first v0.3 startup, archive an existing v1 `requests` table as `requests_v1_archive`, create the v2 table, and start with no reusable pending approval. Preserve the redacted audit ledger as historical evidence.

- [ ] **Step 5: Make approval and execution transitions deterministic**

Implement `LocalRequestStore.approve(request_id, payload_hash, expected_class)`. It validates hash, expiry, state, and mutation class, then moves the request to `READY_TO_EXECUTE` with `approval_count=1` in one transaction.

Add `ERPExecutor.approve_and_execute(request_id, payload_hash, expected_class)`. It calls `approve`, revalidates catalog/version/target/credentials/preflights, marks dispatch, and preserves the current completion outcomes. Never retry a dispatched request whose result is unknown.

- [ ] **Step 6: Keep audit output human-readable and secretless**

Audit `approval_level`, `mutation_class`, action ID, version ID, environment, payload hash, and state. Remove user-visible `required_confirmations`. Never log request inputs or provider response bodies.

- [ ] **Step 7: Run execution tests**

```bash
uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py tests/test_executor.py
```

Expected: standard and elevated mutations each need exactly one approval; payload/replay/network safeguards remain green.

- [ ] **Step 8: Commit Task 7**

```bash
git add src/mercury_tools/execution src/mercury_tools/local/audit.py tests/test_execution_policy.py tests/test_request_store.py tests/test_executor.py
git commit -m "refactor: bind ERP writes to one approval"
```

---

### Task 8: Split advanced local ERP execution by static risk class

**Files:**
- Modify: `src/mercury_tools/mcp/local_server.py`
- Modify: `src/mercury_tools/mcp/local_runtime.py`
- Create: `docs/ADVANCED_LOCAL_ERP.md`
- Modify: `tests/test_local_mcp_contract.py`
- Modify: `tests/test_plugin_package.py`

- [ ] **Step 1: Change the expected local MCP contract first**

Replace `preview_erp_write`, `confirm_erp_write`, and `execute_erp_write` with:

```text
prepare_erp_mutation
execute_erp_create
execute_erp_update
execute_sensitive_erp_action
```

Keep `search_erp_actions`, `get_erp_action_schema`, `run_erp_read`, `get_erp_request_status`, `import_erp_spec`, `list_connector_drivers`, and `credential_status`.

- [ ] **Step 2: Add exact annotation tests**

Assert:

| Tool | Read only | Destructive | Open world |
| --- | --- | --- | --- |
| `run_erp_read` | true | false | true |
| `prepare_erp_mutation` | false | false | false |
| `execute_erp_create` | false | false | true |
| `execute_erp_update` | false | true | true |
| `execute_sensitive_erp_action` | false | true | true |
| `import_erp_spec` | false | false | true |

All execute tools use `idempotentHint=False` because the selected catalog action may not be idempotent.

- [ ] **Step 3: Run local contract tests and observe the old three-step tools**

```bash
uv run pytest -q tests/test_local_mcp_contract.py tests/test_plugin_package.py -k 'tool_contract or annotations or write'
```

Expected: failures for tool names, annotations, and old confirmation flow.

- [ ] **Step 4: Implement preparation and static-class execution**

Use exact signatures:

```python
prepare_erp_mutation(action_id, inputs, environment=None, repo_root=None)
execute_erp_create(request_id, payload_hash, repo_root=None)
execute_erp_update(request_id, payload_hash, repo_root=None)
execute_sensitive_erp_action(request_id, payload_hash, repo_root=None)
```

Preparation returns sanitized summary, payload hash, mutation class, approval level, expiry, and exact `next_tool`. Each execute tool calls `approve_and_execute` with its expected class. Class mismatch fails before auth or network access.

- [ ] **Step 5: Remove the host-visible duplicate confirmation ceremony**

Do not expose `confirm_erp_write`. Keep internal preview creation mandatory. The host approval UI is triggered by the selected execute tool's annotations and immutable summary. Sensitive actions use the explicitly named sensitive tool and elevated summary.

- [ ] **Step 6: Document the advanced local execution path outside the public plugin**

Create `docs/ADVANCED_LOCAL_ERP.md` with the `mercury mcp serve-local` setup, local credential boundary, and `prepare_erp_mutation -> execute_erp_create/update/sensitive` sequence. State that official FlowAccount MCP remains read-only while a separately reviewed FlowAccount API-driver action may write. Public plugin Skills must return this advanced handoff and must not invoke local-only tool names as though they are present in the one-click hosted MCP.

- [ ] **Step 7: Run local MCP tests**

```bash
uv run pytest -q tests/test_local_mcp_contract.py tests/test_plugin_package.py
```

Expected: the local server exposes the split write tools and one-call approval behavior while credential, action allowlist, and audit tests remain green.

- [ ] **Step 8: Commit Task 8**

```bash
git add src/mercury_tools/mcp/local_server.py src/mercury_tools/mcp/local_runtime.py docs/ADVANCED_LOCAL_ERP.md tests/test_local_mcp_contract.py tests/test_plugin_package.py
git commit -m "feat: split local ERP mutation tools by risk"
```

---

### Task 9: Make the Codex plugin one-click, hosted, and connector-neutral

**Files:**
- Modify: `plugins/mercury-finance/.mcp.json`
- Modify: `plugins/mercury-finance/.codex-plugin/plugin.json`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/flowaccount-journal-posting-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/flowaccount-connector-setup-th/SKILL.md`
- Modify: `plugins/mercury-finance/skills/peak-connector-setup-th/SKILL.md`
- Modify: `README.md`
- Create: `docs/CONNECTOR_CATALOG.md`
- Modify: `docs/LOCAL_CREDENTIALS.md`
- Modify: `tests/test_plugin_package.py`
- Modify: `tests/test_plugin_clean_install.py`
- Modify: `scripts/validate_release_plugin.py`

- [ ] **Step 1: Write the one-click package test first**

Assert the plugin registers exactly one hosted MCP:

```python
assert data == {
    "mcpServers": {
        "mercury-finance": {
            "type": "http",
            "url": "https://mercury-tools-mcp.onrender.com/mcp",
            "note": "Mercury Accounting and ERP connector platform.",
        }
    }
}
```

Assert there is no `command`, `args`, `cwd`, `uvx`, bearer token, environment variable, or second provider MCP. Assert primary copy contains `Accounting and ERP connector platform` and no default prompt begins with FlowAccount or PEAK.

- [ ] **Step 2: Run package tests and confirm the current local uvx launcher fails**

```bash
uv run pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py -k 'registers or one_click or launcher'
```

Expected: failures because `.mcp.json` launches `uvx ... serve-local`.

- [ ] **Step 3: Switch the public plugin to hosted HTTP**

Use the exact `.mcp.json` above. The installed plugin must require no clone, Python, uv, Supabase URL, Mercury Owner Token, or ERP secret.

Keep `mercury mcp serve-local` documented as an alternative advanced connector path. Do not auto-register it beside the hosted core because two Mercury servers would duplicate tool names and confuse routing.

Provider-specific public Skills use hosted connector lifecycle tools and return the advanced-local documentation handoff when a reviewed API-driver write is requested. They must not directly invoke `prepare_erp_mutation` or an `execute_erp_*` tool unless the user has separately connected the local MCP.

- [ ] **Step 4: Rewrite product metadata and starter prompts**

Set connector-neutral prompts:

```json
[
  "Connect an accounting or ERP system",
  "Check which accounting capabilities are ready for this workspace",
  "Prepare an evidence-backed company health review",
  "Plan a reconciliation using my connected ERP and spreadsheet tools"
]
```

Keep FlowAccount, PEAK, Express, Custom ERP, and Generic MCP visible in the catalog, not in the product headline.

- [ ] **Step 5: Document the two installation paths**

README order:

1. Marketplace one-click plugin.
2. No-clone hosted MCP URL fallback.
3. GitHub development install.
4. Advanced local API-driver/Local Bridge setup.

`docs/CONNECTOR_CATALOG.md` must show every mode, factual readiness, environment, auth owner, provider capability status, and validation date. `docs/LOCAL_CREDENTIALS.md` remains the only secret setup guide and uses hidden CLI input, never chat examples.

- [ ] **Step 6: Make the offline plugin validator require hosted HTTP**

Update `scripts/validate_release_plugin.py` to reject local commands in the public plugin, reject multiple MCP entries, require HTTPS Render `/mcp`, and retain recursive credential-literal scanning.

- [ ] **Step 7: Run package validation**

```bash
uv run pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
uv run python scripts/validate_release_plugin.py
```

Expected: clean install validates without a local runtime and the plugin validator prints a successful result.

- [ ] **Step 8: Commit Task 9**

```bash
git add plugins/mercury-finance/.mcp.json plugins/mercury-finance/.codex-plugin/plugin.json plugins/mercury-finance/skills/connector-credential-setup-th/SKILL.md plugins/mercury-finance/skills/flowaccount-journal-posting-th/SKILL.md plugins/mercury-finance/skills/flowaccount-connector-setup-th/SKILL.md plugins/mercury-finance/skills/peak-connector-setup-th/SKILL.md .agents/plugins/marketplace.json README.md docs/CONNECTOR_CATALOG.md docs/LOCAL_CREDENTIALS.md tests/test_plugin_package.py tests/test_plugin_clean_install.py scripts/validate_release_plugin.py
git commit -m "feat: make Mercury a hosted one-click plugin"
```

---

### Task 10: Align OpenAI submission assets and eliminate unclear arguments

**Files:**
- Create: `scripts/review_mcp_contract.py`
- Create: `tests/test_mcp_review_contract.py`
- Modify: `chatgpt-app-submission.json`
- Modify: `submission/openai-plugin/listing.json`
- Modify: `submission/openai-plugin/test-cases.json`
- Modify: `submission/openai-plugin/skills/accounting-knowledge-research-th/SKILL.md`
- Modify: `submission/openai-plugin/skills/company-health-context-th/SKILL.md`
- Modify: `submission/openai-plugin/skills/connector-onboarding-th/SKILL.md`
- Modify: `submission/openai-plugin/skills/erp-endpoint-catalog-th/SKILL.md`
- Modify: `submission/openai-plugin/skills/invoice-review-context-th/SKILL.md`
- Modify: `submission/openai-plugin/skills/mercury-flow-planner/SKILL.md`
- Modify: `tests/test_openai_plugin_submission.py`
- Modify: `scripts/build_openai_plugin_bundle.py`

- [ ] **Step 1: Add an offline MCP review linter test**

The linter loads `mcp.list_tools()` and fails when:

- an input object has no named properties or allows arbitrary extra keys;
- a workspace-scoped tool omits required `workspace_id`;
- an environment lacks an enum;
- a list lacks a typed item schema and a maximum length;
- a tool has mutually exclusive top-level source fields;
- an annotation field required by the behavior matrix is missing; or
- a public schema contains a credential-bearing field name.

The success output must be:

```text
Mercury MCP review: 0 unclear arguments; annotations verified
```

- [ ] **Step 2: Run the linter test before implementation**

```bash
uv run pytest -q tests/test_mcp_review_contract.py
```

Expected: failure because the linter does not exist.

- [ ] **Step 3: Implement the review linter and update submission metadata**

Update tool descriptions to state exactly what changes, what external system is contacted, and what happens when optional values are omitted. Do not mark audited reads as mutations.

Update `chatgpt-app-submission.json` and `listing.json` to describe Mercury as connector-neutral. Keep `custom_ui=false`. State that the hosted core stores sanitized profile/audit metadata but no ERP credential; advanced local drivers may execute reviewed ERP actions after host approval.

- [ ] **Step 4: Update public submission Skills and test cases**

Public Skills may use only hosted tools. Their connector onboarding flow is:

```text
list_connectors
-> get_connector_setup
-> link_connector_profile
-> host/provider OAuth or local handoff
-> validate_connector_connection
-> connector_status
```

Add positive cases for a native MCP read-only provider, PEAK API-driver handoff, Express Local Bridge handoff, portable Skill routing, and cited knowledge. Add negative cases for secret-in-chat, unavailable provider write, and ambiguous multi-profile selection.

- [ ] **Step 5: Run review and deterministic bundle tests**

```bash
uv run python scripts/review_mcp_contract.py
uv run pytest -q tests/test_mcp_review_contract.py tests/test_openai_plugin_submission.py
uv run python scripts/build_openai_plugin_bundle.py
```

Expected: zero unclear arguments, correct annotation matrix, and byte-identical repeated submission bundles.

- [ ] **Step 6: Commit Task 10**

```bash
git add scripts/review_mcp_contract.py tests/test_mcp_review_contract.py chatgpt-app-submission.json submission/openai-plugin tests/test_openai_plugin_submission.py scripts/build_openai_plugin_bundle.py
git commit -m "fix: clarify Mercury plugin tools for review"
```

---

### Task 11: Prepare the v0.3.0 package and release controls

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/mercury_tools/__init__.py`
- Create: `docs/RELEASE_V0.3.0.md`
- Create: `tests/test_release_v030_contract.py`
- Create: `.github/workflows/release-v0.3.0.yml`
- Modify: `.github/workflows/post-public-verify.yml`
- Modify: `scripts/validate_release_plugin.py`
- Modify: `scripts/verify_render_release.py`
- Modify: `release-control/expected-public-tree.json`
- Modify: `tests/test_release_workflows.py`
- Modify: `tests/test_release_control_publication.py`

- [ ] **Step 1: Add a failing release identity test**

Assert:

```python
assert project_version == "0.3.0"
assert package_version == "0.3.0"
assert plugin_version == "0.3.0+codex.20260719"
assert release_workflow_tag == "v0.3.0"
assert latest_required_migration == "20260719120000"
```

Assert release artifacts contain the hosted plugin config, revised tool contract, connector-neutral catalog, and no usable secret.

- [ ] **Step 2: Run the release contract test and confirm v0.2.2 identity fails**

```bash
uv run pytest -q tests/test_release_v030_contract.py
```

Expected: version and workflow failures.

- [ ] **Step 3: Bump all candidate-owned version surfaces**

Set package version `0.3.0`, plugin version `0.3.0+codex.20260719`, release tag `v0.3.0`, and migration identity `20260719120000`. Do not modify dependency pins unless a Task 1-10 implementation requires a dependency change and its test proves why.

- [ ] **Step 4: Create a v0.3.0 release workflow from the reviewed v0.2.2 controls**

Retain full-history secret scans, networkless candidate tests, exact reviewed SHA binding, immutable annotated tag checks, Supabase migration verification, artifact digests, trusted release-control attestation, and post-public verification. Update only release identity, expected tree, tests, migration ID, and revised MCP/plugin contract.

- [ ] **Step 5: Update the external trusted release-control repository in a separate worktree**

Repository: `/Users/natthaphon/Desktop/mercury-release-control-v2`

Create branch `release/v0.3.0-controls` from its reviewed remote branch. Add `policy-v0.3.0.json`, `release-notes-v0.3.0.md`, `.github/workflows/attest-v0.3.0.yml`, and `.github/workflows/publish-v0.3.0.yml`; update release-control tests to expect Mercury `v0.3.0`, workflow `.github/workflows/release-v0.3.0.yml`, and migration `20260719120000`. Do not weaken repository ID, run attempt, artifact digest, reviewed SHA, tag, or provider-state checks.

Commit that repository separately:

```bash
git add policy-v0.3.0.json release-notes-v0.3.0.md .github/workflows/attest-v0.3.0.yml .github/workflows/publish-v0.3.0.yml tests
git commit -m "feat: add Mercury v0.3.0 release controls"
```

- [ ] **Step 6: Run release tests in both repositories**

Mercury:

```bash
uv run pytest -q tests/test_release_v030_contract.py tests/test_release_workflows.py tests/test_release_control_publication.py
```

Release control:

```bash
uv run pytest -q
```

Expected: both suites pass with exact v0.3.0 and migration identities.

- [ ] **Step 7: Commit Mercury release preparation**

```bash
git add pyproject.toml src/mercury_tools/__init__.py docs/RELEASE_V0.3.0.md tests/test_release_v030_contract.py .github/workflows/release-v0.3.0.yml .github/workflows/post-public-verify.yml scripts/validate_release_plugin.py scripts/verify_render_release.py release-control/expected-public-tree.json tests/test_release_workflows.py tests/test_release_control_publication.py
git commit -m "chore: prepare Mercury v0.3.0 release"
```

---

### Task 12: Verify, merge, migrate, deploy, and smoke-test the public product

**Files:**
- Modify only when verification finds a defect in a Task 1-11 file.
- Generate: `dist/` release artifacts through the existing deterministic build commands; do not commit generated artifacts unless the release policy explicitly tracks them.

- [ ] **Step 1: Run formatting and focused contract suites**

```bash
uv run ruff check .
uv run pytest -q \
  tests/test_connector_catalog.py \
  tests/test_connector_neutral_profile_migration.py \
  tests/test_connector_mcp_tools.py \
  tests/test_mcp_contract.py \
  tests/test_mcp_review_contract.py \
  tests/test_skill_routing.py \
  tests/test_execution_policy.py \
  tests/test_request_store.py \
  tests/test_executor.py \
  tests/test_local_mcp_contract.py \
  tests/test_plugin_package.py \
  tests/test_plugin_clean_install.py \
  tests/test_openai_plugin_submission.py \
  tests/test_release_v030_contract.py
```

Expected: all pass.

- [ ] **Step 2: Run the complete networkless suite**

```bash
uv run pytest -q
```

Expected: no regression from the approved baseline of 5,766 passed and 16 skipped; any intentional count change is explained by added tests, not removed coverage.

- [ ] **Step 3: Build and inspect release artifacts**

```bash
uv build
uv run python scripts/build_openai_plugin_bundle.py
uv run python scripts/validate_release_plugin.py
uv run python scripts/review_mcp_contract.py
uv run mercury release verify --version 0.3.0
```

Expected: wheel, sdist, plugin bundle, schema review, and release verification all pass.

- [ ] **Step 4: Run fail-closed secret scans**

```bash
uv run mercury release scan-secrets --all-history --artifacts dist
```

Expected: zero unresolved secret findings in Git history or generated artifacts. Do not add a new allowlist entry for a real credential; remove the value and rotate it if discovered.

- [ ] **Step 5: Review the branch before merge**

```bash
git status --short
git diff --check origin/main...HEAD
git log --oneline origin/main..HEAD
```

Expected: clean worktree, no whitespace errors, and one focused commit per task. Request code review using `superpowers:requesting-code-review` and resolve findings before merge.

- [ ] **Step 6: Merge through a PR and apply the migration before the new server contract**

Push the implementation branch, open a PR to `main`, and require CI plus release-control review. Apply `20260719120000_connector_neutral_profiles.sql` through the existing trusted Supabase migration job before Render serves code that reads the new columns. A failed migration blocks deployment.

- [ ] **Step 7: Verify Render after main deploy**

```bash
uv run mercury remote verify --url https://mercury-tools-mcp.onrender.com --json
```

Expected JSON includes `"ready": true`, MCP URL `https://mercury-tools-mcp.onrender.com/mcp`, public unauthenticated core access, configured Supabase, and version `0.3.0` in health metadata.

- [ ] **Step 8: Run a public MCP smoke test without ERP secrets**

From a clean MCP client:

1. list tools and confirm one Mercury server;
2. call `list_connectors` and confirm all five connector families appear without a default selection;
3. create a public workspace;
4. call `get_connector_setup` for FlowAccount native MCP, PEAK API driver, and Express Local Bridge;
5. link a sanitized test profile;
6. call `connector_status`, `list_accounting_skills`, and `get_accounting_skill_schema`;
7. run a portable Skill and verify capability-based routing plus citations; and
8. confirm no tool asks for or returns an ERP credential.

- [ ] **Step 9: Run the advanced local contract smoke test with fixtures only**

```bash
uv run pytest -q tests/test_local_mcp_contract.py::test_real_stdio_initialize_and_tools_list
```

Expected: the stdio client initializes, lists the split read/prepare/create/update/sensitive surface, and exits cleanly. Use fixture transports only; do not dispatch a production provider mutation during release acceptance.

- [ ] **Step 10: Publish v0.3.0 through trusted release control**

After Render and migration evidence are green, run the v0.3.0 attestation and immutable publication workflows from `mercury-release-control-v2`. Verify the annotated `v0.3.0` tag resolves to reviewed `main`, GitHub release artifacts match their digests, and the marketplace/OpenAI submission points at the live hosted MCP.

---

## Final Acceptance Checklist

- [ ] Product copy and first prompts are connector-neutral.
- [ ] Public plugin installs one hosted Mercury MCP with no local runtime requirement.
- [ ] FlowAccount, PEAK, Express, Custom ERP, and Generic MCP appear with factual mode-specific readiness.
- [ ] Official provider read-only limitations affect only unavailable provider capabilities.
- [ ] Workspace tools require `workspace_id`; all tool inputs are explicit and secretless.
- [ ] Offline review reports zero unclear arguments.
- [ ] Tool annotations match actual business behavior and external effects.
- [ ] One portable accounting Skill routes across two compatible connectors.
- [ ] Ordinary and sensitive mutations each require one immutable host approval at the correct level.
- [ ] Payload changes, untrusted hosts, environment mismatch, replay, and `outcome_unknown` remain blocked.
- [ ] Full tests, deterministic builds, Git-history scan, artifact scan, Supabase migration, Render verification, and clean-client MCP smoke tests pass.
