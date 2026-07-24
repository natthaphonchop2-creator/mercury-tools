# Mercury Hosted MCP Deployment

The Render service is the single MCP used by the Mercury Finance plugin.

## Public endpoints

- Service: `https://mercury-tools-mcp.onrender.com`
- Health: `https://mercury-tools-mcp.onrender.com/healthz`
- Status: `https://mercury-tools-mcp.onrender.com/api/status`
- MCP: `https://mercury-tools-mcp.onrender.com/mcp`
- Privacy: `https://mercury-tools-mcp.onrender.com/privacy`
- Terms: `https://mercury-tools-mcp.onrender.com/terms`
- Support: `https://mercury-tools-mcp.onrender.com/support`

The MCP uses Streamable HTTP and exposes 24 hosted tools. Public clients do not
provide a Mercury token, Supabase key, custom header, environment variable, or local
launch command.

## Render configuration

Deploy `main` using `render.yaml` and configure these server-side values:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
MERCURY_TOOLS_PUBLIC_BASE_URL=https://mercury-tools-mcp.onrender.com
MERCURY_TOOLS_EMBEDDING_PROVIDER=hash
MERCURY_TOOLS_HTTP_REQUIRE_AUTH=false
MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API=false
```

Do not add FlowAccount, PEAK, Express, or custom ERP credentials to Render. Provider
authorization remains with the ERP or MCP host.

## Supabase boundary

Supabase stores:

- reviewed knowledge sources, documents, chunks, and citations
- connector catalog and sanitized capability evidence
- public workspace, Skill, and Flow metadata
- redacted MCP audit metadata

It must not store ERP API keys, OAuth tokens, passwords, raw provider payloads, tax IDs,
or personal contact data.

## Deploy and verify

Render deploys automatically after `main` changes. Verify the public service with:

```bash
curl --fail https://mercury-tools-mcp.onrender.com/healthz
uv run python scripts/smoke_hosted_plugin.py --remote-only
```

The smoke test initializes MCP, lists the exact hosted tool surface, and executes safe
read-only calls.
