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

## Review Fix TDD Evidence

- RED: `uv run pytest tests/test_redaction.py tests/test_credential_cli.py tests/test_cli_search.py -q`
  - Result: failed during collection with `ImportError: cannot import name 'redact_credential_text' from mercury_tools.safety.redaction`.
- RED: `uv run pytest tests/test_redaction.py::test_credential_redaction_handles_basic_pairs_with_repeated_values -q`
  - Result: `1 failed`; the Basic encoding of a repeated credential pair was not redacted.
- GREEN: `uv run pytest tests/test_redaction.py::test_credential_redaction_handles_basic_pairs_with_repeated_values tests/test_redaction.py tests/test_credential_cli.py tests/test_cli_search.py -q`
  - Result: `41 passed in 0.41s`.
- GREEN: `uv run pytest tests/test_local_repository.py tests/test_credential_cli.py tests/test_redaction.py tests/test_cli_search.py -q`
  - Result: `176 passed in 0.74s`.
- GREEN: `uv run pytest -m 'not integration' -q`
  - Result: `940 passed, 1 deselected, 1 warning in 2.22s`; the existing Starlette/httpx deprecation warning remains.
- GREEN: `uv run ruff check .`
  - Result: `All checks passed!`.

## Critical Review Fix: Reversible Credential Transform Closure

### RED/GREEN Evidence

- RED: `uv run pytest tests/test_redaction.py::test_credential_redaction_handles_repeated_mixed_reversible_transformations tests/test_redaction.py::test_credential_redaction_fails_closed_when_sensitive_bounds_are_exceeded tests/test_flowaccount_driver.py::test_flowaccount_probe_redacts_base64_encoded_derived_access_token tests/test_generic_drivers.py::test_probe_redacts_base64_encoded_generic_auth_representations -q`
  - Result: `7 failed in 0.10s`; nested transformations, bounded fail-closed handling, and provider probe boundaries returned unredacted output.
- GREEN: the same focused command
  - Result: `7 passed in 0.08s`.
- RED: `uv run pytest tests/test_redaction.py tests/test_flowaccount_driver.py tests/test_generic_drivers.py tests/test_credential_cli.py -q`
  - Result: `3 failed, 205 passed in 0.94s`; lowercase URL escape forms were not normalized after the initial closure implementation.
- GREEN: the same focused command
  - Result: `208 passed in 0.76s`.

### Security Closure

- `redact_credential_text` now enumerates a bounded closure of URL quote/unquote and standard or URL-safe Base64 representations, including padded and unpadded forms. It limits transform depth to 8, representations to 512, and each UTF-8 representation to 4096 bytes; any bound exhaustion returns `[REDACTED]`.
- FlowAccount company-name probes pass stored credential values together with the derived authorization header and split access token to the central sanitizer. Generic probes pass stored credential values plus header and query auth values, including split Bearer and Basic values. PEAK company-name behavior remains `None`.
- Token-collision validation logic was left unchanged. New mock-transport coverage confirms public probe data and repr omit the derived values and their Base64 representations without recording sensitive fixture values in this report.

### Verification

- `uv run pytest -m 'not integration' -q`
  - Result: `947 passed, 1 deselected, 1 warning in 2.78s`; the existing Starlette/httpx deprecation warning remains.
- `uv run ruff check .`
  - Result: `All checks passed!`.
- `git diff --check`
  - Result: passed.
