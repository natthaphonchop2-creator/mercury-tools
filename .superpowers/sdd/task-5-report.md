# Task 5 Implementer Report

## Status

Implemented the immutable Supabase ERP action catalog, its service-role-only
PostgREST publisher, deterministic artifact CLI, and GitHub Actions publisher.

## RED

Before production files existed, ran:

```text
uv run pytest tests/test_catalog_migration.py tests/test_catalog_publisher.py -q
8 failed in 0.14s
```

The migration contract tests failed because
`20260711090000_erp_action_catalog.sql` was absent. Publisher tests failed with
the expected missing `mercury_tools.db.catalog` module and absent
`scripts/publish_catalog.py` file. The RED suite covered immutable migration
contract, idempotent version publication, unsafe source preflight, deterministic
active-action filter serialization and round-trip, artifact traversal, CLI error
status, and workflow secret references.

## Files

- `.github/workflows/publish-catalog.yml`
- `scripts/publish_catalog.py`
- `src/mercury_tools/db/catalog.py`
- `supabase/migrations/20260711090000_erp_action_catalog.sql`
- `tests/test_catalog_migration.py`
- `tests/test_catalog_publisher.py`

## GREEN

Final verification:

```text
uv run pytest tests/test_catalog_migration.py tests/test_catalog_publisher.py -q
8 passed in 0.08s

uv run pytest -m "not integration" -q
623 passed, 1 deselected, 1 warning in 2.11s

uv run ruff check .
All checks passed!

git diff --check
passed
```

The one warning is the existing Starlette `httpx` TestClient deprecation warning.
All catalog HTTP tests monkeypatch `httpx.request`; no live Supabase migration or
live Supabase call was performed.

## Self-review

- Version rows are insert-only: their trigger rejects both updates and deletes,
  and the publisher only uses `POST` with `resolution=ignore-duplicates`.
- Sources and active catalog rows use merge upserts. The active catalog's
  deferrable composite foreign key points to the immutable version identity.
- RLS is enabled on all four tables, `anon` and `authenticated` are explicitly
  revoked, and `service_role` receives explicit table grants required by the
  2026 Supabase Data API behavior. The trigger function is not directly
  executable by public, anonymous, or authenticated roles.
- The store revalidates all source/action models before its first request,
  whitelists active-list filters, validates returned definitions, and never puts
  arbitrary Supabase response text or service-role keys into error messages.
- Observation rows have no local runtime writer. Their metadata schema permits
  only `source`, `reviewed_by`, and `note` at the top level.
- The CLI recursively discovers sorted `source.json`/`actions.json` pairs,
  canonical-model validates them before publishing, returns nonzero for errors,
  and prints no environment values. The workflow obtains both Supabase values
  only from GitHub secrets.

## Commit

`d3d9bd75577f829cc0ccfa96b1355df13a90fd1f` - `feat: publish immutable ERP action catalog`

## Residual risks

- This task intentionally did not apply the migration. Parent review should run
  the migration against the intended Supabase project and confirm the deployed
  PostgREST relationship name used for the composite active-version embed.
- The GitHub workflow is the trusted publication path. Repository protection and
  secret access policy remain deployment configuration outside this worktree.
