# Task 9 Report: Hosted One-Click Plugin

## Scope

- Task base: `45c6955`
- Public plugin: exactly one hosted HTTP MCP at
  `https://mercury-tools-mcp.onrender.com/mcp`.
- Advanced local runtime: remains a separately connected path documented at both
  `docs/ADVANCED_LOCAL_ERP.md` and the identical packaged path
  `plugins/mercury-finance/docs/ADVANCED_LOCAL_ERP.md`.
- Deliberately unchanged: Task 10 submission bundle and Task 11 version/release files.

## Review Fixes

1. Removed `policy.authentication: "NONE"` from
   `.agents/plugins/marketplace.json`. Codex CLI accepts an omitted optional field for
   this no-auth hosted plugin; `NONE` is not a valid Codex value. The actual enum accepts
   only `ON_INSTALL` and `ON_USE`, so the validator recognizes those as the only supported
   explicit values and rejects any explicit authentication policy for this no-auth plugin.
   The clean CLI install completed without a credential input prompt. Codex reports
   `authPolicy: "ON_INSTALL"` as its internal default after reading the omitted field; this
   does not add a provider credential field or prompt.
2. Replaced the starter guide's local-only `credential_status` and local CLI commands with
   the hosted lifecycle: `list_connectors` -> `get_connector_setup` ->
   `link_connector_profile` -> host/provider OAuth or separately connected advanced-local
   handoff -> `validate_connector_connection` -> `connector_status`.
3. Added the package-local advanced ERP guide and checked every public Skill's backtick tool
   reference against the actual hosted MCP registry. Local handoff prose is explicitly
   allowlisted, and every `docs/` or `skills/` Markdown path must resolve under the installed
   plugin. The release validator now recursively scans public plugin files for
   high-confidence credential literals.

## TDD Evidence

### RED

Command:

```bash
uv run --extra dev pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py \
  -k 'marketplace_points or public_skill_backtick or package_local_markdown or packaged_advanced or connector_setup_guide_uses or clean_install_one_click'
```

Result: `6 failed, 66 deselected`. Failures covered the invented `NONE` value,
the local-only starter guide, and all four unresolved packaged-guide references.

### GREEN

The focused command returned `12 passed, 62 deselected` after the manifest, Skill,
packaged documentation, and validator were corrected.

## Clean Codex CLI Reconstruction

Command:

```bash
tmp_root=$(mktemp -d)
mkdir -p "$tmp_root/home" "$tmp_root/codex"
HOME="$tmp_root/home" CODEX_HOME="$tmp_root/codex" \
  codex plugin marketplace add "$PWD" --json
HOME="$tmp_root/home" CODEX_HOME="$tmp_root/codex" \
  codex plugin add mercury-finance@mercury-tools --json
```

Output:

```text
codex=codex-cli 0.145.0-alpha.18
marketplaceName=mercury-tools
pluginId=mercury-finance@mercury-tools
version=0.2.2+codex.20260717
authPolicy=ON_INSTALL
installed_plugin_contract=passed
```

The installed cache was checked for the package-local ERP guide, the exact one-server
hosted `.mcp.json`, no local launcher fields, and no `pyproject.toml`.

## Final Verification

```text
uv run --extra dev pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
75 passed

uv run --extra dev pytest -q tests/test_runtime_skills.py tests/test_skill_routing.py \
  tests/test_connector_mcp_tools.py tests/test_local_mcp_contract.py
144 passed, 1 existing Starlette/httpx deprecation warning

uv run --extra dev ruff check .
All checks passed!

uv run python scripts/validate_release_plugin.py
release plugin validation passed (hosted MCP static checks only; remote smoke is a post-review gate)

git diff --check
no output; exit 0
```

The validator's recursive public-plugin credential scan is clean. The manifest, Skills,
and packaged guide contain no credential literals, provider secrets, or local runtime
configuration.

## Residual Risks

- The hosted Render endpoint was not contacted during this packaging verification; remote
  deployment and public MCP smoke checks remain outside Task 9.
