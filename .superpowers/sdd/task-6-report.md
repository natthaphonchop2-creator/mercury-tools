# Task 6 Report: Capability-routed accounting Skills

## Scope completed

- Added one immutable `AccountingSkillDefinition` catalog in
  `src/mercury_tools/skills/catalog.py` and generated the backward-compatible
  `SKILL_CATALOG_SEED`, Skill ID enum, catalog summaries, and exact Pydantic
  input schemas from it.
- Added deterministic profile routing in `src/mercury_tools/skills/routing.py`.
  The same `company-health-check-th` Skill resolves `company.read` through the
  FlowAccount `company.info.read` and PEAK `user.info.read` manifest aliases.
- Explicit `inputs.connector_id` is applied before any other profile. Multiple
  qualifying profiles return `connector_selection_required` with sorted,
  sanitized choices and no preferred vendor.
- Native MCP routes return provider-capability host steps. API-driver routes
  return the advanced local Mercury handoff. Local Bridge routes return
  `local_bridge_required`.
- A provider-unavailable write returns the exact reason
  `provider_capability_unavailable` without blocking an unrelated observed
  read on the same profile.
- Added the exact hosted public tools:
  `list_accounting_skills()`, `get_accounting_skill_schema(skill_id)`, and
  `run_accounting_skill(workspace_id, skill_id, inputs, evidence_mode=False)`.
  Discovery is workspace-independent. Run canonicalizes the required top-level
  public workspace ID before input handling or storage access.
- `run_accounting_skill` uses the deferred-safe FastMCP input pattern. The
  handler validates the common envelope, duplicate names, secret-looking names
  and values, then the selected Skill's exact Pydantic schema before workspace
  storage, routing, or audit persistence.
- Updated the exact public annotation matrix with both discovery tools; all
  three Skill tools are closed reads.
- Updated all ten generic Skill Markdown files to request their own catalog
  schema by exact Skill ID, inspect connector status, follow returned routing
  and host steps, preserve citations/evidence/accountant review/output schema,
  and avoid duplicated vendor mappings or capability lists. Provider-specific
  setup Skills remain provider-specific.
- Preserved the existing local advanced `run_accounting_skill` surface and the
  cloud Skill seed fields consumed by v0.3 clients.

## TDD evidence

- Initial RED command attempt:
  `uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py -k skill`
  -> collection stopped on a missing `pytest` test import. The test-only import
  was corrected before implementation.
- Feature RED with the same command -> `12 failed, 44 deselected, 1 warning`.
  Failures were the missing `mercury_tools.skills` package and discovery tools,
  plus the old run signature without top-level `workspace_id`.
- Catalog/routing GREEN:
  `uv run pytest -q tests/test_skill_routing.py` -> `6 passed`.
- Focused RED-to-GREEN command after the first implementation pass:
  `uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py -k skill`
  -> `12 passed, 44 deselected, 1 warning`.
- Real FastMCP/schema/annotation regression:
  `uv run pytest -q tests/test_mcp_contract.py::test_public_mcp_tool_schemas_are_explicit_for_plugin_review tests/test_mcp_contract.py::test_accounting_skill_nested_rejection_is_sanitized_before_route_and_audit tests/test_mcp_contract.py::test_public_mcp_tools_have_submission_annotations`
  -> `3 passed`.
- Generic Markdown/catalog contract:
  `uv run pytest -q tests/test_plugin_package.py -k skill`
  -> `22 passed, 37 deselected`.

## Verification evidence

- Task 6 focused suite:
  `uv run pytest -q tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_plugin_package.py`
  -> `116 passed, 1 warning`.
- Public MCP contract:
  `uv run pytest -q tests/test_mcp_contract.py` -> `66 passed`.
- Runtime and cloud Skill consumers:
  `uv run pytest -q tests/test_runtime_skills.py tests/test_cloud_api.py tests/test_cloud_client.py -k skill`
  -> `49 passed, 324 deselected`.
- Existing local advanced Skill runtime:
  `uv run pytest -q tests/test_local_mcp_contract.py -k run_accounting_skill`
  -> `1 passed, 36 deselected`.
- Ruff for every changed Python source and test file:
  `uv run ruff check src/mercury_tools/skills src/mercury_tools/db/product.py src/mercury_tools/mercury_runtime.py src/mercury_tools/mcp/server.py src/mercury_tools/mcp/schemas.py tests/test_skill_routing.py tests/test_connector_mcp_tools.py tests/test_mcp_contract.py tests/test_plugin_package.py`
  -> `All checks passed!`.
- `git diff --check` -> passed.

## Concerns

- The focused connector suite still emits the pre-existing Starlette
  `TestClient` deprecation warning about the `httpx` transport.
- Verification covers Task 6 plus the shared public MCP, cloud Skill, runtime
  Markdown, and local advanced Skill consumers. The repository-wide full suite
  was not required or run for this task.
- No Task 7 implementation was started and no remote push was performed.
