import re
from pathlib import Path

MIGRATION = Path("supabase/migrations/20260713100000_erp_action_validation_knowledge.sql")


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def _compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_validation_migration_defines_strict_version_bound_knowledge() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "create table public.erp_action_validation_knowledge" in sql
    assert "erp_action_versions_connector_identity_unique" in sql
    assert "unique (connector_id, action_id, version_id)" in sql
    assert "unique (connector_id, action_id, version_id, environment, run_id)" in sql
    assert (
        "foreign key (connector_id, action_id, version_id) references "
        "public.erp_action_versions(connector_id, action_id, version_id) on delete restrict"
    ) in compact
    assert "approved_public boolean not null default false" in compact
    assert "opaque_evidence_id ~ '^ev_[a-z0-9_]{8,128}$'" in compact
    assert "run_id ~ '^run_[a-z0-9_]{8,128}$'" in compact
    assert "evidence_sha256 ~ '^[0-9a-f]{64}$'" in compact
    assert "latency_ms is null or latency_ms >= 0" in compact
    assert "expires_at is null or expires_at > evaluated_at" in compact

    for values in (
        "'sandbox', 'test', 'uat', 'production'",
        (
            "'live_success', 'live_failed', 'contract_validated', "
            "'blocked_missing_credentials', 'blocked_missing_prerequisite', "
            "'blocked_external_effect', 'unsupported_by_sandbox', 'outcome_unknown'"
        ),
        "'documented', 'contract_validated', 'sandbox_observed', 'accountant_reviewed'",
        (
            "'discovery_only', 'sandbox_read', 'sandbox_write_with_approval', "
            "'production_pending_validation', 'blocked'"
        ),
        "'completed', 'quarantined', 'failed'",
    ):
        assert values in compact


def test_validation_migration_has_exact_lookup_and_coverage_indexes() -> None:
    compact = _compact(_sql())

    assert (
        "create index if not exists erp_validation_exact_lookup_idx on "
        "public.erp_action_validation_knowledge "
        "(connector_id, action_id, version_id, environment, evaluated_at desc)"
    ) in compact
    assert (
        "create index if not exists erp_validation_coverage_idx on "
        "public.erp_action_validation_knowledge "
        "(connector_id, environment, validation_status, approved_public)"
    ) in compact


def test_validation_and_observations_are_append_only_with_constant_errors() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "set search_path = pg_catalog, pg_temp" in compact
    assert "message = 'erp_validation_evidence_is_append_only'" in compact
    assert "errcode = '55000'" in compact
    assert (
        "create trigger erp_action_validation_knowledge_is_append_only before update or delete "
        "on public.erp_action_validation_knowledge"
    ) in compact
    assert (
        "create trigger erp_action_observations_are_append_only before update or delete "
        "on public.erp_action_observations"
    ) in compact
    assert (
        "drop trigger if exists erp_action_validation_knowledge_is_append_only on "
        "public.erp_action_validation_knowledge"
    ) in compact
    assert (
        "drop trigger if exists erp_action_observations_are_append_only on "
        "public.erp_action_observations"
    ) in compact
    assert "revoke truncate on table public.erp_action_validation_knowledge" in compact
    assert "revoke truncate on table public.erp_action_observations" in compact


def test_validation_migration_is_service_role_only_without_permissive_policies() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "alter table public.erp_action_validation_knowledge enable row level security" in compact
    assert (
        "revoke all on table public.erp_action_validation_knowledge "
        "from public, anon, authenticated"
    ) in compact
    assert "grant all on table public.erp_action_validation_knowledge to service_role" in compact
    assert "create policy" not in sql
    for function in (
        "public.jsonb_has_forbidden_validation_key(jsonb)",
        "public.reject_validation_evidence_mutation()",
    ):
        assert f"revoke all on function {function} from public, anon, authenticated" in compact
        assert f"grant execute on function {function} to service_role" in compact


def test_validation_migration_recursively_rejects_normalized_sensitive_key_fragments() -> None:
    sql = _sql()
    compact = _compact(sql)

    assert "jsonb_path_query" in sql
    assert "$.**.keyvalue()" in sql
    assert "regexp_replace" in sql
    for fragment in (
        "authorization",
        "token",
        "secret",
        "password",
        "api_key",
        "client_id",
        "client_secret",
        "path",
        "uri",
        "raw",
        "payload",
        "response",
    ):
        assert f"'{fragment}'" in sql

    assert "constraint erp_validation_public_json_safe check" in compact
    for column in ("prerequisites", "limitations", "response_shape", "semantic_contract"):
        assert f"not public.jsonb_has_forbidden_validation_key({column})" in compact


def test_validation_migration_preserves_safe_typed_schema_field_names() -> None:
    sql = _sql()
    function_body = sql.split(
        "create or replace function public.jsonb_has_forbidden_validation_key", 1
    )[1].split("$function$;", 1)[0]

    for safe_field_name in (
        "email",
        "document_id",
        "record_id",
        "counterparty_tax_id",
    ):
        assert f"'{safe_field_name}'" not in function_body
