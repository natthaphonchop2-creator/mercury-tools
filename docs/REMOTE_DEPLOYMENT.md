# Mercury Tools Hosted HTTP Deployment

The Render service is the separate hosted Mercury MCP. It does not replace the
repository-local Mercury Finance plugin.

Cloud stores catalog, RAG, and audit metadata only. ERP credentials remain repository-local and never enter Render, Supabase, hosted workspace records, MCP arguments, or hosted tool results.

## Public Endpoints

- Base URL: `https://mercury-tools-mcp.onrender.com`
- Mandatory health check: `https://mercury-tools-mcp.onrender.com/healthz`
- Deployment status: `https://mercury-tools-mcp.onrender.com/api/status`
- Streamable HTTP MCP: `https://mercury-tools-mcp.onrender.com/mcp`

The hosted endpoint exposes 20 tools for catalog, cited knowledge, accounting
skills, flow validation, and public workspace metadata. It has no ERP
credential schema and no arbitrary ERP write surface. The local plugin remains
one `mercury-finance` stdio MCP with 19 tools and owns ERP execution.

## Supabase Boundary

Apply every tracked migration in `supabase/migrations/` in lexical order. The
release gate specifically verifies the validation-knowledge migration, RAG
filters, reconciliation skill catalog, batch resolver, and the migration that
removes obsolete Cloud ERP secret storage.

Supabase may contain only:

- immutable ERP action catalog and version metadata;
- approved, sanitized endpoint validation knowledge;
- RAG documents, chunks, citations, and search indexes;
- public skill and flow metadata;
- redacted audit metadata that contains no credential or ERP payload value.

RLS revokes direct validation-table access from `public`, `anon`, and
`authenticated`. Server-side publication uses the service role. ERP request
bodies, local request state, confirmations, credentials, and the local audit
ledger stay under the operator's repository.

## Render Settings

Deploy the reviewed commit using `render.yaml`, then set only the server-side
values required by the hosted metadata surface:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
MERCURY_DEPLOYMENT_COMMIT=<40-character-lowercase-reviewed-git-commit>
MERCURY_TOOLS_PUBLIC_BASE_URL=https://mercury-tools-mcp.onrender.com
MERCURY_TOOLS_EMBEDDING_PROVIDER=hash
MERCURY_TOOLS_HTTP_REQUIRE_AUTH=false
MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API=false
```

`MERCURY_DEPLOYMENT_COMMIT` is a deployment setting, not a value inferred from
the filesystem, a mutable branch, or process state. Set it to the exact reviewed
commit deployed by Render. Do not add FlowAccount, PEAK, or imported ERP
credentials to the Render environment.

## Catalog And RAG Ingestion

With Supabase settings available only to the operator process:

```bash
uv run mercury ingest wiki --path ./wiki
uv run mercury search "FlowAccount invoice endpoint" --json
uv run mercury search "PEAK invoice endpoint" --json
```

Results must preserve connector routing and include citations. Published
validation rows must already be reviewed and sanitized; raw provider traffic is
not an ingestion input.

## Release Verification

The release verifier requires `/healthz` independently of `/api/status` and
compares both requested identity values exactly:

```bash
export REVIEWED_MAIN_SHA='<40-character-lowercase-reviewed-git-commit>'
uv run python scripts/verify_render_release.py \
  --url https://mercury-tools-mcp.onrender.com \
  --version 0.2.1 \
  --commit "$REVIEWED_MAIN_SHA"
```

The command fails unless health, exact package version, exact deployment
commit, MCP initialize/list, 254-action catalog, cited RAG retrieval, hosted
read-only boundary, and Render build/runtime log scans all pass.
