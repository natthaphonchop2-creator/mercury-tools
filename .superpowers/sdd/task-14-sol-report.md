# Task 14 Sol Local MCP/Runtime Slice Report

## Scope

Implemented the repository-local runtime and the one-server FastMCP surface in:

- `src/mercury_tools/mcp/local_runtime.py`
- `src/mercury_tools/mcp/local_server.py`
- `tests/test_local_mcp_contract.py`
- `tests/test_local_mcp_roots.py`
- `tests/test_mcp_contract.py`

The `mercury mcp serve-local` parser and deferred import wiring already existed at
HEAD `f18de2a`, so `src/mercury_tools/cli.py` did not require a change. No MCP
package `__init__.py`, Cloud, flow, plugin, or `tests/test_flows.py` files were
modified by this slice.

## Contract

- Server count/name: exactly one local `FastMCP("Mercury Finance")`.
- Tool count: exactly 19 (9 knowledge/product/flow plus 10 generic ERP tools).
- Resource URI family count: exactly 5.
- Prompt count: exactly 5.
- Legacy provider-specific journal tools are absent.
- FastMCP `Context` is injected and absent from every model-visible tool schema.
- Read-only, non-destructive local mutation, and destructive execution annotations
  are asserted by exact tool-name sets.

## RED / GREEN

- RED: local MCP/root/contract collection failed with two
  `ModuleNotFoundError: mercury_tools.mcp.local_server` errors before production
  files existed.
- GREEN: initial exact contract, root, and stdio suite passed: `16 passed`.
- RED: prompt contract exposed one unintended closure argument: `1 failed`.
- GREEN: zero-argument prompt registration passed: `21 passed`.
- RED: connector status did not refresh catalog capabilities before combining
  status: `1 failed`.
- GREEN: unfiltered refresh now precedes connector capability/status assembly:
  `27 passed`.
- RED: rootless audit-resource handling referenced an unbound event: `1 failed`.
- GREEN: audit resource now fails closed and preserves only its validated opaque
  event ID after sanitization: `28 passed` local MCP/root tests.

## Verification

- Focused local MCP/root/contract/flow/RAG/executor/search/importer suite:
  `236 passed`.
- Full non-integration suite: `1456 passed, 1 deselected, 1 warning`.
- Ruff: `uv run ruff check .` passed.
- PTY stdio smoke using `uv run mercury mcp serve-local`:
  - initialize returned server name `Mercury Finance` and MCP version `1.26.0`;
  - `tools/list` returned exactly 19 tools;
  - the server was terminated and `pgrep -af 'mercury mcp serve-local'` returned no
    remaining process.

## Commit

This report is included in the single commit named:

`feat: add unified local Mercury Finance MCP`

## Integration Concerns

- The current Cloud public catalog projection at `f18de2a` intentionally removes
  executable input schemas and rewrites projected action version IDs. The local
  runtime consumes that strict public snapshot as required, but full execution of
  global actions that need inputs remains dependent on a later approved Cloud
  catalog contract change. Repository-local imported actions retain full schemas
  and are executable immediately.
- Seven Cloud/redaction files were concurrently modified by another worker while
  this slice was implemented. Their completed working-tree state was included in
  full-suite verification but is excluded from this commit.
- The controller checklist requests two independent read-only reviews. This
  session exposed no callable subagent tools, so the slice received two manual
  self-review passes (contract/lifecycle and security/root boundaries) rather than
  independent agent approvals.
