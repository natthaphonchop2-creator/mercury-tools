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
