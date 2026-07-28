# Task 6 Surgical Breaker Report

## Status

PASS. The deterministic upgrade-owner finding from `task-6-rereview-5.md`
is fixed without changing the provider runtime protocol, provider APIs, Task 7,
deployment configuration, direct REST driver, or public tool signatures.

## Changed Files

- `supabase/migrations/20260728120000_mercury_v1_provider_oauth_generations.sql`
  replaces the nondeterministic generation backfill with one materialized
  selected-candidate relation, adds the supporting target-connection index,
  holds unowned upgrade targets, and closes list/load dispatch for unowned
  targets with OAuth attempt history.
- `tests/integration/test_postgres_task4_provider_connections.py` adds the
  PostgreSQL 17 base-to-head adversarial regression.
- `tests/test_provider_connection_store.py` updates the migration structure
  contract to assert the selected relation, unique-rank guard, index, and
  history gate.
- `.superpowers/sdd/2026-07-26-mercury-v1-authorization-gateway/task-6-surgical-breaker-report.md`
  records this implementation and verification.

## Migration Reasoning

The original migration acknowledged every finalized attempt on a ready target,
then used an unrestricted `UPDATE ... FROM` to assign one arbitrary finalized,
failed, or revoked row. A reused target could therefore acknowledge both
generation A and generation B while assigning A to B's material.

The replacement is one atomic writable CTE:

1. `upgrade_targets` considers only unassigned connections with existing OAuth
   attempt history. This preserves an existing generation marker on replay.
2. `binding_compatible_candidates` requires the same tenant, workspace, user,
   provider, environment, authorization method, and granted permissions.
   Provider account binding must either match the current target or use the
   exact pre-generation `oauth-pending-<attempt-id>` discriminator retained by
   legacy finalized attempts.
3. Ready targets with envelopes accept only finalized, non-revoking attempts
   whose `target_revision` is no greater than the current connection revision.
   Disconnected targets with a revocation obligation accept only failed,
   revoking attempts whose target revision exactly matches the disconnected
   connection revision. Revoked history is never a candidate.
4. Candidates rank by greatest target revision. A candidate is selected only
   when the greatest ownership rank contains exactly one row. Updated time,
   created time, and UUID provide stable evaluation order after target revision
   but never resolve an equal-revision ownership ambiguity.
5. The same materialized `selected_candidates` relation drives both the
   finalized-attempt acknowledgement update and connection
   `oauth_generation_id` assignment in one statement.
6. A ready upgrade target without a selected owner moves to
   `requires_validation`. Public list/load additionally reject any unassigned
   connection that still has OAuth attempt history, so ambiguous or absent
   ownership cannot dispatch even with legacy nullable generation markers.
7. The migration skips every connection with an existing generation marker.
   Replay therefore preserves a valid current owner and does not acknowledge a
   later same-target contender.

The migration remains forward-only and replayable. It contains no `DROP` or
`TRUNCATE`.

## Regression Coverage

The new PostgreSQL 17 regression starts from migrations through
`20260728110000` and builds these schema-valid histories before applying head:

- generation A finalizes at target revision 1;
- the target disconnects and completes provider revocation while A remains
  finalized;
- generation B reconnects into the same target and finalizes revision 3 with a
  newer envelope;
- two binding-compatible finalized attempts share the same ownership rank;
- a ready target has only completed revoked history;
- a disconnected revocation target has one exact failed owner.

It applies and replays head, proves only B is assigned and acknowledged, proves
ambiguous and absent ready targets are held and rejected by public list/load,
and proves the failed owner is assigned without acknowledgement. It then adds a
same-target finalized contender and replays again to prove B remains the valid
owner. Finally it simulates B's lost-response failure and proves B's latest
envelope is retained on the failed attempt, the target is disconnected with a
revocation obligation, persisted public envelopes are removed, and public
list/load reject the target.

Test-first evidence:

```text
MERCURY_V1_POSTGRES_TEST=1 uv run pytest -q \
  tests/integration/test_postgres_task4_provider_connections.py::test_base_to_head_upgrade_selects_only_proven_oauth_generation_owners
1 failed in 85.31s
```

The failure showed A acknowledged, both ambiguous attempts acknowledged, an
ambiguous owner assigned, and the revoked-only target left ready.

After the migration fix:

```text
MERCURY_V1_POSTGRES_TEST=1 uv run pytest -q \
  tests/integration/test_postgres_task4_provider_connections.py::test_base_to_head_upgrade_selects_only_proven_oauth_generation_owners
1 passed in 49.53s
```

The final PostgreSQL suite below reran the regression after all edits.

## Exact Verification

Focused Task 6 suite:

```text
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q -p no:cacheprovider \
  tests/test_provider_oauth_production.py \
  tests/test_protected_resource_routes.py \
  tests/test_flowaccount_provider_oauth.py \
  tests/test_provider_connection_store.py \
  tests/test_mercury_consent.py \
  tests/test_http_app.py \
  tests/test_connector_mcp_tools.py
247 passed, 1 warning in 2.95s
```

Affected auth/cloud/MCP/config/driver/store suite:

```text
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q -p no:cacheprovider \
  tests/test_auth_jwt.py \
  tests/test_cloud_api.py \
  tests/test_connector_mcp_tools.py \
  tests/test_flowaccount_provider_driver.py \
  tests/test_flowaccount_provider_oauth.py \
  tests/test_http_app.py \
  tests/test_mercury_consent.py \
  tests/test_protected_resource_routes.py \
  tests/test_provider_connection_store.py \
  tests/test_provider_driver_manifest.py \
  tests/test_provider_oauth_production.py \
  tests/test_v1_config.py
569 passed, 1 warning in 7.93s
```

Full repository:

```text
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q -p no:cacheprovider
5755 passed, 28 skipped, 1 warning in 216.07s (0:03:36)
```

The extra default skip is the new PostgreSQL opt-in regression. The warning is
the existing Starlette `httpx` TestClient deprecation warning.

PostgreSQL 17 opt-in suite:

```text
MERCURY_V1_POSTGRES_TEST=1 PYTHONDONTWRITEBYTECODE=1 \
  uv run --frozen pytest -q -p no:cacheprovider \
  tests/integration/test_postgres_task4_provider_connections.py
15 passed in 288.41s (0:04:48)
```

Static and integrity checks:

```text
uv run --frozen ruff check --no-cache .
All checks passed!

uv run --frozen ruff format --check --no-cache \
  tests/integration/test_postgres_task4_provider_connections.py \
  tests/test_provider_connection_store.py
2 files already formatted

uv lock --check
Resolved 66 packages in 35ms

git diff --check
passed
```

A case-insensitive destructive scan of the changed head migration found no
`DROP` or `TRUNCATE`. A focused added-line scan for AWS, OpenAI, GitHub, Slack,
private-key, and JWT credential signatures found no matches.

The protected direct REST FlowAccount driver remains unchanged:

```text
git rev-parse e99ad20a91d0bc1d8f35ea85ba1991fbc7742630:src/mercury_tools/drivers/flowaccount.py
2aeb95644cd6f01ee6a842643e10992a68705e3e

git rev-parse HEAD:src/mercury_tools/drivers/flowaccount.py
2aeb95644cd6f01ee6a842643e10992a68705e3e

git hash-object src/mercury_tools/drivers/flowaccount.py
2aeb95644cd6f01ee6a842643e10992a68705e3e
```

## Scoped Re-review

One fresh scoped re-review was performed against the final migration, dynamic
regression, static migration contract, and report. It found no P0, P1, P2, or
P3 issue and no change outside the surgical contract. The review specifically
rechecked ownership ordering, exact legacy binding compatibility, equal-rank
ambiguity, failed-versus-revoked ownership, atomic acknowledgement and
assignment, nullable-marker dispatch denial, valid-owner replay preservation,
and latest-material quarantine after B failure.

## Residual Boundaries

- No live FlowAccount authorization, exchange, discovery, validation, refresh,
  or revocation call was made.
- No live Supabase/PostgREST, Render, deployment, push, tag, or other external
  call was made. Disposable local PostgreSQL 17 is the migration authority for
  this remediation.
- Public SQL list/load eligibility was narrowed only as required to keep
  ambiguous or absent upgrade ownership non-dispatchable. No Python runtime
  protocol, provider API, public tool signature, direct REST driver, or Task 7
  surface changed.
- Ambiguous and absent owners intentionally remain held for reviewed
  remediation. Designing that operator workflow remains outside this surgical
  fix.
- A transaction already running before migration commit cannot be
  retroactively cancelled. The migration covers committed base-to-head state.
