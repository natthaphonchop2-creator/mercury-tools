# Mercury Public MCP Product Design

Date: 2026-07-10
Status: Approved direction, written for review
Repo: `mercury-tools`
Supersedes: `2026-07-09-mercury-codex-plugin-design.md`

## Decision

Mercury v1 ships as a GitHub marketplace plugin backed by one public remote MCP
server. The MCP endpoint does not require OAuth, a bearer token, login, or a web
setup experience.

Mercury is not a web app. Codex, Cursor, Claude, or another MCP host remains the
conversation and model runtime. Render hosts the MCP server and Supabase stores
knowledge, audit records, public workspace state, connector profiles, and flow
records.

The public deployment is deliberately a contest and product-preview boundary.
It does not claim private tenant isolation. Production-changing ERP operations
remain blocked.

## Product Experience

The primary user flow is:

1. Add the `mercury-tools` marketplace from GitHub.
2. Install the `mercury-finance` plugin.
3. Start a new Codex task.
4. Ask Mercury to search accounting knowledge, inspect an ERP connector, create
   or select a public workspace, validate connector access, or run a safe flow.
5. Continue working in the host AI. No Mercury dashboard or Mercury chat page is
   introduced.

The plugin contains presentation metadata, starter prompts, skills, and the
remote MCP configuration. Product logic remains on the remote MCP server.

## Architecture

```text
GitHub marketplace
  -> Mercury Finance plugin
       -> bundled accounting and connector skills
       -> remote MCP config
            -> Mercury Tools on Render
                 -> public knowledge and connector tools
                 -> public workspace and flow tools
                 -> Supabase RAG, workspace state, and audit events
                 -> FlowAccount, PEAK, and future ERP APIs
```

### GitHub Marketplace Plugin

The repository marketplace lives at `.agents/plugins/marketplace.json`. The
plugin lives at `plugins/mercury-finance/` and contains:

- `.codex-plugin/plugin.json`
- `.mcp.json`
- `skills/<skill-id>/SKILL.md`
- optional presentation assets under `assets/`

The plugin MCP configuration points directly to:

```text
https://mercury-tools-mcp.onrender.com/mcp
```

No user token or secret is embedded in the plugin.

### Public MCP Runtime

The Streamable HTTP endpoint is public. It exposes three tool groups:

1. Global read tools for knowledge, documents, connector catalogs, skill
   packages, and flow inspection.
2. Public workspace tools for selecting a company context, an ERP connector,
   saved flows, and connector setup state.
3. Connector execution tools limited to validation and explicitly allowed
   read-only capabilities in v1.

The MCP tool list must not expose obsolete local Mercury Agent paths or require
the old `client_token` argument.

### Public Workspace State

Mercury needs a workspace context to know which company and ERP documentation
to use, even though the endpoint is public. It uses a public `workspace_id`
instead of authentication.

The server provides `create_public_workspace`. It returns a random opaque
workspace ID. Workspace-scoped tools accept this ID and return only sanitized
state. The plugin skills keep the active workspace ID in the current host task.

This is routing, not authentication:

- Anyone who knows a workspace ID can use that workspace.
- A workspace ID must not be described as secure access control.
- Losing the task context can require the user to provide or create a workspace
  ID again.
- A reserved isolated demo workspace may be used for the contest walkthrough.

The product can add OAuth and private tenant isolation after the public v1
without changing accounting capability names or connector manifests.

## MCP Interface

### Global Tools

- `search_knowledge(query, filters=None, top_k=8, mode="hybrid")`
- `retrieve_context_pack(query, task=None, filters=None, max_chunks=12)`
- `get_document(document_id)`
- `list_connectors()`
- `connector_capabilities(connector_id)`
- `list_accounting_skills()`
- `get_accounting_skill_schema(skill_id)`
- `run_accounting_skill(workspace_id, skill_id, inputs, evidence_mode=False)`
- `flow_cheat_sheet()`
- `check_flow_syntax(flow_yaml)`
- `inspect_flow_files(flow_files, config_yaml=None)`
- `run_mercury_flow(..., dry_run=True)`

### Public Workspace Tools

- `create_public_workspace(company_name=None)`
- `get_public_workspace(workspace_id)`
- `select_workspace_connector(workspace_id, connector_id, environment)`
- `connector_status(workspace_id=None)`
- `retrieve_workspace_context_pack(workspace_id, query, task=None, max_chunks=12)`
- `list_workspace_flows(workspace_id)`
- `save_workspace_flow(workspace_id, title, flow_yaml, metadata=None)`
- `run_workspace_flow(workspace_id, flow_id, dry_run=True, env=None)`

### Connector Setup Tools

- `start_connector_setup(workspace_id, connector_id, environment)`
- `submit_connector_credentials(workspace_id, connector_id, environment, credentials)`
- `validate_connector_connection(workspace_id, connector_id, environment, credentials=None)`

Connector setup returns preset values and the names of missing credential
fields. Field names such as `client_id` and `client_secret` are metadata and
must remain visible. Credential values, access tokens, tax IDs, and customer
data remain redacted from tool responses and audit summaries.

## Connector Routing

The active workspace connector determines knowledge and endpoint routing:

1. Read the workspace connector profile.
2. Resolve connector ID, environment, and validated capabilities.
3. Apply connector-aware RAG filters automatically.
4. Retrieve connector endpoint dictionaries and relevant accounting knowledge.
5. Return a cited context pack.
6. Execute only capabilities allowed by the connector manifest and v1 policy.

If no connector is selected, Mercury returns available connectors and the next
setup tool. It must not guess FlowAccount, PEAK, Express, or a custom ERP.

If a query explicitly names an ERP but has no workspace, search may infer a
connector filter from the query. Inferred filters must be returned in the tool
result so the host AI can explain what Mercury selected.

## RAG Requirements

Supabase remains the source of truth for Mercury Wiki documents and chunks.
Each connector document carries:

- `connector`
- `doc_type`
- `jurisdiction`
- `environment` when applicable
- `review_status`
- source URI, URL, or repository path

Every search result returns citation fields and metadata. Connector filtering
must be verified against FlowAccount and PEAK endpoint dictionaries. A plain
FlowAccount endpoint query must not rank PEAK above FlowAccount after connector
inference or workspace routing is applied.

Hash embeddings remain acceptable for the contest deployment only when hybrid
keyword matching produces deterministic connector-specific results. Mercury
does not require its own LLM; the host AI produces the final response.

## Connector Credentials

Public v1 may accept connector credentials through MCP tool arguments because
the user selected a fully public MCP design. Mercury must still follow these
boundaries:

- Never return raw credential values.
- Never include raw values in audit events or exception messages.
- Encrypt persisted credentials before storage.
- Store credentials under the selected public workspace ID.
- Show credential field names and fingerprints only.
- Validate with the safest provider-specific probe before enabling reads.
- Do not enable production-changing calls.

This design does not claim that public workspace IDs provide private customer
security. Private deployments require a later authentication layer.

## Flow And Write Policy

Mercury Flows may be created, validated, saved, and dry-run in a public
workspace. Flow execution is constrained as follows:

- Knowledge retrieval and report assembly are allowed.
- Connector validation and approved read-only endpoint calls are allowed.
- Production create, update, delete, payment, void, email, share, approval, and
  journal-posting operations are blocked in public v1.
- A flow that requests a blocked capability returns a structured `blocked`
  result before calling the ERP API.

Plugin capability metadata should describe the deployed behavior accurately.
It should not claim unrestricted ERP write access.

## Error Handling

All tools return structured status values such as:

- `ok`
- `requires_workspace`
- `requires_setup`
- `awaiting_credentials`
- `validation_failed`
- `connected_read_only`
- `blocked`

Expected setup states are normal tool results, not protocol errors. Provider
failures include the connector ID, environment, safe step name, and sanitized
message. They never include credential values or raw provider payloads.

The obsolete remote result containing `/root/.mercury-agent` is removed.

## Audit

Every MCP tool call records a sanitized event with:

- timestamp and request/session reference
- tool name
- public workspace ID when present
- connector ID and environment when present
- input hash
- status and sanitized output summary

The audit record excludes raw credentials, bearer tokens, tax IDs, emails,
customer records, and full accounting payloads.

## Testing

### Unit And Contract Tests

- Public MCP starts without authentication.
- Plugin MCP config contains no bearer-token requirement.
- Public tool schemas use `workspace_id`, not `client_token`.
- Connector metadata exposes required field names but not values.
- `connector_status` never exposes a local Mercury Agent path.
- Workspace routing applies the selected connector filter.
- Production-changing capabilities are blocked before network calls.
- Audit events contain no raw secrets.

### Integration Tests

- Install the marketplace and plugin in an isolated `CODEX_HOME`.
- Verify Codex registers the bundled remote MCP server.
- Initialize an MCP session and list tools without authorization.
- Create a public workspace and select FlowAccount.
- Retrieve FlowAccount endpoint context without PEAK results.
- Run a Mercury Flow in dry-run mode.
- Validate a connector with test credentials only when explicitly available.

### Production Smoke Tests

- `/healthz` reports public HTTP mode and configured Supabase/RAG state.
- MCP `initialize` and `tools/list` succeed without authorization.
- `search_knowledge`, connector-filtered context retrieval, skill loading, and
  flow dry-run return structured results.
- Plugin installation from GitHub discovers all committed skills.

## Acceptance Criteria

- Judges install Mercury Finance from the GitHub marketplace without cloning
  the repo or configuring a token.
- The installed plugin registers the Render MCP endpoint and bundled skills.
- Mercury has no user-facing web dashboard, setup console, or chat page.
- Public tools no longer depend on the old local Mercury Agent runtime.
- Workspace-scoped tools use public `workspace_id` consistently.
- Connector setup shows only fields the selected ERP actually requires.
- RAG automatically stays within the selected ERP documentation.
- FlowAccount and PEAK endpoint dictionaries are committed, ingested, and
  retrievable with citations.
- Production-changing ERP operations are blocked.
- Tests, plugin validation, isolated installation, and production MCP smoke
  tests pass.

## Deferred Beyond Public v1

- OAuth and private tenant isolation
- private customer production credentials
- production ERP mutations
- billing and subscription enforcement
- a public Plugins Directory submission
- any standalone Mercury web application
