# Task 7 Report: Codex Plugin Package

## Status

DONE

## Summary

- Added `.agents/plugins/marketplace.json` pointing `mercury-finance` to `./plugins/mercury-finance`.
- Added Mercury Finance Codex plugin metadata and remote MCP config for `https://mercury-tools-mcp.onrender.com/mcp`.
- Added seven compact plugin skills that route host AI workflows to Mercury MCP tools for connector setup, VAT, invoice review, management reports, company health, setup guidance, and Mercury Flows.
- Connector credential setup is gated, tells the host not to ask users to paste secrets in normal chat, and requires validation before continuing.
- Added plugin package tests covering marketplace path, remote MCP config, gated connector skill wording, skill tool routing, compact skill docs, and secret-like env literal hygiene.

## TDD Evidence

- RED: `uv run pytest tests/test_plugin_package.py -v`
  - Result: 5 failed as expected with missing `.agents/plugins/marketplace.json`, plugin metadata, and skill files.
  - Note: plain `pytest ...` was unavailable on PATH, so `uv run pytest ...` was used.
- GREEN: `uv run pytest tests/test_plugin_package.py -v`
  - Result: 5 passed after adding the plugin package files and skills.

## Verification

- `uv run pytest tests/test_plugin_package.py -v`
  - Result: 5 passed.
- `uv run ruff check .`
  - Result: all checks passed.
- `uv run pytest -q`
  - Result: 131 passed, 1 skipped, 1 existing Starlette `TestClient` deprecation warning.

## Concerns

- The only observed warning is the existing Starlette `TestClient` deprecation warning triggered by `tests/test_connector_mcp_tools.py`.
