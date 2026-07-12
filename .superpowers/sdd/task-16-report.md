# Task 16 Report: Package One Pinned Local stdio Plugin

## Scope

- Replaced the hosted MCP declaration with one local `mercury-finance` stdio
  launcher pinned to `v0.2.0`.
- Set the Codex plugin and Python package versions to `0.2.0`, pinned the
  required runtime dependencies, and moved OpenAI to the optional extra.
- Added an offline release validator and wheel-only clean-install coverage.
- Rewrote the judge quickstart for the local-only release boundary.

## TDD Evidence

- Initial package run was red: 12 failures covering the HTTP launcher, version
  metadata, eager OpenAI import, missing release validator, and old quickstart.
- Added the private-token-name validator regression with `MERCURY_PRIVATE_TOKEN`.
  It failed before the validator matched generic private token names, then passed
  after the policy update.
- The clean wheel test exposed that no-cache dependency installation can exceed
  the original 30 second MCP read timeout. The test now permits 120 seconds
  while retaining empty cwd, cleared `PYTHONPATH`, and `uvx --no-cache`.

## Verification

Passed:

- `uv lock`
- `uv lock --check`
- `uv build`
- `uvx --from . mercury --help`
- `uv run python /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance`
- `uv run python scripts/validate_release_plugin.py`
- `uv run pytest tests/test_plugin_package.py -q` - 26 passed
- `uv run pytest tests/test_plugin_clean_install.py -q` - 2 passed in 98.67s
- `uv run ruff check scripts/validate_release_plugin.py src/mercury_tools/rag/embeddings.py src/mercury_tools/__init__.py tests/test_plugin_clean_install.py tests/test_plugin_package.py`

Shared-worktree blockers recorded, not changed by Task 16:

- `uv run pytest -m "not integration" -q` stops during collection because
  `tests/test_cloud_secret_removal.py` imports the not-yet-created
  `scripts.purge_cloud_erp_secrets`, and `tests/test_journal_write_store.py`
  imports the removed `vault_key` symbol.
- `uv run ruff check .` reports only Task 17 files:
  `scripts/purge_cloud_erp_secrets.py`, `src/mercury_tools/db/product.py`, and
  `tests/test_cloud_secret_removal.py`.

## Commit

`feat: package one pinned Mercury Finance MCP`
