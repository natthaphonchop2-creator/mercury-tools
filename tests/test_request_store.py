from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from mercury_tools.catalog.models import RiskTier
from mercury_tools.drivers.models import AuthContext
from mercury_tools.execution.models import (
    PREVIEW_TTL,
    PreparedRequest,
    RequestState,
    canonical_payload_hash,
)
from mercury_tools.execution.policy import RiskDecision
from mercury_tools.execution.store import LocalRequestStore, RequestStateError
from mercury_tools.local.repository import RepositoryContext


@dataclass(frozen=True)
class RequestTemplate:
    method: str = "POST"
    final_path: str = "/invoices"
    sanitized_summary: dict[str, Any] | None = None
    request_inputs: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sanitized_summary",
            self.sanitized_summary or {"document_type": "invoice"},
        )
        object.__setattr__(
            self,
            "request_inputs",
            self.request_inputs
            or {
                "query": {"include": "lines"},
                "headers": {"Idempotency-Key": "request-key"},
                "body": {"amount": 1000, "customer": "Ada"},
            },
        )


@pytest.fixture
def request_store(repository_context: RepositoryContext) -> LocalRequestStore:
    return LocalRequestStore(repository_context)


@pytest.fixture
def prepared_request(
    repository_context: RepositoryContext,
    catalog_action: Any,
) -> PreparedRequest:
    template = RequestTemplate()
    payload_hash = canonical_payload_hash(
        {
            "repository_id": repository_context.repository_id,
            "connector_id": "flowaccount",
            "environment": "production",
            "action_id": catalog_action.action_id,
            "version_id": catalog_action.version_id,
            "method": template.method,
            "final_path": template.final_path,
            "request_inputs": template.request_inputs,
        }
    )
    return PreparedRequest.from_template(
        repository=repository_context,
        action=catalog_action,
        environment="production",
        request=template,
        risk=RiskDecision(
            tier=RiskTier.STANDARD_WRITE,
            required_confirmations=1,
            reasons=(),
        ),
        payload_hash=payload_hash,
    )


def test_canonical_payload_hash_is_deterministic_json() -> None:
    first = canonical_payload_hash({"z": [2, {"b": True, "a": "Thai"}], "a": 1})
    second = canonical_payload_hash({"a": 1, "z": [2, {"a": "Thai", "b": True}]})

    assert first == second
    assert first == hashlib.sha256(
        b'{"a":1,"z":[2,{"a":"Thai","b":true}]}'
    ).hexdigest()


def test_prepared_request_is_immutable_bound_and_auth_is_not_in_the_hash(
    prepared_request: PreparedRequest,
) -> None:
    assert prepared_request.state is RequestState.AWAITING_CONFIRMATION
    assert prepared_request.required_confirmations == 1
    assert prepared_request.expires_at - prepared_request.created_at == PREVIEW_TTL
    with pytest.raises(TypeError):
        prepared_request.request_inputs["body"]["amount"] = 2000  # type: ignore[index]
    with pytest.raises(ValidationError):
        prepared_request.request_id = "request_other"  # type: ignore[misc]

    rendered = prepared_request.to_httpx_request(
        AuthContext(
            headers={"Authorization": "Bearer token-that-must-not-leak"},
            query={"access_token": "token-that-must-not-leak"},
            expires_at=None,
        )
    )

    assert isinstance(rendered, httpx.Request)
    assert rendered.method == "POST"
    assert rendered.url.path == "/invoices"
    assert rendered.headers["Authorization"] == "Bearer token-that-must-not-leak"
    assert "token-that-must-not-leak" not in repr(prepared_request)
    assert "customer" not in repr(prepared_request)


def test_public_request_state_exposes_summary_shape_without_business_values(
    prepared_request: PreparedRequest,
) -> None:
    request = prepared_request.model_copy(
        update={
            "sanitized_summary": {
                "document_type": "invoice",
                "customer": {"name": "Ada Lovelace"},
                "amount": 1000,
            },
            "response_summary": {
                "status_class": "2xx",
                "invoice_number": "INV-0001",
                "customer": "Ada Lovelace",
            },
        }
    )

    public = request.public_dict()

    assert "Ada Lovelace" not in str(public)
    assert "1000" not in str(public)
    assert "INV-0001" not in str(public)
    assert "customer" in str(public)
    assert public["sanitized_summary"]["document_type"] == "[REDACTED]"
    assert public["response_summary"]["invoice_number"] == "[REDACTED]"


def test_tier_two_needs_two_confirmations(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    request = request_store.create_preview(
        prepared_request.model_copy(
            update={"risk_tier": RiskTier.HIGH_RISK, "required_confirmations": 2}
        )
    )

    first = request_store.confirm(request.request_id, request.payload_hash)
    second = request_store.confirm(request.request_id, request.payload_hash)

    assert first.state is RequestState.AWAITING_FINAL_CONFIRMATION
    assert first.confirmation_count == 1
    assert second.state is RequestState.READY_TO_EXECUTE
    assert second.confirmation_count == 2


def test_wrong_hash_fails_without_increasing_confirmation_count(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    request = request_store.create_preview(prepared_request)

    with pytest.raises(RequestStateError, match="^payload_hash_mismatch$"):
        request_store.confirm(request.request_id, "0" * 64)

    assert request_store.get(request.request_id).confirmation_count == 0


def test_expired_request_is_invalidated_before_confirmation(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    expired = prepared_request.model_copy(
        update={
            "created_at": datetime.now(UTC) - PREVIEW_TTL - timedelta(seconds=1),
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    request = request_store.create_preview(expired)

    with pytest.raises(RequestStateError, match="^preview_expired$"):
        request_store.confirm(request.request_id, request.payload_hash)

    stored = request_store.get(request.request_id)
    assert stored.state is RequestState.FAILED
    assert stored.failure_reason == "preview_expired"


def test_outcome_unknown_blocks_same_hash(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    request = request_store.create_preview(prepared_request)
    request_store.confirm(request.request_id, request.payload_hash)
    request_store.start_execution(request.request_id)
    request_store.complete(
        request.request_id,
        "outcome_unknown",
        {"status_class": "timeout"},
    )

    with pytest.raises(RequestStateError, match="^replay_blocked_outcome_unknown$"):
        request_store.assert_replay_allowed(request.payload_hash)


def test_start_execution_rechecks_same_hash_within_write_transaction(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    first = request_store.create_preview(prepared_request)
    second = request_store.create_preview(
        prepared_request.model_copy(update={"request_id": "req_second_preview"})
    )
    request_store.confirm(first.request_id, first.payload_hash)
    request_store.confirm(second.request_id, second.payload_hash)
    request_store.start_execution(first.request_id)

    with pytest.raises(RequestStateError, match="^replay_blocked_active_request$"):
        request_store.start_execution(second.request_id)


def test_credential_clear_invalidates_matching_pending_previews(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    request = request_store.create_preview(prepared_request)

    assert request_store.invalidate_pending("flowaccount", "production") == 1
    with pytest.raises(RequestStateError, match="^credentials_cleared$"):
        request_store.require_ready(request.request_id)


def test_store_rejects_tampered_json_without_echoing_request_inputs(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    request = request_store.create_preview(prepared_request)
    database = request_store.database_path
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE requests SET request_json = ? WHERE request_id = ?",
            ('{"request_inputs":{"email":"person@example.com"}}', request.request_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RequestStateError, match="^invalid_stored_request$") as error:
        request_store.get(request.request_id)
    assert "person@example.com" not in str(error.value)
