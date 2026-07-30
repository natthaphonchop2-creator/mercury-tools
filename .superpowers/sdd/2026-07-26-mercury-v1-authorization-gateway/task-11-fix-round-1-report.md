# Task 11 Fix Round 1 Report

## Status

Implemented the release-owned first-party Skill publication path for Task 11.
The change is limited to generation, migration publication, and focused tests.
No deployment, push, tag, or live Supabase, Render, or provider call was made.

## Design Choice

The fix uses a deterministic generated migration seed:

- `scripts/build_v1_skill_publication_migration.py` imports
  `ACCOUNTING_SKILL_CATALOG` and derives each global publication row's
  `projection`, `projection_sha256`, `skill_id`, `skill_version`, and
  `git_source_path`.
- The checked-in migration executes as a deployment migration with an anonymous
  PostgreSQL `DO` block. It does not create a function, PostgREST RPC, MCP tool,
  or runtime request path.
- Publication uses `INSERT ... ON CONFLICT DO NOTHING`, then verifies exactly
  one row exists for the first-party `skill_id` and `skill_version` and compares
  visibility, ownership, status, projection, hash, and Git source path.
- An exact matching row is idempotent. Any drift, superseded state, or
  visibility/ownership collision raises
  `mercury_first_party_skill_publication_mismatch` and rolls back the migration.
- The migration never updates or deletes a published row, so immutable
  projections and terminal supersession remain unchanged.
- The payload is fixed to `global` visibility with null tenant and workspace
  ownership. No workspace-Skill publication API was added.

## Changed Files

- `scripts/build_v1_skill_publication_migration.py`
  - Added deterministic payload and migration generation plus a `--check` mode.
- `supabase/migrations/20260731100000_mercury_v1_publish_first_party_skills.sql`
  - Added the generated release publication migration for all 15 current
    first-party Skills.
- `tests/test_v1_skill_publication.py`
  - Added catalog coverage, exact generated artifact, and hosted-surface
    boundary tests.
- `tests/integration/test_postgres_task11_knowledge_scope.py`
  - Replaced the canonical privileged hand insert with the real migration path.
  - Added rerun idempotency, mismatch fail-closed, all-runtime-role DML denial,
    and canonical resolver coverage.
- `.superpowers/sdd/2026-07-26-mercury-v1-authorization-gateway/task-11-fix-round-1-report.md`
  - Added this implementation and verification report.

## Test-First Evidence

The focused tests were written before the builder and migration existed.

Command:

```bash
uv run pytest -q tests/test_v1_skill_publication.py tests/integration/test_postgres_task11_knowledge_scope.py::test_task11_migrations_exist_before_postgres_setup
```

Exact RED output:

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
______ test_release_publication_payload_covers_every_git_canonical_skill _______

    def test_release_publication_payload_covers_every_git_canonical_skill() -> None:
>       builder = _publication_builder()
                  ^^^^^^^^^^^^^^^^^^^^^^

tests/test_v1_skill_publication.py:30:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def _publication_builder() -> ModuleType:
>       assert BUILD_SCRIPT.exists(), "release-owned Skill publication builder is missing"
E       AssertionError: release-owned Skill publication builder is missing
E       assert False

tests/test_v1_skill_publication.py:18: AssertionError
___ test_checked_in_publication_migration_is_exact_deterministic_projection ____

    def test_checked_in_publication_migration_is_exact_deterministic_projection() -> None:
>       builder = _publication_builder()
                  ^^^^^^^^^^^^^^^^^^^^^^

tests/test_v1_skill_publication.py:56:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def _publication_builder() -> ModuleType:
>       assert BUILD_SCRIPT.exists(), "release-owned Skill publication builder is missing"
E       AssertionError: release-owned Skill publication builder is missing
E       assert False

tests/test_v1_skill_publication.py:18: AssertionError
______________ test_task11_migrations_exist_before_postgres_setup ______________

    def test_task11_migrations_exist_before_postgres_setup() -> None:
        assert TASK_11_MIGRATION.exists(), "Task 11 migration is missing"
>       assert TASK_11_PUBLICATION_MIGRATION.exists(), (
            "Task 11 first-party Skill publication migration is missing"
        )
E       AssertionError: Task 11 first-party Skill publication migration is missing
E       assert False

tests/integration/test_postgres_task11_knowledge_scope.py:50: AssertionError
=========================== short test summary info ============================
FAILED tests/test_v1_skill_publication.py::test_release_publication_payload_covers_every_git_canonical_skill
FAILED tests/test_v1_skill_publication.py::test_checked_in_publication_migration_is_exact_deterministic_projection
FAILED tests/integration/test_postgres_task11_knowledge_scope.py::test_task11_migrations_exist_before_postgres_setup
3 failed in 0.08s
```

After implementing the generator and migration, the same command produced:

```text
...                                                                      [100%]
3 passed in 0.18s
```

## Final Verification

Task 11 routing and knowledge scope:

```bash
uv run pytest -q tests/test_v1_skill_routing.py tests/test_v1_knowledge_scope.py
```

```text
....................                                                     [100%]
20 passed in 1.02s
```

Fresh PostgreSQL 17 migration and publication path:

```bash
MERCURY_V1_POSTGRES_TEST=1 uv run pytest -q tests/integration/test_postgres_task11_knowledge_scope.py
```

```text
.........                                                                [100%]
9 passed in 84.74s (0:01:24)
```

Additional publication, legacy routing, and RAG regressions:

```bash
uv run pytest -q tests/test_v1_skill_publication.py tests/test_knowledge_routing.py tests/test_skill_routing.py tests/test_mcp_rag_routing.py
```

```text
..................................                                       [100%]
34 passed in 0.68s
```

The brief names `tests/test_mcp_contracts.py`, which does not exist in this
worktree. The exact attempted command and output were:

```bash
uv run pytest -q tests/test_mcp_review_contract.py tests/test_mcp_contracts.py
```

```text
ERROR: file or directory not found: tests/test_mcp_contracts.py


no tests ran in 0.00s
```

The existing contract files are `tests/test_mcp_contract.py` and
`tests/test_v1_mcp_tool_contract.py`. Running both with the review contract:

```bash
uv run pytest -q tests/test_mcp_review_contract.py tests/test_mcp_contract.py tests/test_v1_mcp_tool_contract.py
```

```text
........................................................................ [ 24%]
........................................................................ [ 49%]
........................................................................ [ 74%]
........................................................................ [ 99%]
..                                                                       [100%]
290 passed in 6.54s
```

Generated artifact drift check:

```bash
uv run python scripts/build_v1_skill_publication_migration.py --check
```

Result: exit 0 with no output.

Python lint:

```bash
uv run ruff check scripts/build_v1_skill_publication_migration.py tests/test_v1_skill_publication.py tests/integration/test_postgres_task11_knowledge_scope.py
```

```text
All checks passed!
```

Python formatting:

```bash
uv run ruff format --check scripts/build_v1_skill_publication_migration.py tests/test_v1_skill_publication.py tests/integration/test_postgres_task11_knowledge_scope.py
```

```text
3 files already formatted
```

Lockfile:

```bash
uv lock --check
```

```text
Resolved 66 packages in 3ms
```

Staged diff whitespace check:

```bash
git diff --cached --check
```

Result: exit 0 with no output.

## Security Checks

- The generated payload covers all 15 catalog identities and all 15
  `git_source_path` files exist.
- PostgreSQL recomputes each SHA-256 through
  `mercury_canonical_jsonb(projection)` before insertion.
- A forged existing projection causes the real migration to fail and leaves the
  forged row unchanged; it is not silently overwritten.
- Reapplying the real migration preserves the complete stored rows, including
  IDs and timestamps.
- Direct `INSERT`, `UPDATE`, and `DELETE` are denied for `anon`,
  `authenticated`, and `service_role`.
- Existing service-role `SELECT` and exact authorized-workspace resolution
  resolve every canonical first-party Skill.
- Static checks prove the migration adds no function, executable grant, DML
  grant, update, or delete path.
- No database credential, service key, provider credential, raw provider
  payload, personal identifier, or tax identifier was added to source, logs,
  test output, or this report.

## Self-Review

- Confirmed the new migration sorts after the Task 11 schema and canonical JSON
  authority migrations.
- Confirmed every publication value comes from the Git catalog or the fixed
  global publication policy.
- Confirmed idempotency does not use an update and cannot reactivate a
  superseded identity.
- Confirmed visibility or ownership collisions for a first-party
  `skill_id`/`skill_version` fail closed rather than creating ambiguous
  resolver output.
- Confirmed the change does not touch runtime MCP schemas, PostgREST RPCs, RLS
  policies, or service-role grants.
- Confirmed the canonical integration test no longer performs its prior
  privileged hand insert.

## Remaining Concerns

- The generated SQL payload is intentionally large because it contains every
  exact JSON schema projection. It must be regenerated, not hand-edited, when a
  first-party catalog definition changes.
- A pre-existing manually inserted workspace or global row that reuses a
  first-party `skill_id` and `skill_version` will intentionally block the
  release migration for manual review.
- Production application remains unverified by scope because no live service
  or deployment was called.
- The brief's plural `tests/test_mcp_contracts.py` path should be corrected in a
  later controller/documentation update; no compatibility alias was added in
  this scoped fix.

## Controller Follow-up: Multiline Generated Payload

Controller verification of commit
`5399842c45af790bc182b45c8ccc4ce649556783` found a Gitleaks
`generic-api-key` false positive because the entire generated JSON payload was
one source line. The renderer now parses its canonical JSON and emits
deterministic, sorted, two-space-indented JSON. Parsed payload values,
projection hashes, SQL postconditions, and publication behavior are unchanged.

The follow-up test was added before changing the renderer.

Command:

```bash
uv run pytest -q tests/test_v1_skill_publication.py::test_generated_publication_payload_is_multiline_and_reviewable
```

Exact RED output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________ test_generated_publication_payload_is_multiline_and_reviewable ________

    def test_generated_publication_payload_is_multiline_and_reviewable() -> None:
        builder = _publication_builder()
        rendered = builder.render_publication_migration()
        _, payload, _ = rendered.split("$mercury_v1_first_party_skill_payload$")
        payload_lines = payload.splitlines()

        assert json.loads(payload) == list(builder.build_publication_payload())
>       assert len(payload_lines) > len(ACCOUNTING_SKILL_CATALOG)
E       assert 1 > 15
E        +  where 1 = len(['[{"git_source_path":"plugins/mercury-finance/skills/company-health-check-th/SKILL.md",...}]'])
E        +  and   15 = len((AccountingSkillDefinition(...), ...))

tests/test_v1_skill_publication.py:83: AssertionError
=========================== short test summary info ============================
FAILED tests/test_v1_skill_publication.py::test_generated_publication_payload_is_multiline_and_reviewable
1 failed in 0.20s
```

Focused unit verification after the renderer change:

```bash
uv run pytest -q tests/test_v1_skill_publication.py
```

```text
...                                                                      [100%]
3 passed in 0.47s
```

Fresh PostgreSQL 17 verification:

```bash
MERCURY_V1_POSTGRES_TEST=1 uv run pytest -q tests/integration/test_postgres_task11_knowledge_scope.py
```

```text
.........                                                                [100%]
9 passed in 92.19s (0:01:32)
```

Generated artifact check:

```bash
uv run python scripts/build_v1_skill_publication_migration.py --check
```

Result: exit 0 with no output.

Follow-up lint:

```bash
uv run ruff check scripts/build_v1_skill_publication_migration.py tests/test_v1_skill_publication.py
```

```text
All checks passed!
```

Follow-up formatting:

```bash
uv run ruff format --check scripts/build_v1_skill_publication_migration.py tests/test_v1_skill_publication.py
```

```text
2 files already formatted
```

The regenerated migration file itself passes pinned Gitleaks:

```bash
/tmp/gitleaks dir --config .gitleaks.toml --no-banner --redact --exit-code 1 supabase/migrations/20260731100000_mercury_v1_publish_first_party_skills.sql
```

```text
5:20AM INF scanned ~231180 bytes (231.18 KB) in 33.2ms
5:20AM INF no leaks found
```

The controller's required history command was reproduced before the follow-up
commit:

```bash
/tmp/gitleaks git --config .gitleaks.toml --no-banner --redact --exit-code 1 --log-opts=da23f804d20d4d0c3dd65fac2a48f178e0d764ef..HEAD .
```

```text
5:18AM INF 1 commits scanned.
5:18AM INF scanned ~142261 bytes (142.26 KB) in 143ms
5:18AM WRN leaks found: 1
```

Because `gitleaks git` executes `git log -p`, a temporary dangling commit object
was used to test the required two-commit history without changing the branch.
The fixed final tree still leaves the original one-line addition in commit
`5399842` reachable:

```text
5:22AM INF 2 commits scanned.
5:22AM INF scanned ~371012 bytes (371.01 KB) in 172ms
5:22AM WRN leaks found: 1
```

This remaining history finding cannot be removed by a second child commit.
Passing the exact history-range command requires rewriting or squashing
`5399842`, or suppressing the finding. Both were explicitly excluded from this
follow-up at that stage, so neither was performed then.

## Authorized Local History Consolidation

The controller subsequently authorized a narrow local history rewrite after
confirming that the branch had no upstream and that commits `5399842` and
`bb4b4be` were unpublished Task 11 fix commits only.

Those two commits were consolidated into one focused commit named
`fix: publish canonical first-party skills`, directly on
`da23f804d20d4d0c3dd65fac2a48f178e0d764ef`. The consolidation preserves the
exact final implementation tree, including deterministic multiline JSON,
canonical projection values and hashes, idempotent publication checks, tests,
and this report. The original one-line generated payload is no longer present
in the reachable `da23f80..HEAD` history.

No Gitleaks configuration, allowlist, or suppression was changed. No commit at
or before `da23f804d20d4d0c3dd65fac2a48f178e0d764ef` was altered. No deployment,
push, or tag was performed.
