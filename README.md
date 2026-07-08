# Mercury Tools

Mercury Tools is the MCP and RAG companion repo for Mercury Agent. It exposes
accounting knowledge, curated LLM Wiki pages, connector metadata, and skill
prompts to MCP hosts such as Codex, Cursor, and Claude Desktop.

v1 is remote-first and read-oriented:

- Python package with `mercury-tools` CLI
- MCP server, Streamable HTTP first
- Supabase Postgres + pgvector RAG store
- Hybrid search over curated knowledge
- Context packs with citations for host agents
- Redacted MCP audit events

## Quick Start

```bash
cd mercury-tools
cp .env.example .env
uv sync --extra dev
```

Apply the Supabase migration in `supabase/migrations/0001_mercury_tools_rag.sql`
to your Supabase project, then ingest the seed wiki:

```bash
uv run mercury-tools doctor
uv run mercury-tools ingest wiki --path ./wiki
uv run mercury-tools search "vat input tax" --json
```

Start the remote MCP server locally:

```bash
uv run mercury-tools mcp serve
```

The default remote endpoint is:

```text
http://localhost:8000/mcp
```

For stdio compatibility:

```bash
uv run mercury-tools mcp serve --transport stdio
```

Example local stdio MCP client config:

```json
{
  "mcpServers": {
    "mercury-tools": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/mercury-tools",
        "run",
        "mercury-tools",
        "mcp",
        "serve",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

Remote deployment guide:

- [docs/REMOTE_DEPLOYMENT.md](docs/REMOTE_DEPLOYMENT.md)

## Environment

Required for live RAG:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `OPENAI_API_KEY`
- `MERCURY_TOOLS_HTTP_BEARER_TOKEN` for remote MCP auth

The service role key must stay local/server-side. Do not put it in MCP client
configs that sync to cloud services.

## MCP Surface

Tools:

- `search_knowledge`
- `retrieve_context_pack`
- `get_document`
- `connector_status`
- `run_accounting_skill`

Resources:

- `mercury://wiki/index`
- `mercury://wiki/doc/{document_id}`
- `mercury://skills/{skill_id}`
- `mercury://connectors`
- `mercury://audit/{event_id}`

Prompts:

- `company_health_check_th`
- `vat_summary_th`
- `invoice_review_th`
- `management_report_th`
- `connector_setup_guide_th`

## Development

```bash
uv run pytest
uv run ruff check .
uv run mcp run src/mercury_tools/mcp/server.py
```
