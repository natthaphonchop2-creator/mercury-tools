# Mercury Tools Remote MCP Deployment

This guide turns Mercury Tools into a cloud-hosted Streamable HTTP MCP server.

## Current contest deployment

- GitHub repo: `https://github.com/natthaphonchop2-creator/mercury-tools`
- Render service: `mercury-tools-mcp`
- Render URL: `https://mercury-tools-mcp.onrender.com`
- Mercury server landing: `https://mercury-tools-mcp.onrender.com/`
- MCP endpoint: `https://mercury-tools-mcp.onrender.com/mcp`
- Health endpoint: `https://mercury-tools-mcp.onrender.com/healthz`
- Supabase project ref: `vbnlkqvauqwnjbxngkas`
- Supabase URL: `https://vbnlkqvauqwnjbxngkas.supabase.co`

The contest MCP endpoint is public/read-oriented so judges can install the
GitHub marketplace plugin and use Mercury tools without manually managing a
bearer token. Private deployments can enable bearer auth later with
`MERCURY_TOOLS_HTTP_REQUIRE_AUTH=true`.

## 1. Prepare Supabase

Create a Supabase project and run both migrations:

```sql
-- supabase/migrations/0001_mercury_tools_rag.sql
-- supabase/migrations/0002_mercury_product_layer.sql
```

Required extensions:

- `vector`
- `pgcrypto`

Required environment variables for the MCP service:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `MERCURY_TOOLS_EMBEDDING_PROVIDER=hash`
- `MERCURY_TOOLS_HTTP_REQUIRE_AUTH=false` for the contest/demo MCP endpoint
- `MERCURY_CONNECT_INVITE_CODE`
- `MERCURY_CONNECT_SIGNING_SECRET`

Do not expose the Supabase service role key to MCP clients.
Do not expose raw connector credentials through MCP outputs. For private
customer environments, enable MCP auth or put the service behind the host
platform's authentication layer.

Mercury Tools v1 does not need to call an LLM by itself. In contest/demo mode it
uses deterministic local `hash` embeddings and serves cited context packs to the
host AI tool, such as Codex, Cursor, or Claude Desktop. Set
`MERCURY_TOOLS_EMBEDDING_PROVIDER=openai` and provide `OPENAI_API_KEY` only if
you explicitly want OpenAI embeddings later.

If the Supabase project is in a different organization than the currently
connected Codex/Supabase integration, re-authenticate that integration with an
account that is a member of the target organization before applying the
migration. The target project for Mercury Tools v1 is:

```text
vbnlkqvauqwnjbxngkas
```

For a dashboard-first setup, paste the full migrations from:

```text
supabase/migrations/0001_mercury_tools_rag.sql
supabase/migrations/0002_mercury_product_layer.sql
```

into the Supabase SQL Editor for that project and run them once in order.

The migrations are private by default:

- RLS is enabled on all Mercury RAG and product tables.
- `anon` and `authenticated` are revoked.
- `service_role` is the only role granted table access.
- `match_knowledge_chunks` execution is granted only to `service_role`.

## 2. Deploy the MCP service

Recommended v1 route for the contest build:

1. Push this repo to GitHub.
2. Create a Render Blueprint from `render.yaml`, or create a Docker web service.
3. Set the secret env vars in the cloud provider.
4. Set `MERCURY_TOOLS_PUBLIC_BASE_URL` to the deployed service URL.
5. Confirm:

```bash
curl https://your-service.example.com/healthz
```

Expected response:

```json
{
  "status": "ok",
  "supabase": true,
  "openai": false,
  "embedding_provider": "hash",
  "embedding_configured": true,
  "mcp_path": "/mcp",
  "http_auth_required": false
}
```

## 3. Ingest the LLM Wiki

After migration and env vars are ready:

```bash
uv run mercury-tools ingest wiki --path ./wiki
```

For GitHub-based ingestion, add these repository secrets:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Then run the `Ingest Mercury Wiki` workflow manually.

Safe local secret entry pattern:

```bash
export SUPABASE_URL="https://vbnlkqvauqwnjbxngkas.supabase.co"
read -rsp "SUPABASE_SERVICE_ROLE_KEY: " SUPABASE_SERVICE_ROLE_KEY; echo

gh secret set SUPABASE_URL --repo natthaphonchop2-creator/mercury-tools --body "$SUPABASE_URL"
gh secret set SUPABASE_SERVICE_ROLE_KEY --repo natthaphonchop2-creator/mercury-tools --body "$SUPABASE_SERVICE_ROLE_KEY"
```

Set the same values in the Render service environment. After that, redeploy and
confirm `/healthz` returns `"supabase": true` and `"embedding_configured": true`.

Set product-layer secrets in Render as well:

```text
MERCURY_CONNECT_INVITE_CODE=<demo invite code>
MERCURY_CONNECT_SIGNING_SECRET=<long random signing secret>
```

Users connect through their MCP host. The browser root is only a server landing
page:

```text
https://mercury-tools-mcp.onrender.com/
```

This page is not the Mercury chat/runtime surface, not a setup gateway, and not
a product dashboard. Users install the GitHub marketplace plugin and let the
host AI client call the MCP endpoint.

If `0002_mercury_product_layer.sql` is not applied yet, client-token issuance
still works, but product persistence runs in degraded mode. After `0002` is
applied, the product layer persists:

- workspaces
- workspace members
- client token records
- connector profiles
- enabled workspace skills
- uploaded skill drafts
- product usage/audit events
- sanitized Mercury Flow run history

In v1, connector profiles intentionally do not store raw accounting API keys or
client secrets. Store those in a proper host/user secret vault until Mercury has
a dedicated encrypted connector vault.

You can verify the deployed service from this repo with:

```bash
uv run mercury-tools remote verify \
  --url https://mercury-tools-mcp.onrender.com
```

The command exits with code `0` only when:

- `/healthz` is reachable.
- Supabase env vars and the selected embedding provider are configured on Render.
- The MCP endpoint is reachable with the configured public/private auth mode.

## 4. MCP client connection

Remote MCP endpoint:

```text
https://your-service.example.com/mcp
```

Client configuration differs by host app. The important contract is:

- transport: Streamable HTTP
- URL: deployed `/mcp` endpoint
- auth: none for the contest public demo endpoint; bearer/OAuth for private deployments

## 5. Security rules

- Keep connector credentials outside Supabase.
- Keep Supabase service role key server-side only.
- Keep the contest public endpoint read-oriented.
- Use bearer/OAuth before enabling private customer data or production mutations.
- Keep production accounting write tools disabled until tenant auth and approval workflows exist.
