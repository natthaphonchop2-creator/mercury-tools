from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260719120000_connector_neutral_profiles.sql"


def test_connector_neutral_profile_migration_contract() -> None:
    sql = MIGRATION.read_text()
    normalized = sql.lower()

    for fragment in (
        "connection_mode text",
        "company_ref text",
        "external_server_name text",
        "capability_states jsonb",
        "evidence_source text",
        "validated_at timestamptz",
        "required_capabilities jsonb",
        "revoke all",
        "grant all",
    ):
        assert fragment in normalized

    assert "native_mcp" in normalized
    assert "api_driver" in normalized
    assert "local_bridge" in normalized
    assert "jsonb_typeof(capability_states) = 'object'" in normalized
    assert "jsonb_typeof(required_capabilities) = 'array'" in normalized
    assert "mercury_connector_profiles_workspace_id_connector_id_environment_key" in normalized
    assert "unique (workspace_id, connector_id, connection_mode, environment)" in normalized
    assert (
        "revoke all on table public.mercury_connector_profiles from anon, authenticated"
        in normalized
    )
    assert "revoke all on table public.mercury_skill_catalog from anon, authenticated" in normalized


def test_connector_neutral_profile_migration_is_rerun_safe_and_scrubs_legacy_rows() -> None:
    sql = MIGRATION.read_text()
    normalized = sql.lower()

    assert "information_schema.columns" in normalized
    assert "profile_mode_column_existed" in normalized
    assert "metadata = '{}'::jsonb" in normalized
    assert "where status = 'requires_credentials'" in normalized
    assert "set\n  connection_mode = 'api_driver'" not in normalized


def test_connector_neutral_profile_migration_validates_every_capability_state() -> None:
    normalized = MIGRATION.read_text().lower()

    assert "jsonb_each(capability_states)" in normalized
    assert "jsonb_typeof(capability_state) <> 'string'" in normalized
    assert "provider_access_token" in normalized
    for forbidden_name in (
        "credential",
        "bearer",
        "api[_-]?key",
        "password",
        "tax[_-]?id",
        "email",
        "response[_-]?body",
        "payload",
    ):
        assert forbidden_name in normalized
    for state in (
        "observed",
        "provider_unavailable",
        "not_authorized",
        "validation_failed",
        "environment_mismatch",
    ):
        assert state in normalized


def test_capability_state_validator_has_explicit_function_security() -> None:
    normalized = MIGRATION.read_text().lower()

    assert "set search_path = pg_catalog" in normalized
    assert (
        "revoke execute on function public.mercury_capability_states_are_safe(jsonb) from public"
        in normalized
    )
    assert (
        "revoke execute on function public.mercury_capability_states_are_safe(jsonb) "
        "from anon, authenticated"
        in normalized
    )
    assert (
        "grant execute on function public.mercury_capability_states_are_safe(jsonb) to service_role"
        in normalized
    )
