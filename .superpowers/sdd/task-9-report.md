# Task 9 Report: Interactive Credential and Trusted-Host CLI

## Delivered

- Added repository-local `credentials` commands for setup, status, test, and clear.
- Added `connector configure` with exact host confirmation and supported nonsecret OAuth metadata.
- Added the `mercury` console-script alias while retaining `mercury-tools`.
- Extended `doctor` with repository, permission, catalog, cloud URL, connector, and missing-field diagnostics.
- Added the lazy `mcp serve-local` parser boundary without importing the cloud MCP server.
- Added validated, atomic nonsecret safe-probe metadata persistence under the optional `validations` config section.

## TDD Evidence

- RED: `uv run pytest tests/test_credential_cli.py -q` failed because `credentials` and `connector` parsers did not exist.
- RED: the unexpected-driver-error regression test failed by raising the injected secret-bearing exception.
- GREEN: focused tests passed after parser and handler implementation.

## Verification

- `uv run pytest tests/test_credential_cli.py tests/test_cli_search.py -q`: 12 passed.
- `uv run mercury --help`: lists `credentials`, `connector`, `mcp`, `flow`, `search`, and `doctor`.
- `uv run pytest -m 'not integration' -q`: 914 passed, 1 deselected, with one existing Starlette/httpx deprecation warning.
- `uv run ruff check .`: passed.
- `git diff --check`: passed.

## Security Review

- CLI status, doctor, setup output, test metadata, and failures emit field names and sanitized state only.
- Successful probes persist only connector and environment identity, sanitized company display name, connected state, probe action, and timestamp. Existing config schema, trusted hosts, and connector records are retained through atomic writes.
- Interactive setup and trusted-host configuration return nonzero safe errors when stdin is unavailable. Credential testing refuses to call `asyncio.run` from an active event loop.
