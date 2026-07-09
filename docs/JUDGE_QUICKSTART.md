# Mercury Finance Judge Quickstart

Mercury Finance is an online MCP accounting agent layer for ERP/API connectors,
RAG context, audit-safe workflows, and Thai finance reporting.

## Connect In Codex

Open Mercury Connect:

```text
https://mercury-tools-mcp.onrender.com/connect
```

Enter the invite code, email, company name, and choose Codex. Mercury returns
one copy-paste command that installs the `Mercury Finance` plugin and connects
Codex to the hosted MCP server.

The generated command includes `codex plugin marketplace add`, `codex plugin add
mercury-finance`, and `codex mcp add` so judges do not need to assemble the MCP
setup manually.

Advanced MCP endpoint:

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
