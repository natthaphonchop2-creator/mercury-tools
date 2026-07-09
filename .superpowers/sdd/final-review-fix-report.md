# Final Review Fix Report

status: completed

summary:
- Fixed raw MCP flow bypass by applying connector-backed raw-flow detection and workspace readiness gating to `run_flow`, `run_flow_files`, and `run_mercury_flow(flow_yaml=...)`.
- Moved encrypted connector credential vault data onto profile-owned server-side storage and removed `vault_record` from audit summaries/events.
- Added public connector profile/event sanitization so dashboard and MCP status outputs do not expose raw ciphertext.
- Added environment-specific FlowAccount presets for production and sandbox validation URLs.
- Updated successful validation profile writes to mark ready profiles as `connected_read_only`.
- Added `workspace_connector_status(client_token)` for hosted plugin workflows and updated plugin skills to use it instead of local `connector_status`.

focused tests:
- `uv run pytest tests/test_connector_mcp_tools.py::test_run_flow_blocks_connector_backed_raw_yaml_without_client_token tests/test_connector_mcp_tools.py::test_run_flow_preserves_non_connector_raw_yaml_without_client_token tests/test_connector_mcp_tools.py::test_run_flow_blocks_connector_backed_raw_yaml_when_workspace_unready tests/test_connector_mcp_tools.py::test_run_flow_files_blocks_connector_backed_raw_yaml_without_client_token tests/test_connector_mcp_tools.py::test_run_mercury_flow_blocks_connector_backed_flow_yaml_without_client_token tests/test_connector_mcp_tools.py::test_workspace_connector_status_returns_token_scoped_sanitized_profiles tests/test_connector_mcp_tools.py::test_workspace_connector_status_requires_setup_without_ready_profile -q`
  - 7 passed, 1 existing Starlette TestClient warning.
- `uv run pytest tests/test_connector_setup.py::test_validate_flowaccount_uses_sandbox_token_and_company_info_urls tests/test_connector_setup.py::test_start_connector_setup_stores_environment_specific_preset tests/test_connector_setup.py::test_ready_connector_profile_metadata_sets_connected_read_only_status tests/test_connector_setup.py::test_product_table_credentials_store_server_vault_on_profile_not_audit tests/test_product_fallback.py::test_product_store_audit_fallback_encrypts_connector_credentials tests/test_plugin_package.py::test_hosted_workflow_skills_use_token_scoped_connector_status -q`
  - 6 passed.
- `uv run pytest tests/test_connector_mcp_tools.py tests/test_connector_setup.py tests/test_product_fallback.py tests/test_plugin_package.py -q`
  - 62 passed, 1 existing Starlette TestClient warning.
- `uv run pytest tests/test_mcp_contract.py tests/test_http_app.py tests/test_connector_catalog.py -q`
  - 26 passed, 1 existing Starlette TestClient warning.

full verification:
- `uv run pytest -q`
  - 144 passed, 1 skipped, 1 existing Starlette TestClient warning.
- `uv run ruff check .`
  - All checks passed.

concerns:
- The only observed warning is the existing Starlette `TestClient` deprecation warning from tests importing `starlette.testclient.TestClient`.
