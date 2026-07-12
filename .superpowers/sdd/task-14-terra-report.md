# Task 14 Terra Flow Slice Report

## Changed Files

- `src/mercury_tools/flows/models.py`
  - Adds `erpRead`, `erpWritePreview`, and their snake-case aliases only.
- `src/mercury_tools/flows/runner.py`
  - Adds injected ERP callbacks, terminal preview handling, retry mutation rejection,
    and repository-bound nested-flow path resolution.
- `src/mercury_tools/flows/templates.py`
  - Documents the two ERP flow commands and the retry mutation restriction.
- `tests/test_flows.py`
  - Covers command aliases, callbacks, terminal sanitized previews, recursive retry
    rejection, and traversal/symlink escape handling.

## Callback Interface

`MercuryFlowRunner` remains synchronous and accepts these injected interfaces:

```python
erp_read_callback: Callable[[str, dict[str, Any], str], dict[str, Any]]
erp_write_preview_callback: Callable[[str, dict[str, Any], str], dict[str, Any]]
flow_path_resolver: Callable[[Path, str], Path]
```

- ERP callbacks receive `(action_id, inputs, environment)` and must be bound to one
  repository by the local runtime.
- `erp_read_callback` owns catalog lookup and effective Tier 0 enforcement.
- `erp_write_preview_callback` owns catalog lookup, preview binding, and request-store
  enforcement. It must return `request_id` and `payload_hash`; the flow exposes only
  those fields plus `status="confirmation_required"`.
- Flows do not receive confirm or execute callbacks and cannot invoke either action.
- `repository_flow_path_resolver(repository_root)` is provided for local runtime use.
  It rejects absolute/traversal paths and resolved symlink escapes before parsing a
  nested flow file.

## RED / GREEN Evidence

- RED: `uv run pytest tests/test_flows.py -q` -> `5 failed, 47 passed` before the
  command/callback/resolver implementation.
- RED: recursive retry policy assertion -> `1 failed`; the previous behavior retried
  a policy rejection. The runner now propagates that rejection immediately.
- RED: nested `runFlow` preview summary assertion -> `1 failed`; the parent omitted
  the public request identity. Terminal propagation now carries only the public
  `request_id` and `payload_hash` through nested `runFlow`, `repeat`, and `retry`.
- GREEN: `uv run pytest tests/test_flows.py -q` -> `53 passed`.
- GREEN: focused flow and hosted regressions -> `75 passed` across `test_flows.py`,
  `test_mcp_contract.py`, `test_mcp_rag_routing.py`, and `test_product_fallback.py`.
- GREEN: `uv run pytest -m 'not integration' -q` -> `1308 passed, 1 deselected`.
- GREEN: `uv run ruff check .` and `git diff --check` passed.

## Commit

`feat: add ERP flow callbacks and preview safety`

## Integration Notes For Sol

- Construct a fresh repository-bound runner per local MCP request. Pass
  `repository_flow_path_resolver(repository.root)` for every nested file lookup.
- Bind the two callbacks to the local runtime's catalog, executor, request store, and
  repository. Do not reimplement Tier 0 or preview policy in `flows`.
- Run the synchronous runner in a worker from FastMCP (for example,
  `asyncio.to_thread`). Do not call `asyncio.run` from the active FastMCP event loop.
- Propagate `FlowRunResult.status == "confirmation_required"` directly. A flow preview
  is terminal and must not schedule confirmation or execution.
- The legacy hosted runner retains its existing public capability gate and uses its
  contained relative nested-flow fallback; local MCP must provide the repository-bound
  resolver above.
