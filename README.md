# Mercury Tools

Mercury Finance is an Accounting and ERP connector platform for cited accounting
knowledge, connector discovery, workspace readiness, and portable accounting Skills.
Mercury Tools is an independent open-source project and is not affiliated with Mercury
Technologies, Inc.

## Installation

### 1. Marketplace one-click plugin

Install **Mercury Finance** from the Codex marketplace. The public plugin registers one
hosted Mercury MCP and needs no repository clone, Python or uv installation, Supabase
configuration, Mercury Owner Token, or ERP secret.

The installed server is `mercury-finance`, with the note **Mercury Accounting and ERP
connector platform.** It exposes exactly 24 hosted tools.

### 2. Hosted MCP URL fallback

When marketplace installation is unavailable, add this hosted endpoint in the MCP host:

```text
https://mercury-tools-mcp.onrender.com/mcp
```

Use HTTP transport. The hosted endpoint has no token, headers, environment variables, or
local launch command in its public configuration.

### 3. GitHub development install

For repository development, clone the source and install development dependencies:

```bash
git clone https://github.com/natthaphonchop2-creator/mercury-tools.git
cd mercury-tools
uv sync --extra dev
uv run ruff check .
uv run pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
```

This development path does not alter the one-click hosted plugin contract.

### 4. Advanced local API-driver / Local Bridge setup

Reviewed API-driver reads and approval-gated ERP mutations, plus Local Bridge work, use a
separately connected local Mercury MCP. It is never auto-registered next to the hosted
server because duplicate Mercury tool names would make routing ambiguous. The advanced-local
server exposes exactly 20 tools.

Start the advanced server only after reviewing
[LOCAL_CREDENTIALS.md](docs/LOCAL_CREDENTIALS.md) and
[ADVANCED_LOCAL_ERP.md](docs/ADVANCED_LOCAL_ERP.md):

```bash
mercury mcp serve-local
```

## Connect a system

1. Call `create_public_workspace` once when no current workspace exists. Reuse its
   `workspace_id` for the remaining steps and keep it private; it is an expiring access
   handle for that workspace.
2. Use `list_connectors` to choose one connector, mode, and environment.
3. Use `get_connector_setup`, then complete provider or host authorization outside
   Mercury.
4. Use `link_connector_profile` with sanitized profile details only.
5. After the host or separately connected local runtime performs the documented safe
   probe, record only its sanitized result with `validate_connector_connection`.
6. Use `connector_status` and `connector_capabilities` to confirm host/local-attested
   readiness.

The hosted product never receives ERP credentials or raw provider payloads. For a
reviewed API-driver write, Mercury returns an advanced-local handoff instead of invoking
the local runtime. A ready status means the host or local runtime supplied matching,
catalog-bound evidence; it does not mean the hosted Mercury server called the ERP itself.

## Connector catalog

[CONNECTOR_CATALOG.md](docs/CONNECTOR_CATALOG.md) lists supported connector families,
connection modes, ownership boundaries, attested readiness, capability states, and review
dates. Catalog presence is not a claim of live production support.

## Safety boundaries

- Hosted profiles contain only sanitized metadata and evidence references.
- Provider OAuth sessions remain owned by the MCP host or provider.
- Local API-driver credentials are entered only through the hidden terminal prompt in
  [LOCAL_CREDENTIALS.md](docs/LOCAL_CREDENTIALS.md).
- Advanced mutations require immutable preparation, one immutable approval, payload
  binding, audit logging, and no replay after an unknown outcome.

## Development checks

```bash
uv run ruff check .
uv run pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
uv run python scripts/validate_release_plugin.py
```
