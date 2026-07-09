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
3. Use the Mercury Tools repository:

```text
https://github.com/natthaphonchop2-creator/mercury-tools
```

4. Use git ref:

```text
main
```

5. Use relative marketplace path:

```text
.agents/plugins
```

6. Codex discovers the `Mercury Finance` plugin.
7. Judge clicks `Install plugin`.
8. Judge uses starter prompts in Codex.

This keeps the security confirmation boundary inside Codex while removing the
manual clone/setup burden from the contest demo.

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
      "url": "https://mercury-tools-mcp.onrender.com/mcp"
    }
  }
}
```

The plugin must not contain Supabase service role keys, server bearer tokens,
accounting connector credentials, or user secrets.

If the host app requires MCP auth configuration during install, the plugin must
direct the user to Mercury Connect to obtain a scoped `mc_...` client token. The
repository plugin files must remain safe to publish publicly.

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
4. The judge pastes that token into the host app's secure MCP auth prompt.
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
- No secrets are committed.
