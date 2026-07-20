# Task 5 Report

Status: complete

Files changed:
- `src/mercury_tools/mcp/schemas.py`
- `src/mercury_tools/mcp/server.py`
- `tests/test_mcp_contract.py`
- `tests/test_connector_mcp_tools.py`
- `.superpowers/sdd/task-5-report.md`

Commit: `2462a7548983d8cc551e90abc2f14664450e4467` (`refactor: split public Mercury flow sources`)

Test commands and results:
- RED before implementation: `uv run pytest -q tests/test_mcp_contract.py tests/test_plugin_package.py -k 'flow and schema'` -> expected failure because `run_inline_flow` was not registered (`1 failed, 63 deselected`).
- `uv run pytest -q tests/test_mcp_contract.py tests/test_plugin_package.py -k 'flow and schema'` -> `1 passed, 69 deselected`.
- `uv run pytest -q tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py -k flow` -> `28 passed, 86 deselected`; one existing Starlette `TestClient` deprecation warning.
- `uv run pytest -q tests/test_mcp_contract.py -k 'hosted_flow_environment or annotations or explicit_for_plugin_review or run_inline_flow_schema'` -> `12 passed, 10 deselected`.
- Direct async registry/status check -> passed: `run_inline_flow`, `run_flow_files`, and `run_workspace_flow` are registered; `run_flow` and `run_mercury_flow` are not registered or listed in hosted flow tools.
- `uv run ruff check src/mercury_tools/mcp/schemas.py src/mercury_tools/mcp/server.py tests/test_mcp_contract.py tests/test_connector_mcp_tools.py` -> passed.
- `git diff --check` -> passed before commit.
- Full suite was not run, as instructed.

Self-review:
- Replaced the registered multi-source and ambiguous flow tools with explicit `run_inline_flow` and `run_flow_files`; retained undecorated `run_flow` and `run_mercury_flow` compatibility functions.
- Required `workspace_id` for every public hosted run path and kept planning/inspection tools as closed reads; `save_workspace_flow` remains the existing idempotent closed write.
- Added typed flow-file/tag/environment schemas. Environment item validation is deferred with `SkipValidation` so FastMCP does not reflect nested invalid input; the handler performs fixed, secretless rejection before parsing, execution, or audit.
- Exercised real asynchronous `mcp.call_tool` requests for invalid secret-bearing names, redaction-detected values, and extra fields across inline, files, and saved-workspace runs. Markers never reached output or audit assertions.
- No provider lifecycle behavior or Task 6 tool surface changed.

Concerns:
- `tests/test_http_app.py`, outside the allowed write scope, still asserts retired names in `status().flow_tools`. The hosted status list was correctly updated so compatibility helpers are not advertised; its focused test needs an owner-scope follow-up. Full-suite status is therefore intentionally unverified.

Concern resolution:
- Updated `tests/test_http_app.py::test_connect_page_and_status` to assert the exact emitted `flow_tools` contract: `flow_cheat_sheet`, `check_flow_syntax`, `inspect_flow_files`, `run_inline_flow`, `run_flow_files`, `save_workspace_flow`, `list_workspace_flows`, and `run_workspace_flow`.
- The same assertion explicitly verifies that retired compatibility helpers `run_flow` and `run_mercury_flow` are excluded. Server behavior was unchanged because the emitted status list already matched the MCP registry.

Additional test evidence:
- `uv run pytest -q tests/test_http_app.py::test_connect_page_and_status tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py -k 'flow or connect_page_and_status'` -> `29 passed, 86 deselected, 1 warning`; focused HTTP status and Task 5 flow tests passed.
- `uv run ruff check tests/test_http_app.py` -> passed.
- `git diff --check` -> passed.

## Review-fix addendum: public flow workspace validation

Files changed by this review fix:
- `src/mercury_tools/mcp/server.py`
- `tests/test_mcp_contract.py`
- `tests/test_connector_mcp_tools.py`
- `.superpowers/sdd/task-5-report.md`

Implementation:
- `run_inline_flow` and `run_flow_files` now normalize `workspace_id` with the
  canonical `normalize_public_workspace_id()` before hosted-environment
  validation, flow parsing, planning/execution, or audit persistence.
- Blank, whitespace-only, and malformed values return the fixed sanitized
  response `Invalid Mercury public workspace ID.` and produce no audit event.
- The public `run_flow_files` connector-readiness test now asserts this earlier
  boundary. Undecorated `run_flow` and `run_mercury_flow` compatibility helpers
  are unchanged; Task 6 behavior is unchanged.

Test commands and results:
- RED before implementation: `uv run pytest -q tests/test_mcp_contract.py -k 'hosted_flow_tools_reject_invalid_workspace_ids_before_side_effects'` -> `6 failed, 22 deselected`; both public tools reached `_hosted_flow_environment_overrides` before workspace validation.
- GREEN after implementation: `uv run pytest -q tests/test_mcp_contract.py -k 'hosted_flow_tools_reject_invalid_workspace_ids_before_side_effects'` -> `6 passed, 22 deselected`.
- `uv run pytest -q tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py -k flow` -> `34 passed, 86 deselected, 1 warning`.
- `uv run pytest -q tests/test_http_app.py::test_connect_page_and_status tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py -k 'flow or connect_page_and_status'` -> `35 passed, 86 deselected, 1 warning`.
- `uv run ruff check src/mercury_tools/mcp/server.py tests/test_mcp_contract.py tests/test_connector_mcp_tools.py` -> `All checks passed!`.
- `git diff --check` -> passed.
- Full-suite status remains intentionally unverified; the focused runs emitted one existing Starlette `TestClient` deprecation warning.

## Review-fix 2 addendum: security and compatibility boundaries

Files changed by this review fix:
- `src/mercury_tools/mcp/schemas.py`
- `src/mercury_tools/mcp/server.py`
- `tests/test_mcp_contract.py`
- `.superpowers/sdd/task-5-report.md`

Implementation:
- Hosted environment arguments retain an explicit array schema with 100-item
  and 10,000-character value bounds, while FastMCP defers the raw outer value
  and every item to the sanitized handler. Rejected top-level values, malformed
  items, oversized arrays, secret-bearing names, and redaction-detected values
  return one fixed payload without dispatch or audit persistence.
- Secret-name rejection now covers separator and case variants of `private_key`,
  `service_role_key`, `credentials`, and `cookie` in addition to the prior
  token/password/authorization families.
- Public inline YAML retains a host-visible 1..500,000-character schema while
  runtime validation occurs inside the sanitized handler. Invalid content does
  not expose `input_value`, dispatch a plan, or create an audit event.
- `run_inline_flow`, `run_flow_files`, and `run_workspace_flow` canonicalize the
  public workspace before environment handling or flow dispatch. Rejected
  workspace IDs produce no audit event.
- Plain, undecorated `run_mercury_flow` again validates the retired discriminated
  source models before dispatch. Inline YAML and optional workspace IDs retain
  their 1..500,000 and 1..2,048 bounds, while valid legacy workspace IDs are not
  forced into the canonical public `mw_` pattern.
- No Task 6 runtime or planning-document behavior changed.

TDD and verification evidence:
- RED before implementation: focused security/compatibility regressions ->
  `36 failed, 7 passed, 13 deselected`; failures confirmed raw FastMCP
  `input_value` reflection, saved-workspace ordering, missing inline bounds, and
  compatibility dispatch. The exact schema test separately failed on the new
  host contract before implementation.
- GREEN focused regressions after implementation -> `43 passed, 13 deselected`;
  final expanded environment cases are included in the full contract result.
- `uv run pytest -q tests/test_mcp_contract.py` -> `65 passed`.
- `uv run pytest -q tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py -k flow`
  -> `71 passed, 86 deselected, 1 warning`.
- `uv run pytest -q tests/test_http_app.py::test_connect_page_and_status tests/test_mcp_contract.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py -k 'flow or connect_page_and_status'`
  -> `72 passed, 86 deselected, 1 warning`.
- Direct real `mcp.call_tool` probes passed for raw string environment, secret
  environment item, oversized inline YAML, and invalid saved-workspace ID. All
  returned fixed messages with no marker, `input_value`, or planned result.
- `uv run ruff check src/mercury_tools/mcp/schemas.py src/mercury_tools/mcp/server.py tests/test_mcp_contract.py`
  -> passed.
- `uv run ruff format --check src/mercury_tools/mcp/schemas.py src/mercury_tools/mcp/server.py tests/test_mcp_contract.py`
  -> passed.
- `git diff --check` -> passed.

Concerns:
- Focused HTTP runs still emit the existing Starlette `TestClient` deprecation
  warning. No new functional concern remains in the verified Task 5 scope.
