# Mercury Tools

Mercury Finance is one hosted MCP and Codex plugin for accounting knowledge, ERP
connector discovery, workspace readiness, and reusable finance workflows. It uses the
LLM already provided by Codex or another MCP host; users do not need an OpenAI API key,
Python, Supabase credentials, or a local Mercury server.

Mercury Tools is an independent open-source project and is not affiliated with Mercury
Technologies, Inc.

## Install the Codex plugin

Run this single command:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools --ref v0.3.1 \
  && codex plugin add mercury-finance@mercury-tools
```

Restart the ChatGPT desktop app, open a new task, and select **Mercury Finance**. The
plugin installs one remote server named `mercury-finance`. On first use, the MCP host
opens a secure Mercury sign-in; users do not manually configure a bearer token,
Supabase key, Python runtime, or local Mercury server.

Example requests:

- `Use Mercury Finance to prepare a Thai VAT context pack.`
- `List the ERP connectors Mercury knows and show the setup steps for PEAK.`
- `Create a Mercury workspace and prepare a company health review.`
- `Plan a reconciliation using Mercury and my connected spreadsheet tools.`

## Hosted MCP

Hosts that support Streamable HTTP can connect directly:

```text
https://mercury-tools-mcp.onrender.com/mcp
```

The plugin has no custom-header, environment-variable, or local launch configuration.
Protected tools use OAuth 2.1 with PKCE through the secure Mercury sign-in. The service
exposes accounting knowledge, workspace, connector, Skill, and controlled operation
tools.

- cited accounting and ERP knowledge
- authenticated tenant and workspace state
- connector catalog, setup, status, and capability routing
- accounting Skill catalog and Skill plans
- Mercury Flow validation, execution planning, and saved flows

## Connector model

Mercury is connector-neutral. It can describe FlowAccount, PEAK, Express, and custom ERP
interfaces from reviewed catalog and RAG sources. Provider authorization is completed
through the provider's approved OAuth or credential setup flow. Encrypted provider
credentials are stored only in a tenant-bound server-side vault; they never enter chat or
model context and are never returned by MCP tools, logs, RAG, or audit output.

Mercury executes only exact provider capabilities that have passed qualification for the
selected environment. Catalog presence alone is not a claim that a provider endpoint is
currently available.

## Development

```bash
git clone https://github.com/natthaphonchop2-creator/mercury-tools.git
cd mercury-tools
uv sync --extra dev
uv run ruff check .
uv run pytest -q
uv run python scripts/review_mcp_contract.py
uv run python scripts/validate_plugin.py
```

Run the MCP locally when developing server changes:

```bash
uv run mercury-tools mcp serve --transport streamable-http --allow-unauthenticated
```

Useful references:

- [Connector catalog](docs/CONNECTOR_CATALOG.md)
- [Action catalog](docs/ACTION_CATALOG.md)
- [Remote deployment](docs/REMOTE_DEPLOYMENT.md)
- [Judge quickstart](docs/JUDGE_QUICKSTART.md)

## Release

The active release path is deliberately small:

1. CI runs lint, tests, MCP contract review, plugin validation, and package build.
2. Render deploys `main`.
3. A semantic version tag such as `v0.3.1` triggers the GitHub release workflow.
4. The release workflow packages the Python distribution and Codex plugin archive.

The release path is intentionally limited to CI, package build, secret scan, Hosted MCP
smoke, and a GitHub release.
