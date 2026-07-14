# Task 14 Review-Fix Report

## Status

IMPLEMENTED_WITH_CONCURRENT_CLOUD_REDS

This review-fix is based on `77e5b1a`. It changes only Task 14-owned flow and
local-MCP files plus this report. Concurrent Cloud RED work in
`tests/test_cloud_api.py` and `tests/test_redaction.py` remains unmodified and
unstaged.

## Changed Files

- `src/mercury_tools/flows/models.py`
- `src/mercury_tools/flows/runner.py`
- `src/mercury_tools/mcp/local_server.py`
- `tests/test_flows.py`
- `tests/test_local_mcp_contract.py`
- `tests/test_local_mcp_roots.py`
- `.superpowers/sdd/task-14-report.md`

## RED Evidence

Provenance and capability-gate regressions were added first:

```text
$ uv run pytest tests/test_flows.py tests/test_local_mcp_contract.py -q
9 failed, 68 passed in 1.77s
```

The failures showed that `MercuryFlowRunner` had no injectable gate, ERP read
values reached every Cloud-bound flow command, nested `runFlow`/`repeat`/`retry`
lost provenance, and local preview was blocked by the hosted public gate.

The local preview fixture was then corrected to include its repository root;
the intended RED was observed:

```text
assert 'blocked' == 'confirmation_required'
1 failed in 0.35s
```

Descriptor-pinned loader/race regressions were added before implementation:

```text
$ uv run pytest <four Task-14 race tests> -q
3 failed, 1 passed in 0.59s
```

The vulnerable top-level local flow parsed the swapped outside YAML and invoked
the Cloud search callback. The missing loader assertions and this observable
outside dispatch established the TOCTOU RED condition.

## Delivered Behavior

- `erpRead` results carry internal provenance. Template interpolation propagates
  that provenance through nested mappings, lists, `saveAs`, derived command
  output, inline/nested flows, `repeat`, and `retry`.
- Cloud-bound commands (`searchKnowledge`, `retrieveContextPack`, `getDocument`,
  and `runSkill`) reject tainted arguments before dispatch with stable
  `status="blocked"` and `reason="erp_to_cloud_taint"`.
- Taint rejection sanitizes variables, affected step summaries, and report
  artifacts so raw ERP values do not appear in the returned flow result.
- `MercuryFlowRunner` retains `public_capability_gate` by default for hosted
  compatibility, accepts an injected gate, and accepts explicit `None`.
  Local MCP passes `None`; ERP reads remain constrained by effective Tier 0 and
  write flows remain preview-only through `ERPExecutor`.
- Added `RepositoryFlowLoader`, which uses descriptor-pinned `openat` traversal,
  `O_NOFOLLOW`, directory and final regular-file `fstat`, a 500 KB bound, strict
  UTF-8 decoding, and parsing from the exact opened bytes. Unsupported platforms
  fail closed.
- Local top-level run/list paths and local nested flow loading use the new
  loader. The hosted path loader remains unchanged.
- Local save now also fails closed when no secure POSIX no-follow primitives are
  available; the old path-based fallback was removed.
- Deterministic race coverage swaps a validated top-level file, listed file, or
  nested directory to an outside symlink before `openat`. These cases reject
  without parsing outside content or invoking ERP/Cloud callbacks.

## GREEN Evidence

```text
$ uv run pytest <provenance and local-preview regressions> -q
9 passed in 0.50s
```

```text
$ uv run pytest tests/test_flows.py tests/test_local_mcp_roots.py \
    tests/test_local_mcp_contract.py tests/test_mcp_contract.py \
    tests/test_mcp_rag_routing.py -q
113 passed in 2.59s
```

```text
$ uv run pytest tests/test_http_app.py tests/test_mcp_contract.py \
    tests/test_mcp_rag_routing.py tests/test_flows.py \
    tests/test_local_mcp_roots.py tests/test_local_mcp_contract.py -q
134 passed, 1 warning in 1.31s
```

```text
$ uv run pytest tests/test_local_mcp_contract.py::test_real_stdio_initialize_and_tools_list -q
1 passed in 0.60s
```

The stdio initialization returned server name `Mercury Finance` and the exact
expected local tool list.

```text
$ uv run pytest -m 'not integration' -q <deselect concurrent Cloud RED tests>
1495 passed, 17 deselected, 1 warning in 6.96s
```

```text
$ uv run ruff check <Task-14-owned files>
All checks passed!

$ git diff --check
clean
```

## Concurrent Cloud REDs

The raw full non-integration run produced `14 failed, 1497 passed, 1 deselected`.
Every failure was in concurrent worker-owned Cloud RED work:

- `tests/test_cloud_api.py`: four new redaction/canonical-catalog tests
- `tests/test_redaction.py`: ten redaction-projection/encoded-text cases

`uv run ruff check .` is likewise blocked only by import ordering in those two
unowned test files. No Cloud source/model/client/redaction file or Cloud test was
modified or staged for this task.

## Residual Concern

The local loader intentionally fails closed on platforms without POSIX
descriptor/no-follow support. The remaining Cloud RED tests and their Ruff import
ordering must be resolved and committed by the concurrent Cloud worker before an
unqualified full-suite/whole-repository Ruff claim is appropriate.

## Acceptance Fix A: Inline Label and Schema Projection

### Scope

This follow-up starts at `af6f80b` and changes only Task 14-owned flow/local-MCP
code, local/flow tests, and this report:

- `src/mercury_tools/flows/runner.py`
- `src/mercury_tools/mcp/local_server.py`
- `tests/test_flows.py`
- `tests/test_local_mcp_contract.py`
- `.superpowers/sdd/task-14-report.md`

No importer, request-builder, Cloud, catalog, executor, or unrelated concurrent
worker file was changed or staged by this fix.

### RED

```text
$ uv run pytest -q tests/test_flows.py -k "untrusted_base_dir or inline_flow_label"
13 failed, 2 passed, 69 deselected
```

The vulnerable loader accepted `base_dir` values containing `..`, and inline
`runFlow`, `repeat`, and `retry` labels such as `../pivot` and its variants
caused the external YAML to be parsed and to dispatch both ERP and Cloud
callbacks.

```text
$ uv run pytest -q tests/test_local_mcp_contract.py -k "get_erp_action_schema_keeps"
1 failed, 17 deselected
```

Generic `redact_json` replaced schema nodes named `client_secret`, `password`,
and `access_token`, so callers lost their declared `type` and required-field
structure.

### GREEN

- Inline labels now remain display-only. Inline child flows use the constant
  `.mercury-inline-flow.yaml` within the existing trusted parent directory.
- `RepositoryFlowLoader` pins its root, accepts a nested base only when it is an
  absolute decomposition of that root with no dot components, verifies every
  base directory component through no-follow descriptor traversal, and retains
  descriptor-pinned final reads.
- Regression coverage proves all inline wrappers and label variants stay inside
  the repository, parse no external YAML, dispatch no external ERP or Cloud
  callback, and retain parent-to-child ERP taint blocking.
- `get_erp_action_schema` now projects the catalog's validated executable schema
  directly. Schema field and parameter names, types, and required lists remain
  available, while examples, defaults, enums, constants, and top-level examples
  never expose values.

```text
$ uv run pytest -q tests/test_flows.py -k "untrusted_base_dir or inline_flow_label"
15 passed, 69 deselected

$ uv run pytest -q tests/test_local_mcp_contract.py -k "get_erp_action_schema_keeps"
1 passed, 17 deselected

$ uv run pytest -q tests/test_flows.py tests/test_local_mcp_roots.py \
    tests/test_local_mcp_contract.py tests/test_mcp_contract.py \
    tests/test_mcp_rag_routing.py
130 passed

$ uv run pytest -q tests/test_local_mcp_contract.py::test_real_stdio_initialize_and_tools_list
1 passed

$ uv run pytest -q -m "not integration"
1544 passed, 1 deselected, 1 warning

$ uv run ruff check .
All checks passed!
```

### Residual

The only verification residual is the existing Starlette/httpx deprecation
warning from `tests/test_connector_mcp_tools.py`; it is unrelated to this fix.

## Task 14: Reproducible Release Artifacts

### Scope and Candidate

- Candidate commit before this implementation: `4ef6b6246e378b9f95c04fc8e79e50d04039746d`.
- The tracked repository version and immutable plugin reference remain `0.2.0`
  and `@v0.2.0`; this task makes no release, tag, deploy, visibility, or
  credential changes.
- The release builder and verifier bind a requested version to the candidate
  commit SHA and its commit-derived `SOURCE_DATE_EPOCH`.
- This report path is ignored by `.gitignore` in the current checkout but was
  already tracked at the candidate. This section is appended to preserve the
  existing report history and is committed with the implementation so the
  worktree can be clean.

### RED Evidence

Before the Task 14 release modules existed, the focused RED command was:

```text
$ uv run pytest -q tests/test_release_artifacts.py tests/test_release_verify.py tests/test_public_staging.py
3 errors during collection
ModuleNotFoundError: No module named 'mercury_tools.release.artifacts'
```

### Implementation

- `release.artifacts` produces exactly one normalized wheel, sdist, plugin
  archive, source archive, and `SHA256SUMS.json`; archive member ordering,
  ownership, modes, paths, and timestamps are deterministic.
- `release.verify` rejects incomplete, duplicate, extra, or digest-mismatched
  artifacts; requires a clean candidate, exact project version, immutable
  plugin reference, one local stdio MCP, and the exact current 19 tool names.
- Artifact and staging construction invoke the existing Task 13 scanner gate.
  Tests use only an explicit fake successful attestation.
- Public staging is created from `git archive <candidate-sha>` in a new,
  single-commit repository and verifies the archived tree digest.

### Verification

```text
$ uv run pytest -q tests/test_release_artifacts.py tests/test_release_verify.py tests/test_public_staging.py
10 passed in 18.07s

$ uv run pytest -q tests/test_release_artifacts.py tests/test_release_verify.py tests/test_public_staging.py tests/test_release_secret_scanner.py tests/test_release_hosted_surfaces.py tests/test_plugin_package.py tests/test_local_mcp_contract.py tests/test_cli_search.py
223 passed in 26.02s

$ uv run pytest -q -m "not integration"
5332 passed, 17 deselected, 1 warning in 126.23s (0:02:06)

$ uv run ruff check .
All checks passed!

$ git diff --check
(no output)
```

The one warning is the existing Starlette/httpx deprecation warning from
`tests/test_connector_mcp_tools.py`.

### Intentional Current-Repository Block

The following commands each exited with status `1`, emitted only the sanitized
`release_version_mismatch` error, and left no output directory:

```text
$ uv run python scripts/build_release_artifacts.py --version 0.2.1 --output /tmp/mercury-task14-current-block.C7Iohm/artifacts --repo-root .
$ uv run python scripts/verify_release.py --version 0.2.1 --artifacts /tmp/mercury-task14-current-block.C7Iohm/artifacts --repo-root .
$ uv run python scripts/build_public_staging.py --version 0.2.1 --output /tmp/mercury-task14-current-block.C7Iohm/staging --repo-root .
```

This is intentional: a real `0.2.1` build and verify remain fail-closed until
Task 15 performs the version and immutable-reference bump. Successful `0.2.1`
coverage uses isolated, version-consistent Git fixtures.

### Concerns

- The real Task 13 gate remains fail-closed when required scanner or hosted
  surface evidence is unavailable; no weaker production path was introduced.
- The implementation does not mutate the source checkout. Build and staging
  work happen in temporary directories and publish only after all checks pass.
