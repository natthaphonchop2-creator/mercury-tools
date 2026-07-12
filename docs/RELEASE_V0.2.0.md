# Mercury v0.2.0 Release Checklist

This document records release preparation for the unified local ERP MCP. It is
not evidence that the `v0.2.0` Git tag already exists. Tagging and clean
marketplace installation happen only after review and merge.

## Local Gate

Run from the repository checkout:

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -m "not integration" -q
uv run pytest tests/integration/test_local_erp_mcp.py -q
uv run mercury doctor --repo-root .
uv run python scripts/validate_release_plugin.py
uv run python scripts/smoke_local_plugin.py
```

The acceptance test uses fake Cloud and fake ERP transports. `smoke_local_plugin.py`
builds a local wheel and launches it with `uvx`; it verifies exactly one
`mercury-finance` stdio server surface with all 19 tools. It intentionally has
no dependency on a remote `v0.2.0` tag.

## CI Contract

CI installs the dev extra, runs Ruff, all non-integration tests, the fake Task
18 acceptance test, offline plugin/release validation, and the local packaged
smoke. CI sets neither live connector flag and has no ERP credentials or live
service dependency.

## Release Boundary

Before creating a release, confirm:

- The plugin manifest declares one local `uvx` MCP server and no HTTP URL or
  credential environment values.
- Documentation states that credentials, audit records, request state, and ERP
  execution stay repository-local; there is no web UI, local LLM, or Cloud
  credential storage.
- FlowAccount and PEAK live checks remain explicit opt-in GET probes only.
- `credentials clear --all` is documented as file removal, not forensic erase.

After PR review is green, the release owner may merge, create and push the
immutable `v0.2.0` tag, then run a clean marketplace install from that tag.
That post-release exercise must confirm one `mercury-finance` MCP and no
retired private MCP. Do not substitute a branch, a mutable reference, or an
installed plugin cache for the tagged package.
