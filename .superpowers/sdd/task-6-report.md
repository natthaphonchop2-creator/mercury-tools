# Task 6 Report: Connector-Aware RAG Routing

## Status

DONE

## Summary

- Added MCP tool `retrieve_workspace_context_pack(client_token, query, task=None, max_chunks=12)`.
- The tool validates the Mercury client token, reads the workspace dashboard, and selects the first connector profile that passes the Task 5 readiness rules.
- Ready workspace context retrieval filters RAG with `connector=<ready connector>` and `review_status=reviewed` only.
- The returned payload includes sanitized connector context with connector id, environment, status, setup state, and enabled capabilities.
- If no ready available connector profile exists, the tool returns `requires_setup` with `next_tool=start_connector_setup` and `next_skill=connector-credential-setup-th`.
- Added regression coverage for FlowAccount ready routing and setup-required blocking for PEAK/setup-target or unready profiles.

## TDD Evidence

- RED: `uv run pytest tests/test_connector_mcp_tools.py::test_retrieve_workspace_context_pack_uses_active_connector -v`
  - Result: failed as expected with `ImportError: cannot import name 'retrieve_workspace_context_pack'`.
  - Note: plain `pytest ...` was unavailable on PATH, so `uv run pytest ...` was used.
- GREEN: `uv run pytest tests/test_connector_mcp_tools.py::test_retrieve_workspace_context_pack_uses_active_connector -v`
  - Result: passed after adding the active connector routing path.
- RED: `uv run pytest tests/test_connector_mcp_tools.py::test_retrieve_workspace_context_pack_requires_setup_without_ready_available_connector -v`
  - Result: failed as expected with `AttributeError: 'NoneType' object has no attribute 'get'`.
- GREEN: `uv run pytest tests/test_connector_mcp_tools.py::test_retrieve_workspace_context_pack_uses_active_connector tests/test_connector_mcp_tools.py::test_retrieve_workspace_context_pack_requires_setup_without_ready_available_connector -v`
  - Result: 2 passed after adding the setup-required branch.

## Verification

- `uv run pytest tests/test_connector_mcp_tools.py tests/test_search_filters.py -v`
  - Result: 25 passed, 1 existing Starlette `TestClient` deprecation warning.
- `uv run pytest tests/test_connector_setup.py tests/test_mcp_contract.py -v`
  - Result: 17 passed.
- `uv run ruff check .`
  - Result: all checks passed.
- `uv run pytest`
  - Result: 126 passed, 1 skipped, 1 existing Starlette `TestClient` deprecation warning.

## Concerns

- The only observed warning is the existing Starlette `TestClient` deprecation warning triggered by `tests/test_connector_mcp_tools.py`.
