# Mercury Finance Judge Quickstart

Mercury Finance exposes one repository-local `stdio` MCP. It performs catalog
search, safe reads, and approval-gated writes locally for FlowAccount, PEAK,
and repository-configured ERP connectors. It has no web UI, no local LLM, no
hosted HTTP MCP, and no Cloud credential storage.

## Pre-Release Local Check

Task 18 prepares the `v0.2.0` release but does not treat the Git tag as already
published. Judge the current checkout locally:

```bash
uv sync --extra dev
uv run python scripts/validate_release_plugin.py
uv run python scripts/smoke_local_plugin.py
uv run mercury mcp serve-local
```

The smoke builds a local wheel and starts one local stdio server. It does not
download a `v0.2.0` Git tag. The remote-tag smoke is intentionally deferred
until after the Task 18 release creates the immutable tag.

## Repository Demo

Open the repository that will own the ERP connection, then use these prompts:

1. `Set up local FlowAccount access for this repository and verify it.`
2. `Search the local ERP action catalog and run a safe read action.`
3. `Preview an approval-gated PEAK write for this repository without executing it.`

Do not put credentials in chat or plugin configuration. Mercury writes local
credential material and redacted audit events under the active repository's
`.mercury/` directory. See [LOCAL_CREDENTIALS.md](LOCAL_CREDENTIALS.md) and
[ACTION_CATALOG.md](ACTION_CATALOG.md) for the operational sequence.

## Marketplace Install After Release

`uvx` must be available through a local [uv installation](https://docs.astral.sh/uv/).
Only after `v0.2.0` has been created, the GitHub marketplace install is:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref v0.2.0 \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
codex plugin add mercury-finance@mercury-tools
codex mcp list
```

The expected installed surface is one server named `mercury-finance`. The
launcher is local `uvx` stdio, not a remote MCP endpoint, and contains no
client token, provider secret, or Supabase service-role key.
