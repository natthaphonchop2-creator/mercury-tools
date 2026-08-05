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

The MCP uses Streamable HTTP. Public health, legal, support, and OAuth metadata routes
remain available without a session. Protected tools require a secure Mercury sign-in
using OAuth 2.1 with PKCE. Clients do not manually configure a Mercury bearer token,
Supabase key, custom header, environment variable, or local launch command.

## Render configuration

Deploy `main` using `render.yaml` and configure these server-side values:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
MERCURY_TOOLS_PUBLIC_BASE_URL=https://mercury-tools-mcp.onrender.com
MERCURY_TOOLS_EMBEDDING_PROVIDER=hash
MERCURY_V1_ENABLED=true
MERCURY_TOOLS_HTTP_REQUIRE_AUTH=true
MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API=false
```

Configure every `sync: false` value declared by `render.yaml` in the Render dashboard.
Provider OAuth exchanges happen server-side. Encrypted provider credentials are stored
in tenant-bound Supabase records using an encryption key held only by Render; they never
enter chat or model context and are never returned to MCP clients.

## Supabase boundary

Supabase stores:

- reviewed knowledge sources, documents, chunks, and citations
- connector catalog and sanitized capability evidence
- private workspace, membership, Skill, and Flow metadata
- encrypted provider credential envelopes and operation state
- redacted MCP audit metadata

Plaintext ERP API keys, OAuth tokens, and passwords must not be persisted. Raw provider
payloads, tax IDs, and personal contact data must not appear in knowledge, logs, or audit
output.

## Deploy and verify

Render deploys automatically after `main` changes. Verify the public service with:

```bash
curl --fail https://mercury-tools-mcp.onrender.com/healthz
uv run python scripts/smoke_hosted_plugin.py --remote-only
```

Without a test access token, the smoke test verifies health, OAuth discovery, and that
protected MCP access is rejected. Set `MERCURY_SMOKE_ACCESS_TOKEN` only in a private
runtime to exercise authenticated safe calls; the script never prints the token.
