# Task 5 Fix-1 Report

## Scope

Fixed catalog publication provenance validation, PostgREST method-filter inner
embedding, database source/connector consistency, and trusted workflow branch
restriction.

## RED Evidence

Before changing production files, ran:

```text
uv run pytest tests/test_catalog_migration.py tests/test_catalog_publisher.py -q
6 failed, 6 passed in 0.12s
```

The failing coverage demonstrated all requested gaps:

- `erp_action_versions` lacked a composite `(source_id, connector_id)` foreign
  key to a matching source key.
- Mismatched action `connector_id`, `source_uri`, and `source_hash` each reached
  the first source HTTP request instead of failing locally.
- A method-filtered active-action select lacked `!inner` on the
  constraint-qualified relation.
- The publisher workflow had no `push.branches: [main]` restriction.

## Implementation

- `publish()` revalidates models, then rejects any action whose connector ID,
  source URI, or source hash differs from the validated source with the constant
  `catalog_action_source_mismatch` before making any request.
- `erp_spec_sources` retains its `source_id` primary key and adds a unique
  `(source_id, connector_id)` key. `erp_action_versions` now references that
  composite key, preventing a version connector from being bound to a different
  source connector.
- Method-filtered reads use
  `erp_action_versions!erp_action_catalog_action_id_active_version_id_fkey!inner`;
  reads without a method filter keep the valid left embed and the existing
  `erp_action_versions` response key.
- Catalog publication on push is restricted to `main`; `workflow_dispatch`
  remains available.

## Verification

```text
uv run pytest tests/test_catalog_migration.py tests/test_catalog_publisher.py -q
12 passed in 0.10s

uv run pytest -m "not integration" -q
627 passed, 1 deselected, 1 warning in 1.73s

uv run ruff check .
All checks passed!

git diff --check
passed
```

The full suite warning is the pre-existing Starlette `httpx` TestClient
deprecation warning in `tests/test_connector_mcp_tools.py`.

## Self-review

- The normal idempotent publication test now uses an action whose provenance
  matches the source; immutable version inserts, RLS grants, and service-role
  behavior remain unchanged.
- Mismatch tests construct valid, revalidated actions for each individual
  provenance field and assert the exact no-echo error plus zero HTTP calls.
- The workflow test parses YAML and asserts the push branch list is exactly
  `main`.

## Commit

`b74770b6c3de329bd9b3bd7e58687ce428dd07c6` - `fix: enforce catalog source provenance`

## Residual Risk

- No live Supabase migration was applied, as required. The composite foreign key
  and PostgREST relation should be verified against the intended deployed
  Supabase project before enabling production publication.
- Main-only workflow triggering limits publication from feature branches, but
  repository branch protection and GitHub secret access remain deployment
  configuration outside this worktree.
