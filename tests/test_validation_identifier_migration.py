from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import mercury_tools.db.validation as validation_module
from mercury_tools.qualification.models import ValidationStatus
from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

MIGRATION = Path(
    "supabase/migrations/20260716100000_validation_identifier_constraints.sql"
)


def _sql() -> str:
    return " ".join(MIGRATION.read_text(encoding="utf-8").split())


def _runtime_accepts_identifiers(
    opaque_evidence_id: str,
    run_id: str,
    *,
    approved_public: bool,
    action_id: str = "act_" + "1" * 24,
    version_id: str = "av_" + "2" * 64,
    connector_id: str = "flowaccount",
) -> bool:
    status = ValidationStatus.CONTRACT_VALIDATED
    try:
        validation_module._validated_validation(
            {
                "opaque_evidence_id": opaque_evidence_id,
                "run_id": run_id,
                "action_id": action_id,
                "version_id": version_id,
                "connector_id": connector_id,
                "environment": "sandbox",
                "validation_status": status,
                "evidence_level": "contract_validated",
                "execution_eligibility": "discovery_only",
                "approved_public": approved_public,
                "summary_th": SUMMARY_TH[status],
                "summary_en": SUMMARY_EN[status],
                "prerequisites": (),
                "limitations": ("provider_call_not_observed",),
                "recommended_next_step": "complete_sandbox_validation",
                "response_shape": {},
                "status_class": "not_attempted",
                "latency_ms": None,
                "semantic_contract": {
                    "business_object": "invoice",
                    "operation": "list",
                    "accounting_uses": ("revenue_review",),
                },
                "evidence_sha256": "3" * 64,
                "reviewed_by": "release_reviewer",
                "runner_version": "v0.2.1",
                "run_state": "completed",
                "evaluated_at": datetime(2026, 7, 16, tzinfo=UTC),
                "expires_at": None,
            }
        )
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    ("opaque_evidence_id", "run_id", "approved_public", "expected"),
    [
        (
            "ev_01J00000000000000000000000",
            "run_01J00000000000000000000000",
            True,
            True,
        ),
        ("evidence_123456789", "validation_run_123456789", False, True),
        ("public_evidence_alpha", "validation_run_alpha", True, False),
        ("ev unsafe", "validation_run_123456789", False, False),
        ("evidence_123456789", "run/unsafe", False, False),
    ],
)
def test_runtime_identifier_acceptance_matrix(
    opaque_evidence_id: str,
    run_id: str,
    approved_public: bool,
    expected: bool,
) -> None:
    assert (
        _runtime_accepts_identifiers(
            opaque_evidence_id,
            run_id,
            approved_public=approved_public,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_id", "unsafe action"),
        ("version_id", "unsafe/version"),
        ("connector_id", "unsafe connector"),
    ],
)
def test_runtime_rejects_unsafe_binding_identifiers(field: str, value: str) -> None:
    assert not _runtime_accepts_identifiers(
        "evidence_123456789",
        "validation_run_123456789",
        approved_public=False,
        **{field: value},
    )


def test_identifier_migration_is_transaction_wrapped_and_forward_only() -> None:
    raw_sql = MIGRATION.read_text(encoding="utf-8")
    sql = _sql()

    assert raw_sql.lstrip().startswith("begin;")
    assert raw_sql.rstrip().endswith("commit;")
    assert "create table" not in sql.lower()
    assert "drop table" not in sql.lower()
    assert "drop trigger" not in sql.lower()
    assert "reject_validation_evidence_mutation" not in sql


def test_identifier_migration_replaces_all_runtime_identifier_mismatches() -> None:
    sql = _sql()

    for table, constraint in (
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_opaque_evidence_id_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_run_id_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_action_id_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_version_id_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_connector_id_check",
        ),
        (
            "erp_action_observations",
            "erp_action_observations_opaque_event_id_check",
        ),
        ("erp_action_observations", "erp_action_observations_action_id_check"),
        ("erp_action_observations", "erp_action_observations_version_id_check"),
        ("erp_action_observations", "erp_action_observations_connector_id_check"),
    ):
        assert f"alter table public.{table} drop constraint if exists {constraint}" in sql

    assert (
        "add constraint erp_action_validation_knowledge_opaque_evidence_id_safe_check "
        "check ( opaque_evidence_id ~ '^[A-Za-z0-9._:-]{1,200}$' and ( "
        "not approved_public or opaque_evidence_id ~* "
        "'^ev_[0-9A-HJKMNP-TV-Z]{26}$' ) )"
    ) in sql
    assert (
        "add constraint erp_action_validation_knowledge_run_id_safe_check "
        "check ( run_id ~ '^[A-Za-z0-9._:-]{1,200}$' and ( "
        "not approved_public or run_id ~* '^run_[0-9A-HJKMNP-TV-Z]{26}$' ) )"
    ) in sql
    assert (
        "add constraint erp_action_observations_opaque_event_id_safe_check "
        "check (opaque_event_id ~ '^[A-Za-z0-9._:-]{1,200}$')"
    ) in sql
    for table in (
        "erp_action_validation_knowledge",
        "erp_action_observations",
    ):
        for field in ("action_id", "version_id", "connector_id"):
            assert (
                f"add constraint {table}_{field}_safe_check "
                f"check ({field} ~ '^[A-Za-z0-9._:-]{{1,200}}$')"
            ) in sql

    assert "opaque_evidence_id ~ '^ev_[a-z0-9_]{8,128}$'" not in sql
    assert "run_id ~ '^run_[a-z0-9_]{8,128}$'" not in sql
    assert "char_length(opaque_event_id) > 0" not in sql


def test_identifier_constraints_are_idempotently_added_by_catalog_name() -> None:
    sql = _sql()

    for table, constraint in (
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_opaque_evidence_id_safe_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_run_id_safe_check",
        ),
        (
            "erp_action_observations",
            "erp_action_observations_opaque_event_id_safe_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_action_id_safe_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_version_id_safe_check",
        ),
        (
            "erp_action_validation_knowledge",
            "erp_action_validation_knowledge_connector_id_safe_check",
        ),
        ("erp_action_observations", "erp_action_observations_action_id_safe_check"),
        ("erp_action_observations", "erp_action_observations_version_id_safe_check"),
        ("erp_action_observations", "erp_action_observations_connector_id_safe_check"),
    ):
        assert (
            "if not exists ( select 1 from pg_catalog.pg_constraint "
            f"where conrelid = 'public.{table}'::regclass "
            f"and conname = '{constraint}' )"
        ) in sql
