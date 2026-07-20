# Mercury Tools

Mercury Finance is a repository-local accounting and ERP plugin for Codex. It
combines cited accounting knowledge with FlowAccount, PEAK, and imported ERP
action catalogs. Codex remains the host AI; Mercury does not run a local LLM.

Mercury Tools is an independent open-source project and is not affiliated with Mercury Technologies, Inc.

## Product Boundaries

- The Codex plugin installs exactly one local `stdio` MCP named
  `mercury-finance` and exposes exactly 19 local tools.
- The Render deployment is a separate 20-tool hosted HTTP surface at `/mcp`.
  Installing the local plugin does not register that hosted endpoint.
- ERP credentials remain in `.mercury/credentials.env` under the selected
  repository. They are never stored in Mercury Cloud, plugin metadata, chat,
  or dotenv files.
- ERP reads, write previews, confirmations, execution state, and the redacted
  audit ledger run locally. Cloud stores catalog, RAG, and audit metadata only.
- A write requires a fresh preview and the catalog's required number of
  explicit confirmations. Mercury never retries an `outcome_unknown` write.

## Public one-click plugin

The public **Mercury Finance** app-plus-skills submission is prepared under
[`submission/openai-plugin`](submission/openai-plugin). It uses the hosted MCP
at `https://mercury-tools-mcp.onrender.com/mcp`, has no custom web UI, and does
not ask end users to clone this repository. After OpenAI review and publication,
users install it directly from the Plugins Directory with one click.

Until the directory review is approved, use the immutable repository
marketplace install below for local ERP execution.

## Install v0.2.2

Install [uv](https://docs.astral.sh/uv/) first and confirm `uvx` is available:

```bash
uvx --version
```

After the reviewed `v0.2.2` release is published, install the immutable
marketplace source:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref v0.2.2 \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
codex plugin add mercury-finance@mercury-tools
codex mcp list --json
```

The installed launcher source is:

```text
git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.2
```

The expected result is one enabled local MCP named `mercury-finance`. See the
[judge quickstart](docs/JUDGE_QUICKSTART.md) for credential setup, safe read,
write preview, Cross-MCP reconciliation, and cleanup commands.

## Local Development

```bash
uv sync --extra dev
uv run mercury doctor --repo-root .
uv run python scripts/validate_release_plugin.py --root .
uv run python scripts/smoke_local_plugin.py
```

Set up ERP credentials only in the repository that owns the accounting work:

```bash
uv run mercury credentials setup flowaccount --env sandbox --repo-root .
uv run mercury credentials test flowaccount --env sandbox --repo-root .
uv run mercury credentials status --repo-root .
```

The setup command prompts locally and does not accept credential values on the
command line. Clear the profile when the work is complete:

```bash
uv run mercury credentials clear flowaccount --env sandbox --repo-root .
```

## Catalog And Write Safety

Use `search_erp_actions` and `get_erp_action_schema` before executing an action.
Tier 0 safe reads run through `run_erp_read`. Tier 1 and Tier 2 mutations start
with `preview_erp_write`, then require one immutable approval before
`confirm_erp_write` and a single `execute_erp_write` call. Standard and elevated
mutations use the same one-approval count; the level describes approval severity,
not an extra prompt. Imported API specifications and trusted-host decisions remain
repository-local.

When a provider outcome is unknown, call `get_erp_request_status` and reconcile
with an approved safe status action or manually. Do not retry the mutation.
See [ACTION_CATALOG.md](docs/ACTION_CATALOG.md) for the complete state model.

## Release Verification

Task 15 prepares release files and workflows only. It does not tag, publish,
deploy, push, open a pull request, or change repository visibility. The manual
release workflow binds every gate to a reviewed `main` SHA before it can
publish assets.

```bash
uv run ruff check .
uv run pytest -q --junitxml=release-evidence/pytest.xml
uv run python scripts/verify_test_skips.py \
  --junit release-evidence/pytest.xml \
  --waivers docs/release/v0.2.2-test-waivers.json
uv run python scripts/validate_release_plugin.py --root .
```

Release details and operator gates are in
[RELEASE_V0.2.2.md](docs/RELEASE_V0.2.2.md). Deployment boundaries are in
[REMOTE_DEPLOYMENT.md](docs/REMOTE_DEPLOYMENT.md), and repository credential
handling is in [LOCAL_CREDENTIALS.md](docs/LOCAL_CREDENTIALS.md).
