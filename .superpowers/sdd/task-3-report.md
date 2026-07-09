# Task 3 Report: Connector Setup MCP Tools

Status: DONE

Commit:
- `b60c3db` Expose connector setup MCP tools

Scope:
- Added MCP tool functions in `src/mercury_tools/mcp/server.py`:
  - `list_connectors()`
  - `start_connector_setup(client_token, connector_id, environment, company_name=None)`
  - `submit_connector_credentials(client_token, connector_id, environment, credentials)`
  - `validate_connector_connection(client_token, connector_id, environment, credentials)`
- Added `tests/test_connector_mcp_tools.py`.
- Did not implement FlowAccount or other ERP HTTP read-only validation. `validate_connector_connection` is a non-network Task 3 stub that returns `not_validated` with a clear Task 4 message after checking token, connector, environment, and required credential presence.

TDD Evidence:
- First attempted brief command:
  - `pytest tests/test_connector_mcp_tools.py -v`
  - Result: shell could not find `pytest` on PATH.
- RED:
  - `uv run pytest tests/test_connector_mcp_tools.py -v`
  - Result: 6 failed as expected because `list_connectors`, `start_connector_setup`, `submit_connector_credentials`, and `validate_connector_connection` were not exposed by `mercury_tools.mcp.server`.
- GREEN:
  - `uv run pytest tests/test_connector_mcp_tools.py -v`
  - Result: 6 passed.

Verification:
- `uv run ruff check .`
  - Result: passed.
- `uv run pytest tests/test_connector_mcp_tools.py tests/test_connector_setup.py tests/test_product_fallback.py tests/test_mcp_contract.py -v`
  - Result: 27 passed.
- `uv run pytest`
  - Result: 101 passed, 1 skipped, 1 warning.
  - Warning: existing Starlette/httpx deprecation warning in `tests/test_http_app.py`.

Self-Review:
- Write scope kept to requested code/test files plus this report file.
- Existing untracked `.superpowers/sdd/*` files were left untouched.
- MCP audit payloads use token hash/prefix and credential field names only, not credential values.
- Tool outputs are passed through `redact_json` where profiles/results could include sensitive metadata.
- `validate_connector_connection` does not call external ERP APIs.

Concerns:
- None for Task 3.

## Review Fix: Secret-safe MCP credential response

Status: DONE

Changes:
- Updated `submit_connector_credentials` to validate `client_token` before returning connector or credential details.
- Replaced the direct product-store result in MCP output with a public summary containing only `status`, `connector_id`, `environment`, `credential_fields`, and `setup_state`.
- Removed the fixed future token issue time from connector MCP tests so generated test tokens do not expire on future runs.

Verification:
- `uv run pytest tests/test_connector_mcp_tools.py -v`
  - Result: 7 passed.
- `uv run pytest tests/test_connector_mcp_tools.py tests/test_connector_setup.py tests/test_product_fallback.py tests/test_mcp_contract.py -v`
  - Result: 28 passed.
- `uv run ruff check src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py`
  - Result: passed.

Concerns:
- None.
