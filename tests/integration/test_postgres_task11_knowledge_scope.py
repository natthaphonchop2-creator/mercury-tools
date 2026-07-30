from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TASK_11_MIGRATION = (
    ROOT / "supabase/migrations/20260726104000_mercury_v1_workspace_knowledge_scope.sql"
)
TASK_11_PUBLICATION_MIGRATION = (
    ROOT / "supabase/migrations/20260731100000_mercury_v1_publish_first_party_skills.sql"
)
MIGRATIONS = (
    ROOT / "supabase/migrations/0001_mercury_tools_rag.sql",
    ROOT / "supabase/migrations/0003_match_knowledge_chunks_null_embedding.sql",
    ROOT / "supabase/migrations/0004_match_knowledge_chunks_endpoint_terms.sql",
    ROOT / "supabase/migrations/20260713101000_validation_knowledge_rag_filters.sql",
    ROOT / "supabase/migrations/0002_mercury_product_layer.sql",
    ROOT / "supabase/migrations/20260726100000_mercury_v1_identity.sql",
    ROOT / "supabase/migrations/20260711090000_erp_action_catalog.sql",
    ROOT / "supabase/migrations/20260726103000_mercury_v1_catalog_qualification.sql",
)
AUTH_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_AUTH_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
_OPT_IN = "MERCURY_V1_POSTGRES_TEST"


@dataclass(frozen=True)
class PostgresContext:
    container: str
    tenant_id: UUID
    workspace_id: UUID
    sibling_workspace_id: UUID
    other_tenant_id: UUID
    other_workspace_id: UUID


def test_task11_migrations_exist_before_postgres_setup() -> None:
    assert TASK_11_MIGRATION.exists(), "Task 11 migration is missing"
    assert TASK_11_PUBLICATION_MIGRATION.exists(), (
        "Task 11 first-party Skill publication migration is missing"
    )


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                check=False,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def _docker(
    *args: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )


def _psql_result(container: str, sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec",
        "-i",
        container,
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "supabase_admin",
        "-d",
        "mercury_task11_test",
        input_text=sql,
    )


def _psql(container: str, sql: str) -> str:
    result = _psql_result(container, sql)
    if result.returncode != 0:
        raise AssertionError(f"{result.stderr.strip()}\n{result.stdout[-4_000:].strip()}")
    return result.stdout.strip()


def _authenticated(
    context: PostgresContext,
    sql: str,
    *,
    auth_user_id: UUID = AUTH_USER_ID,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> str:
    return (
        "set role authenticated;\n"
        f"set request.jwt.claim.sub = '{auth_user_id}';\n"
        f"set request.jwt.claim.tenant_id = '{tenant_id or context.tenant_id}';\n"
        f"set request.jwt.claim.workspace_id = '{workspace_id or context.workspace_id}';\n"
        f"{sql}"
    )


def _service(sql: str) -> str:
    return f"set role service_role;\n{sql}"


def _search_sql(
    context: PostgresContext,
    *,
    tenant_id: UUID | None = None,
    workspace_id: UUID | None = None,
    auth_user_id: UUID = AUTH_USER_ID,
) -> str:
    return f"""
        select coalesce(
          jsonb_agg(source_uri order by source_uri),
          '[]'::jsonb
        )::text
        from public.search_mercury_v1_knowledge(
          '{tenant_id or context.tenant_id}',
          '{workspace_id or context.workspace_id}',
          '{auth_user_id}',
          'invoice',
          20,
          'keyword',
          null,
          null,
          null,
          null,
          null,
          null,
          null
        );
    """


@pytest.fixture(scope="module")
def postgres_context() -> PostgresContext:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"set {_OPT_IN}=1 to run disposable PostgreSQL regression")
    if not TASK_11_MIGRATION.exists() or not TASK_11_PUBLICATION_MIGRATION.exists():
        pytest.skip("Task 11 migration or publication path is missing")
    if not _docker_available():
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")

    container = f"mercury-task11-postgres-{uuid4().hex[:12]}"
    started = _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=mercury_task11_test",
        "public.ecr.aws/supabase/postgres:17.6.1.143",
    )
    assert started.returncode == 0, started.stderr
    try:
        for _ in range(180):
            health = _docker(
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                container,
            )
            if health.returncode == 0 and health.stdout.strip() == "healthy":
                break
            time.sleep(0.5)
        else:
            pytest.fail("disposable PostgreSQL did not become ready")

        assert _psql(container, "select current_setting('server_version_num');").startswith("17")
        _psql(
            container,
            """
            do $$
            begin
              if not exists (select 1 from pg_roles where rolname = 'anon') then
                create role anon nologin;
              end if;
              if not exists (
                select 1 from pg_roles where rolname = 'authenticated'
              ) then
                create role authenticated nologin;
              end if;
              if not exists (
                select 1 from pg_roles where rolname = 'service_role'
              ) then
                create role service_role nologin bypassrls;
              end if;
            end;
            $$;
            do $$
            begin
              if not exists (
                select 1
                from pg_roles
                where rolname = 'service_role'
                  and rolbypassrls
              ) then
                raise exception 'service_role must have BYPASSRLS';
              end if;
            end;
            $$;
            grant usage, create on schema public to postgres;
            grant usage on schema public to anon, authenticated, service_role;
            create schema if not exists auth;
            create or replace function auth.uid()
            returns uuid
            language sql
            stable
            as $$
              select nullif(
                current_setting('request.jwt.claim.sub', true),
                ''
              )::uuid
            $$;
            grant usage on schema auth to anon, authenticated, service_role;
            grant execute on function auth.uid()
              to anon, authenticated, service_role;
            """,
        )
        for migration in MIGRATIONS:
            _psql(container, migration.read_text(encoding="utf-8"))
        task_11_sql = TASK_11_MIGRATION.read_text(encoding="utf-8")
        _psql(container, task_11_sql)
        _psql(container, task_11_sql)
        task_11_publication_sql = TASK_11_PUBLICATION_MIGRATION.read_text(encoding="utf-8")
        _psql(container, task_11_publication_sql)
        _psql(container, task_11_publication_sql)

        first_context = json.loads(
            _psql(
                container,
                (
                    "set role authenticated;\n"
                    f"set request.jwt.claim.sub = '{AUTH_USER_ID}';\n"
                    "select row_to_json(context)::text "
                    "from public.bootstrap_mercury_context() as context;\n"
                ),
            )
        )
        tenant_id = UUID(first_context["memberships"][0]["tenant_id"])
        workspace_id = UUID(first_context["active_workspace_id"])
        sibling_workspace_id = uuid4()
        other_tenant_id = uuid4()
        other_workspace_id = uuid4()
        _psql(
            container,
            _service(
                f"""
                insert into public.mercury_workspaces (
                  id, workspace_key, name, plan, status, tenant_id
                ) values (
                  '{sibling_workspace_id}', 'task-11-sibling', 'Sibling',
                  'v1-personal', 'active', '{tenant_id}'
                );
                insert into public.mercury_workspace_members (
                  workspace_id, auth_user_id, tenant_id, role, host_app, status
                ) values (
                  '{sibling_workspace_id}', '{AUTH_USER_ID}', '{tenant_id}',
                  'member', 'mercury-v1', 'active'
                );
                insert into public.mercury_tenants (
                  id, tenant_type, display_name, personal_owner_auth_user_id
                ) values (
                  '{other_tenant_id}', 'personal', 'Other',
                  '{OTHER_AUTH_USER_ID}'
                );
                insert into public.mercury_workspaces (
                  id, workspace_key, name, plan, status, tenant_id,
                  owner_auth_user_id, is_automatic_default
                ) values (
                  '{other_workspace_id}', 'task-11-other', 'Other',
                  'v1-personal', 'active', '{other_tenant_id}',
                  '{OTHER_AUTH_USER_ID}', true
                );
                insert into public.mercury_workspace_members (
                  workspace_id, auth_user_id, tenant_id, role, host_app, status
                ) values (
                  '{other_workspace_id}', '{OTHER_AUTH_USER_ID}',
                  '{other_tenant_id}', 'owner', 'mercury-v1', 'active'
                );
                """
            ),
        )
        context = PostgresContext(
            container=container,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            sibling_workspace_id=sibling_workspace_id,
            other_tenant_id=other_tenant_id,
            other_workspace_id=other_workspace_id,
        )
        _seed_knowledge(context)
        _seed_skills(context)
        yield context
    finally:
        _docker("rm", "-f", container)


def _seed_knowledge(context: PostgresContext) -> None:
    rows = (
        ("global-reviewed", "global", None, None, "published", "reviewed"),
        (
            "workspace-published",
            "workspace",
            context.tenant_id,
            context.workspace_id,
            "published",
            "reviewed",
        ),
        (
            "sibling-published",
            "workspace",
            context.tenant_id,
            context.sibling_workspace_id,
            "published",
            "reviewed",
        ),
        (
            "other-tenant-published",
            "workspace",
            context.other_tenant_id,
            context.other_workspace_id,
            "published",
            "reviewed",
        ),
        (
            "workspace-draft",
            "workspace",
            context.tenant_id,
            context.workspace_id,
            "draft",
            "draft",
        ),
        (
            "workspace-rejected",
            "workspace",
            context.tenant_id,
            context.workspace_id,
            "rejected",
            "rejected",
        ),
        (
            "workspace-superseded",
            "workspace",
            context.tenant_id,
            context.workspace_id,
            "superseded",
            "reviewed",
        ),
    )
    statements: list[str] = []
    for name, scope, tenant_id, workspace_id, publication_status, review_status in rows:
        source_id = uuid4()
        document_id = uuid4()
        tenant_sql = "null" if tenant_id is None else f"'{tenant_id}'"
        workspace_sql = "null" if workspace_id is None else f"'{workspace_id}'"
        statements.append(
            f"""
            insert into public.knowledge_sources (
              id, source_uri, title, jurisdiction, provider, connector,
              doc_type, review_status, visibility_scope, tenant_id,
              workspace_id, publication_status
            ) values (
              '{source_id}', 'mercury://task11/{name}', '{name}', 'TH',
              'flowaccount', 'flowaccount', 'tax', '{review_status}',
              '{scope}', {tenant_sql}, {workspace_sql}, '{publication_status}'
            );
            insert into public.knowledge_documents (
              id, source_id, document_uri, title, body, sha256, effective_date
            ) values (
              '{document_id}', '{source_id}', 'mercury://task11/{name}/document',
              '{name}', 'invoice evidence for {name}', repeat('a', 64),
              '2026-07-01'
            );
            insert into public.knowledge_chunks (
              document_id, chunk_uri, chunk_index, chunk_text, citation
            ) values (
              '{document_id}', 'mercury://task11/{name}/document#chunk-0',
              0, 'invoice evidence for {name}',
              jsonb_build_object('heading', '{name}')
            );
            """
        )
    _psql(context.container, _service("\n".join(statements)))


def _seed_skills(context: PostgresContext) -> None:
    rows = (
        ("global-published", "global", None, None, "published"),
        (
            "workspace-published",
            "workspace",
            context.tenant_id,
            context.workspace_id,
            "published",
        ),
        (
            "workspace-superseded",
            "workspace",
            context.tenant_id,
            context.workspace_id,
            "superseded",
        ),
        (
            "sibling-published",
            "workspace",
            context.tenant_id,
            context.sibling_workspace_id,
            "published",
        ),
    )
    statements: list[str] = []
    for skill_id, scope, tenant_id, workspace_id, status in rows:
        tenant_sql = "null" if tenant_id is None else f"'{tenant_id}'"
        workspace_sql = "null" if workspace_id is None else f"'{workspace_id}'"
        superseded_at = "statement_timestamp()" if status == "superseded" else "null"
        projection = json.dumps(
            {
                "skill_id": skill_id,
                "skill_version": "1.0.0",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "git_source_path": (f"plugins/mercury-finance/skills/{skill_id}/SKILL.md"),
            },
            sort_keys=True,
        )
        statements.append(
            f"""
            insert into public.mercury_published_skills (
              visibility_scope, tenant_id, workspace_id, skill_id,
              skill_version, publication_status, projection,
              projection_sha256, git_source_path, superseded_at
            ) values (
              '{scope}', {tenant_sql}, {workspace_sql}, '{skill_id}',
              '1.0.0', '{status}', $projection${projection}$projection$::jsonb,
              encode(digest(
                public.mercury_canonical_jsonb(
                  $projection${projection}$projection$::jsonb
                ),
                'sha256'
              ), 'hex'),
              'plugins/mercury-finance/skills/{skill_id}/SKILL.md',
              {superseded_at}
            );
            """
        )
    _psql(context.container, "\n".join(statements))


def test_postgresql_17_migration_twice_and_fts_index(
    postgres_context: PostgresContext,
) -> None:
    payload = json.loads(
        _psql(
            postgres_context.container,
            """
            select json_build_object(
              'version', current_setting('server_version_num')::integer,
              'index_method', index_definition.indexdef,
              'generated', column_definition.is_generated
            )::text
            from pg_indexes as index_definition
            join information_schema.columns as column_definition
              on column_definition.table_schema = 'public'
             and column_definition.table_name = 'knowledge_chunks'
             and column_definition.column_name = 'search_tsv'
            where index_definition.schemaname = 'public'
              and index_definition.indexname = 'knowledge_chunks_search_tsv_idx';
            """,
        )
    )

    assert 170000 <= payload["version"] < 180000
    assert "USING gin (search_tsv)" in payload["index_method"]
    assert payload["generated"] == "ALWAYS"


def test_authenticated_rls_is_bound_to_exact_requested_workspace(
    postgres_context: PostgresContext,
) -> None:
    direct_rows = json.loads(
        _psql(
            postgres_context.container,
            _authenticated(
                postgres_context,
                """
                select coalesce(
                  jsonb_agg(source_uri order by source_uri),
                  '[]'::jsonb
                )::text
                from public.knowledge_sources;
                """,
            ),
        )
    )

    assert direct_rows == [
        "mercury://task11/global-reviewed",
        "mercury://task11/workspace-published",
    ]
    assert all(
        excluded not in direct_rows
        for excluded in (
            "mercury://task11/sibling-published",
            "mercury://task11/other-tenant-published",
            "mercury://task11/workspace-draft",
            "mercury://task11/workspace-rejected",
            "mercury://task11/workspace-superseded",
        )
    )
    identity_probe = _psql_result(
        postgres_context.container,
        _authenticated(
            postgres_context,
            f"""
            select public.mercury_v1_workspace_member(
              '{postgres_context.other_tenant_id}',
              '{postgres_context.other_workspace_id}',
              '{OTHER_AUTH_USER_ID}'
            );
            """,
        ),
    )
    assert identity_probe.returncode != 0
    assert "permission denied for function mercury_v1_workspace_member" in (identity_probe.stderr)


def test_service_role_application_predicate_matches_rls_and_rejects_identity_switches(
    postgres_context: PostgresContext,
) -> None:
    visible = json.loads(
        _psql(
            postgres_context.container,
            _service(_search_sql(postgres_context)),
        )
    )
    sibling = json.loads(
        _psql(
            postgres_context.container,
            _service(
                _search_sql(
                    postgres_context,
                    workspace_id=postgres_context.sibling_workspace_id,
                )
            ),
        )
    )
    wrong_user = json.loads(
        _psql(
            postgres_context.container,
            _service(
                _search_sql(
                    postgres_context,
                    auth_user_id=OTHER_AUTH_USER_ID,
                )
            ),
        )
    )
    cross_tenant = json.loads(
        _psql(
            postgres_context.container,
            _service(
                _search_sql(
                    postgres_context,
                    tenant_id=postgres_context.other_tenant_id,
                    workspace_id=postgres_context.other_workspace_id,
                )
            ),
        )
    )

    assert visible == [
        "mercury://task11/global-reviewed",
        "mercury://task11/workspace-published",
    ]
    assert sibling == [
        "mercury://task11/global-reviewed",
        "mercury://task11/sibling-published",
    ]
    assert wrong_user == []
    assert cross_tenant == []


def test_only_exact_published_skill_versions_are_executable(
    postgres_context: PostgresContext,
) -> None:
    def resolve(skill_id: str) -> list[dict[str, object]]:
        payload = _psql(
            postgres_context.container,
            _service(
                f"""
                select coalesce(jsonb_agg(skill), '[]'::jsonb)::text
                from public.resolve_mercury_v1_published_skill(
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  '{skill_id}',
                  '1.0.0'
                ) as skill;
                """
            ),
        )
        return json.loads(payload)

    assert len(resolve("global-published")) == 1
    assert len(resolve("workspace-published")) == 1
    assert resolve("workspace-superseded") == []
    assert resolve("sibling-published") == []
    assert resolve("workspace-published-v2") == []


def test_git_canonical_skill_projection_matches_postgres_hash_authority(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.skills.catalog import ACCOUNTING_SKILL_CATALOG

    for skill in ACCOUNTING_SKILL_CATALOG:
        rows = json.loads(
            _psql(
                postgres_context.container,
                _service(
                    f"""
                    select coalesce(jsonb_agg(skill), '[]'::jsonb)::text
                    from public.resolve_mercury_v1_published_skill(
                      '{postgres_context.tenant_id}',
                      '{postgres_context.workspace_id}',
                      '{AUTH_USER_ID}',
                      '{skill.skill_id}',
                      '{skill.skill_version}'
                    ) as skill;
                    """
                ),
            )
        )

        assert rows == [
            {
                "skill_id": skill.skill_id,
                "skill_version": skill.skill_version,
                "projection": skill.published_projection(),
                "projection_sha256": skill.projection_sha256,
                "git_source_path": skill.git_source_path,
                "publication_status": "published",
            }
        ]


def test_release_publication_path_is_idempotent(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.skills.catalog import ACCOUNTING_SKILL_CATALOG

    skill_ids = ", ".join(f"'{skill.skill_id}'" for skill in ACCOUNTING_SKILL_CATALOG)
    query = _service(
        f"""
        select coalesce(
          jsonb_agg(to_jsonb(skill) order by skill.skill_id),
          '[]'::jsonb
        )::text
        from public.mercury_published_skills as skill
        where skill.visibility_scope = 'global'
          and skill.tenant_id is null
          and skill.workspace_id is null
          and skill.skill_id in ({skill_ids});
        """
    )
    before = json.loads(_psql(postgres_context.container, query))

    _psql(
        postgres_context.container,
        TASK_11_PUBLICATION_MIGRATION.read_text(encoding="utf-8"),
    )
    after = json.loads(_psql(postgres_context.container, query))

    assert after == before
    assert len(after) == len(ACCOUNTING_SKILL_CATALOG)


def test_release_publication_path_fails_closed_on_projection_mismatch(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.skills.catalog import ACCOUNTING_SKILL_CATALOG

    skill = ACCOUNTING_SKILL_CATALOG[0]
    projection = skill.published_projection()
    projection["summary"] = "deliberately mismatched release projection"
    serialized = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    identity_predicate = (
        "visibility_scope = 'global' "
        "and tenant_id is null "
        "and workspace_id is null "
        f"and skill_id = '{skill.skill_id}' "
        f"and skill_version = '{skill.skill_version}'"
    )

    try:
        _psql(
            postgres_context.container,
            f"""
            delete from public.mercury_published_skills
            where {identity_predicate};
            insert into public.mercury_published_skills (
              visibility_scope, tenant_id, workspace_id, skill_id,
              skill_version, publication_status, projection,
              projection_sha256, git_source_path
            ) values (
              'global', null, null, '{skill.skill_id}',
              '{skill.skill_version}', 'published',
              $projection${serialized}$projection$::jsonb,
              encode(digest(
                public.mercury_canonical_jsonb(
                  $projection${serialized}$projection$::jsonb
                ),
                'sha256'
              ), 'hex'),
              '{skill.git_source_path}'
            );
            """,
        )

        result = _psql_result(
            postgres_context.container,
            TASK_11_PUBLICATION_MIGRATION.read_text(encoding="utf-8"),
        )

        assert result.returncode != 0
        assert "mercury_first_party_skill_publication_mismatch" in result.stderr
        stored_summary = _psql(
            postgres_context.container,
            f"""
            select projection ->> 'summary'
            from public.mercury_published_skills
            where {identity_predicate};
            """,
        )
        assert stored_summary == "deliberately mismatched release projection"
    finally:
        _psql(
            postgres_context.container,
            f"""
            delete from public.mercury_published_skills
            where {identity_predicate};
            """,
        )
        _psql(
            postgres_context.container,
            TASK_11_PUBLICATION_MIGRATION.read_text(encoding="utf-8"),
        )


def test_runtime_roles_cannot_mutate_published_skills(
    postgres_context: PostgresContext,
) -> None:
    statements = (
        "insert into public.mercury_published_skills default values;",
        """
        update public.mercury_published_skills
        set publication_status = publication_status
        where false;
        """,
        "delete from public.mercury_published_skills where false;",
    )

    for role in ("anon", "authenticated", "service_role"):
        for statement in statements:
            result = _psql_result(
                postgres_context.container,
                f"set role {role};\n{statement}",
            )
            assert result.returncode != 0
            assert "permission denied for table mercury_published_skills" in result.stderr
