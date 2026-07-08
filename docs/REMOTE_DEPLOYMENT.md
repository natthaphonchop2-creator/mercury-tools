# Mercury Tools Remote MCP Deployment

This guide turns Mercury Tools into a cloud-hosted Streamable HTTP MCP server.

## 1. Prepare Supabase

Create a Supabase project and run:

```sql
-- supabase/migrations/0001_mercury_tools_rag.sql
```

Required extensions:

- `vector`
- `pgcrypto`

Required environment variables for the MCP service:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `OPENAI_API_KEY`
- `MERCURY_TOOLS_HTTP_BEARER_TOKEN`

Do not expose the Supabase service role key to MCP clients.

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
  "openai": true,
  "mcp_path": "/mcp",
  "http_auth_required": true,
  "http_auth_configured": true
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
- `OPENAI_API_KEY`

Then run the `Ingest Mercury Wiki` workflow manually.

## 4. MCP client connection

Remote MCP endpoint:

```text
https://your-service.example.com/mcp
```

Send this header:

```text
Authorization: Bearer <MERCURY_TOOLS_HTTP_BEARER_TOKEN>
```

Client configuration differs by host app. The important contract is:

- transport: Streamable HTTP
- URL: deployed `/mcp` endpoint
- auth header: bearer token

## 5. Security rules

- Keep connector credentials outside Supabase.
- Keep Supabase service role key server-side only.
- Use a long random `MERCURY_TOOLS_HTTP_BEARER_TOKEN`.
- Rotate the bearer token before sharing a demo build.
- Keep production accounting write tools disabled until tenant auth and approval workflows exist.
