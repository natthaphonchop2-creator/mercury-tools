from pathlib import Path


def test_private_journal_migration_is_service_role_only() -> None:
    sql = Path(
        "supabase/migrations/0005_flowaccount_private_journal_writes.sql"
    ).read_text()

    assert "create table if not exists public.connector_write_requests" in sql
    assert "enable row level security" in sql
    assert (
        "revoke all on table public.connector_write_requests from anon, authenticated"
        in sql
    )
    assert "grant all on table public.connector_write_requests to service_role" in sql
    assert (
        "where status in ('executing', 'draft_created', 'approved', 'outcome_unknown')"
        in sql
    )


def test_private_journal_migration_has_state_and_lookup_guards() -> None:
    sql = Path(
        "supabase/migrations/0005_flowaccount_private_journal_writes.sql"
    ).read_text()

    for status in (
        "previewed",
        "executing",
        "draft_created",
        "approved",
        "failed",
        "outcome_unknown",
        "expired",
        "cancelled",
    ):
        assert f"'{status}'" in sql
    assert "connector_write_requests_dedupe_idx" in sql
    assert "connector_write_requests_profile_idx" in sql
    assert "connector_write_requests_record_idx" in sql
