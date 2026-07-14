import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

import mercury_tools.db.validation as validation_module
from mercury_tools.catalog.models import HttpMethod
from mercury_tools.config import Settings
from mercury_tools.db.validation import (
    CoverageResult,
    ObservationMetadata,
    ObservationState,
    ResolveResult,
    SupabaseValidationStore,
    ValidationObservation,
)
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.selection import EvidenceOutcome, EvidenceRequest
from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]
BATCH_RESOLVE_MIGRATION = (
    ROOT
    / "supabase/migrations/20260714120000_resolve_erp_action_validation_batch.sql"
)


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: Any = None,
        *,
        text: str | None = None,
        json_error: ValueError | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self._json_error = json_error
        self.text = text if text is not None else ("" if body is None else json.dumps(body))

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._body


def _table_name(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def _postgrest_insert_representation(
    rows: list[dict[str, Any]],
    select: str | None,
) -> list[dict[str, Any]]:
    server_rows = [
        {
            **row,
            "id": f"server-generated-{index}",
            "created_at": "2026-07-13T12:00:00Z",
        }
        for index, row in enumerate(rows, start=1)
    ]
    if select is None:
        return server_rows
    columns = select.split(",")
    return [{column: row[column] for column in columns} for row in server_rows]


def _settings(**overrides: Any) -> Settings:
    values = {
        "supabase_url": "https://example.supabase.co",
        "supabase_service_role_key": "test-service-role-key",
        "openai_api_key": "",
    }
    values.update(overrides)
    return Settings(**values)


def _record(**overrides: Any) -> ValidationKnowledge:
    status = ValidationStatus(overrides.get("validation_status", ValidationStatus.LIVE_SUCCESS))
    run_number = int(overrides.pop("run_number", 1))
    action_number = int(overrides.pop("action_number", 1))
    version_number = int(overrides.pop("version_number", action_number))
    values = {
        "opaque_evidence_id": f"ev_{run_number:026d}",
        "run_id": f"run_{run_number:026d}",
        "action_id": f"act_{action_number:024x}",
        "version_id": f"av_{version_number:064x}",
        "connector_id": "flowaccount",
        "environment": "sandbox",
        "validation_status": status,
        "evidence_level": EvidenceLevel.SANDBOX_OBSERVED,
        "execution_eligibility": ExecutionEligibility.SANDBOX_READ,
        "approved_public": True,
        "summary_th": SUMMARY_TH[status],
        "summary_en": SUMMARY_EN[status],
        "prerequisites": ("review_fixture",),
        "limitations": ("sandbox_only",),
        "recommended_next_step": "review_accounting_result",
        "response_shape": {
            "counterparty_tax_id": "string",
            "document_id": "string",
            "email": "string",
        },
        "status_class": "2xx",
        "latency_ms": 25,
        "semantic_contract": SemanticContract(
            business_object="invoice",
            operation="list",
            accounting_uses=("revenue_review",),
            output_semantics={"document_id": "document identifier string"},
        ),
        "evidence_sha256": f"{run_number:064x}",
        "reviewed_by": "release_reviewer",
        "runner_version": "v0.2.1",
        "run_state": QualificationRunState.COMPLETED,
        "evaluated_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=1),
    }
    values.update(overrides)
    values["validation_status"] = status
    return ValidationKnowledge.model_validate(values)


def _request(action_number: int = 1, version_number: int | None = None) -> EvidenceRequest:
    version_number = action_number if version_number is None else version_number
    return EvidenceRequest(
        connector_id="flowaccount",
        action_id=f"act_{action_number:024x}",
        version_id=f"av_{version_number:064x}",
        environment="sandbox",
    )


def _observation(**overrides: Any) -> ValidationObservation:
    values = {
        "opaque_event_id": "evt_00000000000000000000000001",
        "action_id": f"act_{1:024x}",
        "version_id": f"av_{1:064x}",
        "connector_id": "flowaccount",
        "method": HttpMethod.GET,
        "observed_state": ObservationState.SUCCESS,
        "status_class": "2xx",
        "latency_ms": 25,
        "metadata": ObservationMetadata(
            source="sandbox_runner",
            reviewed_by="release_reviewer",
            note="reviewed_expected_outcome",
        ),
    }
    values.update(overrides)
    return ValidationObservation.model_validate(values)


def _identity(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        row["connector_id"],
        row["action_id"],
        row["version_id"],
        row["environment"],
        row["run_id"],
    )


def _binding_row(observation: ValidationObservation) -> dict[str, str]:
    return {
        "connector_id": observation.connector_id,
        "action_id": observation.action_id,
        "version_id": observation.version_id,
    }


def test_store_reuses_required_supabase_settings() -> None:
    with pytest.raises(
        RuntimeError,
        match="^SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required\\.$",
    ):
        SupabaseValidationStore(_settings(supabase_url="", supabase_service_role_key=""))


def test_publish_validates_entire_batch_before_first_network_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    invalid = _record(run_number=2, environment="development")
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *args, **kwargs: calls.append({"args": args, **kwargs}),
    )

    with pytest.raises(ValueError, match="^validation_evidence_invalid$"):
        SupabaseValidationStore(_settings()).publish([_record(), invalid])

    assert calls == []


def test_publish_uses_json_mode_and_exact_append_conflict_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if method == "GET":
            return FakeResponse(body=[])
        return FakeResponse(
            status_code=201,
            body=_postgrest_insert_representation(
                kwargs["json"],
                kwargs["params"].get("select"),
            ),
        )

    monkeypatch.setattr(validation_module.httpx, "request", request)

    created = SupabaseValidationStore(_settings()).publish([_record()])

    assert created == 1
    post = next(call for call in calls if call["method"] == "POST")
    row = post["json"][0]
    assert post["params"] == {
        "on_conflict": "connector_id,action_id,version_id,environment,run_id",
        "select": ",".join(ValidationKnowledge.model_fields),
    }
    assert post["headers"]["Prefer"] == "resolution=ignore-duplicates,return=representation"
    assert row["validation_status"] == "live_success"
    assert row["evidence_level"] == "sandbox_observed"
    assert row["run_state"] == "completed"
    assert row["evaluated_at"] == "2026-07-13T11:00:00Z"
    assert row["expires_at"] == "2026-07-14T12:00:00Z"
    assert row["prerequisites"] == ["review_fixture"]
    assert row["semantic_contract"]["accounting_uses"] == ["revenue_review"]
    assert row["semantic_contract"]["output_semantics"] == {
        "document_id": "document identifier string"
    }
    assert row["response_shape"] == {
        "counterparty_tax_id": "string",
        "document_id": "string",
        "email": "string",
    }
    assert type(row["response_shape"]) is dict
    assert not any(call["method"] in {"PATCH", "PUT", "DELETE"} for call in calls)


def test_identical_publish_retry_is_idempotent_and_never_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    stored: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if method == "GET":
            matched = [
                row
                for row in stored.values()
                if all(
                    kwargs["params"][field] == f"eq.{row[field]}"
                    for field in (
                        "connector_id",
                        "action_id",
                        "version_id",
                        "environment",
                        "run_id",
                    )
                )
            ]
            return FakeResponse(body=matched)
        inserted = []
        for row in kwargs["json"]:
            if _identity(row) not in stored:
                stored[_identity(row)] = row
                inserted.append(row)
        return FakeResponse(status_code=201, body=inserted)

    monkeypatch.setattr(validation_module.httpx, "request", request)
    store = SupabaseValidationStore(_settings())
    record = _record()

    assert store.publish([record, record]) == 1
    assert store.publish([record]) == 0
    assert sum(call["method"] == "POST" for call in calls) == 1
    assert not any(call["method"] in {"PATCH", "PUT", "DELETE"} for call in calls)


@pytest.mark.parametrize(
    "conflict",
    [
        _record(evidence_sha256="f" * 64),
        _record(
            validation_status=ValidationStatus.LIVE_FAILED,
            summary_th=SUMMARY_TH[ValidationStatus.LIVE_FAILED],
            summary_en=SUMMARY_EN[ValidationStatus.LIVE_FAILED],
            run_state=QualificationRunState.FAILED,
            status_class="4xx",
        ),
    ],
)
def test_conflicting_duplicate_publish_raises_constant_error_without_update(
    monkeypatch: pytest.MonkeyPatch,
    conflict: ValidationKnowledge,
) -> None:
    original = _record().model_dump(mode="json")
    calls: list[str] = []

    def request(method: str, _url: str, **_kwargs: Any) -> FakeResponse:
        calls.append(method)
        return FakeResponse(body=[original])

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(RuntimeError, match="^supabase_validation_conflict$"):
        SupabaseValidationStore(_settings()).publish([conflict])

    assert calls == ["GET"]


def test_conflicting_duplicate_inside_batch_is_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="^supabase_validation_conflict$"):
        SupabaseValidationStore(_settings()).publish([_record(), _record(evidence_sha256="f" * 64)])

    assert calls == []


def test_publish_detects_conflict_inserted_during_append_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conflicting = _record(evidence_sha256="f" * 64).model_dump(mode="json")
    get_count = 0

    def request(method: str, _url: str, **_kwargs: Any) -> FakeResponse:
        nonlocal get_count
        if method == "POST":
            return FakeResponse(status_code=201, body=[])
        get_count += 1
        return FakeResponse(body=[] if get_count == 1 else [conflicting])

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(RuntimeError, match="^supabase_validation_conflict$"):
        SupabaseValidationStore(_settings()).publish([_record()])


def test_record_observation_retries_by_opaque_event_id_without_raw_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    stored: dict[str, dict[str, Any]] = {}
    observation = _observation()

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        table = _table_name(url)
        if method == "GET" and table == "erp_action_observations":
            event_id = kwargs["params"]["opaque_event_id"].removeprefix("eq.")
            return FakeResponse(body=[stored[event_id]] if event_id in stored else [])
        if method == "GET" and table == "erp_action_versions":
            return FakeResponse(body=[_binding_row(observation)])
        row = kwargs["json"][0]
        stored.setdefault(row["opaque_event_id"], row)
        return FakeResponse(
            status_code=201,
            body=_postgrest_insert_representation(
                [row],
                kwargs["params"].get("select"),
            ),
        )

    monkeypatch.setattr(validation_module.httpx, "request", request)
    store = SupabaseValidationStore(_settings())

    assert store.record_observation(observation) is True
    assert store.record_observation(observation) is False
    post = next(call for call in calls if call["method"] == "POST")
    assert post["params"] == {
        "on_conflict": "opaque_event_id",
        "select": (
            "opaque_event_id,action_id,version_id,connector_id,method,observed_state,"
            "status_class,latency_ms,metadata"
        ),
    }
    assert post["json"] == [
        {
            "opaque_event_id": "evt_00000000000000000000000001",
            "action_id": f"act_{1:024x}",
            "version_id": f"av_{1:064x}",
            "connector_id": "flowaccount",
            "method": "GET",
            "observed_state": "success",
            "status_class": "2xx",
            "latency_ms": 25,
            "metadata": {
                "source": "sandbox_runner",
                "reviewed_by": "release_reviewer",
                "note": "reviewed_expected_outcome",
            },
        }
    ]
    assert sum(call["method"] == "POST" for call in calls) == 1
    assert [(_table_name(call["url"]), call["method"]) for call in calls] == [
        ("erp_action_versions", "GET"),
        ("erp_action_observations", "GET"),
        ("erp_action_observations", "POST"),
        ("erp_action_versions", "GET"),
        ("erp_action_observations", "GET"),
    ]
    for binding_call in (calls[0], calls[3]):
        assert binding_call["params"] == {
            "connector_id": "eq.flowaccount",
            "action_id": f"eq.act_{1:024x}",
            "version_id": f"eq.av_{1:064x}",
            "select": "connector_id,action_id,version_id",
            "limit": "2",
        }


@pytest.mark.parametrize("binding_kind", ["missing", "mismatched"])
def test_equal_observation_retry_requires_valid_binding_before_false(
    monkeypatch: pytest.MonkeyPatch,
    binding_kind: str,
) -> None:
    observation = _observation()
    existing = observation.model_dump(mode="json")
    mismatched = {
        **_binding_row(observation),
        "connector_id": "legacy_connector",
    }
    binding_body = [] if binding_kind == "missing" else [mismatched]
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if _table_name(url) == "erp_action_versions":
            return FakeResponse(body=binding_body)
        return FakeResponse(body=[existing])

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(RuntimeError, match="^supabase_observation_scope_mismatch$") as raised:
        SupabaseValidationStore(_settings()).record_observation(observation)

    assert str(raised.value) == "supabase_observation_scope_mismatch"
    assert "legacy_connector" not in str(raised.value)
    assert [(_table_name(call["url"]), call["method"]) for call in calls] == [
        ("erp_action_versions", "GET"),
    ]


def test_record_observation_rejects_raw_provider_metadata_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    secret = "synthetic-provider-body"
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    unsafe = {
        **_observation().model_dump(mode="json"),
        "metadata": {"raw_response": {"provider_body": secret}},
    }

    with pytest.raises(ValueError, match="^validation_observation_invalid$") as raised:
        SupabaseValidationStore(_settings()).record_observation(unsafe)

    assert secret not in str(raised.value)
    assert calls == []


def test_record_observation_rejects_provider_json_hidden_in_note_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    raw_provider_json = '{"provider_response":{"document_id":"synthetic"}}'
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    unsafe = {
        **_observation().model_dump(mode="json"),
        "metadata": {"note": raw_provider_json},
    }

    with pytest.raises(ValueError, match="^validation_observation_invalid$") as raised:
        SupabaseValidationStore(_settings()).record_observation(unsafe)

    assert raw_provider_json not in str(raised.value)
    assert calls == []


@pytest.mark.parametrize("field", ["source", "reviewed_by", "note"])
def test_record_observation_rejects_provider_record_like_metadata_before_network(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    calls: list[Any] = []
    provider_record = "provider_record_482916375"
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    observation = _observation().model_dump(mode="json")
    observation["metadata"][field] = provider_record

    with pytest.raises(ValueError, match="^validation_observation_invalid$") as raised:
        SupabaseValidationStore(_settings()).record_observation(observation)

    assert provider_record not in str(raised.value)
    assert calls == []


def test_observation_requires_exact_connector_action_version_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        table = _table_name(url)
        if table == "erp_action_observations" and method == "GET":
            return FakeResponse(body=[])
        if table == "erp_action_versions" and method == "GET":
            return FakeResponse(body=[])
        raise AssertionError("observation_insert_must_not_run")

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(RuntimeError, match="^supabase_observation_scope_mismatch$"):
        SupabaseValidationStore(_settings()).record_observation(_observation(connector_id="peak"))

    assert [(_table_name(call["url"]), call["method"]) for call in calls] == [
        ("erp_action_versions", "GET"),
    ]


def test_observation_binding_rejects_a_valid_row_for_a_different_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    observation = _observation(connector_id="peak")
    mismatched = _binding_row(_observation(connector_id="flowaccount"))

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if _table_name(url) == "erp_action_observations":
            return FakeResponse(body=[])
        return FakeResponse(body=[mismatched])

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(RuntimeError, match="^supabase_observation_scope_mismatch$"):
        SupabaseValidationStore(_settings()).record_observation(observation)

    assert all(call["method"] == "GET" for call in calls)


@pytest.mark.parametrize(
    "binding_body",
    [
        {"connector_id": "flowaccount"},
        [
            {
                "connector_id": "flowaccount",
                "action_id": f"act_{1:024x}",
                "version_id": f"av_{1:064x}",
                "id": "unexpected-server-column",
            }
        ],
        [
            {
                "connector_id": "flowaccount",
                "action_id": f"act_{1:024x}",
                "version_id": f"av_{1:064x}",
            },
            {
                "connector_id": "flowaccount",
                "action_id": f"act_{1:024x}",
                "version_id": f"av_{1:064x}",
            },
        ],
    ],
)
def test_observation_binding_wrong_shapes_fail_closed_before_insert(
    monkeypatch: pytest.MonkeyPatch,
    binding_body: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if _table_name(url) == "erp_action_observations":
            return FakeResponse(body=[])
        return FakeResponse(body=binding_body)

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(RuntimeError, match="^supabase_validation_response_invalid$"):
        SupabaseValidationStore(_settings()).record_observation(_observation())

    assert all(call["method"] == "GET" for call in calls)


def test_observation_binding_malformed_json_is_constant_and_no_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    secret = "synthetic-binding-provider-body"
    existing = _observation().model_dump(mode="json")

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if _table_name(url) == "erp_action_versions":
            return FakeResponse(
                text=f'{{"provider_body":"{secret}"}}',
                json_error=ValueError(secret),
            )
        return FakeResponse(body=[existing])

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(
        RuntimeError,
        match="^supabase_validation_response_invalid$",
    ) as raised:
        SupabaseValidationStore(_settings()).record_observation(_observation())

    assert str(raised.value) == "supabase_validation_response_invalid"
    assert secret not in str(raised.value)
    assert [(_table_name(call["url"]), call["method"]) for call in calls] == [
        ("erp_action_versions", "GET"),
    ]


def test_conflicting_observation_retry_raises_constant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _observation().model_dump(mode="json")
    attempt = _observation(status_class="4xx", observed_state=ObservationState.FAILED)
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        if _table_name(url) == "erp_action_versions":
            return FakeResponse(body=[_binding_row(attempt)])
        return FakeResponse(body=[existing])

    monkeypatch.setattr(validation_module.httpx, "request", request)

    with pytest.raises(RuntimeError, match="^supabase_observation_conflict$"):
        SupabaseValidationStore(_settings()).record_observation(attempt)

    assert [(_table_name(call["url"]), call["method"]) for call in calls] == [
        ("erp_action_versions", "GET"),
        ("erp_action_observations", "GET"),
    ]


def test_resolve_uses_one_set_based_request_and_preserves_request_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = (_request(2), _request(1))
    rows = [
        {
            "request_index": 1,
            **requested[1].model_dump(mode="json"),
            "records": [],
        },
        {
            "request_index": 0,
            **requested[0].model_dump(mode="json"),
            "records": [
                _record(action_number=2, run_number=2).model_dump(mode="json")
            ],
        },
    ]
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(body=rows)

    monkeypatch.setattr(validation_module.httpx, "request", request)

    result = SupabaseValidationStore(_settings()).resolve(requested, now=NOW)

    assert isinstance(result, ResolveResult)
    assert tuple(entry.request.action_id for entry in result.entries) == (
        _request(2).action_id,
        _request(1).action_id,
    )
    assert result.entries[0].selection.outcome is EvidenceOutcome.LIVE_SUCCESS
    assert result.entries[1].selection.outcome is EvidenceOutcome.NO_EVIDENCE
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert _table_name(calls[0]["url"]) == "resolve_erp_action_validation_batch"
    assert calls[0]["json"] == {
        "p_requests": [request.model_dump(mode="json") for request in requested],
        "p_now": NOW.isoformat(),
    }
    assert "params" not in calls[0]
    with pytest.raises(ValidationError, match="frozen"):
        result.entries = ()


def test_resolve_accepts_exactly_one_hundred_scopes_in_one_backend_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = tuple(_request(index) for index in range(1, 101))
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(
            body=[
                {
                    "request_index": index,
                    **evidence_request.model_dump(mode="json"),
                    "records": [],
                }
                for index, evidence_request in reversed(tuple(enumerate(requested)))
            ]
        )

    monkeypatch.setattr(validation_module.httpx, "request", request)

    result = SupabaseValidationStore(_settings()).resolve(requested, now=NOW)

    assert len(result.entries) == 100
    assert tuple(entry.request for entry in result.entries) == requested
    assert all(
        entry.selection.outcome is EvidenceOutcome.NO_EVIDENCE
        for entry in result.entries
    )
    assert len(calls) == 1
    assert len(calls[0]["json"]["p_requests"]) == 100


@pytest.mark.parametrize(
    "row_update",
    [
        {"connector_id": "peak"},
        {"request_index": True},
        {"private_field": "must-not-be-accepted"},
    ],
)
def test_resolve_rejects_non_exact_rpc_rows(
    monkeypatch: pytest.MonkeyPatch,
    row_update: dict[str, Any],
) -> None:
    evidence_request = _request()
    row = {
        "request_index": 0,
        **evidence_request.model_dump(mode="json"),
        "records": [],
        **row_update,
    }
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *_args, **_kwargs: FakeResponse(body=[row]),
    )

    with pytest.raises(
        RuntimeError,
        match="^supabase_validation_response_invalid$",
    ):
        SupabaseValidationStore(_settings()).resolve((evidence_request,), now=NOW)


def test_resolve_validates_all_requests_and_now_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="^evidence_request_invalid$"):
        SupabaseValidationStore(_settings()).resolve(
            [_request(), {**_request(2).model_dump(), "environment": "local,or=(id.gt.0)"}],
            now=NOW,
        )
    assert calls == []

    with pytest.raises(ValueError, match="^evidence_timestamp_naive$"):
        SupabaseValidationStore(_settings()).resolve([_request()], now=datetime(2026, 7, 13, 12))
    assert calls == []

    with pytest.raises(ValueError, match="^evidence_request_invalid$"):
        SupabaseValidationStore(_settings()).resolve([_request()] * 2, now=NOW)
    assert calls == []

    with pytest.raises(ValueError, match="^evidence_request_invalid$"):
        SupabaseValidationStore(_settings()).resolve(
            [_request(index) for index in range(1, 102)],
            now=NOW,
        )
    assert calls == []


def test_batch_resolve_migration_is_bounded_set_based_and_service_role_only() -> None:
    sql = BATCH_RESOLVE_MIGRATION.read_text(encoding="utf-8").lower()

    assert "function public.resolve_erp_action_validation_batch" in sql
    assert "jsonb_array_length(p_requests) between 1 and 100" in sql
    assert "jsonb_array_elements(p_requests) with ordinality" in sql
    assert "left join lateral" in sql
    assert "knowledge.connector_id = parsed.connector_id" in sql
    assert "knowledge.action_id = parsed.action_id" in sql
    assert "knowledge.version_id = parsed.version_id" in sql
    assert "knowledge.environment = parsed.environment" in sql
    assert "revoke all on function public.resolve_erp_action_validation_batch" in sql
    assert "grant execute on function public.resolve_erp_action_validation_batch" in sql
    assert "to service_role" in sql


def test_coverage_uses_exact_filters_and_returns_deterministic_typed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _record(action_number=1, run_number=1, evaluated_at=NOW - timedelta(days=1))
    tied_low = _record(action_number=2, run_number=2, evaluated_at=NOW)
    tied_high = _record(action_number=2, run_number=3, evaluated_at=NOW)
    response_rows = [
        tied_low.model_dump(mode="json"),
        first.model_dump(mode="json"),
        tied_high.model_dump(mode="json"),
    ]
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        return FakeResponse(body=response_rows)

    monkeypatch.setattr(validation_module.httpx, "request", request)

    result = SupabaseValidationStore(_settings()).coverage("flowaccount", "sandbox")

    assert isinstance(result, CoverageResult)
    assert result.connector_id == "flowaccount"
    assert result.environment == "sandbox"
    assert result.records == (first, tied_high, tied_low)
    assert list(calls[0]["params"]) == ["connector_id", "environment", "select", "order"]
    assert calls[0]["params"]["connector_id"] == "eq.flowaccount"
    assert calls[0]["params"]["environment"] == "eq.sandbox"
    assert calls[0]["params"]["order"] == (
        "action_id.asc,version_id.asc,evaluated_at.desc,run_id.desc"
    )
    with pytest.raises(ValidationError, match="frozen"):
        result.records = ()


def test_coverage_rejects_unsafe_filter_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    secret = "Bearer synthetic-filter-secret"
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="^validation_coverage_filter_invalid$") as raised:
        SupabaseValidationStore(_settings()).coverage(secret, "sandbox")

    assert secret not in str(raised.value)
    assert calls == []


def _non_2xx(secret: str) -> Callable[..., FakeResponse]:
    return lambda *_args, **_kwargs: FakeResponse(
        status_code=503,
        text=f'{{"provider_body":"{secret}"}}',
    )


def _transport_error(secret: str) -> Callable[..., FakeResponse]:
    def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise httpx.ConnectError(
            f"transport failed with {secret}",
            request=httpx.Request("GET", "https://example.supabase.co"),
        )

    return request


def _malformed_json(secret: str) -> Callable[..., FakeResponse]:
    return lambda *_args, **_kwargs: FakeResponse(
        text=f'{{"provider_body":"{secret}"}}',
        json_error=ValueError(f"malformed {secret}"),
    )


def _wrong_shape(secret: str) -> Callable[..., FakeResponse]:
    return lambda *_args, **_kwargs: FakeResponse(body={"provider_body": secret})


@pytest.mark.parametrize(
    ("factory", "expected_error"),
    [
        (_non_2xx, "supabase_validation_request_failed"),
        (_transport_error, "supabase_validation_request_failed"),
        (_malformed_json, "supabase_validation_response_invalid"),
        (_wrong_shape, "supabase_validation_response_invalid"),
    ],
)
def test_store_errors_never_echo_bodies_exceptions_urls_headers_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[str], Callable[..., FakeResponse]],
    expected_error: str,
) -> None:
    secret = "synthetic-secret-that-must-not-echo"
    monkeypatch.setattr(validation_module.httpx, "request", factory(secret))

    with pytest.raises(RuntimeError, match=f"^{expected_error}") as raised:
        SupabaseValidationStore(_settings()).coverage("flowaccount", "sandbox")

    error = str(raised.value)
    assert error == expected_error
    assert secret not in error
    assert "example.supabase.co" not in error
    assert "test-service-role-key" not in error


def test_malformed_row_raises_constant_no_echo_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "synthetic-provider-row"
    monkeypatch.setattr(
        validation_module.httpx,
        "request",
        lambda *_args, **_kwargs: FakeResponse(body=[{"raw_response": {"provider_body": secret}}]),
    )

    with pytest.raises(
        RuntimeError,
        match="^supabase_validation_response_invalid$",
    ) as raised:
        SupabaseValidationStore(_settings()).coverage("flowaccount", "sandbox")

    assert secret not in str(raised.value)
