# Mercury Finance v0.3.1 Quickstart

Mercury Finance installs one hosted MCP named `mercury-finance`. No repository clone,
Python runtime, Supabase key, Mercury token, or local server is required.

## Install

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools --ref v0.3.1 \
  && codex plugin add mercury-finance@mercury-tools
```

Restart the ChatGPT desktop app and open a new task. The installed server must point to:

```text
https://mercury-tools-mcp.onrender.com/mcp
```

## Demo 1: Accounting knowledge

Ask:

```text
Use Mercury Finance to retrieve a cited Thai VAT context pack for input tax review.
```

Mercury should use `retrieve_context_pack` and return citations from the hosted RAG
store.

## Demo 2: ERP connector discovery

Ask:

```text
Use Mercury Finance to list supported ERP connectors and explain the setup for PEAK
production without asking me for credentials.
```

Mercury should use `list_connectors` and `get_connector_setup`. Catalog presence is not
presented as live provider readiness.

## Demo 3: Workspace and finance workflow

Ask:

```text
Create a Mercury workspace and prepare an evidence-backed company health review plan.
```

Mercury should create one workspace, inspect connector readiness, and run the appropriate
accounting Skill plan. It must not invent provider evidence.

## Demo 4: Work with other connected tools

Ask:

```text
Use Mercury Finance and my connected spreadsheet tools to plan an accounts-receivable
reconciliation. Preserve source provenance and do not post an ERP mutation.
```

The host can combine Mercury's accounting plan with Google Sheets, Drive, email, or
another installed provider. Each provider remains separately authorized by the host.

## Remove

```bash
codex plugin remove mercury-finance@mercury-tools
codex plugin marketplace remove mercury-tools
```
