# Task 10 Report: MCP Review Linter Hardening

## Status

Complete from hardening base `6fc10bd`. The hosted MCP review linter now fails
closed on unconstrained JSON Schema branches, resolves local references with
bounded traversal, owns workspace scope in the behavior matrix, and produces a
cross-platform deterministic OpenAI Skill bundle. Task 11 version and release
identity files remain unchanged.

The current 24 hosted tools still report exactly:

```text
Mercury MCP review: 0 unclear arguments; annotations verified
```

## TDD Evidence

### Baseline

Before adding the hardening mutations:

```text
uv run pytest -q tests/test_mcp_review_contract.py
17 passed
```

### RED

The adversarial tests were added before the linter and ZIP implementation
changes:

```text
uv run pytest -q tests/test_mcp_review_contract.py tests/test_openai_plugin_submission.py
24 failed, 30 passed
```

The failures reproduced empty and non-object roots, unconstrained nested
schemas and alternatives, incomplete local reference traversal, cycles,
incorrect composition guarantees, drifting workspace scope, and platform
dependent `ZipInfo` defaults.

### GREEN

```text
uv run pytest -q tests/test_mcp_review_contract.py tests/test_openai_plugin_submission.py
55 passed

uv run --extra dev pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
81 passed

uv run pytest -q tests/test_mcp_contract.py
69 passed
```

## Linter Hardening

- Root `inputSchema` must resolve to an object-only accepting branch with
  `additionalProperties=false`. Strict no-argument object roots remain valid.
- Every nested property and array item branch must resolve to a concrete type,
  enum, or const. Empty schemas no longer pass silently.
- Local JSON pointers resolve through both `$defs` and `definitions`, including
  escaped pointer components. Invalid, unresolved, cyclic, over-depth, and
  over-expanded references fail with the tool name and use-site schema path.
- `anyOf` and `oneOf` are reviewed per accepting branch. A strict branch cannot
  hide an unconstrained or open branch.
- `allOf` is treated as an intersection. Type, property, required,
  `additionalProperties`, item, maximum, and enum guarantees can be supplied by
  separate members while nested schemas remain reviewable.
- Objects and arrays are traversed through references and compositions.
  Environment enums, bounded typed arrays, root source conflicts, and
  credential-bearing field names retain their existing checks.
- Findings are stable and actionable, for example
  `tool.profile.anyOf[1].field` and `tool.filters.$ref`.

## Behavior Matrix

`BEHAVIOR_MATRIX` now stores one mandatory `ToolBehavior` record per hosted
tool. Each record contains the four annotation expectations and
`requires_workspace`. The separate workspace-scoped tool registry was removed.

Mutation coverage adds a future matrix entry with `requires_workspace=true`
and proves that a schema without required `workspace_id` fails. Annotation and
workspace schema checks therefore consume the same metadata entry.

## Mutation Coverage

Committed tests cover:

- empty, descriptive-only, non-object, nullable-object, and non-schema roots;
- empty nested schemas and open or unnamed nested objects;
- `$defs`, `definitions`, unresolved constraints, cyclic refs, and ref depth;
- `anyOf`, `oneOf`, and intersection-correct `allOf` behavior;
- scalar environment enums in every accepting alternative;
- typed array items, finite `maxItems`, refs, and split `allOf` guarantees;
- root extra keys and mutually exclusive source fields across compositions;
- direct and referenced credential-bearing names;
- missing and incorrect annotations; and
- current and future workspace metadata.

Every negative assertion requires a finding prefixed by the tool name and exact
schema path.

## Deterministic Bundle

The builder now sets all cross-platform ZIP metadata that can affect bytes:

```text
timestamp       (2026, 7, 17, 0, 0, 0)
create_system   3 (Unix)
create_version  20
extract_version 20
flag_bits       0
volume          0
internal_attr   0
mode            0100644
compression     deflate, level 9
archive comment empty
entry extra/comment/reserved empty/empty/0
```

Tests monkeypatch `ZipInfo` with simulated Windows/platform defaults, old/new
version fields, alternate attributes, comments, extras, and compression level.
The rebuilt archive remains byte-identical to the normal build. Entry order,
content, timestamp, mode, comments, and extras are inspected directly.

Two fresh independent bundles produced:

```text
first  sha256 7350346eee6d636467006be4fc67045387e3f08811cf0b603150f309acbee64f
second sha256 7350346eee6d636467006be4fc67045387e3f08811cf0b603150f309acbee64f
byte-identical true
```

## Additional Verification

```text
uv run python scripts/validate_release_plugin.py --root . --codex-cli
release plugin static validation passed
release plugin Codex CLI validation passed

uv run --extra dev ruff check .
All checks passed!

git diff --check
no output; exit 0

Gitleaks 8.24.3, checksum-verified Darwin arm64 binary, Task 10 diff
no leaks found
```

## Scope

- No hosted-tool exception or schema allowlist was added.
- No existing check was weakened.
- Submission copy, public Skills, test cases, and hosted server behavior from
  Task 10 remain unchanged.
- Task 11 and `pyproject.toml` remain untouched.
