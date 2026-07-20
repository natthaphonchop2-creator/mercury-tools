# Task 9 Report: Hosted One-Click Plugin Validation Fix

## Scope

- Validation-fix base: `ef59a0c` (`fix: validate one-click Mercury packaging`).
- Public plugin: exactly one hosted HTTP MCP at
  `https://mercury-tools-mcp.onrender.com/mcp`.
- Advanced local runtime: remains a separately connected path documented at both
  `docs/ADVANCED_LOCAL_ERP.md` and the identical packaged path
  `plugins/mercury-finance/docs/ADVANCED_LOCAL_ERP.md`.
- Deliberately unchanged: Task 10 submission bundle and Task 11 version/release files.

## Validation Fixes

1. Added `scripts/validate_release_plugin.py --codex-cli`. It reconstructs a local-only
   marketplace from `.agents/plugins/marketplace.json` and the public plugin directory,
   isolates `HOME` and `CODEX_HOME`, runs `codex plugin marketplace add ... --json` and
   `codex plugin add mercury-finance@mercury-tools --json` with empty stdin, and verifies
   the installed cache exposes exactly the one expected hosted MCP. The ordinary unit test
   skips when `codex` is unavailable; the explicit flag fails with an actionable error in
   that environment. Static validation and actual CLI validation print separate results.
2. Replaced the broad backtick allowlist with an imperative-call parser for the English and
   Thai verbs used by public Skills. Every parsed call name must occur in the actual hosted
   MCP registry. Prose such as argument names, statuses, package-local paths, code literals,
   and returned handoff steps is not interpreted as a tool call. Mutation coverage proves
   that imperative calls to `inputs` and `credential_status` fail.
3. Added a public-package boundary check. `mercury mcp serve-local` and all local-only tool
   names may occur only in `plugins/mercury-finance/docs/ADVANCED_LOCAL_ERP.md`; every public
   Skill reference to that document must call it a handoff. A release-layout mutation placing
   the command in a hosted Skill is rejected.

## TDD Evidence

### RED

Command:

```bash
uv run --extra dev pytest -q tests/test_plugin_package.py \
  -k 'imperative_tool or local_commands_and_tools or codex_cli_gate or release_validator_accepts'
```

Result: `1 passed, 3 failed`. The failures showed that static and actual CLI validation were
not distinct, `--codex-cli` did not exist, and the initial boundary assertion was too narrow
for existing prose handoffs. The assertion was corrected to the documented handoff contract
before implementing the validator changes.

### GREEN

The focused validator, parser, boundary, and mutation suite returned
`29 passed, 47 deselected`.

## Actual Codex CLI Gate

Command:

```bash
uv run python scripts/validate_release_plugin.py --root . --codex-cli
```

Output:

```text
release plugin static validation passed (hosted MCP contract and public-package boundary checks only)
release plugin Codex CLI validation passed (isolated local marketplace add/install; no network)
```

The installed cache is checked for the exact one-server hosted `.mcp.json`. The reconstructed
marketplace contains only the marketplace manifest and public plugin package, so the gate does
not rely on a clone, Python package source, or network access.

## Final Verification

```text
uv run --extra dev pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
81 passed in 19.86s

uv run --extra dev pytest -q tests/test_runtime_skills.py tests/test_skill_routing.py \
  tests/test_connector_mcp_tools.py tests/test_local_mcp_contract.py
144 passed, 1 existing Starlette/httpx deprecation warning

uv run --extra dev ruff check .
All checks passed!

uv run python scripts/validate_release_plugin.py --root .
release plugin static validation passed (hosted MCP contract and public-package boundary checks only)

uv run python scripts/validate_release_plugin.py --root . --codex-cli
release plugin static validation passed (hosted MCP contract and public-package boundary checks only)
release plugin Codex CLI validation passed (isolated local marketplace add/install; no network)

git diff --check
no output; exit 0
```

The validator's recursive public-plugin credential scan is clean. The manifest, Skills,
and packaged guide contain no credential literals, provider secrets, or local runtime
configuration.

## Residual Risks

- The hosted Render endpoint was not contacted during this packaging verification; remote
  deployment and public MCP smoke checks remain outside Task 9.
