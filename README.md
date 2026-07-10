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
- MCP product layer for workspace setup, connector profiles, skill enablement,
  skill uploads, and usage events. The AI host remains Codex, Cursor, Claude,
  or another MCP client.
- Mercury Flows: Maestro-inspired YAML workflows for accounting agents, with
  CLI validation/execution and MCP tool access.

## Contest Install

Judges add the GitHub marketplace and install **Mercury Finance** from Codex:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref main \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
```

The plugin registers the public Remote MCP at
`https://mercury-tools-mcp.onrender.com/mcp`. No clone, local runtime, browser
dashboard, bearer token, or Mercury LLM is required. The host AI remains Codex,
Cursor, Claude, or another MCP client.

On first use, call `connector_status`. Mercury creates an opaque public
`workspace_id`, guides ERP selection and credentials, validates a low-impact
read endpoint, then routes RAG and flows to the selected connector. Public v1
enables read capabilities only.

See [docs/JUDGE_QUICKSTART.md](docs/JUDGE_QUICKSTART.md) for the demo sequence
and explicit contest security boundary.

## Knowledge Boundaries

Mercury separates three cited knowledge domains:

- ERP endpoint dictionaries for FlowAccount and PEAK request routing.
- Source-backed Thai accounting summaries for TFRS 9, TFRS 15, TFRS 16,
  TAS 2, TAS 7, TAS 12, TAS 16, and TFRS for NPAEs.
- Thai VAT and withholding-tax workflow guidance sourced from the Revenue
  Department.

Domain routing prevents accounting-standard questions from being filled with
unrelated endpoint chunks. Hybrid results below the v1 relevance threshold are
returned as `no_relevant_knowledge`. A ready workspace can retrieve both its
selected ERP context and relevant accounting or tax context in one cited pack.

Mercury stores original summaries and source links, not complete copyrighted
standards. The host LLM produces the response, and professional accounting or
tax judgment remains required before filing, posting, or issuing an opinion.

## Local Development

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
uv run mercury-tools flow manifest ./my-mercury-flows --json
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
  --url https://mercury-tools-mcp.onrender.com
```

Mercury is MCP/plugin-first. The hosted root is only a minimal server landing;
it is not a setup console, dashboard, or chat surface. Public workspaces and
encrypted connector-vault records are stored through Supabase. MCP responses
show only field names, fingerprints, setup state, and sanitized summaries.

## Mercury Flows

Mercury Flows follow the same product idea that makes Maestro easy: readable
YAML files interpreted at runtime, instead of hard-coded automation scripts.
For Mercury, the commands are accounting-agent commands rather than UI taps.

Example:

```yaml
name: Company Health Check
tags: [accounting, endpoint-capable, flowaccount]
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
- `mercury-tools flow manifest <workspace> --json`
- `mercury-tools flow run-suite <workspace> --dry-run --exclude-tag disabled -e month=2026-09`
- `mercury-tools flow run-suite <workspace> --format junit --output reports/junit.xml`
- `mercury-tools flow run-suite <workspace> --format html --output reports/flow-report.html`
- `mercury-tools flow watch <workspace> --dry-run`
- `mercury-tools flow cheat-sheet`

MCP and HTTP flow runs also accept runtime env overrides. Values are coerced to
strings, matching Maestro-style `-e KEY=value` behavior. Host agents should
prefer `run_mercury_flow`, a single MCP entrypoint that accepts exactly one of
`flow_yaml`, `flow_files`, or `workspace_flow_id`. `run_flow` and
`run_flow_files` remain available as lower-level compatibility tools. Audit and
run history record only the env key names:

```json
{
  "flow_yaml": "name: Monthly VAT\n---\n- emitReport:\n    title: \"VAT ${month}\"",
  "dry_run": true,
  "env": {
    "month": "2026-09",
    "connector": "flowaccount"
  }
}
```

Example `run_mercury_flow` call for a flow pack:

```json
{
  "flow_files": {
    "flows/company-health.yaml": "name: Company Health\\ntags: [accounting]\\n---\\n- emitReport:\\n    title: Company"
  },
  "config_yaml": "flows: flows/**/*.yaml\\nincludeTags: [accounting]\\n",
  "dry_run": true,
  "env": {
    "month": "2026-09"
  }
}
```

For host agents, `inspect_flow_files` returns a Maestro-style workspace
manifest without executing anything. It reports selected/skipped flows,
available tags, execution order, safe env key names, and the MCP/CLI handoff
commands. This is the preferred first call when Codex, Cursor, Claude, or
another agent receives a Mercury flow pack from a user:

```json
{
  "flow_files": {
    "flows/company-health.yaml": "name: Company Health\\ntags: [accounting]\\n---\\n- emitReport:\\n    title: Company"
  },
  "config_yaml": "flows: flows/**/*.yaml\\nincludeTags: [accounting]\\n"
}
```

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

Like Maestro's assertion commands, Mercury `assert` fails a flow when required
conditions are not met. The assertions are adapted for accounting data and MCP
tool output instead of mobile UI selectors: `exists`, `notExists`, `equals`,
`notEquals`, `contains`, `status`, and `minCount` are supported.

```yaml
- assert:
    exists: "${connectorState.status}"
    status:
      value: "${connectorState.status}"
      expected: ok
    minCount:
      value: "${context.context}"
      count: 2
    saveAs: validation
```

Like Maestro's `repeat`, Mercury can run a small command group more than once.
Use `times` for a fixed loop, or `while` with Mercury's deterministic conditions.
When `while` is used without `times`, Mercury caps execution with
`maxIterations` and defaults that cap to `10`. Each iteration exposes
`${repeat.index}`, `${repeat.iteration}`, and `${repeat.remaining}`.

```yaml
- repeat:
    label: Monthly section draft
    times: 3
    commands:
      - emitReport:
          title: "Monthly section ${repeat.iteration}"
          sections:
            - "Prepare one repeated period handoff."
    saveAs: monthlySections
```

Like Maestro's `runFlow`, Mercury can run a subflow file or an inline command
list. Inline `commands` are useful for small conditional accounting handoffs;
`label` names the grouped subflow in reports, and `env` inherits parent values
with per-subflow overrides.

Like Maestro's `retry`, Mercury can retry a small inline command group or flow
file when a transient connector/RAG step fails. `maxRetries` is bounded to `0`
through `3` and defaults to `1`; optional `delayMs` waits between attempts.
Keep retry blocks narrow so real accounting-data issues are not hidden.

```yaml
- retry:
    label: Invoice context retry
    maxRetries: 2
    delayMs: 500
    commands:
      - retrieveContextPack:
          query: "invoice VAT review"
          task: invoice_review_th
          maxChunks: 6
          saveAs: invoiceContext
    saveAs: invoiceRetry
```

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
XML report. For demo reviews or accountant handoffs,
`flow run-suite --format html --output <path>` writes a readable HTML report.
A failed suite exits with code `1` by default; pass `--allow-failures` only when
a pipeline should collect the report without failing the job.

Flow MCP tools:

- `flow_cheat_sheet`
- `check_flow_syntax`
- `run_flow`
- `run_flow_files`
- `run_mercury_flow`
- `save_workspace_flow`
- `list_workspace_flows`
- `run_workspace_flow`

`save_workspace_flow`, `list_workspace_flows`, and `run_workspace_flow` use an
opaque public `workspace_id` to route contest state. The ID is not
authentication and must not be treated as private tenant isolation.

Supported flow commands are read-oriented: `connectorStatus`, `searchKnowledge`,
`retrieveContextPack`, `getDocument`, `runSkill`, `emitReport`, `assert`,
`repeat`, `runFlow`, and `retry`. Production accounting writes remain out of
scope for v1.

## Environment

Required for live RAG:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `MERCURY_TOOLS_EMBEDDING_PROVIDER=hash` for the Codex/host-AI demo mode
- `MERCURY_TOOLS_HTTP_REQUIRE_AUTH=false` for the public contest MCP endpoint
- `MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API=false` so only MCP, health, and status routes are exposed
- `MERCURY_CREDENTIAL_VAULT_SECRET` for encrypted connector credential records

`OPENAI_API_KEY` is optional and only needed when
`MERCURY_TOOLS_EMBEDDING_PROVIDER=openai`.

The service role key must stay local/server-side. Do not put it in MCP client
configs that sync to cloud services.

## MCP Surface

Tools:

- `search_knowledge`
- `retrieve_context_pack`
- `get_document`
- `create_public_workspace`
- `get_public_workspace`
- `list_connectors`
- `connector_capabilities`
- `connector_status`
- `start_connector_setup`
- `submit_connector_credentials`
- `validate_connector_connection`
- `retrieve_workspace_context_pack`
- `run_accounting_skill`
- `flow_cheat_sheet`
- `check_flow_syntax`
- `inspect_flow_files`
- `run_mercury_flow`
- `run_flow`
- `run_flow_files`
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

## Mercury Finance Codex Plugin

See `docs/JUDGE_QUICKSTART.md` for the contest install flow. The plugin
is installed from the GitHub marketplace and connects Codex to the hosted
Mercury Tools MCP server. Connector credentials stay out of Git. Mercury should
be used from Codex or another MCP host, not from a browser UI.
