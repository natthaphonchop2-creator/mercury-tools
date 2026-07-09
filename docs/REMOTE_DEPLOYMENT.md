# Mercury Tools Public MCP Deployment

This guide deploys the contest build as a public Streamable HTTP MCP. The AI
experience stays inside Codex or another MCP host; Render serves tools and
Supabase stores RAG, public workspace state, encrypted connector records, and
sanitized audit events.

## Current Deployment

- GitHub: `https://github.com/natthaphonchop2-creator/mercury-tools`
- Render service: `mercury-tools-mcp`
- Base URL: `https://mercury-tools-mcp.onrender.com`
- MCP: `https://mercury-tools-mcp.onrender.com/mcp`
- Health: `https://mercury-tools-mcp.onrender.com/healthz`
- Supabase project: `vbnlkqvauqwnjbxngkas`

## 1. Apply Supabase Migrations

Run these files in order against the Mercury Supabase project:

1. `supabase/migrations/0001_mercury_tools_rag.sql`
2. `supabase/migrations/0002_mercury_product_layer.sql`
3. `supabase/migrations/0003_match_knowledge_chunks_null_embedding.sql`
4. `supabase/migrations/0004_match_knowledge_chunks_endpoint_terms.sql`

Required extensions are `vector` and `pgcrypto`. Tables use RLS and revoke
direct access from `public`, `anon`, and `authenticated`; the server-side
service role performs RAG and product operations.

Migration 0004 is the final `match_knowledge_chunks` definition. It supports
connector filters and deterministic endpoint-term matching when embeddings are
null.

## 2. Configure Render

Deploy `render.yaml` as a Render Blueprint or Docker web service. Configure:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
MERCURY_CREDENTIAL_VAULT_SECRET
MERCURY_TOOLS_PUBLIC_BASE_URL=https://mercury-tools-mcp.onrender.com
MERCURY_TOOLS_EMBEDDING_PROVIDER=hash
MERCURY_TOOLS_HTTP_REQUIRE_AUTH=false
MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API=false
```

The vault secret must be a long random server-only value. Do not place the
Supabase service role or vault secret in a plugin, MCP client config, repository
secret file, or tool response.

Mercury does not call an LLM. Hash embeddings plus hybrid keyword search serve
cited context to the user's host AI. An external embedding provider is optional
after the contest.

## 3. Ingest The Wiki

With Supabase variables available locally:

```bash
uv run mercury-tools ingest wiki --path ./wiki
uv run mercury-tools search "FlowAccount invoice endpoint" --json
uv run mercury-tools search "PEAK invoice endpoint" --json
```

Alternatively, configure `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as
GitHub repository secrets and run the **Ingest Mercury Wiki** workflow.

Expected routing:

- a FlowAccount query returns FlowAccount sources only;
- a PEAK query returns PEAK sources only;
- each result includes source metadata and citation fields.

## 4. Verify The Service

```bash
uv run mercury-tools remote verify \
  --url https://mercury-tools-mcp.onrender.com
```

Expected health properties:

```json
{
  "status": "ok",
  "supabase": true,
  "embedding_provider": "hash",
  "embedding_configured": true,
  "mcp_path": "/mcp",
  "http_auth_required": false,
  "legacy_http_api": "disabled"
}
```

Then initialize an MCP session and verify `tools/list`,
`create_public_workspace`, connector discovery, inferred RAG routing, skill
loading, and a read-only flow dry run.

## Contest Boundary

- The MCP endpoint has no login and public `workspace_id` values are routing,
  not authorization.
- Use contest, UAT, sandbox, or disposable demo ERP credentials only.
- Connector values are encrypted server-side and never returned.
- Production-changing ERP capabilities are blocked before connector dispatch.
- Private tenant authentication and production mutations are deferred until
  after the contest.
