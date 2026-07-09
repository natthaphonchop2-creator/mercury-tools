# Task 4 Report - FlowAccount Read-Only Validation Adapter

Status: DONE

Commit:
- 910f722 Validate FlowAccount connector read-only

Implementation:
- Added `validate_connector_read_only(manifest, credentials, environment)` in `src/mercury_tools/connectors/setup.py`.
- The adapter supports FlowAccount only, checks required credentials and environment, calls the manifest token endpoint, then calls the read-only company info endpoint.
- Token request uses FlowAccount `client_credentials` grant and `flowaccount-api` scope from the manifest preset.
- Company info request uses only `GET {api_base_url}/company/info`.
- Added company-name extraction for `companyName`, `company_name`, and `name`.
- Failure responses are redacted before returning provider payloads.
- Wired MCP `validate_connector_connection` to the adapter and marks connector profile metadata as `ready` only after read-only validation succeeds.
- Replaced the Task 3 non-network stub behavior in MCP tests with monkeypatched HTTP validation expectations.

TDD evidence:
- RED: `uv run --extra dev pytest tests/test_connector_setup.py::test_validate_flowaccount_uses_token_and_company_info -v`
  - Failed as expected with `ImportError: cannot import name 'validate_connector_read_only'`.
- GREEN adapter: `uv run --extra dev pytest tests/test_connector_setup.py::test_validate_flowaccount_uses_token_and_company_info -v`
  - `1 passed`.
- GREEN MCP: `uv run --extra dev pytest tests/test_connector_mcp_tools.py::test_validate_connector_connection_validates_flowaccount_read_only -v`
  - `1 passed`.
- Brief suite: `uv run --extra dev pytest tests/test_connector_setup.py tests/test_connector_mcp_tools.py tests/test_redaction.py -v`
  - `18 passed`.
- Relevant MCP/product regression: `uv run --extra dev pytest tests/test_connector_catalog.py tests/test_connector_setup.py tests/test_connector_mcp_tools.py tests/test_product_fallback.py tests/test_mcp_contract.py tests/test_http_app.py tests/test_redaction.py -v`
  - `49 passed, 1 warning`.
- Unit suite excluding live integration: `uv run --extra dev pytest tests -m 'not integration' -v`
  - `103 passed, 1 deselected, 1 warning`.
- Lint: `uv run --extra dev ruff check src/mercury_tools/connectors/setup.py src/mercury_tools/mcp/server.py tests/test_connector_setup.py tests/test_connector_mcp_tools.py`
  - `All checks passed`.
- Whitespace: `git diff --check`
  - clean.

Security and safety review:
- Tests monkeypatch `httpx.post` and `httpx.get`; no real FlowAccount network calls are made in tests.
- Success payloads do not include access tokens, client secrets, credential fingerprints, ciphertext, or credential values.
- Tests assert the access token, client secret, and client id do not appear in MCP output.
- MCP output still passes through redaction; numeric validation status fields are restored because `token_status` is an HTTP status field, not a token value.
- Profile metadata stores only setup state, enabled capabilities, and validation HTTP statuses.
- Production ERP writes remain blocked; Task 4 only performs token acquisition plus read-only company info validation.

Self-review notes:
- Write scope was limited to the four allowed code/test files plus this report file.
- Existing untracked `.superpowers/sdd` brief/progress/review files were not staged or changed.
- Live integration tests were not run; this matches the brief's requirement to avoid real network calls and use monkeypatched HTTP for validation behavior.

## Review Fix - 2026-07-09

Status: DONE

Commit:
- bf15383 Sanitize FlowAccount validation failures

Implementation:
- Added a credential-aware validation failure sanitizer in `src/mercury_tools/connectors/setup.py`.
- Failure payloads now mask sensitive fields and values from provider responses, including `client_id`, `client_secret`, access tokens, `credential_fingerprints`, `ciphertext`, and credential values echoed inside arbitrary strings.
- Converted `httpx.HTTPError` transport failures into sanitized `validation_failed` dictionaries for token and company-info calls.
- Converted invalid or non-object JSON responses into sanitized `validation_failed` dictionaries without provider stack traces or raw credential values.
- Left `src/mercury_tools/mcp/server.py` unchanged because adapter-level handling now returns sanitized failure dictionaries to the existing MCP wrapper.

Regression tests:
- Added MCP tests for token failure and company-info failure where the provider echoes `client_id`, `client_secret`, access token, `credential_fingerprints`, and `ciphertext`; MCP output does not include the raw values.
- Added adapter tests for `httpx.HTTPError` and invalid JSON response failures with raw credential values embedded in exception text.

Validation evidence:
- Focused failure-path tests: `uv run --extra dev pytest tests/test_connector_setup.py::test_validate_flowaccount_http_error_returns_sanitized_validation_failed tests/test_connector_setup.py::test_validate_flowaccount_invalid_json_returns_sanitized_validation_failed tests/test_connector_mcp_tools.py::test_validate_connector_connection_token_failure_sanitizes_provider_echoes tests/test_connector_mcp_tools.py::test_validate_connector_connection_company_info_failure_sanitizes_provider_echoes tests/test_connector_mcp_tools.py::test_validate_connector_connection_http_error_is_sanitized -v`
  - `5 passed`.
- Relevant Task 4 suite: `uv run --extra dev pytest tests/test_connector_setup.py tests/test_connector_mcp_tools.py tests/test_redaction.py -v`
  - `23 passed`.
- Ruff for touched files: `uv run --extra dev ruff check src/mercury_tools/connectors/setup.py tests/test_connector_setup.py tests/test_connector_mcp_tools.py`
  - `All checks passed`.
- Whitespace: `git diff --check`
  - clean.

## Review Fix - 2026-07-09 - Provider Response Key Redaction

Status: DONE

Commit:
- Created after this report entry with subject `Sanitize validation failure provider keys`.

Implementation:
- Updated `_sanitize_validation_failure_value()` in `src/mercury_tools/connectors/setup.py` to sanitize dictionary keys as well as values.
- Sensitive key names such as `client_id`, `client_secret`, `access_token`, `ciphertext`, and `credential_fingerprints` are replaced with redacted key labels.
- Provider response keys containing raw credential values, access tokens, ciphertext values, or credential fingerprint values are masked before MCP output is returned.
- Validation failure sanitization now pre-collects values from sensitive provider-response fields so the same ciphertext/fingerprint/token value is also redacted if echoed elsewhere as an object key.
- Redacted key collisions are made deterministic without restoring the original sensitive key text.

Regression tests:
- Added an adapter test where provider response keys contain raw `client_id`, `client_secret`, access token, ciphertext, and fingerprint values.
- Updated MCP failure-path tests to assert sensitive provider response key names no longer survive in returned payloads.

Validation evidence:
- Focused key-redaction regression: `uv run --extra dev pytest tests/test_connector_setup.py::test_validate_flowaccount_token_failure_sanitizes_provider_response_keys tests/test_connector_mcp_tools.py::test_validate_connector_connection_token_failure_sanitizes_provider_echoes tests/test_connector_mcp_tools.py::test_validate_connector_connection_company_info_failure_sanitizes_provider_echoes -v`
  - `3 passed`.
- Relevant Task 4 suite: `uv run --extra dev pytest tests/test_connector_setup.py tests/test_connector_mcp_tools.py tests/test_redaction.py -v`
  - `24 passed`.
- Ruff for touched files: `uv run --extra dev ruff check src/mercury_tools/connectors/setup.py tests/test_connector_setup.py tests/test_connector_mcp_tools.py`
  - `All checks passed`.
- Whitespace: `git diff --check`
  - clean.
