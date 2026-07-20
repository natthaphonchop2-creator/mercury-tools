# Task 7 Report: ERP Approval Dependency Binding

## Scope

Fixed all five independent-review findings on Task 7 base `cba0404`. The v0.3
flow still uses one immutable approval and preserves atomic/concurrent approval,
`outcome_unknown` replay blocking, audit redaction, and the Task 1-6 tool surface.
Task 8 changes are not included.

## RED Evidence

The first adversarial run covered credential drift, preflight version drift,
classification, SQLite migration artifacts/index collisions, and stale v1
consumers:

```text
uv run pytest -q tests/test_execution_policy.py tests/test_erp_executor.py tests/test_request_store.py tests/test_builtin_action_catalog.py tests/test_runtime_skills.py tests/test_plugin_package.py -k 'mutation_confidence_and_observation_matrix or credential_save_after_preview or credential_save_during_auth or approved_preflight_version_drift or preflight_version_drift_after_preflight or preserves_preexisting_malformed_archive or repairs_colliding_index_name or catalog_builder_applies_mutation_policy or one_immutable_approval_contract or one_immutable_approval_for_every_mutation or current_write_guides_describe_one_immutable_approval'
15 failed, 1 passed, 261 deselected in 1.05s
```

The one passing matrix case was the expected observed, exact, non-sensitive
mutation. The 15 failures reproduced all five findings: three classification
cases, two credential drifts, two preflight drifts, two migration/index cases,
one catalog-builder case, and five skill/document contract cases.

Three narrower RED runs then covered surfaces not exercised by that command:

```text
credential revision canonical binding and public/audit redaction: 1 failed
public MCP action projection without legacy prompt count: 1 failed
malformed full unique live replay index repair: 1 failed
```

The schema-contract follow-up exposed a v2 database with matching column names
but invalid SQLite metadata. Before implementation, all six initial structural
cases were accepted or reached index creation instead of failing with the stable
schema error:

```text
uv run pytest -q tests/test_request_store.py -k 'malformed_request_table_contract or allows_duplicate_request_ids'
6 failed, 81 deselected in 0.22s
```

## Fixes

- Prepared mutation requests now bind an opaque, process-internal credential
  generation. Preview does not return credential values to the executor;
  execution loads an atomic snapshot and rejects generation drift before any
  network request. The generation and its inputs are excluded from public request
  projections, MCP responses, and audit.
- Every approved preflight binds action ID, version ID, connector, method, and
  path identity into the canonical approval hash. Main/preflight versions,
  credential generation, and targets are revalidated before each preflight and
  immediately before mutation dispatch.
- Inferred mutations and unobserved/untested mutations independently elevate to
  sensitive. Matrix coverage includes exact+untested, inferred+success,
  inferred+untested, and observed exact actions.
- SQLite v1-to-v2 migration chooses deterministic non-overwriting archive names,
  validates the exact live v2 table contract through `PRAGMA table_info` and
  `PRAGMA table_xinfo` (order, names, declared type/affinity, null/default
  semantics, primary-key position, and hidden columns), and verifies replay-index
  ownership, uniqueness, columns, and predicate. Colliding legacy names receive
  deterministic suffixes; malformed app-owned live indexes are repaired. Repeated
  initialization is safe.
- The built-in catalog generator treats GET as read-only and uses v0.3 mutation
  fields. Current runtime action projections, plugin skill, README, catalog docs,
  and judge guide describe one immutable approval with standard/elevated severity,
  not a two-prompt ceremony.

## GREEN Evidence

```text
uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py tests/test_erp_executor.py tests/test_local_audit.py
221 passed in 6.92s

uv run pytest -q tests/test_credential_cli.py tests/test_local_credentials.py tests/test_builtin_action_catalog.py tests/test_runtime_skills.py tests/test_plugin_package.py
186 passed in 4.64s

uv run pytest -q tests/test_local_audit.py tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py tests/test_connector_driver_contract.py tests/test_generic_drivers.py tests/test_sandbox_execution_manifest.py tests/integration/test_local_erp_mcp.py
267 passed, 2 skipped in 11.84s

uv run pytest -q tests/test_local_mcp_contract.py
39 passed in 1.28s

uv run pytest -q tests/test_request_store.py -k 'preserves_preexisting_malformed_archive or repairs_colliding_index_name or replaces_malformed_live_replay_index'
3 passed, 78 deselected in 0.26s

uv run ruff check .
All checks passed!

git diff --check
exit 0 (no output)
```

Schema-contract follow-up verification:

```text
uv run pytest -q tests/test_request_store.py -k 'malformed_request_table_contract or allows_duplicate_request_ids or fresh_request_store_uses_v2_schema or v1_migration or v2_initialization'
14 passed, 75 deselected in 0.27s

uv run pytest -q tests/test_request_store.py
89 passed in 1.58s

uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py tests/test_erp_executor.py tests/test_local_audit.py
229 passed in 11.04s

uv run pytest -q tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py tests/test_connector_driver_contract.py tests/test_generic_drivers.py tests/test_sandbox_execution_manifest.py tests/integration/test_local_erp_mcp.py
229 passed, 2 skipped in 16.19s

uv run ruff check .
All checks passed!

git diff --check
exit 0 (no output)
```

A full suite was started, then intentionally stopped at the user's instruction
once focused suites were green. At interruption it was at 24% with 1,465 passed,
13 skipped, one deprecation warning, and no failures. This is partial evidence,
not a completed full-suite result.

## Residual Risk

The credential generation key is process-local by design. A pending approval
therefore fails closed after process restart even when credential content is
unchanged; the user must prepare and approve a new immutable request. The full
suite was not completed, but all required Task 7 and adjacent credential,
catalog, skill, plugin, audit, MCP, driver, manifest, and integration suites above
completed without failures. The v2 contract intentionally preserves the shipped
`request_id TEXT PRIMARY KEY` declaration for migration compatibility; a future
schema revision would be required to add an explicit `NOT NULL` declaration.

## Sensitive Side-Effect Normalization Follow-Up

### Scope

Fixed the Task 7 policy edge case where the valid `CatalogAction` side effect
`payments` was classified as a standard create because the policy recognized
only the exact singular `payment`. Task 8 is not included.

The checked-in FlowAccount and PEAK catalogs use `payment`, `approve`, `void`,
`email`, `share`, `invite`, `delete`, and `writes_remote_data`. The existing
Task 7 tests also establish `post` and `finalize` as elevated effects.

### RED Evidence

The initial matrix covered singular/plural effects plus case, camel-case, and
separator-normalized spellings for payment, approve, void, post, finalize,
email, share, invite, and delete. It also covered near matches that must remain
standard:

```text
uv run pytest -q tests/test_execution_policy.py -k 'sensitive_effect_aliases_are_elevated or non_sensitive_effects_with_sensitive_substrings_remain_standard'
19 failed, 20 passed, 11 deselected in 0.18s
```

Failures included all plural aliases such as `payments`, while
`delete_preview` was incorrectly elevated by the previous token-intersection
implementation. The adjacent request-store suite then exposed the established
`email_customer` sensitive alias. Its camel-case and plural/separator variants
were added test-first and failed as expected:

```text
uv run pytest -q tests/test_execution_policy.py -k 'sensitive_effect_aliases_are_elevated'
2 failed, 36 passed, 14 deselected in 0.14s
```

### Fix

`execution/policy.py` now canonicalizes each entire effect name to lowercase
snake case, splitting camel-case and accepted separators. It then performs an
explicit allowlist lookup from singular/plural aliases to canonical sensitive
effects. This keeps intended aliases elevated without broad substring matching;
`postpone`, `shared_cache`, and `delete_preview` remain standard creates.

### GREEN Evidence

```text
uv run pytest -q tests/test_execution_policy.py -k 'sensitive_effect_aliases_are_elevated or non_sensitive_effects_with_sensitive_substrings_remain_standard'
41 passed, 11 deselected in 0.12s

uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py tests/test_erp_executor.py tests/test_local_audit.py tests/test_builtin_action_catalog.py
275 passed in 9.06s
```

## Security Edge-Case Fix

### Scope

Fixed only the two requested Task 7 security findings: mixed-case and
Unicode-affixed sensitive side effects, and noncanonical SQLite v2 table
semantics that can replace immutable replay history. Task 8 is not included.

### RED Evidence

The policy cases covered mixed-case singular/plural aliases, mixed-case
underscore/hyphen/space compounds, and all required Unicode-affixed negative
controls:

```text
uv run pytest -q tests/test_execution_policy.py -k 'sensitive_effect_aliases_are_elevated or non_sensitive_effects_with_sensitive_substrings_remain_standard'
9 failed, 41 passed, 11 deselected in 0.22s
```

The request-store cases covered `PRIMARY KEY ON CONFLICT REPLACE` with retained
`outcome_unknown` history, other explicit conflict clauses, extra constraints,
table options, collation, a history-replacing trigger, and a semantically
canonical formatting/reopen control:

```text
uv run pytest -q tests/test_request_store.py -k 'on_conflict_replace_before_use or noncanonical_table_ddl or trigger_that_can_replace_request_history or semantically_canonical_formatted_ddl'
7 failed, 3 passed, 89 deselected in 0.47s
```

The three passing controls were table options already rejected by the PRAGMA
contract and the valid formatted DDL reopen case. The seven failures reproduced
the metadata-blind conflict, constraint, collation, and trigger findings.

### Fix

- Sensitive effects are casefolded before separator tokenization. Only complete
  ASCII alias tokens separated by accepted underscore, hyphen, or whitespace
  boundaries are eligible; Unicode letters or marks attached on either side
  invalidate the alias. Explicit compounds, collapsed camel-case compatibility,
  and singular/plural aliases remain allowlisted.
- The v2 store now validates `sqlite_master.sql` in addition to `table_info` and
  `table_xinfo`. A narrow tokenizer accepts keyword case, whitespace, comments,
  terminal semicolons, and standard identifier quoting, then requires the exact
  canonical column grammar. Extra clauses, conflict policies, constraints,
  collations, and table options fail with `request_store_schema_invalid`.
- Any trigger attached to the live `requests` table fails initialization before
  replay/index use. The adversarial tests verify the existing `outcome_unknown`
  row remains unchanged.

### GREEN Evidence

```text
uv run pytest -q tests/test_execution_policy.py -k 'sensitive_effect_aliases_are_elevated or non_sensitive_effects_with_sensitive_substrings_remain_standard'
50 passed, 11 deselected in 0.18s

uv run pytest -q tests/test_request_store.py -k 'on_conflict_replace_before_use or noncanonical_table_ddl or trigger_that_can_replace_request_history or semantically_canonical_formatted_ddl'
10 passed, 89 deselected in 0.37s

uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py
160 passed in 1.13s

uv run pytest -q tests/test_erp_executor.py tests/test_local_audit.py tests/test_builtin_action_catalog.py
134 passed in 12.96s

uv run pytest -q tests/test_credential_cli.py tests/test_local_credentials.py tests/test_runtime_skills.py tests/test_plugin_package.py
172 passed in 5.34s

uv run pytest -q tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py tests/test_connector_driver_contract.py tests/test_generic_drivers.py tests/test_sandbox_execution_manifest.py tests/integration/test_local_erp_mcp.py
229 passed, 2 skipped in 17.33s

uv run ruff check .
All checks passed!

git diff --check
exit 0 (no output)
```
