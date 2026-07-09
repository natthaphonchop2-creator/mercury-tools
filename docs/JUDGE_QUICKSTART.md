# Mercury Finance Judge Quickstart

Mercury Finance is an online MCP accounting agent layer for ERP/API connectors,
RAG context, audit-safe workflows, and Thai finance reporting.

## Connect In Codex

Mercury is used from Codex through a plugin and remote MCP server. It is not a
web app and the hosted website is not the product surface.

Install the plugin marketplace:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref main \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
```

Install `Mercury Finance` from the plugin list, then configure the remote MCP
server with the token provided by the Mercury demo owner or secure host setup
path.

MCP endpoint:

```text
https://mercury-tools-mcp.onrender.com/mcp
```

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
