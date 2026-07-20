# Task 9 Report: Hosted One-Click Plugin

## Scope

- Task base: `1b869da`
- Public plugin: exactly one hosted HTTP MCP at
  `https://mercury-tools-mcp.onrender.com/mcp`.
- Advanced local runtime: retained as a separately connected path documented in
  `docs/ADVANCED_LOCAL_ERP.md`.
- Deliberately unchanged: Task 10 submission bundle and Task 11 version/release files.

## TDD Evidence

### RED

Command:

```bash
uv run pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py -k 'registers or one_click or launcher'
```

Result: `3 failed, 65 deselected`. The failures proved that the public plugin still
registered `uvx ... serve-local` and that the static validator accepted the local
launcher.

### GREEN

The same focused command returned `3 passed, 65 deselected` after the public manifest
and validator were changed to the hosted contract.

## Final Verification

```text
uv run pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
68 passed

uv run pytest -q tests/test_runtime_skills.py tests/test_skill_routing.py \
  tests/test_connector_mcp_tools.py tests/test_local_mcp_contract.py
144 passed, 1 existing Starlette/httpx deprecation warning

uv run ruff check .
All checks passed!

uv run python scripts/validate_release_plugin.py
release plugin validation passed (hosted MCP static checks only; remote smoke is a post-review gate)

git diff --check
no output; exit 0
```

The tracked diff was also scanned for high-confidence bearer, JWT, provider-token, and
credential-assignment literals. No matches were found. The catalog and credential guide
contain no usable secret examples.

## Residual Risks

- The hosted Render endpoint was not contacted in this networkless Task 9 verification;
  deployment and public MCP smoke checks remain Task 12 work.
- Historical v0.2.2 release/submission controls intentionally remain untouched. Task 11
  must update the release contract when it prepares v0.3.0.
