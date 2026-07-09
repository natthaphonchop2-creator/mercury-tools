# Mercury Tools

Mercury Tools is the MCP and RAG companion repo for Mercury Agent. It exposes
accounting knowledge, curated LLM Wiki pages, connector metadata, and skill
prompts to MCP hosts such as Codex, Cursor, and Claude Desktop.

v1 is remote-first and read-oriented:

- Python package with `mercury-tools` CLI
- MCP server, Streamable HTTP first
- Supabase Postgres + pgvector RAG store
- Hybrid search over curated knowledge
- Context packs with citations for host agents
- Redacted MCP audit events
- Mercury Connect control plane for workspace setup, connector profiles, skill
  enablement, skill uploads, and usage events. The AI host remains Codex,
  Cursor, Claude, or another MCP client.
- Mercury Flows: Maestro-inspired YAML workflows for accounting agents, with
  CLI validation/execution and MCP tool access.

## Quick Start

```bash
cd mercury-tools
cp .env.example .env
uv sync --extra dev
```

Apply the Supabase migrations in `supabase/migrations/` to your Supabase
project, then ingest the seed wiki:

```bash
uv run mercury-tools doctor
uv run mercury-tools ingest wiki --path ./wiki
uv run mercury-tools search "vat input tax" --json
```

Create and test a Mercury Flow:

```bash
uv run mercury-tools flow init-workspace ./my-mercury-flows
uv run mercury-tools flow list ./my-mercury-flows
uv run mercury-tools flow run-suite ./my-mercury-flows --dry-run
uv run mercury-tools flow run-suite ./my-mercury-flows --dry-run -e month=2026-09
uv run mercury-tools flow watch ./my-mercury-flows --dry-run
uv run mercury-tools flow run-suite ./my-mercury-flows --format junit --output reports/junit.xml
uv run mercury-tools flow init ./my-flow.yaml --template company-health
uv run mercury-tools flow validate ./my-flow.yaml
uv run mercury-tools flow run ./my-flow.yaml --dry-run
uv run mercury-tools flow list ./examples/flows
uv run mercury-tools flow run-suite ./examples/flows --dry-run
uv run mercury-tools flow push ./examples/flows --dry-run
```

Start the remote MCP server locally:

```bash
uv run mercury-tools mcp serve
```

The default remote endpoint is:

```text
http://localhost:8000/mcp
```

For stdio compatibility:

```bash
uv run mercury-tools mcp serve --transport stdio
```

Example local stdio MCP client config:

```json
{
  "mcpServers": {
    "mercury-tools": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/mercury-tools",
        "run",
        "mercury-tools",
        "mcp",
        "serve",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

Remote deployment guide:

- [docs/REMOTE_DEPLOYMENT.md](docs/REMOTE_DEPLOYMENT.md)

Verify the current contest Render deployment:

```bash
uv run mercury-tools remote verify \
  --url https://mercury-tools-mcp.onrender.com \
  --token-file ~/.mercury-tools/render-mcp-token.txt
```

Open the product onboarding layer:

```text
https://mercury-tools-mcp.onrender.com/
```

Mercury Connect issues per-user signed MCP tokens from an invite code and
generates copy-ready MCP config for Codex, Cursor, Claude, or generic MCP hosts.
Users should not use the server bearer token file directly.

The browser setup console is split into focused pages instead of one large
dashboard. It is not the main Mercury chat surface; Codex, Cursor, Claude, or
another MCP host remains the user-facing AI:

- `/` is the Start page and setup map
- `/connect` creates or restores a host MCP connection
- `/workspace` shows workspace identity and team access
- `/connectors` saves accounting connector profiles and encrypted credentials
- `/knowledge` explains the Supabase RAG/wiki layer used by MCP context tools
- `/skills` enables or uploads workspace skills
- `/flows` validates, saves, and dry-runs Mercury Flow YAML for the workspace
- `/audit` reviews usage and audit events

Mercury Connect also exposes product APIs:

- `GET /api/dashboard`
- `POST /api/team/invite`
- `POST /api/connectors/setup`
- `POST /api/connectors/credentials`
- `POST /api/skills/enable`
- `POST /api/skills/upload`
- `POST /api/flows/validate`
- `POST /api/flows/save`
- `POST /api/flows/import`
- `POST /api/flows/run`

These APIs require a Mercury client token (`mc_...`) generated from the Connect
page. The server bearer token is for MCP/admin compatibility, not normal users.
Connector credentials are not stored in Supabase in v1; connector profiles store
the selected program, environment, company label, and required secret fields.

If the dedicated product tables from `0002_mercury_product_layer.sql` have not
been applied yet, Mercury falls back to an event-sourced product state stored in
the existing `mcp_audit_events` table. This keeps the demo usable while the
database owner applies the full product migration later.

Connector credentials are accepted through the Connect UI and encrypted before
they are stored in the event-backed vault. Dashboard responses show only field
names, fingerprints, and configuration status; raw credentials are not returned.

## Mercury Flows

Mercury Flows follow the same product idea that makes Maestro easy: readable
YAML files interpreted at runtime, instead of hard-coded automation scripts.
For Mercury, the commands are accounting-agent commands rather than UI taps.

Example:

```yaml
name: Company Health Check
tags: [accounting, read-only, flowaccount]
env:
  jurisdiction: TH
  connector: flowaccount
---
- retrieveContextPack:
    query: "company health check revenue VAT cash flow accounting Thailand"
    task: "company_health_check_th"
    filters:
      jurisdiction: "${jurisdiction}"
      connector: "${connector}"
    maxChunks: 8
    saveAs: context
- runSkill:
    skillId: company-health-check-th
    inputs:
      context_query: "{{ context.query }}"
    evidenceMode: true
    saveAs: skill
- emitReport:
    when:
      equals:
        value: "${connector}"
        expected: flowaccount
    title: "FlowAccount-only handoff"
    sections:
      - "This step runs only when the selected connector is FlowAccount."
- runFlow:
    label: Production review handoff
    when:
      equals:
        value: "${environment}"
        expected: production
    env:
      review_level: controller
    commands:
      - emitReport:
          title: "Review ${review_level}"
          sections:
            - "Only generated for production runs."
    saveAs: productionReview
- emitReport:
    title: "Company health-check context pack"
    sections:
      - "Use skill {{ skill.skill_id }} and the cited context pack to answer."
```

Flow CLI:

- `mercury-tools flow init-workspace <path> --connector flowaccount --month YYYY-MM`
- `mercury-tools flow init <path> --template company-health`
- `mercury-tools flow validate <path>`
- `mercury-tools flow run <path> --dry-run -e month=2026-09`
- `mercury-tools flow list <workspace> --tag accounting`
- `mercury-tools flow run-suite <workspace> --dry-run --exclude-tag disabled -e month=2026-09`
- `mercury-tools flow run-suite <workspace> --format junit --output reports/junit.xml`
- `mercury-tools flow watch <workspace> --dry-run`
- `mercury-tools flow push <workspace> --url https://mercury-tools-mcp.onrender.com --client-token <mc_...>`
- `mercury-tools flow cheat-sheet`

Workspace config:

```yaml
# config.yaml or mercury.yaml
flows:
  - "flows/**/*.yaml"
includeTags: [accounting]
excludeTags: [disabled]
testOutputDir: ".mercury/reports"
env:
  jurisdiction: TH
  connector: flowaccount
  month: "2026-07"
executionOrder:
  continueOnFailure: true
  flowsOrder:
    - company-health
    - vat-summary
```

Like Maestro's `-e KEY=value` runtime parameters, Mercury accepts `-e` or
`--env` on `flow run`, `flow run-suite`, and `flow watch`. These values are
strings and override flow/workspace `env` values for that run.

Like Maestro's `when` blocks, Mercury commands can run conditionally. v1 keeps
this deterministic for accounting workflows: `true`, `exists`, `notExists`,
`equals`, and `notEquals` are supported, and multiple conditions are ANDed.
Arbitrary JavaScript evaluation is intentionally not enabled in Mercury v1.

Like Maestro's `runFlow`, Mercury can run a subflow file or an inline command
list. Inline `commands` are useful for small conditional accounting handoffs;
`label` names the grouped subflow in reports, and `env` inherits parent values
with per-subflow overrides.

This mirrors Maestro's workspace model at the Mercury layer: config, folder
architecture, tag-based discovery, deterministic execution order, output
reports, and interpreted execution are separated from the host AI conversation.

`flow init-workspace` creates a runnable starter workspace with:

- `config.yaml`
- `flows/company-health.yaml`
- `flows/vat-summary.yaml`
- `README.md`

When `testOutputDir` is set, `flow run-suite` writes
`suite-report.json` with the selected flow order, step summaries, artifacts, and
sanitized variables.

For CI systems, `flow run-suite --format junit --output <path>` writes a JUnit
XML report. A failed suite exits with code `1` by default; pass
`--allow-failures` only when a pipeline should collect the report without
failing the job.

Flow MCP tools:

- `flow_cheat_sheet`
- `check_flow_syntax`
- `run_flow`
- `save_workspace_flow`
- `list_workspace_flows`
- `run_workspace_flow`

`save_workspace_flow`, `list_workspace_flows`, and `run_workspace_flow` use the
Mercury client token generated by the Connect page to resolve the correct
workspace. They do not require or expose the Supabase service role key to the
MCP host.

Supported flow commands are read-oriented: `connectorStatus`, `searchKnowledge`,
`retrieveContextPack`, `getDocument`, `runSkill`, `emitReport`, `assert`, and
`runFlow`. Production accounting writes remain out of scope for v1.

## Environment

Required for live RAG:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `MERCURY_TOOLS_EMBEDDING_PROVIDER=hash` for the Codex/host-AI demo mode
- `MERCURY_TOOLS_HTTP_BEARER_TOKEN` for remote MCP auth
- `MERCURY_CONNECT_INVITE_CODE` for the Connect page
- `MERCURY_CONNECT_SIGNING_SECRET` for per-user client tokens

`OPENAI_API_KEY` is optional and only needed when
`MERCURY_TOOLS_EMBEDDING_PROVIDER=openai`.

The service role key must stay local/server-side. Do not put it in MCP client
configs that sync to cloud services.

## MCP Surface

Tools:

- `search_knowledge`
- `retrieve_context_pack`
- `get_document`
- `connector_status`
- `run_accounting_skill`
- `flow_cheat_sheet`
- `check_flow_syntax`
- `run_flow`
- `save_workspace_flow`
- `list_workspace_flows`
- `run_workspace_flow`

Resources:

- `mercury://wiki/index`
- `mercury://wiki/doc/{document_id}`
- `mercury://skills/{skill_id}`
- `mercury://flows/cheat-sheet`
- `mercury://connectors`
- `mercury://audit/{event_id}`

Prompts:

- `company_health_check_th`
- `vat_summary_th`
- `invoice_review_th`
- `management_report_th`
- `connector_setup_guide_th`

## Development

```bash
uv run pytest
uv run ruff check .
uv run mcp run src/mercury_tools/mcp/server.py
```
