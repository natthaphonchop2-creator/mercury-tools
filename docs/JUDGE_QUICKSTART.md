# Mercury Finance Judge Quickstart

Mercury Finance is an online MCP accounting agent layer for ERP/API connectors,
RAG context, audit-safe workflows, and Thai finance reporting.

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

If Codex asks for authentication, open Mercury Connect and use the issued client
token through Codex's secure MCP auth path.

## Demo Prompts

```text
Connect FlowAccount and validate read-only access
Prepare a Thai VAT context pack
Run a company health check flow
```

## Safety

Do not paste API keys or client secrets into normal chat. Mercury stores
connector credentials server-side and only returns sanitized status to the host
AI.
