# Mercury V1 Wave B Task 4 Report

## Scope

Implemented Task 4 fix round 2 only in
`/Users/natthaphon/Desktop/mercury-tools/.worktrees/mercury-v1-wave-a`,
amending the Task 4 change based on
`5eb680a3cf29055194627e714a079b977c0fb785`.

No Task 5 code, public MCP tools, provider OAuth clients, Supabase calls,
Render calls, provider calls, push, tag, or deployment were performed.

## Round 2 Findings Resolved

### 1. PostgreSQL disconnect

- Replaced invalid `pg_catalog.coalesce(...)` with the PostgreSQL expression
  `coalesce(...)`.
- The disposable PostgreSQL test saves one envelope, disconnects twice, and
  proves that the first call deletes one envelope and increments revision from
  1 to 2 while the second call deletes zero envelopes and leaves revision 2.

### 2. One OAuth state per setup attempt

- Added a rerunnable unique index on
  `mercury_provider_oauth_states(setup_attempt_id)`.
- `create_mercury_provider_oauth_state` now conditionally updates the exact
  unconsumed, unexpired, tenant/workspace/user/provider/environment-bound setup
  attempt and sets `consumed_at` before inserting the OAuth state in the same
  transaction.
- Sequential replay and two-session concurrent replay each produce exactly one
  state. Failed state insertion rolls back the setup-attempt claim.

### 3. Authenticated envelope persistence

- `ProviderConnectionStore` now requires a configured `CredentialVault`.
- Before any connection or envelope persistence, every envelope is opened
  against its exact tenant, workspace, auth user, connection, provider,
  company/merchant, environment, credential type, and key-version binding.
  This authenticates the AES-GCM tag and fails closed for keys outside the
  configured active/previous set.
- Each request-scoped opened `bytearray` is cleared in `finally` on a
  best-effort basis.
- SQL save, envelope load, and disconnect RPCs are executable only by
  `service_role`. Authenticated users retain only the redacted connection-list
  RPC.
- Added a service-role-only backend membership assertion for the explicit
  tenant/workspace/auth-user triple. It does not depend on ambient workspace
  state or `auth.uid()`.
- PostgreSQL stores no vault master key. The trusted backend authenticates
  envelopes with `CredentialVault` before invoking the service-role save RPC.
- Regressions prove authenticated RPC denial, wrong-member backend denial, and
  that unknown-key or forged-tag envelopes cannot create a ready connection in
  the Python persistence contract.

### 4. Stable secret-safe SQL errors

- Create and consume RPCs now reject null, nil UUID, malformed provider,
  environment, hash, PKCE, callback, permission, and expiry values before
  insert or update.
- Expected uniqueness, not-null, check, foreign-key, text-cast, and datetime
  failures are translated to stable setup/OAuth errors.
- Envelope JSON casts remain inside a sanitized exception boundary.
- PostgreSQL regressions inspect complete stdout plus stderr and prove that
  token-hash, PKCE ciphertext, envelope ID, and ciphertext sentinels are absent
  from returned error text and `DETAIL`.

### 5. Reconnect lifecycle

- A disconnected row may be reactivated only with the same connection ID,
  exact tenant/workspace/user/provider/environment/account binding, and
  `current revision + 1`.
- Reactivation requires replacement envelopes that pass vault authentication,
  preserves `created_at`, and clears `disconnected_at` and
  `provider_revocation_required`.
- A new connection ID for the same provider account remains a conflict,
  including when the existing row is disconnected.
- Python and PostgreSQL regressions cover success, stale revision, changed
  binding, and new-ID conflict.

### 6. Permission arrays

- SQL permission validation iterates JSON values and requires every element to
  be a string matching the closed permission format.
- Duplicate and unsorted permissions are rejected to match the Python model.
- OAuth callback `requested_permissions` uses the same validation.
- PostgreSQL regressions cover valid, null, numeric, duplicate, unsorted, and
  callback-array cases.

## Migration Properties

- Both Task 4 migrations remain expand-first and rerunnable.
- The PostgreSQL regression applies all prerequisite and Task 4 migrations
  twice on the same fresh database.
- No table is dropped or truncated, and no legacy row is deleted.
- The OAuth-state uniqueness rule is added with
  `create unique index if not exists`.
- Task 4 tables retain RLS and direct `anon`/`authenticated` table denial.

## TDD Evidence

Before production changes:

```text
uv run pytest -q tests/test_provider_connection_store.py tests/test_credential_vault.py tests/test_redaction.py
26 failed, 109 passed

MERCURY_V1_POSTGRES_TEST=1 uv run pytest -q tests/integration/test_postgres_task4_provider_connections.py
7 failed, 1 passed in 37.79s
```

After the fixes:

```text
uv run pytest -q tests/test_provider_connection_store.py tests/test_credential_vault.py tests/test_redaction.py
135 passed in 0.18s

MERCURY_V1_POSTGRES_TEST=1 uv run pytest -q tests/integration/test_postgres_task4_provider_connections.py
8 passed in 53.78s
```

## Final Verification

```text
MERCURY_V1_POSTGRES_TEST=1 uv run pytest -q tests/test_credential_vault.py tests/test_provider_connection_store.py tests/test_redaction.py tests/test_v1_config.py tests/integration/test_postgres_v1_workspace.py tests/integration/test_postgres_task4_provider_connections.py
172 passed in 68.44s (0:01:08)

uv run ruff check src/mercury_tools/credentials src/mercury_tools/providers/models.py src/mercury_tools/providers/store.py tests/test_credential_vault.py tests/test_provider_connection_store.py tests/integration/test_postgres_task4_provider_connections.py
All checks passed!

uv lock --check
Resolved 53 packages in 5ms

git diff --check
clean
```

The PostgreSQL checks used disposable `postgres:17-alpine` containers, asserted
that `server_version_num` starts with 17, and removed the containers after each
module.

## Changed Paths

- `.superpowers/sdd/2026-07-26-mercury-v1-authorization-gateway/task-4-report.md`
- `src/mercury_tools/providers/store.py`
- `supabase/migrations/20260726101000_mercury_v1_provider_connections.sql`
- `supabase/migrations/20260726102000_mercury_v1_credential_vault.sql`
- `tests/test_provider_connection_store.py`
- `tests/integration/test_postgres_task4_provider_connections.py`
