# Task 10 Report: MCP Review Linter Edge-Case Fix

## Status

Edge-case follow-up from `447fe0d`. The hosted MCP review linter now rejects
non-empty `patternProperties`, uses exact normalized credential-name tokens,
and traverses RFC 6901 local pointers through mappings and sequences. Task 11
version and release identity files remain unchanged.

The current 24 hosted tools still report exactly:

```text
Mercury MCP review: 0 unclear arguments; annotations verified
```

This follow-up adds `api token` to the credential-field grammar in both
normalized sequence and compact `apitoken` form. Exact metadata semantics keep
`api_token_count`, `token_budget`, and `api_latency` allowed.

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

### Edge-Case Follow-Up TDD

The mutation tests were added before changing the linter:

```text
uv run pytest -q tests/test_mcp_review_contract.py
22 failed, 60 passed
```

The failures showed ignored root and nested `patternProperties`, unsupported
array-index JSON pointers, imprecise pointer findings, and both false-negative
and false-positive credential name handling.

After the linter change:

```text
uv run pytest -q tests/test_mcp_review_contract.py
83 passed

uv run pytest -q tests/test_mcp_review_contract.py tests/test_openai_plugin_submission.py
93 passed
```

### API Token Follow-Up TDD

The API-token rejection and metadata-control cases were added before changing
the grammar:

```text
uv run pytest -q tests/test_mcp_review_contract.py
5 failed, 85 passed
```

The failures were the five requested spellings: `apiToken`, `api_token`,
`api-token`, `api token`, and `APITOKEN`.

After adding the compact token, token sequence, and exact metadata exception:

```text
uv run pytest -q tests/test_mcp_review_contract.py
90 passed
```

## Linter Hardening

- Root `inputSchema` must resolve to an object-only accepting branch with
  `additionalProperties=false`. Strict no-argument object roots remain valid.
- Every nested property and array item branch must resolve to a concrete type,
  enum, or const. Empty schemas no longer pass silently.
- Non-empty `patternProperties` fail closed at the root and nested objects,
  regardless of `propertyNames`, `unevaluatedProperties`, or the matched value
  schema. An empty mapping remains valid because it cannot introduce a key.
- Credential names use normalized identifier tokens, splitting camelCase,
  snake_case, kebab-case, and spaces. Exact credential tokens and compounds are
  rejected while names such as `secretariat`, `token_budget`, `api_latency`,
  `api_token_count`, and `password_policy` remain valid metadata.
- Local JSON pointers resolve through mappings and sequences, including escaped
  `~0` and `~1` components and nonnegative array indexes. Invalid, unresolved,
  out-of-range, cyclic, over-depth, external, and over-expanded references fail
  with the tool name and use-site schema path.
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
- root and nested `patternProperties` with empty and constrained `.*` schemas,
  plus `propertyNames` and `unevaluatedProperties` interactions;
- RFC 6901 sequence traversal through `#/$defs/Choice/anyOf/0`, escaped
  components, invalid and out-of-range indexes, and external references;
- `anyOf`, `oneOf`, and intersection-correct `allOf` behavior;
- scalar environment enums in every accepting alternative;
- typed array items, finite `maxItems`, refs, and split `allOf` guarantees;
- root extra keys and mutually exclusive source fields across compositions;
- credential token matrices covering password/passwd/passphrase, secret,
  API key, access/refresh/bearer token, private key, client secret, safe
  metadata names, and direct or referenced field paths;
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

## Edge-Case Follow-Up Verification

```text
uv run python scripts/review_mcp_contract.py
Mercury MCP review: 0 unclear arguments; annotations verified

uv run --extra dev pytest -q tests/test_plugin_package.py tests/test_plugin_clean_install.py
81 passed

uv run --extra dev ruff check .
All checks passed!

git diff --check
no output; exit 0

Gitleaks 8.24.3, checksum-verified Darwin arm64 binary, Task 10 working-tree diff
no leaks found
```

## API Token Follow-Up Verification

```text
uv run pytest -q tests/test_mcp_review_contract.py tests/test_openai_plugin_submission.py
100 passed

uv run python scripts/review_mcp_contract.py
Mercury MCP review: 0 unclear arguments; annotations verified

uv run --extra dev ruff check .
All checks passed!

git diff --check
no output; exit 0
```

## Scope

- No hosted-tool exception or schema allowlist was added.
- No existing check was weakened.
- Submission copy, public Skills, hosted server behavior, and Task 9 packaging
  remain unchanged.
- Task 11 and `pyproject.toml` remain untouched.

## Compact Credential Alias Follow-Up

The compact credential aliases are now derived from
`_CREDENTIAL_TOKEN_SEQUENCES` by joining each canonical sequence. This keeps
single-token credentials such as `password`, `passwd`, and `passphrase`
explicit while ensuring every canonical multi-token sequence rejects its
snake, kebab, space, camel, Pascal, compact, and uppercase forms. Compact
matches remain exact normalized tokens, so longer unrelated words are allowed.

### TDD Evidence

After adding parameterized cases generated from the sequence table and compact
false-positive controls, before the implementation change:

```text
uv run pytest -q tests/test_mcp_review_contract.py
2 failed, 194 passed
```

After deriving compact aliases from the canonical sequence table:

```text
uv run pytest -q tests/test_mcp_review_contract.py
204 passed

uv run pytest -q tests/test_mcp_review_contract.py tests/test_openai_plugin_submission.py
214 passed
```

### Verification

```text
uv run python scripts/review_mcp_contract.py
Mercury MCP review: 0 unclear arguments; annotations verified

uv run --extra dev ruff check .
All checks passed!

git diff --check
no output; exit 0
```

### Scope

- Credential compact detection now has one canonical source of truth.
- Parameterized sequence-form and longer-word control coverage was added.
- No hosted tool, schema, annotation, submission, or release behavior changed.
