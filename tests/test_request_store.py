from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from mercury_tools.catalog.models import CatalogAction, RiskTier
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


def binding_payload(
    repository_context: RepositoryContext,
    action: CatalogAction,
    template: RequestTemplate,
    risk: RiskDecision,
    *,
    environment: str = "production",
) -> dict[str, Any]:
    return {
        "repository_id": repository_context.repository_id,
        "connector_id": action.connector_id,
        "environment": environment,
        "action_id": action.action_id,
        "version_id": action.version_id,
        "method": template.method,
        "final_path": template.final_path,
        "request_inputs": template.request_inputs,
        "risk_tier": int(risk.tier),
        "required_confirmations": risk.required_confirmations,
    }


def make_prepared_request(
    repository_context: RepositoryContext,
    action: CatalogAction,
    *,
    template: RequestTemplate | None = None,
    risk: RiskDecision | None = None,
    environment: str = "production",
    payload_hash: str | None = None,
) -> PreparedRequest:
    selected_template = template or RequestTemplate()
    selected_risk = risk or RiskDecision(RiskTier.STANDARD_WRITE, 1, ())
    payload = binding_payload(
        repository_context,
        action,
        selected_template,
        selected_risk,
        environment=environment,
    )
    return PreparedRequest.from_template(
        repository=repository_context,
        action=action,
        environment=environment,
        request=selected_template,
        risk=selected_risk,
        payload_hash=payload_hash or canonical_payload_hash(payload),
    )


def rebind_request(prepared: PreparedRequest, **updates: Any) -> PreparedRequest:
    payload = prepared.model_dump(mode="json")
    payload.update(updates)
    binding = {
        key: payload[key]
        for key in (
            "repository_id",
            "connector_id",
            "environment",
            "action_id",
            "version_id",
            "method",
            "final_path",
            "request_inputs",
            "risk_tier",
            "required_confirmations",
        )
    }
    payload["payload_hash"] = canonical_payload_hash(binding)
    return PreparedRequest.model_validate(payload)


@pytest.fixture
def request_store(repository_context: RepositoryContext) -> LocalRequestStore:
    return LocalRequestStore(repository_context)


@pytest.fixture
def prepared_request(
    repository_context: RepositoryContext,
    catalog_action: Any,
) -> PreparedRequest:
    return make_prepared_request(repository_context, catalog_action)


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
    assert prepared_request.state is RequestState.PREVIEWED
    assert prepared_request.required_confirmations == 1
    assert prepared_request.expires_at - prepared_request.created_at == PREVIEW_TTL
    assert prepared_request.binding_payload == {
        "repository_id": prepared_request.repository_id,
        "connector_id": prepared_request.connector_id,
        "environment": prepared_request.environment,
        "action_id": prepared_request.action_id,
        "version_id": prepared_request.version_id,
        "method": prepared_request.method,
        "final_path": prepared_request.final_path,
        "request_inputs": prepared_request.request_inputs,
        "risk_tier": int(prepared_request.risk_tier),
        "required_confirmations": prepared_request.required_confirmations,
    }
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


def test_from_template_rejects_unrelated_payload_hash_without_payload_echo(
    repository_context: RepositoryContext,
    catalog_action: CatalogAction,
) -> None:
    with pytest.raises(ValueError, match="^payload_hash_mismatch$") as error:
        make_prepared_request(
            repository_context,
            catalog_action,
            payload_hash="0" * 64,
        )

    assert "customer" not in str(error.value)


def test_from_template_revalidates_action_identity(
    repository_context: RepositoryContext,
    catalog_action: CatalogAction,
) -> None:
    tampered = catalog_action.model_copy(update={"action_id": "act_tampered"})

    with pytest.raises(ValueError, match="^invalid_action_binding$"):
        make_prepared_request(repository_context, tampered)


def test_from_template_requires_request_method_to_match_action(
    repository_context: RepositoryContext,
    catalog_action: CatalogAction,
) -> None:
    with pytest.raises(ValueError, match="^request_method_mismatch$"):
        make_prepared_request(
            repository_context,
            catalog_action,
            template=RequestTemplate(method="DELETE"),
            risk=RiskDecision(RiskTier.HIGH_RISK, 2, ()),
        )


def test_from_template_enforces_effective_runtime_risk_floor(
    repository_context: RepositoryContext,
    action_factory: Any,
) -> None:
    action = action_factory(side_effects=("email_customer",))

    with pytest.raises(ValueError, match="^risk_below_runtime_floor$"):
        make_prepared_request(
            repository_context,
            action,
            risk=RiskDecision(RiskTier.STANDARD_WRITE, 1, ()),
        )

    raised = make_prepared_request(
        repository_context,
        action,
        risk=RiskDecision(RiskTier.HIGH_RISK, 2, ()),
    )
    assert raised.risk_tier is RiskTier.HIGH_RISK


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("repository_id", "repo_changed"),
        ("connector_id", "peak"),
        ("environment", "sandbox"),
        ("action_id", "act_changed"),
        ("version_id", "ver_changed"),
        ("method", "PATCH"),
        ("final_path", "/changed"),
        ("request_inputs", {"body": {"amount": 9999}}),
        ("risk_tier", RiskTier.HIGH_RISK),
        ("required_confirmations", 2),
    ],
)
def test_model_validation_rejects_changed_binding_field(
    prepared_request: PreparedRequest,
    field: str,
    replacement: Any,
) -> None:
    payload = prepared_request.model_dump(mode="json")
    payload[field] = replacement
    if field == "risk_tier":
        payload["required_confirmations"] = 2
    if field == "required_confirmations":
        payload["risk_tier"] = 2

    with pytest.raises(ValidationError, match="payload_hash_mismatch"):
        PreparedRequest.model_validate(payload)


def test_model_validation_requires_exact_normalized_preview_ttl(
    prepared_request: PreparedRequest,
) -> None:
    payload = prepared_request.model_dump(mode="json")
    payload["expires_at"] = (
        prepared_request.created_at + PREVIEW_TTL + timedelta(microseconds=1)
    ).isoformat()

    with pytest.raises(ValidationError, match="preview_ttl_invalid"):
        PreparedRequest.model_validate(payload)


def test_model_validation_accepts_exact_ttl_across_timezone_offsets(
    prepared_request: PreparedRequest,
) -> None:
    payload = prepared_request.model_dump(mode="json")
    payload["created_at"] = "2026-07-12T07:00:00+07:00"
    payload["expires_at"] = "2026-07-12T00:15:00+00:00"

    validated = PreparedRequest.model_validate(payload)

    assert validated.created_at.isoformat() == "2026-07-12T00:00:00+00:00"
    assert validated.expires_at.isoformat() == "2026-07-12T00:15:00+00:00"


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


def test_public_summary_drops_sensitive_values_encoded_as_dynamic_keys(
    prepared_request: PreparedRequest,
) -> None:
    request = prepared_request.model_copy(
        update={
            "sanitized_summary": {
                "document_type": "invoice",
                "person@example.com": "present",
                "0105559999999": "present",
                "cus_9f83ab12": "present",
                "Ada Lovelace": "present",
                "AdaLovelace": "present",
                "abcDef123456789": "present",
            }
        }
    )

    public = request.public_dict()

    assert public["sanitized_summary"] == {"document_type": "[REDACTED]"}
    assert "person@example.com" not in str(public)
    assert "0105559999999" not in str(public)
    assert "cus_9f83ab12" not in str(public)
    assert "Ada Lovelace" not in str(public)
    assert "AdaLovelace" not in str(public)
    assert "abcDef123456789" not in str(public)


def test_tier_two_needs_two_confirmations(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    request = request_store.create_preview(
        rebind_request(
            prepared_request,
            risk_tier=RiskTier.HIGH_RISK,
            required_confirmations=2,
        )
    )

    first = request_store.confirm(request.request_id, request.payload_hash)
    second = request_store.confirm(request.request_id, request.payload_hash)

    assert first.state is RequestState.AWAITING_FINAL_CONFIRMATION
    assert first.confirmation_count == 1
    assert second.state is RequestState.READY_TO_EXECUTE
    assert second.confirmation_count == 2


def test_create_preview_transitions_previewed_to_awaiting_in_transaction(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    assert prepared_request.state is RequestState.PREVIEWED

    created = request_store.create_preview(prepared_request)

    assert created.state is RequestState.AWAITING_CONFIRMATION
    assert request_store.get(created.request_id).state is RequestState.AWAITING_CONFIRMATION


def test_prepared_state_graph_rejects_invalid_previewed_fields(
    prepared_request: PreparedRequest,
) -> None:
    payload = prepared_request.model_dump(mode="json")
    payload.update({"confirmation_count": 1, "state": "previewed"})

    with pytest.raises(ValidationError, match="invalid_request_state"):
        PreparedRequest.model_validate(payload)


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
    created_at = datetime.now(UTC) - PREVIEW_TTL - timedelta(seconds=1)
    expired = prepared_request.model_copy(
        update={"created_at": created_at, "expires_at": created_at + PREVIEW_TTL}
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


@pytest.mark.parametrize(
    ("field", "replacement", "column"),
    [
        ("repository_id", "repo_changed", None),
        ("connector_id", "peak", "connector_id"),
        ("environment", "sandbox", "environment"),
        ("action_id", "act_changed", None),
        ("version_id", "ver_changed", None),
        ("method", "PATCH", None),
        ("final_path", "/changed", None),
        ("request_inputs", {"body": {"amount": 9999}}, None),
        ("risk_tier", 2, None),
        ("required_confirmations", 2, None),
    ],
)
def test_store_rejects_coordinated_json_and_column_binding_tampering(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    field: str,
    replacement: Any,
    column: str | None,
) -> None:
    request = request_store.create_preview(prepared_request)
    payload = request.model_dump(mode="json")
    payload[field] = replacement
    if field == "risk_tier":
        payload["required_confirmations"] = 2
    if field == "required_confirmations":
        payload["risk_tier"] = 2
    connection = sqlite3.connect(request_store.database_path)
    try:
        connection.execute(
            "UPDATE requests SET request_json = ? WHERE request_id = ?",
            (json.dumps(payload), request.request_id),
        )
        if column is not None:
            connection.execute(
                f"UPDATE requests SET {column} = ? WHERE request_id = ?",  # noqa: S608
                (replacement, request.request_id),
            )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RequestStateError, match="^invalid_stored_request$"):
        request_store.get(request.request_id)


def test_store_rejects_coordinated_expiry_column_tampering(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    request = request_store.create_preview(prepared_request)
    payload = request.model_dump(mode="json")
    changed_expiry = request.expires_at + timedelta(seconds=1)
    payload["expires_at"] = changed_expiry.isoformat()
    connection = sqlite3.connect(request_store.database_path)
    try:
        connection.execute(
            "UPDATE requests SET expires_at = ?, request_json = ? WHERE request_id = ?",
            (changed_expiry.isoformat(), json.dumps(payload), request.request_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RequestStateError, match="^invalid_stored_request$"):
        request_store.get(request.request_id)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and modes")
def test_request_store_enforces_owner_only_cache_and_database_modes(
    repository_context: RepositoryContext,
) -> None:
    os.chmod(repository_context.cache_dir, 0o755)

    store = LocalRequestStore(repository_context)

    assert stat.S_IMODE(repository_context.cache_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.database_path.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(store.database_path) + suffix)
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow behavior")
def test_request_store_rejects_database_symlink(repository_context: RepositoryContext) -> None:
    target = repository_context.cache_dir / "target.sqlite"
    target.touch()
    (repository_context.cache_dir / "requests.sqlite").symlink_to(target)

    with pytest.raises(ValueError, match="^invalid_request_store_path$"):
        LocalRequestStore(repository_context)


@pytest.mark.skipif(os.name != "posix", reason="POSIX link counts")
def test_request_store_rejects_database_hardlink(repository_context: RepositoryContext) -> None:
    target = repository_context.cache_dir / "target.sqlite"
    target.touch()
    os.link(target, repository_context.cache_dir / "requests.sqlite")

    with pytest.raises(ValueError, match="^invalid_request_store_path$"):
        LocalRequestStore(repository_context)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership")
def test_request_store_rejects_owner_mismatch(
    repository_context: RepositoryContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "getuid", lambda: repository_context.cache_dir.stat().st_uid + 1)

    with pytest.raises(ValueError, match="^invalid_request_store_path$"):
        LocalRequestStore(repository_context)


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow behavior")
def test_request_store_rejects_sidecar_symlink(repository_context: RepositoryContext) -> None:
    store = LocalRequestStore(repository_context)
    target = repository_context.cache_dir / "sidecar-target"
    target.touch()
    Path(str(store.database_path) + "-wal").symlink_to(target)

    with pytest.raises(ValueError, match="^invalid_request_store_path$"):
        store.assert_replay_allowed("a" * 64)


@pytest.mark.skipif(os.name != "posix", reason="POSIX link counts")
def test_request_store_rejects_sidecar_hardlink(repository_context: RepositoryContext) -> None:
    store = LocalRequestStore(repository_context)
    target = repository_context.cache_dir / "sidecar-target"
    target.touch()
    os.link(target, Path(str(store.database_path) + "-shm"))

    with pytest.raises(ValueError, match="^invalid_request_store_path$"):
        store.assert_replay_allowed("a" * 64)


@pytest.mark.skipif(os.name != "posix", reason="POSIX inode identity")
def test_request_store_rejects_database_replacement_during_connect(
    repository_context: RepositoryContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    replaced = False

    def replacing_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal replaced
        path = Path(database)
        if not replaced:
            replaced = True
            os.replace(path, path.with_name("retained.sqlite"))
            path.touch(mode=0o600)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", replacing_connect)

    with pytest.raises(ValueError, match="^invalid_request_store_path$"):
        LocalRequestStore(repository_context)


@pytest.mark.skipif(os.name != "posix", reason="POSIX inode identity")
def test_request_store_rejects_sidecar_replacement_during_connect(
    repository_context: RepositoryContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_connect = sqlite3.connect
    replaced = False

    def replacing_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal replaced
        path = Path(str(database) + "-wal")
        if not replaced:
            replaced = True
            os.replace(path, path.with_name("retained.sqlite-wal"))
            path.touch(mode=0o600)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", replacing_connect)

    with pytest.raises(ValueError, match="^invalid_request_store_path$"):
        LocalRequestStore(repository_context)
