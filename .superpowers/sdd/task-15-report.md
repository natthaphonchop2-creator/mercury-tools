# Task 15 Skill Migration Report

## Status

IMPLEMENTED

Task 15 started from `d3a2d95`. Changes are limited to the approved Skill,
marketplace, catalog seed, package/runtime test, deletion, and report scope.
The public plugin MCP configuration and manifest remain unchanged for Task 16.
Private MCP server and runtime source remain unchanged for Task 17.

## RED Evidence

Package and runtime contracts were rewritten before Skill source changes.

```text
$ uv run pytest tests/test_plugin_package.py tests/test_runtime_skills.py -q
13 failed, 15 passed in 0.19s
```

The failures proved that the journal Skill was absent from the public plugin,
the marketplace and filesystem still contained the private plugin, setup and
read Skills used the hosted workspace contract, the catalog retained the
`private` tag, and runtime Skill discovery could not load the migrated journal.

A final Tier 2 contract refinement was also observed RED before the Skill edit:

```text
$ uv run pytest \
    tests/test_plugin_package.py::test_journal_skill_uses_bound_generic_write_sequence_once -q
1 failed in 0.08s
```

The test showed only one explicit generic confirmation call. After documenting
the Tier 2 call after each separate user confirmation, the same test passed.

## Delivered Behavior

- The marketplace contains only `mercury-finance`; the private plugin package
  and obsolete private MCP test are absent.
- The public plugin contains 10 Skills, including
  `flowaccount-journal-posting-th` with tags exactly
  `flowaccount`, `journal`, `write`, and `thai`.
- All four setup Skills enforce the local credential status, terminal setup,
  user confirmation, status recheck, terminal connection test, and connected
  gate. They never receive credentials through chat or name secret fields.
- Read Skills use only the generic local credential, cited context, action
  search/schema, and ERP read sequence. Their default Thai output omits evidence
  counts, audit paths, and verbose evidence.
- The journal Skill validates required accounting context and balance before
  tool use, binds confirmation to the preview request and payload hash, executes
  once, treats Tier 2 approval as a fresh two-confirmation action, and resolves
  `outcome_unknown` by status lookup without replay.
- Mercury Flows may list, save, and run read or write-preview flows, but cannot
  self-confirm, execute, or retry writes.
- Package-wide tests scan for hosted workspace tools, private journal tools,
  secret field names, and credential-in-chat instructions. Runtime tests pin the
  repository Skill source explicitly.

## GREEN Evidence

```text
$ uv run pytest tests/test_plugin_package.py tests/test_runtime_skills.py \
    tests/test_journal_models.py -q
34 passed in 0.48s

$ uv run pytest -m "not integration" -q
1552 passed, 1 deselected, 1 warning in 8.45s

$ uv run ruff check .
All checks passed!

$ git diff --check
clean
```

No repository plugin-validator command exists in this checkout. The installed
Codex CLI has no validate subcommand and the Claude CLI is unavailable, so the
plugin package was validated through the focused contract, JSON, frontmatter,
runtime-loading, and full non-integration tests.

## Residual

The full suite emits the existing Starlette/httpx deprecation warning from
`tests/test_connector_mcp_tools.py`. Task 16 still owns the public local-stdio
MCP packaging, manifest/version/capability changes, and release validator. Task
17 still owns removal of private server/runtime source.
