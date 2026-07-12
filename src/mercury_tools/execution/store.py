"""SQLite-backed local state machine for ERP write previews."""

from __future__ import annotations

import json
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mercury_tools.execution.models import PreparedRequest, RequestState
from mercury_tools.local.repository import RepositoryContext

_PENDING_STATES = (
    RequestState.PREVIEWED.value,
    RequestState.AWAITING_CONFIRMATION.value,
    RequestState.AWAITING_FINAL_CONFIRMATION.value,
    RequestState.READY_TO_EXECUTE.value,
)
_REPLAY_BLOCKING_STATES = (
    RequestState.EXECUTING.value,
    RequestState.SUCCEEDED.value,
    RequestState.OUTCOME_UNKNOWN.value,
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RequestStateError(ValueError):
    """A stable, payload-free error code for local request state failures."""


class LocalRequestStore:
    """Repository-scoped preview state with serialized write transitions."""

    def __init__(self, context: RepositoryContext) -> None:
        if not isinstance(context, RepositoryContext):
            raise ValueError("invalid_repository_context")
        self._context = context
        self._database = context.cache_dir / "requests.sqlite"
        self._validate_storage_path()
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database

    def create_preview(self, prepared: PreparedRequest) -> PreparedRequest:
        request = self._validated_request(prepared)
        if request.repository_id != self._context.repository_id:
            raise RequestStateError("repository_mismatch")
        if (
            request.state is not RequestState.AWAITING_CONFIRMATION
            or request.confirmation_count != 0
            or request.failure_reason is not None
            or request.response_summary
        ):
            raise RequestStateError("invalid_initial_request_state")
        with self._immediate_transaction() as connection:
            self._assert_replay_allowed(connection, request.payload_hash)
            try:
                connection.execute(
                    """
                    INSERT INTO requests (
                        request_id, payload_hash, connector_id, environment,
                        state, expires_at, request_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.request_id,
                        request.payload_hash,
                        request.connector_id,
                        request.environment,
                        request.state.value,
                        request.expires_at.isoformat(),
                        self._serialized_request(request),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RequestStateError("request_already_exists") from exc
        return request

    def get(self, request_id: str) -> PreparedRequest:
        with self._immediate_transaction() as connection:
            request = self._fetch(connection, request_id)
            return self._expire_if_needed(connection, request)

    def confirm(self, request_id: str, payload_hash: str) -> PreparedRequest:
        with self._immediate_transaction() as connection:
            request = self._fetch(connection, request_id)
            if payload_hash != request.payload_hash:
                raise RequestStateError("payload_hash_mismatch")
            request = self._expire_if_needed(connection, request)
            self._require_state(
                request,
                RequestState.AWAITING_CONFIRMATION,
                RequestState.AWAITING_FINAL_CONFIRMATION,
            )
            confirmation_count = request.confirmation_count + 1
            state = (
                RequestState.READY_TO_EXECUTE
                if confirmation_count == request.required_confirmations
                else RequestState.AWAITING_FINAL_CONFIRMATION
            )
            updated = self._updated(
                request,
                state=state,
                confirmation_count=confirmation_count,
            )
            self._store(connection, updated)
            return updated

    def invalidate(self, request_id: str, reason: str) -> PreparedRequest:
        if _REASON.fullmatch(reason) is None:
            raise RequestStateError("invalid_invalidation_reason")
        with self._immediate_transaction() as connection:
            request = self._expire_if_needed(connection, self._fetch(connection, request_id))
            self._require_state_values(request, _PENDING_STATES)
            updated = self._updated(
                request,
                state=RequestState.FAILED,
                failure_reason=reason,
            )
            self._store(connection, updated)
            return updated

    def require_ready(self, request_id: str) -> PreparedRequest:
        with self._immediate_transaction() as connection:
            request = self._expire_if_needed(connection, self._fetch(connection, request_id))
            self._require_state(request, RequestState.READY_TO_EXECUTE)
            return request

    def fail_before_dispatch(
        self,
        request_id: str,
        reason: str = "pre_dispatch_failed",
        response_summary: dict[str, Any] | None = None,
    ) -> PreparedRequest:
        if _REASON.fullmatch(reason) is None:
            raise RequestStateError("invalid_failure_reason")
        with self._immediate_transaction() as connection:
            request = self._expire_if_needed(connection, self._fetch(connection, request_id))
            self._require_state(request, RequestState.READY_TO_EXECUTE)
            updated = self._updated(
                request,
                state=RequestState.FAILED,
                failure_reason=reason,
                response_summary=response_summary or {},
            )
            self._store(connection, updated)
            return updated

    def start_execution(self, request_id: str) -> PreparedRequest:
        with self._immediate_transaction() as connection:
            request = self._expire_if_needed(connection, self._fetch(connection, request_id))
            self._require_state(request, RequestState.READY_TO_EXECUTE)
            self._assert_replay_allowed(
                connection,
                request.payload_hash,
                excluding_request_id=request.request_id,
            )
            updated = self._updated(request, state=RequestState.EXECUTING)
            try:
                self._store(connection, updated)
            except sqlite3.IntegrityError as exc:
                raise self._replay_error(connection, request.payload_hash) from exc
            return updated

    def complete(
        self,
        request_id: str,
        outcome: str,
        response_summary: dict[str, Any] | None = None,
    ) -> PreparedRequest:
        states = {
            "succeeded": RequestState.SUCCEEDED,
            "failed": RequestState.FAILED,
            "outcome_unknown": RequestState.OUTCOME_UNKNOWN,
        }
        state = states.get(outcome)
        if state is None:
            raise RequestStateError("invalid_completion_outcome")
        with self._immediate_transaction() as connection:
            request = self._fetch(connection, request_id)
            self._require_state(request, RequestState.EXECUTING)
            failure_reason = (
                "execution_failed"
                if state is RequestState.FAILED
                else "outcome_unknown"
                if state is RequestState.OUTCOME_UNKNOWN
                else None
            )
            updated = self._updated(
                request,
                state=state,
                failure_reason=failure_reason,
                response_summary=response_summary or {},
            )
            self._store(connection, updated)
            return updated

    def invalidate_pending(
        self,
        connector_id: str | None = None,
        environment: str | None = None,
        reason: str = "credentials_cleared",
    ) -> int:
        if connector_id is not None and _IDENTIFIER.fullmatch(connector_id) is None:
            raise RequestStateError("invalid_invalidation_scope")
        if environment is not None and _IDENTIFIER.fullmatch(environment) is None:
            raise RequestStateError("invalid_invalidation_scope")
        if _REASON.fullmatch(reason) is None:
            raise RequestStateError("invalid_invalidation_reason")
        with self._immediate_transaction() as connection:
            self._expire_pending(connection)
            clauses = ["state IN (?, ?, ?, ?)"]
            parameters: list[str] = list(_PENDING_STATES)
            if connector_id is not None:
                clauses.append("connector_id = ?")
                parameters.append(connector_id)
            if environment is not None:
                clauses.append("environment = ?")
                parameters.append(environment)
            rows = connection.execute(
                "SELECT * FROM requests WHERE " + " AND ".join(clauses),
                parameters,
            ).fetchall()
            for row in rows:
                request = self._row_to_request(row)
                self._store(
                    connection,
                    self._updated(
                        request,
                        state=RequestState.FAILED,
                        failure_reason=reason,
                    ),
                )
            return len(rows)

    def assert_replay_allowed(self, payload_hash: str) -> None:
        if not _payload_hash(payload_hash):
            raise RequestStateError("invalid_payload_hash")
        connection = self._connect()
        try:
            self._assert_replay_allowed(connection, payload_hash)
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
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
                CREATE UNIQUE INDEX IF NOT EXISTS requests_replay_blocking_hash
                ON requests (payload_hash)
                WHERE state IN ('executing', 'succeeded', 'outcome_unknown')
                """
            )
        except sqlite3.Error as exc:
            raise RequestStateError("request_store_unavailable") from exc
        finally:
            connection.close()

    @contextmanager
    def _immediate_transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except RequestStateError:
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise RequestStateError("request_store_unavailable") from exc
        except Exception:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        self._validate_storage_path()
        try:
            connection = sqlite3.connect(
                self._database,
                isolation_level=None,
                timeout=5,
            )
        except sqlite3.Error as exc:
            raise RequestStateError("request_store_unavailable") from exc
        connection.row_factory = sqlite3.Row
        return connection

    def _validate_storage_path(self) -> None:
        root = self._context.root
        expected_mercury = root / ".mercury"
        expected_cache = expected_mercury / "cache"
        if (
            self._context.mercury_dir != expected_mercury
            or self._context.cache_dir != expected_cache
        ):
            raise ValueError("invalid_request_store_path")
        for path in (root, expected_mercury, expected_cache):
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise ValueError("invalid_request_store_path") from exc
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ValueError("invalid_request_store_path")
        try:
            mode = self._database.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("invalid_request_store_path") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError("invalid_request_store_path")

    def _fetch(self, connection: sqlite3.Connection, request_id: str) -> PreparedRequest:
        if not isinstance(request_id, str):
            raise RequestStateError("request_not_found")
        row = connection.execute(
            "SELECT * FROM requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise RequestStateError("request_not_found")
        return self._row_to_request(row)

    def _row_to_request(self, row: sqlite3.Row) -> PreparedRequest:
        try:
            payload = json.loads(row["request_json"])
            request = PreparedRequest.model_validate(payload)
            expected = {
                "request_id": request.request_id,
                "payload_hash": request.payload_hash,
                "connector_id": request.connector_id,
                "environment": request.environment,
                "state": request.state.value,
                "expires_at": request.expires_at.isoformat(),
            }
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError("stored_request_column_mismatch")
            return request
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
            raise RequestStateError("invalid_stored_request") from None

    def _updated(self, request: PreparedRequest, **updates: Any) -> PreparedRequest:
        payload = request.model_dump(mode="json")
        payload.update(updates)
        try:
            return PreparedRequest.model_validate(payload)
        except (TypeError, ValueError, ValidationError) as exc:
            raise RequestStateError("invalid_stored_request") from exc

    def _store(self, connection: sqlite3.Connection, request: PreparedRequest) -> None:
        connection.execute(
            """
            UPDATE requests
            SET payload_hash = ?, connector_id = ?, environment = ?, state = ?,
                expires_at = ?, request_json = ?
            WHERE request_id = ?
            """,
            (
                request.payload_hash,
                request.connector_id,
                request.environment,
                request.state.value,
                request.expires_at.isoformat(),
                self._serialized_request(request),
                request.request_id,
            ),
        )

    def _serialized_request(self, request: PreparedRequest) -> str:
        try:
            return json.dumps(
                request.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise RequestStateError("invalid_stored_request") from exc

    def _expire_if_needed(
        self,
        connection: sqlite3.Connection,
        request: PreparedRequest,
    ) -> PreparedRequest:
        if (
            request.state.value in _PENDING_STATES
            and request.expires_at <= datetime.now(UTC)
        ):
            expired = self._updated(
                request,
                state=RequestState.FAILED,
                failure_reason="preview_expired",
            )
            self._store(connection, expired)
            return expired
        return request

    def _expire_pending(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT * FROM requests WHERE state IN (?, ?, ?, ?)", _PENDING_STATES
        ).fetchall()
        for row in rows:
            self._expire_if_needed(connection, self._row_to_request(row))

    def _assert_replay_allowed(
        self,
        connection: sqlite3.Connection,
        payload_hash: str,
        *,
        excluding_request_id: str | None = None,
    ) -> None:
        if not _payload_hash(payload_hash):
            raise RequestStateError("invalid_payload_hash")
        query = "SELECT state FROM requests WHERE payload_hash = ? AND state IN (?, ?, ?)"
        parameters: list[str] = [payload_hash, *_REPLAY_BLOCKING_STATES]
        if excluding_request_id is not None:
            query += " AND request_id != ?"
            parameters.append(excluding_request_id)
        row = connection.execute(query, parameters).fetchone()
        if row is not None:
            raise self._replay_error(connection, payload_hash, state=row["state"])

    def _replay_error(
        self,
        connection: sqlite3.Connection,
        payload_hash: str,
        *,
        state: str | None = None,
    ) -> RequestStateError:
        current_state = state
        if current_state is None:
            row = connection.execute(
                "SELECT state FROM requests WHERE payload_hash = ? AND state IN (?, ?, ?)",
                (payload_hash, *_REPLAY_BLOCKING_STATES),
            ).fetchone()
            current_state = row["state"] if row is not None else None
        if current_state == RequestState.OUTCOME_UNKNOWN.value:
            return RequestStateError("replay_blocked_outcome_unknown")
        return RequestStateError("replay_blocked_active_request")

    def _validated_request(self, request: PreparedRequest) -> PreparedRequest:
        if not isinstance(request, PreparedRequest):
            raise RequestStateError("invalid_prepared_request")
        try:
            return PreparedRequest.model_validate(request.model_dump(mode="json"))
        except (TypeError, ValueError, ValidationError) as exc:
            raise RequestStateError("invalid_prepared_request") from exc

    @staticmethod
    def _require_state(request: PreparedRequest, *states: RequestState) -> None:
        if request.state not in states:
            raise RequestStateError(request.failure_reason or "invalid_request_state")

    @staticmethod
    def _require_state_values(request: PreparedRequest, states: tuple[str, ...]) -> None:
        if request.state.value not in states:
            raise RequestStateError(request.failure_reason or "invalid_request_state")

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        with suppress(sqlite3.Error):
            connection.execute("ROLLBACK")


def _payload_hash(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))
