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
