from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from mercury_tools.catalog.models import CatalogSource
from mercury_tools.drivers.peak import PeakDriver
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationRunState,
    ValidationStatus,
)
from mercury_tools.qualification.semantics import load_actions, load_semantic_contracts

ROOT = Path(__file__).resolve().parents[1]
PEAK_ROOT = ROOT / "catalog" / "global" / "peak"


def load_source(path: Path) -> CatalogSource:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return CatalogSource.model_validate(payload)


@pytest.fixture(scope="module")
def peak_source() -> CatalogSource:
    return load_source(PEAK_ROOT / "source.json")


@pytest.fixture(scope="module")
def peak_actions():
    return tuple(load_actions(PEAK_ROOT / "actions.json"))


@pytest.fixture(scope="module")
def peak_semantics(peak_actions):
    return load_semantic_contracts(PEAK_ROOT / "semantic-contracts.json", peak_actions)


def _controlled_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "controlled-fixture.pdf"
    fixture.write_bytes(b"contract-only fixture\n")
    return fixture


def test_peak_contract_validation_covers_all_64_without_live_claims(
    tmp_path: Path,
    peak_source: CatalogSource,
    peak_actions,
    peak_semantics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.qualification.peak import validate_peak_documented_contracts

    def forbidden_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("peak_socket_opened")

    def forbidden_auth(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("peak_credentials_read")

    monkeypatch.setattr(socket, "create_connection", forbidden_socket)
    monkeypatch.setattr(PeakDriver, "prepare_auth", forbidden_auth)
    monkeypatch.setattr(PeakDriver, "validate_credentials", forbidden_auth)

    report = validate_peak_documented_contracts(
        source=peak_source,
        actions=peak_actions,
        semantics=peak_semantics,
        file_fixture=_controlled_fixture(tmp_path),
    )

    identities = {(row.action_id, row.version_id) for row in report.records}
    assert report.run_state is QualificationRunState.COMPLETED
    assert report.total == 64
    assert len(identities) == 64
    assert {row.validation_status for row in report.records} == {
        ValidationStatus.BLOCKED_MISSING_CREDENTIALS
    }
    assert {row.evidence_level for row in report.records} == {
        EvidenceLevel.CONTRACT_VALIDATED
    }
    assert {row.execution_eligibility for row in report.records} == {
        ExecutionEligibility.BLOCKED
    }
    assert {row.status_class for row in report.records} == {"not_attempted"}
    assert all(row.latency_ms is None for row in report.records)
    assert report.http_attempts == 0
    assert report.mutation_attempts == 0
    assert report.public_dict()["counts"] == {"blocked_missing_credentials": 64}


def test_peak_contract_validation_rejects_incomplete_frozen_catalog(
    tmp_path: Path,
    peak_source: CatalogSource,
    peak_actions,
    peak_semantics,
) -> None:
    from mercury_tools.qualification.peak import validate_peak_documented_contracts

    with pytest.raises(ValueError, match="peak_catalog_coverage_invalid"):
        validate_peak_documented_contracts(
            source=peak_source,
            actions=peak_actions[1:],
            semantics=peak_semantics,
            file_fixture=_controlled_fixture(tmp_path),
        )


def test_peak_contract_validation_fails_closed_when_documented_endpoint_is_missing(
    tmp_path: Path,
    peak_source: CatalogSource,
    peak_actions,
    peak_semantics,
) -> None:
    from mercury_tools.qualification.peak import validate_peak_documented_contracts

    document = json.loads(json.dumps(peak_source.sanitization["document"]))
    document["endpoints"] = document["endpoints"][1:]
    source = CatalogSource.from_document(
        peak_source.source_uri,
        "peak",
        document,
        dict(peak_source.sanitization["report"]),
        source_type=peak_source.source_type,
    )

    with pytest.raises(ValueError, match="peak_documented_endpoint_missing"):
        validate_peak_documented_contracts(
            source=source,
            actions=peak_actions,
            semantics=peak_semantics,
            file_fixture=_controlled_fixture(tmp_path),
        )


def test_peak_contract_validation_rejects_invalid_documented_required_schema(
    tmp_path: Path,
    peak_source: CatalogSource,
    peak_actions,
    peak_semantics,
) -> None:
    from mercury_tools.qualification.peak import validate_peak_documented_contracts

    document = json.loads(json.dumps(peak_source.sanitization["document"]))
    document["endpoints"][0]["input_schema"]["body"] = {
        "properties": {},
        "required": ["not_documented"],
        "type": "object",
    }
    source = CatalogSource.from_document(
        peak_source.source_uri,
        "peak",
        document,
        dict(peak_source.sanitization["report"]),
        source_type=peak_source.source_type,
    )

    with pytest.raises(ValueError, match="schema_required_contract_invalid"):
        validate_peak_documented_contracts(
            source=source,
            actions=peak_actions,
            semantics=peak_semantics,
            file_fixture=_controlled_fixture(tmp_path),
        )


def test_peak_contract_validation_requires_exact_semantic_identity(
    tmp_path: Path,
    peak_source: CatalogSource,
    peak_actions,
    peak_semantics,
) -> None:
    from mercury_tools.qualification.peak import validate_peak_documented_contracts

    missing_semantics = dict(peak_semantics)
    missing_semantics.pop((peak_actions[0].action_id, peak_actions[0].version_id))

    with pytest.raises(ValueError, match="peak_semantic_contract_coverage_invalid"):
        validate_peak_documented_contracts(
            source=peak_source,
            actions=peak_actions,
            semantics=missing_semantics,
            file_fixture=_controlled_fixture(tmp_path),
        )


def test_peak_contract_validation_passes_only_the_caller_fixture_parent_to_requests(
    tmp_path: Path,
    peak_source: CatalogSource,
    peak_actions,
    peak_semantics,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.qualification import peak as peak_module

    fixture = _controlled_fixture(tmp_path)
    original_build_request = peak_module.build_request
    roots_seen: list[tuple[Path, ...]] = []

    def capture_roots(action, base_url, inputs, roots, **kwargs):
        roots_seen.append(tuple(roots))
        return original_build_request(action, base_url, inputs, roots, **kwargs)

    monkeypatch.setattr(peak_module, "build_request", capture_roots)

    peak_module.validate_peak_documented_contracts(
        source=peak_source,
        actions=peak_actions,
        semantics=peak_semantics,
        file_fixture=fixture,
    )

    assert len(roots_seen) == 64
    assert all(roots == (fixture.parent,) for roots in roots_seen)
