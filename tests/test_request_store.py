from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
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
from mercury_tools.execution.policy import ApprovalLevel, MutationClass, RiskDecision
from mercury_tools.execution.store import LocalRequestStore, RequestStateError
from mercury_tools.local.repository import RepositoryContext

_CREDENTIAL_REVISION = "d" * 64


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
        "approval_level": risk.approval_level.value,
        "mutation_class": risk.mutation_class.value,
        "credential_revision": _CREDENTIAL_REVISION,
        "preflight_actions": [],
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
    selected_risk = risk or RiskDecision(
        RiskTier.STANDARD_WRITE,
        ApprovalLevel.STANDARD,
        MutationClass.CREATE,
        (),
    )
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
        credential_revision=_CREDENTIAL_REVISION,
        preflight_actions=(),
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
            "approval_level",
            "mutation_class",
            "credential_revision",
            "preflight_actions",
        )
    }
    payload["payload_hash"] = canonical_payload_hash(binding)
    return PreparedRequest.model_validate(payload)


def seed_v1_request_store(
    repository_context: RepositoryContext,
    *,
    state: str = "awaiting_final_confirmation",
    request_json: str = '{"historical":"[REDACTED]"}',
) -> tuple[str, str]:
    request_id = "req_legacy_approval"
    payload_hash = "b" * 64
    connection = sqlite3.connect(repository_context.cache_dir / "requests.sqlite")
    try:
        connection.execute(
            """
            CREATE TABLE requests (
                request_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                connector_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                state TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                request_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX requests_replay_blocking_hash
            ON requests (payload_hash)
            WHERE state IN ('executing', 'succeeded', 'outcome_unknown')
            """
        )
        connection.execute(
            """
            INSERT INTO requests (
                request_id, payload_hash, connector_id, environment,
                state, expires_at, request_json
            ) VALUES (?, ?, 'flowaccount', 'production', ?, ?, ?)
            """,
            (
                request_id,
                payload_hash,
                state,
                (datetime.now(UTC) + PREVIEW_TTL).isoformat(),
                request_json,
            ),
        )
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    finally:
        connection.close()
    return request_id, payload_hash


def seed_v2_request_store(
    repository_context: RepositoryContext,
    *,
    columns: str,
) -> Path:
    database = repository_context.cache_dir / "requests.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute(f"CREATE TABLE requests ({columns})")
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()
    return database


_V2_REQUEST_COLUMNS = """
    request_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    state TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    request_json TEXT NOT NULL
"""


def seed_v2_request_store_ddl(
    repository_context: RepositoryContext,
    *,
    ddl: str,
) -> Path:
    database = repository_context.cache_dir / "requests.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute(ddl)
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()
    return database


@pytest.fixture
def request_store(repository_context: RepositoryContext) -> LocalRequestStore:
    return LocalRequestStore(repository_context)


@pytest.fixture
def prepared_request(
    repository_context: RepositoryContext,
    catalog_action: Any,
) -> PreparedRequest:
    return make_prepared_request(repository_context, catalog_action)


def test_fresh_request_store_uses_v2_schema(
    request_store: LocalRequestStore,
) -> None:
    connection = sqlite3.connect(request_store.database_path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()

    assert version == 2
    assert "requests" in tables
    assert "requests_v1_archive" not in tables


def test_brand_new_v0_empty_request_store_initializes_normally(
    repository_context: RepositoryContext,
) -> None:
    database = repository_context.cache_dir / "requests.sqlite"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == []
    finally:
        connection.close()

    store = LocalRequestStore(repository_context)

    connection = sqlite3.connect(store.database_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
        ).fetchall() == [("requests",)]
    finally:
        connection.close()


def test_v2_initialization_rejects_missing_requests_table_without_auto_create(
    repository_context: RepositoryContext,
) -> None:
    database = repository_context.cache_dir / "requests.sqlite"
    history = ("req_outcome_unknown", "a" * 64, "outcome_unknown")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE requests_v1_archive (
                request_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                state TEXT NOT NULL
            )
            """
        )
        connection.execute("INSERT INTO requests_v1_archive VALUES (?, ?, ?)", history)
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RequestStateError, match="^request_store_schema_invalid$"):
        LocalRequestStore(repository_context)

    connection = sqlite3.connect(database)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        retained = connection.execute(
            "SELECT request_id, payload_hash, state FROM requests_v1_archive"
        ).fetchall()
    finally:
        connection.close()

    assert version == 2
    assert "requests" not in tables
    assert retained == [history]


@pytest.mark.parametrize(
    "columns",
    [
        _V2_REQUEST_COLUMNS.replace(
            "payload_hash TEXT NOT NULL", "payload_hash BLOB NOT NULL"
        ),
        _V2_REQUEST_COLUMNS.replace(
            "payload_hash TEXT NOT NULL", "payload_hash VARCHAR(64) NOT NULL"
        ),
        _V2_REQUEST_COLUMNS.replace("expires_at TEXT NOT NULL", "expires_at TEXT"),
        _V2_REQUEST_COLUMNS.replace(
            "state TEXT NOT NULL",
            "state TEXT NOT NULL DEFAULT 'awaiting_confirmation'",
        ),
        _V2_REQUEST_COLUMNS.replace("request_id TEXT PRIMARY KEY", "request_id TEXT NOT NULL"),
        _V2_REQUEST_COLUMNS.replace(
            "request_id TEXT PRIMARY KEY,\n    payload_hash TEXT NOT NULL",
            "request_id TEXT NOT NULL,\n    payload_hash TEXT PRIMARY KEY",
        ),
        _V2_REQUEST_COLUMNS
        + ",\n    archived_hash TEXT GENERATED ALWAYS AS (payload_hash) VIRTUAL",
    ],
    ids=(
        "wrong_affinity",
        "wrong_declared_type",
        "missing_required_not_null",
        "unexpected_default",
        "missing_request_id_primary_key",
        "request_id_primary_key_position",
        "hidden_generated_column",
    ),
)
def test_v2_initialization_rejects_malformed_request_table_contract(
    repository_context: RepositoryContext,
    columns: str,
) -> None:
    seed_v2_request_store(repository_context, columns=columns)

    with pytest.raises(RequestStateError, match="^request_store_schema_invalid$"):
        LocalRequestStore(repository_context)


def test_v2_initialization_rejects_renamed_request_column(
    repository_context: RepositoryContext,
) -> None:
    seed_v2_request_store(
        repository_context,
        columns=_V2_REQUEST_COLUMNS.replace("payload_hash", "payload_digest", 1),
    )

    with pytest.raises(RequestStateError, match="^request_store_schema_invalid$"):
        LocalRequestStore(repository_context)


def test_v2_initialization_rejects_table_that_allows_duplicate_request_ids(
    repository_context: RepositoryContext,
) -> None:
    database = seed_v2_request_store(
        repository_context,
        columns=_V2_REQUEST_COLUMNS.replace(
            "request_id TEXT PRIMARY KEY", "request_id TEXT NOT NULL"
        ),
    )
    connection = sqlite3.connect(database)
    try:
        row = (
            "a" * 64,
            "flowaccount",
            "production",
            "executing",
            "2026-07-20T00:00:00+00:00",
            "{}",
        )
        connection.execute(
            "INSERT INTO requests VALUES ('req_duplicate_identity', ?, ?, ?, ?, ?, ?)",
            row,
        )
        connection.execute(
            "INSERT INTO requests VALUES ('req_duplicate_identity', ?, ?, ?, ?, ?, ?)",
            row,
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RequestStateError, match="^request_store_schema_invalid$"):
        LocalRequestStore(repository_context)


def test_v2_initialization_rejects_request_id_on_conflict_replace_before_use(
    repository_context: RepositoryContext,
) -> None:
    ddl = "CREATE TABLE requests (" + _V2_REQUEST_COLUMNS.replace(
        "request_id TEXT PRIMARY KEY",
        "request_id TEXT PRIMARY KEY ON CONFLICT REPLACE",
    ) + ")"
    database = seed_v2_request_store_ddl(repository_context, ddl=ddl)
    history = (
        "req_outcome_unknown",
        "f" * 64,
        "flowaccount",
        "production",
        "outcome_unknown",
        "2026-07-20T00:00:00+00:00",
        '{"state":"outcome_unknown"}',
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?)", history)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RequestStateError, match="^request_store_schema_invalid$"):
        LocalRequestStore(repository_context)

    connection = sqlite3.connect(database)
    try:
        retained = connection.execute(
            "SELECT request_id, payload_hash, state, request_json FROM requests"
        ).fetchall()
    finally:
        connection.close()
    assert retained == [(history[0], history[1], history[4], history[6])]


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE requests ("
        + _V2_REQUEST_COLUMNS.replace(
            "request_id TEXT PRIMARY KEY",
            "request_id TEXT PRIMARY KEY ON CONFLICT IGNORE",
        )
        + ")",
        "CREATE TABLE requests ("
        + _V2_REQUEST_COLUMNS.replace(
            "request_json TEXT NOT NULL",
            "request_json TEXT NOT NULL ON CONFLICT REPLACE",
        )
        + ")",
        "CREATE TABLE requests ("
        + _V2_REQUEST_COLUMNS
        + ", UNIQUE (payload_hash) ON CONFLICT REPLACE)",
        "CREATE TABLE requests ("
        + _V2_REQUEST_COLUMNS
        + ", CHECK (length(request_id) > 0))",
        "CREATE TABLE requests (" + _V2_REQUEST_COLUMNS + ") STRICT",
        "CREATE TABLE requests (" + _V2_REQUEST_COLUMNS + ") WITHOUT ROWID",
        "CREATE TABLE requests ("
        + _V2_REQUEST_COLUMNS.replace(
            "request_json TEXT NOT NULL",
            "request_json TEXT COLLATE NOCASE NOT NULL",
        )
        + ")",
    ],
    ids=(
        "primary_key_conflict_ignore",
        "not_null_conflict_replace",
        "extra_unique_constraint",
        "extra_check_constraint",
        "strict_table_option",
        "without_rowid_table_option",
        "unexpected_collation",
    ),
)
def test_v2_initialization_rejects_noncanonical_table_ddl(
    repository_context: RepositoryContext,
    ddl: str,
) -> None:
    seed_v2_request_store_ddl(repository_context, ddl=ddl)

    with pytest.raises(RequestStateError, match="^request_store_schema_invalid$"):
        LocalRequestStore(repository_context)


def test_v2_initialization_rejects_trigger_that_can_replace_request_history(
    repository_context: RepositoryContext,
) -> None:
    database = seed_v2_request_store_ddl(
        repository_context,
        ddl="CREATE TABLE requests (" + _V2_REQUEST_COLUMNS + ")",
    )
    history = (
        "req_outcome_unknown",
        "e" * 64,
        "flowaccount",
        "production",
        "outcome_unknown",
        "2026-07-20T00:00:00+00:00",
        '{"state":"outcome_unknown"}',
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?)", history)
        connection.execute(
            """
            CREATE TRIGGER requests_replace_identity
            BEFORE INSERT ON requests
            WHEN EXISTS (
                SELECT 1 FROM requests WHERE request_id = NEW.request_id
            )
            BEGIN
                DELETE FROM requests WHERE request_id = NEW.request_id;
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RequestStateError, match="^request_store_schema_invalid$"):
        LocalRequestStore(repository_context)

    connection = sqlite3.connect(database)
    try:
        retained = connection.execute(
            "SELECT request_id, payload_hash, state, request_json FROM requests"
        ).fetchall()
    finally:
        connection.close()
    assert retained == [(history[0], history[1], history[4], history[6])]


@pytest.mark.parametrize(
    "ddl",
    [
        """
            CREATE TABLE REQUESTS (
                REQUEST_ID TEXT PRIMARY KEY,
                PAYLOAD_HASH TEXT NOT NULL,
                CONNECTOR_ID TEXT NOT NULL,
                ENVIRONMENT TEXT NOT NULL,
                STATE TEXT NOT NULL,
                EXPIRES_AT TEXT NOT NULL,
                REQUEST_JSON TEXT NOT NULL
            )
        """,
        """
            CrEaTe TaBlE [ReQuEsTs](
                `ReQuEsT_Id` text primary key,
                [PaYlOaD_hAsH] TeXt not null,
                "CoNnEcToR_iD" TEXT NOT NULL,
                EnViRoNmEnT TEXT NOT NULL,
                StAtE TEXT NOT NULL,
                ExPiReS_aT TEXT NOT NULL,
                ReQuEsT_jSoN TEXT NOT NULL
            )
        """,
    ],
    ids=("uppercase_identifiers", "mixed_case_identifiers"),
)
def test_v2_initialization_accepts_semantically_canonical_identifier_case(
    repository_context: RepositoryContext,
    ddl: str,
) -> None:
    database = seed_v2_request_store_ddl(
        repository_context,
        ddl=ddl,
    )

    first = LocalRequestStore(repository_context)
    second = LocalRequestStore(repository_context)

    assert first.database_path == database
    assert second.database_path == database


def test_v1_migration_archives_approvals_without_making_them_executable(
    repository_context: RepositoryContext,
) -> None:
    request_id, payload_hash = seed_v1_request_store(repository_context)
    audit_path = repository_context.audit_dir / "audit.jsonl"
    historical_audit = '{"event":"confirmed","state":"ready_to_execute"}\n'
    audit_path.write_text(historical_audit)

    first = LocalRequestStore(repository_context)
    second = LocalRequestStore(repository_context)

    with pytest.raises(RequestStateError, match="^request_not_found$"):
        first.require_ready(request_id)
    first.assert_replay_allowed(payload_hash)
    connection = sqlite3.connect(first.database_path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        current_count = connection.execute("SELECT count(*) FROM requests").fetchone()[0]
        archived = connection.execute(
            "SELECT request_id, payload_hash, state FROM requests_v1_archive"
        ).fetchall()
        live_indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'requests'"
            )
        }
    finally:
        connection.close()

    assert second.database_path == first.database_path
    assert version == 2
    assert current_count == 0
    assert archived == [(request_id, payload_hash, "awaiting_final_confirmation")]
    assert "requests_v2_replay_blocking_hash" in live_indexes
    assert audit_path.read_text() == historical_audit


def test_v1_migration_archives_malformed_legacy_state_without_parsing_it(
    repository_context: RepositoryContext,
) -> None:
    request_id, _ = seed_v1_request_store(
        repository_context,
        state="malformed_legacy_state",
        request_json="not-json-but-historical",
    )

    store = LocalRequestStore(repository_context)

    with pytest.raises(RequestStateError, match="^request_not_found$"):
        store.get(request_id)
    connection = sqlite3.connect(store.database_path)
    try:
        archived = connection.execute(
            "SELECT state, request_json FROM requests_v1_archive WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        current_count = connection.execute("SELECT count(*) FROM requests").fetchone()[0]
    finally:
        connection.close()

    assert archived == ("malformed_legacy_state", "not-json-but-historical")
    assert current_count == 0


def test_v1_migration_preserves_preexisting_malformed_archive_with_new_name(
    repository_context: RepositoryContext,
) -> None:
    request_id, payload_hash = seed_v1_request_store(repository_context)
    connection = sqlite3.connect(repository_context.cache_dir / "requests.sqlite")
    try:
        connection.execute(
            "CREATE TABLE requests_v1_archive (legacy_marker TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO requests_v1_archive (legacy_marker) VALUES ('older-history')"
        )
        connection.commit()
    finally:
        connection.close()

    first = LocalRequestStore(repository_context)
    second = LocalRequestStore(repository_context)

    connection = sqlite3.connect(first.database_path)
    try:
        older = connection.execute(
            "SELECT legacy_marker FROM requests_v1_archive"
        ).fetchall()
        newer = connection.execute(
            "SELECT request_id, payload_hash FROM requests_v1_archive_2"
        ).fetchall()
        live_count = connection.execute("SELECT count(*) FROM requests").fetchone()[0]
    finally:
        connection.close()

    assert second.database_path == first.database_path
    assert older == [("older-history",)]
    assert newer == [(request_id, payload_hash)]
    assert live_count == 0


def test_v2_initialization_repairs_colliding_index_name_and_enforces_replay_block(
    repository_context: RepositoryContext,
) -> None:
    database = repository_context.cache_dir / "requests.sqlite"
    schema = """
        request_id TEXT PRIMARY KEY,
        payload_hash TEXT NOT NULL,
        connector_id TEXT NOT NULL,
        environment TEXT NOT NULL,
        state TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        request_json TEXT NOT NULL
    """
    connection = sqlite3.connect(database)
    try:
        connection.execute(f"CREATE TABLE requests ({schema})")
        connection.execute(f"CREATE TABLE requests_v1_archive ({schema})")
        connection.execute(
            """
            CREATE UNIQUE INDEX requests_v2_replay_blocking_hash
            ON requests_v1_archive (payload_hash)
            WHERE state IN ('executing', 'succeeded', 'outcome_unknown')
            """
        )
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()

    first = LocalRequestStore(repository_context)
    second = LocalRequestStore(repository_context)

    connection = sqlite3.connect(first.database_path)
    try:
        indexes = connection.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
        expiry = (datetime.now(UTC) + PREVIEW_TTL).isoformat()
        row = ("flowaccount", "production", "executing", expiry, "{}")
        connection.execute(
            """
            INSERT INTO requests (
                request_id, payload_hash, connector_id, environment,
                state, expires_at, request_json
            ) VALUES ('req_collision_one', ?, ?, ?, ?, ?, ?)
            """,
            ("c" * 64, *row),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO requests (
                    request_id, payload_hash, connector_id, environment,
                    state, expires_at, request_json
                ) VALUES ('req_collision_two', ?, ?, ?, ?, ?, ?)
                """,
                ("c" * 64, *row),
            )
    finally:
        connection.close()

    assert second.database_path == first.database_path
    assert any(
        name.startswith("requests_v2_replay_blocking_hash_")
        and table == "requests"
        and "WHERE" in sql
        and "outcome_unknown" in sql
        for name, table, sql in indexes
        if sql is not None
    )
    assert any(
        name == "requests_v2_replay_blocking_hash"
        and table == "requests_v1_archive"
        for name, table, _ in indexes
    )


def test_v2_initialization_replaces_malformed_live_replay_index_definition(
    repository_context: RepositoryContext,
) -> None:
    database = repository_context.cache_dir / "requests.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE requests (
                request_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                connector_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                state TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                request_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX requests_v2_replay_blocking_hash
            ON requests (payload_hash)
            """
        )
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()

    LocalRequestStore(repository_context)

    connection = sqlite3.connect(database)
    try:
        expiry = (datetime.now(UTC) + PREVIEW_TTL).isoformat()
        base = ("d" * 64, "flowaccount", "production", expiry, "{}")
        connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, 'awaiting_confirmation', ?, ?)",
            ("req_pending_one", *base),
        )
        connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, 'awaiting_confirmation', ?, ?)",
            ("req_pending_two", *base),
        )
        connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, 'executing', ?, ?)",
            ("req_executing_one", *base),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, 'executing', ?, ?)",
                ("req_executing_two", *base),
            )
    finally:
        connection.close()


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
    assert prepared_request.approval_level is ApprovalLevel.STANDARD
    assert prepared_request.mutation_class is MutationClass.CREATE
    assert prepared_request.approval_count == 0
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
        "approval_level": prepared_request.approval_level.value,
        "mutation_class": prepared_request.mutation_class.value,
        "credential_revision": _CREDENTIAL_REVISION,
        "preflight_actions": [],
    }
    serialized = prepared_request.model_dump(mode="json")
    assert "required_confirmations" not in serialized
    assert "confirmation_count" not in serialized
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
            risk=RiskDecision(
                RiskTier.HIGH_RISK,
                ApprovalLevel.ELEVATED,
                MutationClass.SENSITIVE,
                (),
            ),
        )


def test_from_template_requires_static_action_path_to_match_exactly(
    repository_context: RepositoryContext,
    catalog_action: CatalogAction,
) -> None:
    template = RequestTemplate(final_path="/payments/approve")
    payload_hash = canonical_payload_hash(
        binding_payload(
            repository_context,
            catalog_action,
            template,
            RiskDecision(
                RiskTier.STANDARD_WRITE,
                ApprovalLevel.STANDARD,
                MutationClass.CREATE,
                (),
            ),
        )
    )

    with pytest.raises(ValueError, match="^request_path_mismatch$"):
        make_prepared_request(
            repository_context,
            catalog_action,
            template=template,
            payload_hash=payload_hash,
        )


def test_from_template_renders_dynamic_action_path_deterministically(
    repository_context: RepositoryContext,
    action_factory: Any,
) -> None:
    action = action_factory(
        path_template="/invoices/{invoice_id}",
        input_schema={
            "path": {"invoice_id": {"type": "string"}},
            "query": {},
            "headers": {},
            "body": {"type": "object"},
            "files": {},
        },
    )
    template = RequestTemplate(
        final_path="/invoices/INV%20%E0%B9%91%20~",
        request_inputs={
            "path": {"invoice_id": "INV ๑ ~"},
            "body": {"amount": 1000},
        },
    )

    prepared = make_prepared_request(
        repository_context,
        action,
        template=template,
    )

    assert prepared.final_path == "/invoices/INV%20%E0%B9%91%20~"
    assert prepared.public_dict()["target"] == "/invoices/{invoice_id}"


def test_from_template_rejects_dynamic_final_path_mismatch(
    repository_context: RepositoryContext,
    action_factory: Any,
) -> None:
    action = action_factory(path_template="/invoices/{invoice_id}")
    template = RequestTemplate(
        final_path="/payments/approve",
        request_inputs={"path": {"invoice_id": "INV-1"}, "body": {}},
    )
    payload_hash = canonical_payload_hash(
        binding_payload(
            repository_context,
            action,
            template,
            RiskDecision(
                RiskTier.STANDARD_WRITE,
                ApprovalLevel.STANDARD,
                MutationClass.CREATE,
                (),
            ),
        )
    )

    with pytest.raises(ValueError, match="^request_path_mismatch$"):
        make_prepared_request(
            repository_context,
            action,
            template=template,
            payload_hash=payload_hash,
        )


@pytest.mark.parametrize(
    ("path_template", "path_parameters", "final_path"),
    [
        ("/invoices/{invoice_id}", {}, "/invoices/INV-1"),
        ("/invoices/{invoice_id}", {"invoice_id": "INV-1", "extra": "x"}, "/invoices/INV-1"),
        ("/invoices/{bad-name}", {"bad-name": "INV-1"}, "/invoices/INV-1"),
        ("/invoices/{invoice_id}/tail", {"invoice_id": ".."}, "/invoices/../tail"),
        ("/invoices/{invoice_id}", {"invoice_id": "%2fpayments"}, "/invoices/%252fpayments"),
        ("/invoices/{invoice_id}", {"invoice_id": "a\\b"}, "/invoices/a%5Cb"),
        ("/invoices/{invoice_id}", {"invoice_id": "x?admin=true"}, "/invoices/x%3Fadmin%3Dtrue"),
        ("/invoices/{invoice_id}", {"invoice_id": "x\x00y"}, "/invoices/x%00y"),
    ],
)
def test_from_template_rejects_invalid_dynamic_action_paths(
    repository_context: RepositoryContext,
    action_factory: Any,
    path_template: str,
    path_parameters: dict[str, Any],
    final_path: str,
) -> None:
    action = action_factory(
        path_template=path_template,
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object"},
            "files": {},
        },
    )
    template = RequestTemplate(
        final_path=final_path,
        request_inputs={"path": path_parameters, "body": {}},
    )
    payload_hash = canonical_payload_hash(
        binding_payload(
            repository_context,
            action,
            template,
            RiskDecision(
                RiskTier.STANDARD_WRITE,
                ApprovalLevel.STANDARD,
                MutationClass.CREATE,
                (),
            ),
        )
    )

    with pytest.raises(ValueError, match="^invalid_action_path$"):
        make_prepared_request(
            repository_context,
            action,
            template=template,
            payload_hash=payload_hash,
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
            risk=RiskDecision(
                RiskTier.STANDARD_WRITE,
                ApprovalLevel.STANDARD,
                MutationClass.CREATE,
                (),
            ),
        )

    raised = make_prepared_request(
        repository_context,
        action,
        risk=RiskDecision(
            RiskTier.HIGH_RISK,
            ApprovalLevel.ELEVATED,
            MutationClass.SENSITIVE,
            (),
        ),
    )
    assert raised.risk_tier is RiskTier.HIGH_RISK


@pytest.mark.parametrize(
    ("field", "replacement", "error_code"),
    [
        ("repository_id", "repo_changed", "payload_hash_mismatch"),
        ("connector_id", "peak", "payload_hash_mismatch"),
        ("environment", "sandbox", "payload_hash_mismatch"),
        ("action_id", "act_changed", "payload_hash_mismatch"),
        ("version_id", "ver_changed", "payload_hash_mismatch"),
        ("method", "PATCH", "payload_hash_mismatch"),
        ("final_path", "/changed", "request_path_mismatch"),
        ("request_inputs", {"body": {"amount": 9999}}, "payload_hash_mismatch"),
        ("risk_tier", RiskTier.HIGH_RISK, "payload_hash_mismatch"),
        ("approval_level", ApprovalLevel.ELEVATED, "payload_hash_mismatch"),
        ("mutation_class", MutationClass.SENSITIVE, "payload_hash_mismatch"),
        ("credential_revision", "e" * 64, "payload_hash_mismatch"),
        (
            "preflight_actions",
            [
                {
                    "action_id": "act_preflight",
                    "version_id": "av_preflight",
                    "connector_id": "flowaccount",
                    "method": "GET",
                    "path_template": "/invoices",
                }
            ],
            "payload_hash_mismatch",
        ),
    ],
)
def test_model_validation_rejects_changed_binding_field(
    prepared_request: PreparedRequest,
    field: str,
    replacement: Any,
    error_code: str,
) -> None:
    payload = prepared_request.model_dump(mode="json")
    payload[field] = replacement
    if field == "approval_level":
        payload["mutation_class"] = MutationClass.SENSITIVE.value
    if field == "mutation_class":
        payload["approval_level"] = ApprovalLevel.ELEVATED.value

    with pytest.raises(ValidationError, match=error_code):
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
    assert "credential_revision" not in public
    assert "preflight_actions" not in public


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


def test_public_summary_uses_fixed_preview_and_response_keys_at_every_depth(
    prepared_request: PreparedRequest,
) -> None:
    request = prepared_request.model_copy(
        update={
            "sanitized_summary": {
                "body": {
                    "document_type": "invoice",
                    "adaLovelace": "present",
                    "cus_abcdef": "present",
                },
                "adaLovelace": {"document_type": "invoice"},
            },
            "response_summary": {
                "status_class": "2xx",
                "result": {
                    "status": "created",
                    "adaLovelace": "present",
                    "cus_abcdef": "present",
                },
                "items": [
                    {
                        "status": "created",
                        "body": {"document_type": "must-not-cross-allowlists"},
                    }
                ],
                "body": {"document_type": "must-not-cross-allowlists"},
            },
        }
    )

    public = request.public_dict()

    assert public["sanitized_summary"] == {
        "body": {"document_type": "[REDACTED]"}
    }
    assert public["response_summary"] == {
        "status_class": "[REDACTED]",
        "result": {"status": "[REDACTED]"},
        "items": [{"status": "[REDACTED]"}],
    }
    assert "adaLovelace" not in str(public)
    assert "cus_abcdef" not in str(public)


def test_first_valid_approval_moves_high_risk_request_to_ready(
    request_store: LocalRequestStore,
    repository_context: RepositoryContext,
    action_factory: Any,
) -> None:
    action = action_factory(
        side_effects=("void_document",),
        risk_tier=RiskTier.HIGH_RISK,
        required_confirmations=2,
    )
    prepared = make_prepared_request(
        repository_context,
        action,
        risk=RiskDecision(
            RiskTier.HIGH_RISK,
            ApprovalLevel.ELEVATED,
            MutationClass.SENSITIVE,
            ("sensitive_side_effect",),
        ),
    )
    request = request_store.create_preview(prepared, action=action)

    approved = request_store.approve(
        request.request_id,
        request.payload_hash,
        MutationClass.SENSITIVE,
    )

    assert approved.state is RequestState.READY_TO_EXECUTE
    assert approved.approval_count == 1
    assert "awaiting_final_confirmation" not in {state.value for state in RequestState}


def test_approval_rejects_wrong_expected_mutation_class_without_state_change(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)

    with pytest.raises(RequestStateError, match="^mutation_class_mismatch$"):
        request_store.approve(
            request.request_id,
            request.payload_hash,
            MutationClass.UPDATE,
        )

    stored = request_store.get(request.request_id)
    assert stored.state is RequestState.AWAITING_CONFIRMATION
    assert stored.approval_count == 0


def test_precheck_approval_uses_persisted_state_without_recording_approval(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)

    checked = request_store.precheck_approval(
        request.request_id,
        request.payload_hash,
        MutationClass.CREATE,
    )

    assert checked.request_id == request.request_id
    assert checked.state is RequestState.AWAITING_CONFIRMATION
    assert checked.approval_count == 0
    stored = request_store.get(request.request_id)
    assert stored.state is RequestState.AWAITING_CONFIRMATION
    assert stored.approval_count == 0


def test_repeated_approval_is_rejected_and_remains_single(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)
    request_store.approve(request.request_id, request.payload_hash, request.mutation_class)

    with pytest.raises(RequestStateError, match="^invalid_request_state$"):
        request_store.approve(request.request_id, request.payload_hash, request.mutation_class)

    stored = request_store.get(request.request_id)
    assert stored.state is RequestState.READY_TO_EXECUTE
    assert stored.approval_count == 1


def test_concurrent_approval_records_exactly_one_transition(
    repository_context: RepositoryContext,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    creator = LocalRequestStore(repository_context)
    request = creator.create_preview(prepared_request, action=catalog_action)
    stores = (LocalRequestStore(repository_context), LocalRequestStore(repository_context))

    def approve(store: LocalRequestStore) -> str:
        try:
            store.approve(request.request_id, request.payload_hash, request.mutation_class)
        except RequestStateError as exc:
            return str(exc)
        return "approved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(approve, stores))

    assert sorted(outcomes) == ["approved", "invalid_request_state"]
    stored = creator.get(request.request_id)
    assert stored.state is RequestState.READY_TO_EXECUTE
    assert stored.approval_count == 1


@pytest.mark.parametrize("approval_count", [-1, 2, True])
def test_prepared_request_rejects_non_literal_approval_count(
    prepared_request: PreparedRequest,
    approval_count: object,
) -> None:
    payload = prepared_request.model_dump(mode="json")
    payload["approval_count"] = approval_count

    with pytest.raises(ValidationError, match="invalid_approval_count"):
        PreparedRequest.model_validate(payload)


def test_create_preview_transitions_previewed_to_awaiting_in_transaction(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    assert prepared_request.state is RequestState.PREVIEWED

    created = request_store.create_preview(prepared_request, action=catalog_action)

    assert created.state is RequestState.AWAITING_CONFIRMATION
    assert request_store.get(created.request_id).state is RequestState.AWAITING_CONFIRMATION


def test_create_preview_requires_catalog_action_provenance(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
) -> None:
    with pytest.raises(TypeError):
        request_store.create_preview(prepared_request)


def test_create_preview_rejects_self_consistent_forged_catalog_binding(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    forged = rebind_request(
        prepared_request,
        action_id="act_nonexistent",
        version_id="av_nonexistent",
        method="DELETE",
        path_template="/admin/delete-all",
        final_path="/admin/delete-all",
        risk_tier=RiskTier.HIGH_RISK,
        approval_level=ApprovalLevel.ELEVATED,
        mutation_class=MutationClass.SENSITIVE,
    )

    with pytest.raises(RequestStateError, match="^catalog_binding_mismatch$"):
        request_store.create_preview(forged, action=catalog_action)


def test_create_preview_recomputes_exact_effective_catalog_risk(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    forged = rebind_request(
        prepared_request,
        risk_tier=RiskTier.HIGH_RISK,
    )

    with pytest.raises(RequestStateError, match="^catalog_risk_mismatch$"):
        request_store.create_preview(forged, action=catalog_action)


def test_prepared_state_graph_rejects_invalid_previewed_fields(
    prepared_request: PreparedRequest,
) -> None:
    payload = prepared_request.model_dump(mode="json")
    payload.update({"approval_count": 1, "state": "previewed"})

    with pytest.raises(ValidationError, match="invalid_request_state"):
        PreparedRequest.model_validate(payload)


def test_payload_change_invalidates_approval(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)
    changed_payload = dict(request.binding_payload)
    changed_payload["request_inputs"] = {"body": {"amount": 9999}}
    changed_hash = canonical_payload_hash(changed_payload)

    with pytest.raises(RequestStateError, match="^payload_hash_mismatch$"):
        request_store.approve(
            request.request_id,
            changed_hash,
            request.mutation_class,
        )

    assert request_store.get(request.request_id).approval_count == 0


def test_expired_request_is_invalidated_before_confirmation(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    created_at = datetime.now(UTC) - PREVIEW_TTL - timedelta(seconds=1)
    expired = prepared_request.model_copy(
        update={"created_at": created_at, "expires_at": created_at + PREVIEW_TTL}
    )
    request = request_store.create_preview(expired, action=catalog_action)

    with pytest.raises(RequestStateError, match="^preview_expired$"):
        request_store.approve(
            request.request_id,
            request.payload_hash,
            request.mutation_class,
        )

    stored = request_store.get(request.request_id)
    assert stored.state is RequestState.FAILED
    assert stored.failure_reason == "preview_expired"


def test_outcome_unknown_blocks_same_hash(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)
    request_store.approve(
        request.request_id,
        request.payload_hash,
        request.mutation_class,
    )
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
    catalog_action: CatalogAction,
) -> None:
    first = request_store.create_preview(prepared_request, action=catalog_action)
    second = request_store.create_preview(
        prepared_request.model_copy(update={"request_id": "req_second_preview"}),
        action=catalog_action,
    )
    request_store.approve(first.request_id, first.payload_hash, first.mutation_class)
    request_store.approve(second.request_id, second.payload_hash, second.mutation_class)
    request_store.start_execution(first.request_id)

    with pytest.raises(RequestStateError, match="^replay_blocked_active_request$"):
        request_store.start_execution(second.request_id)


def test_credential_clear_invalidates_matching_pending_previews(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)

    assert request_store.invalidate_pending("flowaccount", "production") == 1
    with pytest.raises(RequestStateError, match="^credentials_cleared$"):
        request_store.require_ready(request.request_id)


def test_store_rejects_tampered_json_without_echoing_request_inputs(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)
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
        ("approval_level", ApprovalLevel.ELEVATED, None),
        ("mutation_class", MutationClass.SENSITIVE, None),
        ("credential_revision", "e" * 64, None),
        (
            "preflight_actions",
            [
                {
                    "action_id": "act_preflight",
                    "version_id": "av_preflight",
                    "connector_id": "flowaccount",
                    "method": "GET",
                    "path_template": "/invoices",
                }
            ],
            None,
        ),
    ],
)
def test_store_rejects_coordinated_json_and_column_binding_tampering(
    request_store: LocalRequestStore,
    prepared_request: PreparedRequest,
    catalog_action: CatalogAction,
    field: str,
    replacement: Any,
    column: str | None,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)
    payload = request.model_dump(mode="json")
    payload[field] = replacement
    if field == "approval_level":
        payload["mutation_class"] = MutationClass.SENSITIVE.value
    if field == "mutation_class":
        payload["approval_level"] = ApprovalLevel.ELEVATED.value
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
    catalog_action: CatalogAction,
) -> None:
    request = request_store.create_preview(prepared_request, action=catalog_action)
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
