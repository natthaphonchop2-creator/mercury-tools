from __future__ import annotations

from pathlib import Path

MIGRATION = Path(
    "supabase/migrations/20260715100000_validation_jsonpath_pg17_compat.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_pg17_hotfix_replaces_all_keyvalue_jsonpaths_with_object_filters() -> None:
    sql = _sql()

    assert sql.count(
        "'lax $.** ? (@.type() == \"object\").keyvalue()'"
    ) == 3
    assert "'lax $.**.keyvalue()'" not in sql
    assert "create or replace function public.jsonb_has_forbidden_validation_key" in sql
    assert "create or replace function public.jsonb_has_forbidden_validation_value" in sql
    assert "create or replace function public.jsonb_is_safe_validation_response_shape" in sql


def test_pg17_hotfix_preserves_safe_numeric_qualified_label_assignments() -> None:
    sql = _sql()

    assert "create or replace function public.validation_label_kind" in sql
    assert "source_provider_reference" in sql
    assert (
        "create or replace function "
        "public.validation_text_has_safe_label_assignment" in sql
    )
    assert "and not public.validation_text_has_safe_label_assignment(value)" in sql
    assert (
        "not public.validation_label_assignment_has_forbidden_value(" in sql
    )


def test_pg17_hotfix_safe_assignment_exemption_rejects_mixed_identifiers() -> None:
    sql = _sql()

    assert "has_mixed_identifier_contamination" in sql
    assert "is_numeric_qualified_label" in sql
    assert "candidate) ~ '[0-9]'" in sql
    assert (
        "create or replace function "
        "public.validation_text_has_label_assignment_contamination" in sql
    )
    assert "or public.validation_text_has_label_assignment_contamination(value)" in sql
    assert (
        "and not labelled_token_assignments.has_mixed_identifier_contamination"
        in sql
    )


def test_numeric_qualified_assignment_allowlist_is_exact() -> None:
    compact = " ".join(_sql().lower().split())

    assert compact.count(
        "^(client[0-9]+[:._=-]?id|api[0-9]+[:._=-]?key)$"
    ) == 2
    assert "^(client|api)[0-9]+([:._=-]?[a-z][a-z0-9]*)+$" not in compact


def test_pg17_hotfix_keeps_helpers_service_role_only() -> None:
    sql = _sql()

    for signature in (
        "public.validation_label_kind(text)",
        "public.validation_text_has_safe_label_assignment(text)",
        "public.validation_text_has_label_assignment_contamination(text)",
        "public.validation_text_has_forbidden_value(text)",
        "public.jsonb_has_forbidden_validation_key(jsonb)",
        "public.jsonb_has_forbidden_validation_value(jsonb)",
        "public.jsonb_is_safe_validation_response_shape(jsonb)",
        "public.reject_validation_evidence_mutation()",
    ):
        assert f"revoke all on function {signature}" in sql
        assert f"grant execute on function {signature}" in sql


def test_append_only_trigger_returns_a_client_error_instead_of_http_500() -> None:
    sql = _sql()

    assert "create or replace function public.reject_validation_evidence_mutation" in sql
    assert "errcode = 'P0001'" in sql
    assert "errcode = '55000'" not in sql
