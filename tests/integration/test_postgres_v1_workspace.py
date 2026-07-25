from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from mercury_tools.workspaces.models import MercuryContext

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PRODUCT_MIGRATION = ROOT / "supabase/migrations/0002_mercury_product_layer.sql"
IDENTITY_MIGRATION = (
    ROOT / "supabase/migrations/20260726100000_mercury_v1_identity.sql"
)
AUTH_USER_ID = UUID("11111111-1111-4111-8111-111111111111")
_OPT_IN = "MERCURY_V1_POSTGRES_TEST"


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _docker(
    *args: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
    )


def _psql(container: str, sql: str) -> str:
    try:
        result = _docker(
            "exec",
            "-i",
            container,
            "psql",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "mercury_v1_test",
            input_text=sql,
        )
    except subprocess.CalledProcessError as exc:
        raise AssertionError(f"psql failed: {exc.stderr.strip()}") from None
    return result.stdout.strip()


def _wait_for_postgres(container: str) -> None:
    for _ in range(120):
        ready = _docker(
            "exec",
            container,
            "psql",
            "-qAt",
            "-U",
            "postgres",
            "-d",
            "mercury_v1_test",
            "-c",
            "select 1",
            check=False,
        )
        if ready.returncode == 0 and ready.stdout.strip() == "1":
            return
        time.sleep(0.25)
    pytest.fail("disposable PostgreSQL did not become ready")


def _bootstrap(container: str) -> dict[str, object]:
    payload = _psql(
        container,
        (
            "set role authenticated;\n"
            f"set request.jwt.claim.sub = '{AUTH_USER_ID}';\n"
            "select pg_catalog.row_to_json(context)::pg_catalog.text\n"
            "from public.bootstrap_mercury_context() as context;\n"
        ),
    )
    value = json.loads(payload)
    assert isinstance(value, dict)
    return value


def test_inactive_default_workspace_bootstrap_restores_valid_context() -> None:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"set {_OPT_IN}=1 to run disposable PostgreSQL regression")
    if not _docker_available():
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")

    container = f"mercury-v1-postgres-{uuid4().hex[:12]}"
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=mercury_v1_test",
        "postgres:17-alpine",
    )
    try:
        _wait_for_postgres(container)
        _psql(
            container,
            """
            create role anon nologin;
            create role authenticated nologin;
            create role service_role nologin bypassrls;
            create schema auth;
            create function auth.uid()
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
        _psql(container, PRODUCT_MIGRATION.read_text(encoding="utf-8"))
        _psql(container, IDENTITY_MIGRATION.read_text(encoding="utf-8"))
        _psql(container, IDENTITY_MIGRATION.read_text(encoding="utf-8"))

        first = MercuryContext.model_validate(_bootstrap(container))
        workspace_id = first.active_workspace_id
        _psql(
            container,
            f"""
            update public.mercury_workspaces
            set workspace_key = 'broken-default',
                name = 'Broken Workspace',
                plan = 'broken-plan',
                status = 'disabled',
                tenant_id = null
            where id = '{workspace_id}';
            update public.mercury_workspace_members
            set role = 'viewer',
                host_app = 'broken-host',
                status = 'inactive',
                tenant_id = null
            where workspace_id = '{workspace_id}'
              and auth_user_id = '{AUTH_USER_ID}';
            """,
        )

        second_payload = _bootstrap(container)
        second = MercuryContext.model_validate(second_payload)
        state = json.loads(
            _psql(
                container,
                f"""
                select pg_catalog.json_build_object(
                  'workspace_key', workspace.workspace_key,
                  'workspace_name', workspace.name,
                  'workspace_plan', workspace.plan,
                  'workspace_status', workspace.status,
                  'workspace_tenant_id', workspace.tenant_id,
                  'workspace_owner_id', workspace.owner_auth_user_id,
                  'is_automatic_default', workspace.is_automatic_default,
                  'member_role', member.role,
                  'member_host_app', member.host_app,
                  'member_status', member.status,
                  'member_tenant_id', member.tenant_id
                )::pg_catalog.text
                from public.mercury_workspaces as workspace
                join public.mercury_workspace_members as member
                  on member.workspace_id = workspace.id
                where workspace.id = '{workspace_id}'
                  and member.auth_user_id = '{AUTH_USER_ID}';
                """,
            )
        )

        assert second.active_workspace_id == workspace_id
        assert len(second.memberships) == 1
        assert second.memberships[0].workspace_id == workspace_id
        assert state == {
            "workspace_key": f"mercury-v1-personal-{AUTH_USER_ID}",
            "workspace_name": "Mercury Workspace",
            "workspace_plan": "v1-personal",
            "workspace_status": "active",
            "workspace_tenant_id": str(second.memberships[0].tenant_id),
            "workspace_owner_id": str(AUTH_USER_ID),
            "is_automatic_default": True,
            "member_role": "owner",
            "member_host_app": "mercury-v1",
            "member_status": "active",
            "member_tenant_id": str(second.memberships[0].tenant_id),
        }
    finally:
        _docker("rm", "-f", container, check=False)
