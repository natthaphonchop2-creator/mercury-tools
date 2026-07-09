# Mercury Codex Plugin Design

Date: 2026-07-09
Status: Draft for user review
Repo: `mercury-tools`

## Decision

Mercury will ship a Codex plugin from the `mercury-tools` GitHub repository.
The plugin will be installed through a repository marketplace link, not by
requiring judges to clone the repo manually and not by attempting silent
auto-install.

Selected approach:

```text
Repo marketplace link + remote MCP
```

The plugin package will provide the marketplace/plugin presentation, starter
prompts, skill guides, icons, and MCP server configuration. The actual tools,
RAG, audit, flows, and product logic will remain in the deployed Mercury Tools
MCP server.

## Judge Install Flow

Judges should not need to clone the repository.

Expected flow:

1. Open Codex.
2. Add a plugin marketplace from GitHub.
3. Use the Mercury Tools repository source:

```text
https://github.com/natthaphonchop2-creator/mercury-tools
```

4. Use git ref:

```text
main
```

5. If Codex asks for sparse paths, include both marketplace metadata and the
   plugin folder:

```text
.agents/plugins
plugins/mercury-finance
```

6. Codex discovers the `Mercury Finance` plugin.
7. Judge clicks `Install plugin`.
8. Judge uses starter prompts in Codex.

This keeps the security confirmation boundary inside Codex while removing the
manual clone/setup burden from the contest demo.

CLI-equivalent setup for judges or maintainers:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref main \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
```

The marketplace file lives at `.agents/plugins/marketplace.json`, but the
marketplace root is the repository root. Therefore `source.path` entries should
point to `./plugins/mercury-finance`, not to a path relative to
`.agents/plugins/`.

## Repository Structure

Target structure:

```text
mercury-tools/
  .agents/
    plugins/
      marketplace.json
  plugins/
    mercury-finance/
      .codex-plugin/
        plugin.json
      .mcp.json
      skills/
        company-health-check-th/
          SKILL.md
        vat-summary-th/
          SKILL.md
        invoice-review-th/
          SKILL.md
        management-report-th/
          SKILL.md
        connector-setup-guide-th/
          SKILL.md
        mercury-flow-runner/
          SKILL.md
      assets/
        logo.png
        mercury-small.svg
  docs/
    JUDGE_QUICKSTART.md
```

## Plugin Identity

Plugin name:

```text
mercury-finance
```

Display name:

```text
Mercury Finance
```

Short description:

```text
Accounting AI for reports, VAT, audit context, and Mercury Flows
```

Category:

```text
Finance
```

Capabilities:

```text
Interactive
Read
```

The plugin should feel like a finance/accounting assistant available inside the
host AI app, not a standalone web app.

## MCP Connection

The plugin MCP config should point to the Render deployment:

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

The plugin must not contain Supabase service role keys, server bearer tokens,
accounting connector credentials, or user secrets.

If the host app requires MCP auth configuration during install, the plugin must
direct the user to Mercury Connect to obtain a scoped `mc_...` client token. The
repository plugin files must remain safe to publish publicly.

## Product Environment

Mercury's contest product runtime should be an online MCP server.

The plugin is the install and discovery package. The product backend is the
hosted Mercury Tools MCP service. The host AI app, such as Codex, Cursor, or
Claude, remains the chat surface and model runtime.

### Render Runtime

Public runtime details that may appear in docs:

```text
Render service: mercury-tools-mcp
Public base URL: https://mercury-tools-mcp.onrender.com
MCP endpoint: https://mercury-tools-mcp.onrender.com/mcp
Health endpoint: https://mercury-tools-mcp.onrender.com/healthz
Mercury Connect: https://mercury-tools-mcp.onrender.com/connect
```

Required Render secret names, not values:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
MERCURY_TOOLS_HTTP_BEARER_TOKEN
MERCURY_CONNECT_INVITE_CODE
MERCURY_CONNECT_SIGNING_SECRET
MERCURY_TOOLS_EMBEDDING_PROVIDER
```

The plugin, quickstart, and marketplace files must not include any Render
secret values.

### Supabase Data Layer

Public data-layer details that may appear in docs:

```text
Supabase project ref: vbnlkqvauqwnjbxngkas
Supabase URL: https://vbnlkqvauqwnjbxngkas.supabase.co
Purpose: RAG, citations, audit events, product workspace state, flow run history
```

Migrations:

```text
supabase/migrations/0001_mercury_tools_rag.sql
supabase/migrations/0002_mercury_product_layer.sql
```

Primary table groups:

```text
knowledge_sources
knowledge_documents
knowledge_chunks
mcp_audit_events
product workspace/member/token/connector/skill/flow tables
```

Supabase service role keys, database passwords, JWT secrets, and raw connector
credentials must remain server-side only.

### Product Boundary

Mercury v1 is not a local LLM, not a local-only CLI, and not a web app-first
product. It is an online MCP product with:

- hosted MCP tools on Render
- Supabase-backed RAG and audit storage
- Codex plugin packaging for install/discovery
- Mercury Connect for scoped client token issuance
- host AI apps as the user-facing conversation surface

The old local Hermes-style CLI is only a shell and should not carry product
logic, connector state, or local RAG.

## Available MCP Tools

The plugin should expose user-facing skills that guide the host agent toward
the existing Mercury Tools MCP tools:

- `search_knowledge`
- `retrieve_context_pack`
- `get_document`
- `connector_status`
- `run_accounting_skill`
- `flow_cheat_sheet`
- `check_flow_syntax`
- `inspect_flow_files`
- `run_mercury_flow`
- `list_workspace_flows`
- `run_workspace_flow`
- `save_workspace_flow`

The plugin should treat `run_mercury_flow` as the preferred high-level flow
entrypoint. Lower-level flow tools can remain available but should not be the
first thing a judge sees.

## ERP Connector Positioning

Mercury should be presented as a connector-first accounting agent layer. The
product value is not only that it can search accounting knowledge, but that it
can connect to accounting or ERP systems that expose APIs, map their endpoints
into accounting capabilities, and let the host AI run safe finance workflows on
top of those capabilities.

Primary positioning:

```text
Mercury connects AI agents to accounting and ERP APIs, then turns those
endpoints into auditable finance workflows.
```

Supported connector model:

- Existing connectors: FlowAccount first, then PEAK Accounting, Express Account,
  and other accounting/ERP systems as connector profiles mature.
- Learnable connectors: a new system can be added by ingesting API docs,
  defining endpoint metadata, mapping endpoints to Mercury capabilities, and
  validating the connector against sandbox or read-only production endpoints.
- Endpoint-to-capability mapping: raw endpoints should be hidden behind
  business capabilities such as `company.info.read`, `documents.invoice.list`,
  `tax.vat_summary.read`, or `journal.draft.create_sandbox`.
- Safety boundary: reads are preferred, production writes are blocked unless a
  future approval workflow explicitly enables them.
- Audit boundary: every connector-backed workflow should preserve evidence,
  citations, status, and sanitized summaries.

Connector learning pipeline:

1. Ingest API documentation into Mercury Wiki.
2. Create a connector manifest with auth, environments, endpoints, schemas, and
   supported capabilities.
3. Validate endpoint reachability and response shape.
4. Map endpoint responses into normalized accounting objects.
5. Publish connector capabilities for use by skills and Mercury Flows.
6. Keep secrets server-side and expose only sanitized connector status to MCP
   clients.

Tool roles in this model:

| Tool | Product role | ERP connector role |
| --- | --- | --- |
| `search_knowledge` | Search accounting, tax, connector, and workflow knowledge. | Finds API docs, endpoint notes, connector rules, and accounting references. |
| `retrieve_context_pack` | Build a cited answer pack for the host AI. | Combines accounting standards with connector-specific endpoint context. |
| `get_document` | Fetch a full indexed document. | Opens a connector doc, API reference, workflow guide, or accounting reference by id. |
| `connector_status` | Show what accounting system is connected and safe to use. | Reports configured ERP/accounting connectors, environment, capabilities, and redacted status. |
| `run_accounting_skill` | Load a guided accounting workflow package. | Applies a skill to the active connector capabilities without exposing raw API details. |
| `run_mercury_flow` | Run or dry-run a Mercury workflow. | Executes connector-backed steps such as read invoices, summarize VAT, and prepare reports. |
| `list_workspace_flows` | Show saved company workflows. | Lists reusable ERP workflows configured for the current workspace. |
| `save_workspace_flow` | Save a reusable workflow. | Stores a new connector-backed workflow after validation. |
| `run_workspace_flow` | Run a saved workflow by id. | Runs an approved workspace flow against the connected accounting or ERP system. |

Potential future connector tools, after the demo core is stable:

- `list_connectors`
- `connector_capabilities`
- `validate_connector_manifest`
- `test_connector_endpoint`
- `ingest_connector_docs`
- `generate_connector_profile`

## Starter Prompts

Use up to three short starter prompts in `plugin.json`:

```text
Prepare a Thai VAT context pack
Run a company health check flow
Search accounting knowledge about input VAT
```

These prompts should demonstrate Mercury as an agentic finance layer:

- It retrieves cited accounting context.
- It can run or dry-run Mercury Flows.
- It can package evidence for the host AI to answer with.

## Skills

The first plugin skill set should be compact and demo-oriented:

### `company-health-check-th`

Guide the host agent to retrieve company health context, use cited knowledge,
check connector status when relevant, and produce a concise Thai management
summary.

### `vat-summary-th`

Guide Thai VAT review workflows. Prefer `retrieve_context_pack` and
`run_accounting_skill` with evidence mode when the user needs support for a VAT
summary.

### `invoice-review-th`

Guide invoice review prompts, anomaly checks, missing evidence flags, and
accountant review points.

### `management-report-th`

Guide management report generation from context packs and Mercury Flow outputs.
The skill should avoid over-showing raw audit paths unless asked.

### `connector-setup-guide-th`

Guide users through Mercury Connect and connector-profile setup. It must not ask
users to paste secrets into normal chat.

### `mercury-flow-runner`

Guide the host agent to use `flow_cheat_sheet`, `check_flow_syntax`, and
`run_mercury_flow`. Use dry-run first when the user is exploring or when a flow
could have external effects.

## Auth And Demo Access

The plugin itself should not store secrets.

Normal contest path:

1. The judge installs the plugin.
2. The judge opens Mercury Connect if Codex asks for MCP authentication.
3. Mercury Connect issues a scoped user or workspace client token.
4. The judge provides that token through Codex's secure MCP auth path or as the
   local environment variable `MERCURY_TOOLS_MCP_TOKEN`.
5. Codex uses the MCP endpoint through the plugin configuration.

The design keeps server bearer tokens and Supabase service role keys on Render.
If additional auth is needed for MCP clients, document it in
`docs/JUDGE_QUICKSTART.md` and keep tokens out of Git.

## Judge Quickstart

Create `docs/JUDGE_QUICKSTART.md` with a short flow:

1. Add Mercury plugin marketplace from GitHub.
2. Install `Mercury Finance`.
3. Connect to Mercury Tools MCP.
4. Try one starter prompt.
5. Open Mercury Connect only when the host asks for workspace credentials.

The quickstart should be screenshot-friendly and avoid internal implementation
language.

## Non-Goals

- Do not rebuild the Mercury product as a web app.
- Do not make Mercury depend on a local-only MCP server for the judge demo.
- Do not make the judge clone the repo manually.
- Do not embed credentials in plugin files.
- Do not move RAG, audit, or flow execution into the plugin package.
- Do not revive the old local Hermes-style accounting connector CLI.

## Acceptance Criteria

- `mercury-tools` contains a repo marketplace at `.agents/plugins/marketplace.json`.
- Codex can discover `Mercury Finance` from the GitHub repository marketplace.
- The plugin page shows Mercury branding, short description, capabilities, and
  starter prompts.
- Installing the plugin registers the remote Mercury Tools MCP server.
- The skill list is visible and maps to the existing MCP tools.
- A judge can run at least one prompt that uses Mercury Tools MCP without
  cloning the repository locally.
- The product environment is documented with public Render and Supabase
  identifiers, while secret names are documented without secret values.
- No secrets are committed.
