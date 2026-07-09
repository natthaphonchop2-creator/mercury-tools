# Task 5 Report: Gate Connector-Backed Skills And Workspace Flows

## Status

DONE

## Summary

- Added workspace connector readiness gating in `src/mercury_tools/mcp/server.py`.
- `run_workspace_flow_tool` now reads the workspace dashboard before loading a saved flow and returns a blocked setup payload when no ready connector profile is available.
- Readiness requires a ready setup state or ready/read-only status plus at least one enabled capability.
- Added public `workspace_connector_ready(dashboard_payload)` wrapper for the task interface.
- Added a regression test that verifies unready connector setup blocks workspace flow execution before the flow is loaded.

## TDD Evidence

- RED: `uv run pytest tests/test_connector_mcp_tools.py::test_run_workspace_flow_requires_ready_connector -v`
  - Result: failed as expected with `AssertionError: assert 'ok' == 'blocked'`.
  - Note: plain `pytest ...` was unavailable on PATH, so the project-standard `uv run pytest ...` runner from README/CI was used.
- GREEN: `uv run pytest tests/test_connector_mcp_tools.py::test_run_workspace_flow_requires_ready_connector -v`
  - Result: passed after the minimal server gate implementation.

## Verification

- `uv run pytest tests/test_connector_mcp_tools.py tests/test_mcp_contract.py -v`
  - Result: 16 passed.
- `uv run ruff check .`
  - Result: all checks passed.
- `uv run pytest -m "not integration"`
  - Result: 110 passed, 1 deselected, 1 warning.
  - Warning: existing `StarletteDeprecationWarning` from `tests/test_http_app.py` importing `TestClient`.

## Commit

- `807fe71 Gate workspace flows on connector readiness`

## Self-Review

- Scope stayed within the task files for code/test changes: `src/mercury_tools/mcp/server.py` and `tests/test_connector_mcp_tools.py`.
- `src/mercury_tools/db/product.py` was inspected but not changed.
- Existing untracked SDD files were left untouched.
- The gate audits blocked attempts using the existing token hash/prefix pattern and does not expose the raw client token.
- Product dashboards from `SupabaseProductStore` include `connector_profiles`; an explicit empty list still blocks. The helper treats a missing `connector_profiles` key as legacy/test-dashboard compatibility so existing contract tests that do not model connector setup continue to pass.

## Concerns

- None for the implemented product path.

## Review Fix Update

### Status

DONE

### Summary

- Changed connector readiness to fail closed when `connector_profiles` is missing or empty.
- Extended `workspace_connector_ready` to check the selected connector, environment, and required capabilities.
- Derived saved workspace-flow connector selection from runtime `env`, saved flow YAML/env metadata, and connector tags.
- Gated both MCP `run_workspace_flow` and HTTP `/api/flows/run` saved-flow execution before loading/executing the saved flow.
- Updated fake dashboards to declare ready connector profiles explicitly and added regression coverage for mismatch/block cases.

### Verification

- `uv run pytest tests/test_connector_mcp_tools.py::test_workspace_connector_ready_blocks_missing_or_empty_profiles tests/test_connector_mcp_tools.py::test_workspace_connector_ready_blocks_ready_profile_without_capabilities tests/test_connector_mcp_tools.py::test_workspace_connector_ready_uses_selected_connector_and_environment tests/test_connector_mcp_tools.py::test_run_workspace_flow_requires_ready_connector tests/test_connector_mcp_tools.py::test_run_workspace_flow_blocks_selected_connector_mismatch tests/test_connector_mcp_tools.py::test_http_workspace_flow_run_requires_ready_connector tests/test_mcp_contract.py::test_mcp_workspace_flow_tools_use_client_token -v`
  - Result: 7 passed, 1 warning.
- `uv run pytest tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_http_app.py -v`
  - Result: 35 passed, 1 warning.
- `uv run ruff check src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py tests/test_mcp_contract.py`
  - Result: all checks passed.
- `uv run pytest -m "not integration"`
  - Result: 115 passed, 1 deselected, 1 warning.

### Concerns

- The only warning is the existing Starlette `TestClient` deprecation warning triggered by the test import.

## Remaining Critical Finding Fix

### Status

DONE

### Summary

- Added connector-backed detection for direct HTTP raw `flow_yaml` runs.
- Detection covers raw flow env connector keys, connector-id tags, connector marker tags, connectorStatus commands, and inline connectorStatus commands parsed through existing flow command parsing.
- `/api/flows/run` now checks workspace connector readiness before executing raw connector-backed flow YAML, including requests that also pass `flow_id`.
- Raw non-connector flows continue to run without connector readiness checks.

### Verification

- `uv run pytest tests/test_http_app.py::test_workspace_flow_run_blocks_connector_backed_raw_yaml_when_unready tests/test_http_app.py::test_workspace_flow_run_allows_non_connector_raw_yaml_without_readiness tests/test_http_app.py::test_workspace_flow_run_records_history_when_supabase_available -v`
  - Result: 3 passed, 1 warning.
- `uv run pytest tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_http_app.py -v`
  - Result: 37 passed, 1 warning.
- `uv run ruff check src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py tests/test_http_app.py tests/test_mcp_contract.py`
  - Result: all checks passed.
- `uv run pytest -m "not integration"`
  - Result: 117 passed, 1 deselected, 1 warning.
- `uv run ruff check .`
  - Result: all checks passed.

### Concerns

- The only warning is the existing Starlette `TestClient` deprecation warning triggered by the test import.

## Critical Environment Gate Fix

### Status

DONE

### Summary

- Changed connector readiness to require both an explicit selected connector and an explicit selected environment before accepting a ready connector profile.
- Connector-backed saved MCP flows with `connector` selected but no `environment` now return the existing connector setup blocked payload before loading/executing the saved flow.
- Connector-backed raw HTTP `flow_yaml` with `connector` selected but no `environment` now returns the existing connector setup blocked payload before execution or run-history recording.
- Updated built-in FlowAccount cheat-sheet/company-health/VAT templates to declare `environment: production`.
- Updated MCP/HTTP callers that intentionally execute connector-backed built-in FlowAccount flows to pass or inherit explicit production environment.
- Non-connector raw HTTP flows remain ungated and continue to run without connector readiness.

### Regression Evidence

- RED: `uv run pytest tests/test_connector_mcp_tools.py::test_workspace_connector_ready_blocks_selected_connector_without_environment tests/test_connector_mcp_tools.py::test_run_workspace_flow_blocks_selected_connector_without_environment tests/test_http_app.py::test_workspace_flow_run_blocks_raw_yaml_with_connector_missing_environment -v`
  - Result: 3 failed before the gate fix, proving the reviewed missing-environment execution path.
- GREEN: `uv run pytest tests/test_connector_mcp_tools.py::test_workspace_connector_ready_blocks_selected_connector_without_environment tests/test_connector_mcp_tools.py::test_run_workspace_flow_blocks_selected_connector_without_environment tests/test_http_app.py::test_workspace_flow_run_blocks_raw_yaml_with_connector_missing_environment -v`
  - Result: 3 passed, 1 existing Starlette `TestClient` deprecation warning.

### Verification

- `uv run pytest tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_http_app.py tests/test_flows.py -v`
  - Result: 85 passed, 1 existing Starlette `TestClient` deprecation warning.
- `uv run pytest -m "not integration"`
  - Result: 120 passed, 1 deselected, 1 existing Starlette `TestClient` deprecation warning.
- `uv run ruff check .`
  - Result: all checks passed.

### Concerns

- The only warning is the existing Starlette `TestClient` deprecation warning triggered by the test import.
