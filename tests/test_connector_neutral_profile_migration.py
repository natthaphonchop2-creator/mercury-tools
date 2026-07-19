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
