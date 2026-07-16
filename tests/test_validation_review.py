from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from importlib import util as importlib_util
from pathlib import Path

import pytest

from mercury_tools.catalog.models import CatalogAction
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationReport,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.publisher import (
    CatalogDefinitions,
    review_validation_report,
)
from mercury_tools.qualification.semantics import load_actions
from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib_util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=2)
def _actions(connector_id: str) -> tuple[CatalogAction, ...]:
    return tuple(
        load_actions(ROOT / "catalog" / "global" / connector_id / "actions.json")
    )


def _record(
    action: CatalogAction,
    index: int,
    *,
    run_id: str,
    environment: str,
    status: ValidationStatus = ValidationStatus.CONTRACT_VALIDATED,
) -> ValidationKnowledge:
    return ValidationKnowledge.model_validate(
        {
            "opaque_evidence_id": f"ev_{index:026d}",
            "run_id": run_id,
            "action_id": action.action_id,
            "version_id": action.version_id,
            "connector_id": action.connector_id,
            "environment": environment,
            "validation_status": status,
            "evidence_level": EvidenceLevel.CONTRACT_VALIDATED,
            "execution_eligibility": ExecutionEligibility.DISCOVERY_ONLY,
            "approved_public": False,
            "summary_th": SUMMARY_TH[status],
            "summary_en": SUMMARY_EN[status],
            "prerequisites": (),
            "limitations": ("provider_call_not_observed",),
            "recommended_next_step": "complete_sandbox_validation",
            "response_shape": {},
            "status_class": "not_attempted",
            "latency_ms": None,
            "semantic_contract": SemanticContract(
                business_object="general",
                operation="validate",
                accounting_uses=("revenue_review",),
            ),
            "evidence_sha256": f"{index:064x}",
            "reviewed_by": "release_reviewer",
            "runner_version": "0.2.1",
            "run_state": QualificationRunState.COMPLETED,
            "evaluated_at": datetime(2026, 7, 13, tzinfo=UTC),
            "expires_at": None,
        }
    )


def _report(
    connector_id: str,
    *,
    status: ValidationStatus = ValidationStatus.CONTRACT_VALIDATED,
) -> tuple[QualificationReport, CatalogDefinitions]:
    actions = _actions(connector_id)
    run_id = "run_" + "1" * 26
    environment = "sandbox"
    records = tuple(
        _record(
            action,
            index,
            run_id=run_id,
            environment=environment,
            status=status,
        )
        for index, action in enumerate(actions, start=1)
    )
    return (
        QualificationReport(
            connector_id=connector_id,
            environment=environment,
            run_id=run_id,
            run_state=QualificationRunState.COMPLETED,
            records=records,
        ),
        CatalogDefinitions(actions),
    )


@pytest.mark.parametrize(
    ("connector_id", "reviewer_role", "expected_count"),
    [
        ("flowaccount", "release_reviewer", 190),
        ("peak", "accountant_reviewer", 64),
    ],
)
def test_review_promotes_exact_connector_coverage(
    connector_id: str,
    reviewer_role: str,
    expected_count: int,
) -> None:
    report, catalog = _report(connector_id)

    reviewed = review_validation_report(
        report,
        reviewer_role=reviewer_role,
        catalog=catalog,
    )

    assert len(reviewed.records) == expected_count
    assert all(record.approved_public for record in reviewed.records)
    assert all(record.reviewed_by == reviewer_role for record in reviewed.records)


@pytest.mark.parametrize(
    "status",
    [
        ValidationStatus.LIVE_SUCCESS,
        ValidationStatus.LIVE_FAILED,
        ValidationStatus.CONTRACT_VALIDATED,
        ValidationStatus.BLOCKED_MISSING_CREDENTIALS,
        ValidationStatus.BLOCKED_MISSING_PREREQUISITE,
        ValidationStatus.BLOCKED_EXTERNAL_EFFECT,
        ValidationStatus.UNSUPPORTED_BY_SANDBOX,
    ],
)
def test_review_allows_controlled_completed_outcome_policy(
    status: ValidationStatus,
) -> None:
    report, catalog = _report("peak", status=status)

    reviewed = review_validation_report(
        report,
        reviewer_role="release_reviewer",
        catalog=catalog,
    )

    assert {record.validation_status for record in reviewed.records} == {status}
    assert all(record.approved_public for record in reviewed.records)


def test_review_rejects_completed_outcome_unknown_without_promotion() -> None:
    report, catalog = _report("peak", status=ValidationStatus.OUTCOME_UNKNOWN)

    with pytest.raises(ValueError, match="^validation_outcome_not_publishable$"):
        review_validation_report(
            report,
            reviewer_role="release_reviewer",
            catalog=catalog,
        )


def test_review_rejects_free_form_reviewer_identity() -> None:
    report, catalog = _report("peak")

    with pytest.raises(ValueError, match="^validation_reviewer_role_invalid$"):
        review_validation_report(
            report,
            reviewer_role="reviewer@example.test",
            catalog=catalog,
        )


@pytest.mark.parametrize(
    "run_state",
    [QualificationRunState.QUARANTINED, QualificationRunState.FAILED],
)
def test_review_rejects_noncompleted_report_run(run_state: QualificationRunState) -> None:
    report, catalog = _report("peak")

    with pytest.raises(ValueError, match="^validation_run_not_publishable$"):
        review_validation_report(
            report.model_copy(update={"run_state": run_state}),
            reviewer_role="release_reviewer",
            catalog=catalog,
        )


@pytest.mark.parametrize("unknown_scope", ["report", "record"])
def test_review_rejects_unknown_keys_without_echo(unknown_scope: str) -> None:
    report, catalog = _report("peak")
    payload = report.model_dump(mode="python")
    unsafe_value = "private-value-must-not-echo"
    if unknown_scope == "report":
        payload["raw_response"] = unsafe_value
    else:
        payload["records"][0]["raw_response"] = unsafe_value

    with pytest.raises(ValueError, match="^validation_report_invalid$") as raised:
        review_validation_report(
            payload,
            reviewer_role="release_reviewer",
            catalog=catalog,
        )

    assert unsafe_value not in str(raised.value)


@pytest.mark.parametrize("coverage_case", ["duplicate", "incomplete"])
def test_review_rejects_duplicate_or_incomplete_exact_coverage(
    coverage_case: str,
) -> None:
    report, catalog = _report("peak")
    records = report.records[:-1]
    if coverage_case == "duplicate":
        records = (*records, report.records[0])

    with pytest.raises(ValueError, match="^validation_coverage_incomplete$"):
        review_validation_report(
            report.model_copy(update={"records": records}),
            reviewer_role="release_reviewer",
            catalog=catalog,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("connector_id", "flowaccount"),
        ("environment", "uat"),
        ("run_id", "run_" + "9" * 26),
    ],
)
def test_review_rejects_mixed_record_scope(field: str, value: str) -> None:
    report, catalog = _report("peak")
    changed = report.records[0].model_copy(update={field: value})

    with pytest.raises(ValueError, match="^validation_report_scope_mismatch$"):
        review_validation_report(
            report.model_copy(update={"records": (changed, *report.records[1:])}),
            reviewer_role="release_reviewer",
            catalog=catalog,
        )


@pytest.mark.parametrize(
    "run_state",
    [QualificationRunState.QUARANTINED, QualificationRunState.FAILED],
)
def test_review_rejects_noncompleted_record_run_state(
    run_state: QualificationRunState,
) -> None:
    report, catalog = _report("peak")
    changed = report.records[0].model_copy(update={"run_state": run_state})

    with pytest.raises(ValueError, match="^validation_run_not_publishable$"):
        review_validation_report(
            report.model_copy(update={"records": (changed, *report.records[1:])}),
            reviewer_role="release_reviewer",
            catalog=catalog,
        )


def test_review_rejects_unsafe_public_promotion_without_echo() -> None:
    report, catalog = _report("peak")
    unsafe_summary = "arbitrary unpublished summary"
    changed = report.records[0].model_copy(update={"summary_en": unsafe_summary})

    with pytest.raises(ValueError, match="^validation_publication_unsafe$") as raised:
        review_validation_report(
            report.model_copy(update={"records": (changed, *report.records[1:])}),
            reviewer_role="release_reviewer",
            catalog=catalog,
        )

    assert unsafe_summary not in str(raised.value)


def test_review_revalidates_mutated_record_instances() -> None:
    report, catalog = _report("peak")
    object.__setattr__(report.records[0], "latency_ms", -1)

    with pytest.raises(ValueError, match="^validation_report_invalid$"):
        review_validation_report(
            report,
            reviewer_role="release_reviewer",
            catalog=catalog,
        )


def test_review_requires_catalog_to_have_exact_connector_coverage() -> None:
    report, _ = _report("peak")
    incomplete_catalog = CatalogDefinitions(_actions("peak")[:-1])

    with pytest.raises(ValueError, match="^validation_coverage_incomplete$"):
        review_validation_report(
            report,
            reviewer_role="release_reviewer",
            catalog=incomplete_catalog,
        )


def test_review_script_combines_inputs_and_writes_deterministic_json(tmp_path: Path) -> None:
    script = _load_script("review_validation_knowledge")
    inputs = []
    for connector_id in ("flowaccount", "peak"):
        report, _ = _report(connector_id)
        path = tmp_path / f"{connector_id}.json"
        path.write_text(report.model_dump_json(), encoding="utf-8")
        inputs.append(path)

    reviewed = script.review_inputs(
        inputs,
        reviewer_role="release_reviewer",
        catalog_root=ROOT / "catalog" / "global",
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    script.write_reviewed_report(reviewed, first)
    script.write_reviewed_report(reviewed, second)

    assert len(reviewed.records) == 254
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    assert "raw_response" not in first.read_text(encoding="utf-8")


def test_review_script_accepts_exact_cli_public_report_envelope(tmp_path: Path) -> None:
    script = _load_script("review_validation_knowledge")
    inputs = []
    for connector_id in ("flowaccount", "peak"):
        report, _ = _report(connector_id)
        counts = Counter(record.validation_status.value for record in report.records)
        payload = {
            **report.model_dump(mode="json"),
            "counts": {key: counts[key] for key in sorted(counts)},
            "http_attempts": 0,
            "mutation_attempts": 0,
            "total": report.total,
        }
        path = tmp_path / f"{connector_id}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        inputs.append(path)

    reviewed = script.review_inputs(
        inputs,
        reviewer_role="release_reviewer",
        catalog_root=ROOT / "catalog" / "global",
    )

    assert len(reviewed.records) == 254


@pytest.mark.parametrize("tampered_field", ["counts", "total", "unknown"])
def test_review_script_rejects_tampered_public_report_envelope(
    tmp_path: Path,
    tampered_field: str,
) -> None:
    script = _load_script("review_validation_knowledge")
    report, _ = _report("peak")
    counts = Counter(record.validation_status.value for record in report.records)
    payload = {
        **report.model_dump(mode="json"),
        "counts": {key: counts[key] for key in sorted(counts)},
        "http_attempts": 0,
        "mutation_attempts": 0,
        "total": report.total,
    }
    if tampered_field == "counts":
        payload["counts"] = {}
    elif tampered_field == "total":
        payload["total"] = report.total - 1
    else:
        payload["raw_response"] = "must-not-be-accepted"
    path = tmp_path / "peak.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="^validation_report_invalid$"):
        script.review_inputs(
            [path],
            reviewer_role="release_reviewer",
            catalog_root=ROOT / "catalog" / "global",
        )


def test_review_script_rejects_duplicate_connector_inputs(tmp_path: Path) -> None:
    script = _load_script("review_validation_knowledge")
    report, _ = _report("peak")
    path = tmp_path / "peak.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="^validation_review_inputs_duplicate$"):
        script.review_inputs(
            [path, path],
            reviewer_role="release_reviewer",
            catalog_root=ROOT / "catalog" / "global",
        )
