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
plugin installs one remote server named `mercury-finance` and requires no authentication.

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

The public configuration has no bearer token, custom headers, environment variables, or
local launch command. The service exposes 24 hosted tools in five groups:

- cited accounting and ERP knowledge
- public workspace state
- connector catalog, setup, status, and capability routing
- accounting Skill catalog and Skill plans
- Mercury Flow validation, execution planning, and saved flows

## Connector model

Mercury is connector-neutral. It can describe FlowAccount, PEAK, Express, and custom ERP
interfaces from reviewed catalog and RAG sources. Provider authorization remains with the
ERP or MCP host. Mercury stores only sanitized connector profile metadata and evidence
references; it does not accept ERP credentials in chat or store raw provider tokens.

When an authorized ERP or productivity provider is already connected to the host, Mercury
returns the ordered capabilities and evidence requirements that the host should use.
Catalog presence alone is not a claim that a provider endpoint is currently available.

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
