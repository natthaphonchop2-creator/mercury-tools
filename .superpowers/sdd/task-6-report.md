# Task 6 Report: Capability-routed accounting Skills

## Scope completed

- Added `connection_mode` to the canonical Skill input base and the explicit hosted MCP
  common envelope. Every generated Skill schema now exposes the same selector.
- Extended `resolve_skill_route` with `requested_connection_mode`. Explicit connector and
  mode selectors resolve same-connector profiles while an omitted environment still returns
  sorted `connector_selection_required` choices when multiple environments qualify.
- Passed the validated `inputs.connection_mode` from hosted `run_accounting_skill` into the
  route resolver.
- Split normalized profile values from sanitized host identifiers. Connector ID, connection
  mode, and environment remain lowercase-normalized; `external_server_name` is trimmed and
  validated without changing case.
- Native host plans now include every required capability plus observed optional
  capabilities. Each ordered step carries `required: true|false`; unavailable optional
  actions remain in capability resolution but are absent from ordered steps and host tool
  requirements.
- Rewrote all ten generic Markdown Skills with exactly one `native_mcp`, `api_driver`, or
  `local_bridge_required` branch. Native mode uses only returned provider steps, API-driver
  mode uses only the returned advanced local handoff, and Local Bridge stops for setup.
  Unconditional local credential, read, and write commands were removed.
- Replaced keyword-only Markdown assertions and stale nine-step runtime assertions with
  semantic branch parsing, route exclusivity, hard-stop, evidence, read-only, and
  no-unconditional-local-command checks.
- Updated the older public product-design document to the current three-tool Skill contract:
  `list_accounting_skills`, `get_accounting_skill_schema`, and workspace-scoped
  `run_accounting_skill`.
- Left `plugins/mercury-finance/.mcp.json` and
  `src/mercury_tools/mcp/local_server.py` unchanged. No hosted workspace tools were copied
  into the advanced local server.

## TDD evidence

- Clean baseline before edits:
  `uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py`
  -> `116 passed, 1 warning`.
- Route/schema/server/Markdown/spec RED:
  `uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_plugin_package.py -k 'skill or accounting_skill_tool_contract'`
  -> `16 failed, 25 passed, 146 deselected, 1 warning` for the missing canonical selector,
  resolver argument, optional native steps, case preservation, route branches, and current
  spec signature.
- Explicit common-envelope and FastMCP-schema RED:
  `uv run pytest -q tests/test_connector_mcp_tools.py::test_connector_id_accepts_generic_mcp tests/test_mcp_contract.py::test_public_mcp_tool_schemas_are_explicit_for_plugin_review`
  -> `2 failed, 1 warning` because `connection_mode` was rejected/absent.
- Python contract GREEN after the minimal implementation:
  `uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py::test_connector_id_accepts_generic_mcp tests/test_connector_mcp_tools.py::test_run_accounting_skill_hands_connection_mode_to_same_connector_route tests/test_mcp_contract.py::test_public_mcp_tool_schemas_are_explicit_for_plugin_review`
  -> `13 passed, 1 warning`.
- Strengthened Markdown/runtime contract RED before rewriting the Skills:
  `uv run pytest -q tests/test_plugin_package.py -k 'generic_skill_markdown or read_skills or cross_mcp_skills or flow_runner or public_product_design'`
  -> `14 failed, 1 passed, 45 deselected`.
- Semantic Markdown/spec GREEN:
  the same command -> `15 passed, 45 deselected`.
- The first cloud/runtime regression exposed five stale tests that still required the retired
  unconditional nine-step local write sequence. Those tests were rewritten around the new
  exclusive route contract; the rerun is recorded below.

## Verification evidence

- Task 6 focused suite:
  `uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py`
  -> `121 passed, 1 warning`.
- Public MCP contract:
  `uv run pytest -q tests/test_mcp_contract.py` -> `66 passed`.
- Runtime and cloud Skill consumers:
  `uv run pytest -q tests/test_runtime_skills.py tests/test_cloud_api.py tests/test_cloud_client.py -k skill`
  -> `49 passed, 324 deselected`.
- Existing local advanced Skill runtime:
  `uv run pytest -q tests/test_local_mcp_contract.py -k run_accounting_skill`
  -> `1 passed, 36 deselected`.
- Ruff for every changed Python source and test file:
  `uv run ruff check src/mercury_tools/skills src/mercury_tools/mcp/schemas.py src/mercury_tools/mcp/server.py tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_plugin_package.py tests/test_runtime_skills.py`
  -> `All checks passed!`.
- `git diff --check` -> passed.

## Concerns

- The focused connector suite still emits the pre-existing Starlette `TestClient`
  deprecation warning about the `httpx` transport.
- This intermediate branch is intentionally not releasable: the public plugin remains pinned
  to v0.2.2 `serve-local`, and Task 9 owns the atomic hosted HTTP switch.
- Verification covers Task 6 plus shared MCP, cloud/runtime Skill, and local advanced Skill
  consumers. The repository-wide full suite was not required or run.
- No Task 7 implementation was started and no remote push was performed.

## Review Fix 2

### Scope completed

- Local Bridge routing now returns `connector_selection_required` with sorted, sanitized
  connector/mode/environment tuples when more than one selected bridge profile remains. A
  single selected bridge profile still returns the exact `local_bridge_required` handoff.
- Added only declared per-mode aliases: FlowAccount API-driver
  `tax.vat.summary.read -> tax.vat_summary.read` and PEAK API-driver
  `journal.read -> daily_journal.get`. FlowAccount native MCP remains without either alias.
- Native MCP routing treats a profile marked ready but missing or carrying an unsafe
  `external_server_name` as `not_validated`, preventing a host tool plan from being returned.

### TDD evidence

- RED after adding catalog and routing regressions:
  `uv run pytest -q tests/test_connector_catalog.py tests/test_skill_routing.py`
  -> `7 failed, 19 passed`. Failures covered both absent aliases, optional capability
  resolution, first-bridge selection, and malformed ready native profiles.
- GREEN after the minimal manifest and routing changes:
  the same command -> `26 passed`.

### Verification evidence

- Connector catalog plus Task 6 focused suite:
  `uv run pytest -q tests/test_connector_catalog.py tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py`
  -> `137 passed, 1 warning`.
- Public MCP contract:
  `uv run pytest -q tests/test_mcp_contract.py` -> `66 passed`.
- Runtime and cloud Skill consumers:
  `uv run pytest -q tests/test_runtime_skills.py tests/test_cloud_api.py tests/test_cloud_client.py -k skill`
  -> `49 passed, 324 deselected`.
- Existing local advanced Skill runtime:
  `uv run pytest -q tests/test_local_mcp_contract.py -k run_accounting_skill`
  -> `1 passed, 36 deselected`.
- Ruff for the changed Python source and tests:
  `uv run ruff check src/mercury_tools/connectors/catalog.py src/mercury_tools/skills/routing.py tests/test_connector_catalog.py tests/test_skill_routing.py`
  -> `All checks passed!`.

### Concerns

- The focused suite retains the pre-existing Starlette `TestClient` deprecation warning about
  the `httpx` transport.
- No Task 7 work, Task 9 launcher edits, or remote push was performed. The repository-wide
  full suite was not required or run.
