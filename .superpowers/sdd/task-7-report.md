# Task 7 Report: One Immutable ERP Approval

## Scope

Completed the inherited dirty Task 7 implementation without resetting prior work.
ERP mutations now bind one approval level and one mutation class into the canonical
payload hash, archive v1 request state on SQLite schema migration, and execute at
most once after one transactional approval. Task 8 tool-surface changes are not
included.

The repository uses `tests/test_erp_executor.py`; the plan's
`tests/test_executor.py` path does not exist.

## RED Evidence

The inherited Task 7 focused suite was already green on the first continuation
run, so no original pre-implementation RED output was available. Contract review
found an adjacent read-path regression and added a focused reproduction:

```text
uv run pytest -q tests/test_local_mcp_contract.py::test_local_runtime_read_does_not_apply_mutation_policy
1 failed in 0.49s
ValueError: read_action_has_no_mutation_class
```

Root cause: `effective_risk()` correctly became mutation-only, while
`LocalMercuryRuntime.run_read()` still called it for GET actions. The read gate now
checks the catalog's `GET` and `SAFE_READ` fields directly.

A broad non-integration diagnostic was intentionally interrupted at 81% after the
user reprioritized focused Task 7 verification. Before interruption it identified
six failures, all in `tests/test_credential_cli.py`, with 4,818 tests passed and 21
deselected. Every failure came from legacy fixtures still constructing the v0.2
`RiskDecision(tier, confirmation_count, reasons)` shape and hashing
`required_confirmations`; production credential-clear logic was not failing.

## GREEN Evidence

```text
uv run pytest -q tests/test_local_mcp_contract.py::test_local_runtime_read_does_not_apply_mutation_policy
1 passed in 0.36s

uv run pytest -q tests/test_credential_cli.py
32 passed in 1.74s

uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py tests/test_erp_executor.py -k "approval or confirmation or outcome_unknown"
25 passed, 142 deselected in 7.71s

uv run pytest -q tests/test_execution_policy.py tests/test_request_store.py tests/test_erp_executor.py tests/test_local_audit.py
206 passed in 6.79s

uv run pytest -q tests/test_credential_cli.py tests/test_local_mcp_contract.py tests/test_local_mcp_roots.py tests/test_local_credentials.py tests/test_connector_driver_contract.py tests/test_generic_drivers.py tests/test_sandbox_execution_manifest.py tests/integration/test_local_erp_mcp.py
313 passed, 2 skipped in 12.62s

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

## Contract Coverage

- `ApprovalLevel` is `standard` or `elevated`; `MutationClass` is `create`,
  `update`, or `sensitive`.
- POST creates and PUT/PATCH updates use one standard approval unless a sensitive
  effect or an inferred, never-observed mutation elevates them.
- DELETE and payment, approve, void, post, finalize, email, share, invite, and
  delete effects use one elevated sensitive approval.
- New request JSON contains `approval_level`, `mutation_class`, and a literal
  `approval_count` of 0 or 1. It has no reusable v0.2 confirmation count.
- Approval level and mutation class are part of the canonical payload hash.
- SQLite `user_version=2` archives v1 requests in `requests_v1_archive`, creates an
  empty v2 live table, preserves the audit ledger, and is rerun-safe.
- Approval is a single immediate transaction that validates hash, expiry, state,
  and expected mutation class. Repeated and concurrent approvals record one
  transition only.
- `approve_and_execute()` revalidates action version, target, credentials,
  preflights, and payload before dispatch. Unknown dispatched outcomes block retry.
- Audit output includes human-readable approval and mutation fields but excludes
  request inputs, provider bodies, and legacy required-confirmation fields.

## Residual Risk

The complete non-integration repository suite was not rerun to completion after
the fixture fixes. Required focused and adjacent execution/local suites are green;
the unexecuted tail consists primarily of broader release and artifact tests.
