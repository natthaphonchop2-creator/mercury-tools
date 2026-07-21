# Task 8 Report: Split Local ERP Mutation Tools by Risk

## Scope

- Replaced the local MCP preview/confirm/generic-execute ceremony with
  `prepare_erp_mutation`, `execute_erp_create`, `execute_erp_update`, and
  `execute_sensitive_erp_action`.
- Kept the old names as unregistered Python compatibility helpers through v0.3.x.
- Added `docs/ADVANCED_LOCAL_ERP.md` and changed the public journal Skill to return
  an advanced-local handoff rather than invoke local-only tools.
- Did not change the Task 9 hosted launcher, marketplace metadata, release scripts,
  or public installation path.

## RED Evidence

Tests were changed before the implementation.

```text
uv run pytest -q tests/test_local_mcp_contract.py tests/test_plugin_package.py \
  -k 'tool_contract or annotations or write'
3 failed, 5 passed, 96 deselected
```

The failures showed the missing public handoff, missing guide, and unchanged local MCP
tool registry. Focused preparation and class-specific execution coverage then failed
before implementation:

```text
5 failed
```

The final compatibility cycle caught the incorrect status merge in the retained Python
preview helper and its server wrapper:

```text
2 failed
```

## Implementation

- `prepare_erp_mutation` creates the mandatory internal immutable preview and returns a
  redacted summary, hash, mutation class, approval level, expiry, and exact next tool.
- The class-specific execution tools refresh the catalog and call
  `approve_and_execute` with `create`, `update`, or `sensitive` respectively. Existing
  request-store validation rejects a class mismatch before credential loading or ERP
  network activity.
- Tool annotations now distinguish closed preparation from open-world reads/imports and
  class-specific external mutations. Every execute tool has `idempotentHint=False`.
- The retained `preview_erp_write`, `confirm_erp_write`, and `execute_erp_write` helper
  functions are not registered with FastMCP. The legacy preview result still reports
  `confirmation_required`.
- The public journal Skill now points to the advanced-local guide and does not name any
  local-only ERP tool. The guide keeps the official FlowAccount MCP read-only boundary
  separate from a reviewed local FlowAccount API-driver mutation.

## GREEN Verification

```text
uv run pytest -q tests/test_local_mcp_contract.py tests/test_plugin_package.py \
  tests/test_runtime_skills.py tests/test_execution_policy.py \
  tests/test_request_store.py tests/test_erp_executor.py
380 passed

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

## Commit

- `feat: split local ERP mutation tools by risk`

---

## Fix Implementer Follow-Up

### Scope

- Added a persisted-state local precheck before catalog refresh for every
  class-specific execute tool.
- Replaced the local `inputs` mapping schema for `run_erp_read` and
  `prepare_erp_mutation` with one strict `inputs.json_object` envelope.
- Updated the advanced-local guide and FlowAccount journal handoff with the exact
  envelope shape. No hosted launcher or old MCP tool registration changed.

### RED Evidence

The new regression coverage was added before this follow-up implementation:

```text
uv run pytest -q tests/test_local_mcp_contract.py tests/test_request_store.py \
  tests/test_plugin_package.py -k 'erp_input_envelope or precheck_approval or \
  class_specific_execute or journal_skill_returns_advanced_local_handoff or \
  advanced_local_erp_guide'
11 failed, 205 deselected in 1.17s
```

The failures showed raw mapping inputs reaching the runtime, absent prechecks before
`refresh_catalog`, and missing exact-envelope documentation.

### Implementation

- `LocalRequestStore.precheck_approval` validates request ID, payload hash, expiry,
  pending state, and mutation class from persisted state without recording an
  approval. `approve` reuses the same checks, so the existing authoritative recheck
  still occurs after the catalog refresh.
- `_approve_and_execute` now runs that precheck immediately after local runtime
  construction and before `refresh_catalog`, credential loading, authentication, or
  any Cloud/provider work. Stable request-state errors return only sanitized status
  codes and do not include request IDs, hashes, or payloads.
- `ErpInputEnvelope` has `extra=forbid` and a required bounded `json_object` string.
  The handler validates it with strict JSON parsing, rejects duplicate keys,
  non-finite constants, non-object roots, over-byte payloads, excessive depth, and
  excessive key or array counts, then returns only `erp_inputs_invalid` on failure.
  Runtime and Python helper APIs retain their mapping inputs.
- List-tools exposes `inputs` as an object with required `json_object`,
  `additionalProperties=false`, a description, and explicit 2 to 65,536 character
  bounds for both local action tools.

### GREEN Verification

```text
uv run pytest -q tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py \
  tests/test_plugin_package.py tests/test_runtime_skills.py
155 passed in 5.28s

uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py \
  tests/test_erp_executor.py tests/test_local_audit.py \
  tests/test_builtin_action_catalog.py tests/test_local_credentials.py
358 passed in 13.29s

uv run ruff check .
All checks passed!
```
