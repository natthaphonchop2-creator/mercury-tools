from pathlib import Path

MIGRATION = Path("supabase/migrations/20260711090000_erp_action_catalog.sql")
VALIDATION_MIGRATION = Path(
    "supabase/migrations/20260713100000_erp_action_validation_knowledge.sql"
)


def test_catalog_migration_has_normalized_immutable_service_role_only_contract() -> None:
    sql = MIGRATION.read_text().lower()

    for table in (
        "erp_spec_sources",
        "erp_action_catalog",
        "erp_action_versions",
        "erp_action_observations",
    ):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"revoke all on table public.{table} from anon, authenticated" in sql
        assert f"grant all on table public.{table} to service_role" in sql

    assert "unique (action_id, version_id)" in sql
    assert "unique (source_id, connector_id)" in sql
    assert (
        "foreign key (source_id, connector_id)\n"
        "    references public.erp_spec_sources(source_id, connector_id)\n"
        "    on delete restrict"
    ) in sql
    assert "references public.erp_action_versions(action_id, version_id)" in sql
    assert "erp_action_versions_are_immutable" in sql
    assert "before update or delete on public.erp_action_versions" in sql
    assert "raise exception 'erp_action_versions_are_immutable'" in sql
    assert "revoke all on function public.reject_erp_action_version_mutation()" in sql


def test_catalog_migration_has_required_indexes_and_checks() -> None:
    sql = MIGRATION.read_text().lower()

    assert "erp_action_versions_connector_method_idx" in sql
    assert "(connector_id, method)" in sql
    assert "erp_action_catalog_connector_capability_idx" in sql
    assert "(connector_id, capability)" in sql
    assert "erp_action_observations_action_created_idx" in sql
    assert "(action_id, created_at desc)" in sql
    assert "source_type in ('openapi3', 'swagger2', 'postman2.1', 'documentation')" in sql
    assert "observed_state in ('success', 'failed', 'outcome_unknown')" in sql
    assert "latency_ms is null or latency_ms >= 0" in sql


def test_validation_migration_extends_version_identity_without_rewriting_catalog_history() -> None:
    catalog_sql = MIGRATION.read_text(encoding="utf-8").lower()
    validation_sql = VALIDATION_MIGRATION.read_text(encoding="utf-8").lower()

    assert "unique (action_id, version_id)" in catalog_sql
    assert "alter table public.erp_action_versions" in validation_sql
    assert "erp_action_versions_connector_identity_unique" in validation_sql
    assert "unique (connector_id, action_id, version_id)" in validation_sql
