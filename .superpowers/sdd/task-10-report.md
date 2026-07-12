# Task 10 Report: Preview State Store and Local Audit Ledger

## Status

Complete. Task 10 adds repository-local write preview state, replay protection,
and an append-only local audit ledger without adding network execution.

## TDD Evidence

1. Initial RED: `uv run pytest tests/test_request_store.py tests/test_local_audit.py -q`
   failed during collection because `mercury_tools.execution.models` and
   `mercury_tools.local.audit` did not exist.
2. Security RED: public summary and audit response tests failed before their
   business-value redaction boundaries were added.
3. Permission RED: an existing JSONL audit file retained mode `0644` before
   constructor-side no-follow `fchmod(0600)` enforcement was added.

## Delivered

- Immutable `PreparedRequest` previews with deterministic canonical hashes,
  exact 15-minute UTC TTLs, safe public summaries, and auth attachment only at
  HTTPX rendering time.
- SQLite request state machine using WAL, `BEGIN IMMEDIATE` for every state
  transition, explicit allowed states, durable expiry invalidation, and a
  replay-blocking partial unique index for executing/succeeded/outcome-unknown
  hashes.
- Append-only audit JSONL with opaque IDs, canonical redaction plus personal
  data/provider-record filtering, no request inputs, no-follow path handling,
  mode `0600`, and `fsync` per event.
- Credential clear now invalidates matching/all pending previews and removes
  matching/all non-secret validation metadata while preserving connector config
  and existing safe CLI output.

## Self Review

- Transactions roll back on all raised state errors; no transition retries.
- `BEGIN IMMEDIATE`, application replay checks, and the partial unique index
  serialize competing process transitions and agree on replay-blocking states.
- SQLite cache paths reject symlink/non-directory components; audit accesses
  use parent directory descriptors with `O_NOFOLLOW`.
- Stored JSON tampering returns payload-free errors; audit JSON tampering is
  re-sanitized on read. All timestamps are timezone-aware UTC.
- Repr, public summaries, validation errors, audit rows, and CLI clear output
  exclude raw request inputs, credentials, and personal/provider values.

## Verification

- `uv run pytest tests/test_request_store.py tests/test_local_audit.py tests/test_redaction.py tests/test_credential_cli.py -q` -> `55 passed`
- `uv run pytest -q -m 'not integration'` -> `962 passed, 1 deselected, 1 warning`
- `uv run ruff check .` -> passed
- `git diff --check` -> passed

## Review Fix Wave

### RED Evidence

1. `uv run pytest tests/test_request_store.py tests/test_local_audit.py tests/test_credential_cli.py tests/test_redaction.py tests/test_local_repository.py -q`
   -> `42 failed, 191 passed`. Failures reproduced missing canonical binding and
   revalidation, exact TTL enforcement, PREVIEWED transition, credential-clear
   ordering, strict dynamic-key handling, SQLite path hardening, and bounded
   audit scans.
2. `uv run pytest tests/test_local_audit.py::test_audit_ledger_strict_allowlists_drop_dynamic_keys -q`
   -> `1 failed`, proving an allowlisted field could still carry a sensitive
   nested JSON key before scalar enforcement.
3. `uv run pytest tests/test_request_store.py::test_public_summary_drops_sensitive_values_encoded_as_dynamic_keys -q`
   -> `1 failed`, proving camel-cased personal values and opaque provider IDs
   could remain visible as summary keys.

### GREEN Evidence

- Focused: `uv run pytest tests/test_request_store.py tests/test_local_audit.py tests/test_audit.py tests/test_credential_cli.py tests/test_redaction.py tests/test_local_repository.py -q`
  -> `236 passed in 0.87s`.
- Full non-integration: `uv run pytest -q -m 'not integration'`
  -> `1007 passed, 1 deselected, 1 warning in 2.41s`.
- Ruff: `uv run ruff check .` -> `All checks passed!`.
- Diff check: `git diff --check` -> passed with no output.

### Review Fixes Delivered

- Canonical ten-field request bindings are recomputed and constant-time checked
  at template creation and every model/SQLite validation, after catalog identity,
  method, and effective-risk-floor validation.
- Exact normalized 15-minute TTLs and the explicit transactional
  `previewed -> awaiting_confirmation` transition are enforced.
- Credential clearing now invalidates previews, clears validation metadata, and
  deletes secrets in that order, with injected-failure coverage for every step.
- Public summaries and audit rows drop sensitive/dynamic keys; audit top-level
  and response fields are strict allowlists with scalar-only retained values.
- SQLite cache/database/sidecars enforce owner, mode, type, link-count, no-follow,
  and retained-descriptor identity checks around WAL connections.
- Audit lookup streams with 64 KiB line, 8 MiB byte, and 100,000-line scan caps
  and does not return an early match from a ledger that later exceeds a bound.

## Final Hardening Wave

### RED Evidence

1. `uv run pytest tests/test_request_store.py -q` -> `11 failed, 48 passed`.
   Static and dynamic action paths were trusted, unsafe path parameters were not
   rejected, no safe target was exposed, and heuristic public-summary keys
   remained visible.
2. `uv run pytest tests/test_operation_lock.py tests/test_credential_cli.py -q`
   failed during collection because the repository operation-lock module did not
   exist. The added forked-process interleaving tests exercise clear versus
   preview readiness and credential save versus clear.
3. `uv run pytest tests/test_local_audit.py -q` -> `15 failed, 5 passed`.
   Known-field cycles recursed, field values were weakly typed, duplicate IDs
   used last-row-wins behavior, no event index existed, and ledgers beyond 8 MiB
   were unavailable.
4. The response-list allowlist regression test failed once before nested lists
   retained the response-summary allowlist instead of falling back to preview
   keys.

### GREEN Evidence

- Focused request/audit/credential/lock/repository/redaction suite:
  `uv run pytest tests/test_request_store.py tests/test_local_audit.py tests/test_audit.py tests/test_local_credentials.py tests/test_credential_cli.py tests/test_connector_setup.py tests/test_operation_lock.py tests/test_local_repository.py tests/test_redaction.py -q`
  -> `344 passed`.
- Full non-integration: `uv run pytest -q -m 'not integration'`
  -> `1035 passed, 1 deselected, 1 warning`.
- Ruff: `uv run ruff check .` -> `All checks passed!`.
- Diff check: `git diff --check` -> passed with no output.

### Final Hardening Delivered

- Structured segment renderer binds the verified final path to the catalog
  template and exact path-parameter set, rejects encoded traversal and path
  ambiguity, and exposes only the action template as the public target.
- Fixed preview and response structural allowlists replace runtime key
  heuristics at every depth.
- One owner-only, no-follow repository operation lock serializes request-store
  mutations and credential setup/clear operations with process/thread
  reentrancy and a portable fallback.
- Audit fields now have bounded field-specific validation before scalar
  redaction; unknown values are never traversed and artifact paths retain only
  opaque Mercury references.
- The append-only JSONL ledger now has an owner-only SQLite event index with
  row hashes, byte offsets, ledger fingerprints, streaming stale-index rebuild,
  duplicate detection, and exact bounded lookup beyond the former 8 MiB cap.

## External Review Final Correction

### Changed Files

- `src/mercury_tools/execution/store.py`
- `src/mercury_tools/local/audit.py`
- `src/mercury_tools/local/credential_cli.py`
- `src/mercury_tools/local/credentials.py`
- `tests/test_request_store.py`
- `tests/test_local_audit.py`
- `tests/test_credential_cli.py`
- `.superpowers/sdd/task-10-report.md`

### RED Evidence

1. Adversarial review suite:
   `uv run pytest tests/test_request_store.py::test_create_preview_requires_catalog_action_provenance tests/test_request_store.py::test_create_preview_rejects_self_consistent_forged_catalog_binding tests/test_request_store.py::test_create_preview_recomputes_exact_effective_catalog_risk tests/test_local_audit.py::test_audit_ledger_redacts_unknown_semantic_values_in_allowlisted_fields tests/test_local_audit.py::test_repeated_get_keeps_ledger_fingerprint_and_does_not_rebuild tests/test_local_audit.py::test_audit_ledger_recovers_malformed_sqlite_index_from_jsonl tests/test_local_audit.py::test_audit_index_miss_for_present_event_forces_one_rebuild tests/test_local_audit.py::test_audit_ledger_recovers_malicious_index_coordinates_and_hashes tests/test_local_audit.py::test_audit_ledger_non_fcntl_fallback_serializes_cross_process_writers tests/test_credential_cli.py::test_credentials_test_does_not_restore_validation_after_concurrent_clear -q`
   -> `8 failed, 4 passed in 0.71s`. Failures proved that a bare/forged
   `PreparedRequest` was accepted, unknown allowlisted values leaked, repeated
   `get` changed ctime, malformed SQLite was fatal, the non-`fcntl` path did not
   serialize writers, and a completed probe restored validation after clear.
2. Isolated current-metadata index checks with same-mode chmod neutralized:
   `uv run pytest tests/test_local_audit.py::test_audit_index_miss_for_present_event_forces_one_rebuild tests/test_local_audit.py::test_audit_ledger_recovers_malicious_index_coordinates_and_hashes -q`
   -> `4 failed in 0.22s`. The miss returned `None`; malicious offsets, lengths,
   and hashes raised `audit_index_corrupt` instead of rebuilding from JSONL.

### Final Correction Delivered

- `LocalRequestStore.create_preview` now requires a revalidated `CatalogAction`
  and verifies action/version/connector/environment/method/template/rendered
  path plus the exact recomputed effective risk before SQLite insertion.
- Audit hardening only calls `fchmod` when mode differs and fingerprints the
  post-hardening descriptor state. The versioned derivative index validates
  completeness metadata, event count, byte coverage, row boundaries, hashes,
  and event identity; it performs one bounded JSONL rebuild on misses or bad
  coordinates and securely recreates malformed/legacy SQLite under the ledger
  lock. Duplicate JSONL event IDs remain stable ledger corruption, with no
  total-ledger scan ceiling.
- Audit event, failure, state, status, error, outcome, and provider strings now
  use fixed operational classifications and redact unknown values.
- The audit ledger uses an owner-only atomic guard-directory process lock when
  `fcntl` is unavailable.
- Credential probes capture a keyed, in-memory credential generation snapshot
  before network I/O, then re-read and compare it under the repository lock
  before persisting validation. Cleared or changed credentials make the probe
  result stale; generation material is neither persisted nor printed.

### Final Verification

- Focused Task-10 suite:
  `uv run pytest tests/test_request_store.py tests/test_local_audit.py tests/test_audit.py tests/test_credential_cli.py tests/test_local_credentials.py tests/test_operation_lock.py tests/test_redaction.py tests/test_local_repository.py -q`
  -> `329 passed in 2.96s`.
- Full non-integration: `uv run pytest -q -m 'not integration'`
  -> `1047 passed, 1 deselected, 1 warning in 4.49s`.
- Ruff: `uv run ruff check .` -> `All checks passed!`.
- Diff check: `git diff --check` -> passed with no output.
- Scope review: only Task-10-owned source/tests and this report changed; no
  Task 11 executor or network-dispatch implementation was added.

### Commit And Concerns

- Implementation commit: `cacc681` (`fix: complete Task 10 boundary hardening`).
- Concern: the full suite retains the pre-existing Starlette `httpx`
  deprecation warning in `tests/test_connector_mcp_tools.py`; no Task-10 test or
  lint failures remain.
