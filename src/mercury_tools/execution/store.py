"""SQLite-backed local state machine for ERP write previews."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.execution.models import PreparedRequest, RequestState, render_action_path
from mercury_tools.execution.policy import MutationClass, effective_risk
from mercury_tools.local.operation_lock import repository_locked, repository_operation_lock
from mercury_tools.local.repository import RepositoryContext

_PENDING_STATES = (
    RequestState.PREVIEWED.value,
    RequestState.AWAITING_CONFIRMATION.value,
    RequestState.READY_TO_EXECUTE.value,
)
_REPLAY_BLOCKING_STATES = (
    RequestState.EXECUTING.value,
    RequestState.SUCCEEDED.value,
    RequestState.OUTCOME_UNKNOWN.value,
)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_SQLITE_SCHEMA_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DATABASE_NAME = "requests.sqlite"
_SIDECAR_NAMES = ("requests.sqlite-wal", "requests.sqlite-shm")
_SCHEMA_VERSION = 2
_V1_ARCHIVE_NAME = "requests_v1_archive"
_REPLAY_INDEX_NAME = "requests_v2_replay_blocking_hash"
_REPLAY_INDEX_PATTERN = re.compile(
    r"^create unique index [a-z0-9_]+ on requests\s*\(\s*payload_hash\s*\)\s*"
    r"where state in\s*\(\s*'executing'\s*,\s*'succeeded'\s*,\s*"
    r"'outcome_unknown'\s*\)\s*$",
    re.IGNORECASE,
)
_SQLITE_DDL_TOKEN_PATTERN = re.compile(
    r"""
    (?P<whitespace>\s+)
    |(?P<line_comment>--[^\r\n]*(?:\r\n|\r|\n|$))
    |(?P<block_comment>/\*.*?\*/)
    |(?P<quoted_identifier>"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])
    |(?P<word>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<punctuation>[(),;])
    """,
    re.DOTALL | re.VERBOSE,
)
_V2_REQUEST_TABLE_CONTRACT = (
    (0, "request_id", "TEXT", "TEXT", False, None, 1),
    (1, "payload_hash", "TEXT", "TEXT", True, None, 0),
    (2, "connector_id", "TEXT", "TEXT", True, None, 0),
    (3, "environment", "TEXT", "TEXT", True, None, 0),
    (4, "state", "TEXT", "TEXT", True, None, 0),
    (5, "expires_at", "TEXT", "TEXT", True, None, 0),
    (6, "request_json", "TEXT", "TEXT", True, None, 0),
)
_V2_REQUEST_TABLE_XINFO_CONTRACT = tuple(
    (*column, 0) for column in _V2_REQUEST_TABLE_CONTRACT
)
_V2_REQUEST_TABLE_DDL_CONTRACT = (
    ("keyword", "create"),
    ("keyword", "table"),
    ("identifier", "requests"),
    ("punctuation", "("),
    ("identifier", "request_id"),
    ("keyword", "text"),
    ("keyword", "primary"),
    ("keyword", "key"),
    ("punctuation", ","),
    ("identifier", "payload_hash"),
    ("keyword", "text"),
    ("keyword", "not"),
    ("keyword", "null"),
    ("punctuation", ","),
    ("identifier", "connector_id"),
    ("keyword", "text"),
    ("keyword", "not"),
    ("keyword", "null"),
    ("punctuation", ","),
    ("identifier", "environment"),
    ("keyword", "text"),
    ("keyword", "not"),
    ("keyword", "null"),
    ("punctuation", ","),
    ("identifier", "state"),
    ("keyword", "text"),
    ("keyword", "not"),
    ("keyword", "null"),
    ("punctuation", ","),
    ("identifier", "expires_at"),
    ("keyword", "text"),
    ("keyword", "not"),
    ("keyword", "null"),
    ("punctuation", ","),
    ("identifier", "request_json"),
    ("keyword", "text"),
    ("keyword", "not"),
    ("keyword", "null"),
    ("punctuation", ")"),
)


def _normalized_sqlite_identifier(value: object) -> str | None:
    return value.casefold() if isinstance(value, str) else None


def _matches_v2_request_table_ddl(sql: object) -> bool:
    if not isinstance(sql, str):
        return False
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(sql):
        match = _SQLITE_DDL_TOKEN_PATTERN.match(sql, position)
        if match is None:
            return False
        position = match.end()
        kind = match.lastgroup
        if kind in {"whitespace", "line_comment", "block_comment"}:
            continue
        value = match.group()
        if kind == "quoted_identifier":
            if value[0] == "[":
                value = value[1:-1]
            else:
                quote = value[0]
                value = value[1:-1].replace(quote * 2, quote)
            if _SQLITE_SCHEMA_NAME.fullmatch(value) is None:
                return False
            tokens.append(("identifier", value.casefold()))
        elif kind == "word":
            tokens.append(("word", value.casefold()))
        elif kind == "punctuation":
            tokens.append(("punctuation", value))
        else:
            return False

    if tokens and tokens[-1] == ("punctuation", ";"):
        tokens.pop()
    if len(tokens) != len(_V2_REQUEST_TABLE_DDL_CONTRACT):
        return False
    for actual, expected in zip(tokens, _V2_REQUEST_TABLE_DDL_CONTRACT, strict=True):
        expected_kind, expected_value = expected
        actual_kind, actual_value = actual
        if actual_value != expected_value:
            return False
        if expected_kind == "identifier":
            if actual_kind not in {"identifier", "word"}:
                return False
        elif expected_kind == "keyword":
            if actual_kind != "word":
                return False
        elif actual_kind != expected_kind:
            return False
    return True


class RequestStateError(ValueError):
    """A stable, payload-free error code for local request state failures."""


class LocalRequestStore:
    """Repository-scoped preview state with serialized write transitions."""

    def __init__(self, context: RepositoryContext) -> None:
        if not isinstance(context, RepositoryContext):
            raise ValueError("invalid_repository_context")
        self._context = context
        self._database = context.cache_dir / "requests.sqlite"
        try:
            with repository_operation_lock(context):
                self._validate_storage_path()
                self._initialize()
        except ValueError as exc:
            if str(exc) == "invalid_operation_lock_path":
                raise ValueError("invalid_request_store_path") from exc
            raise

    @property
    def database_path(self) -> Path:
        return self._database

    @repository_locked
    def create_preview(
        self,
        prepared: PreparedRequest,
        *,
        action: CatalogAction,
    ) -> PreparedRequest:
        request = self._validated_request(prepared)
        self._verify_catalog_binding(request, action)
        if request.repository_id != self._context.repository_id:
            raise RequestStateError("repository_mismatch")
        if (
            request.state is not RequestState.PREVIEWED
            or request.approval_count != 0
            or request.failure_reason is not None
            or request.response_summary
        ):
            raise RequestStateError("invalid_initial_request_state")
        with self._immediate_transaction() as connection:
            self._assert_replay_allowed(connection, request.payload_hash)
            awaiting_confirmation = self._updated(
                request,
                state=RequestState.AWAITING_CONFIRMATION,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO requests (
                        request_id, payload_hash, connector_id, environment,
                        state, expires_at, request_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        awaiting_confirmation.request_id,
                        awaiting_confirmation.payload_hash,
                        awaiting_confirmation.connector_id,
                        awaiting_confirmation.environment,
                        awaiting_confirmation.state.value,
                        awaiting_confirmation.expires_at.isoformat(),
                        self._serialized_request(awaiting_confirmation),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RequestStateError("request_already_exists") from exc
        return awaiting_confirmation

    @staticmethod
    def _verify_catalog_binding(
        request: PreparedRequest,
        action: CatalogAction,
    ) -> None:
        try:
            if not isinstance(action, CatalogAction):
                raise TypeError
            validated = revalidate_catalog_action(action)
            rendered_path = render_action_path(
                validated.path_template,
                request.request_inputs.get("path", {}),
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise RequestStateError("invalid_catalog_action") from None

        expected_bindings = (
            (request.action_id, validated.action_id),
            (request.version_id, validated.version_id),
            (request.connector_id, validated.connector_id),
            (request.method, validated.method.value),
            (request.path_template, validated.path_template),
            (request.final_path, rendered_path),
        )
        if request.environment not in validated.environments or any(
            not secrets.compare_digest(actual, expected)
            for actual, expected in expected_bindings
        ):
            raise RequestStateError("catalog_binding_mismatch")
        if tuple(item.action_id for item in request.preflight_actions) != tuple(
            validated.preflight_action_ids
        ) or any(
            item.connector_id != validated.connector_id
            for item in request.preflight_actions
        ):
            raise RequestStateError("catalog_binding_mismatch")

        risk = effective_risk(validated)
        if (
            request.risk_tier != risk.tier
            or request.approval_level is not risk.approval_level
            or request.mutation_class is not risk.mutation_class
        ):
            raise RequestStateError("catalog_risk_mismatch")

    @repository_locked
    def get(self, request_id: str) -> PreparedRequest:
        with self._immediate_transaction() as connection:
            request = self._fetch(connection, request_id)
            return self._expire_if_needed(connection, request)

    @repository_locked
    def approve(
        self,
        request_id: str,
        payload_hash: str,
        expected_class: MutationClass,
    ) -> PreparedRequest:
        with self._immediate_transaction() as connection:
            request = self._approval_candidate(
                connection,
                request_id,
                payload_hash,
                expected_class,
            )
            updated = self._updated(
                request,
                state=RequestState.READY_TO_EXECUTE,
                approval_count=1,
            )
            self._store(connection, updated)
            return updated

    @repository_locked
    def precheck_approval(
        self,
        request_id: str,
        payload_hash: str,
        expected_class: MutationClass,
    ) -> PreparedRequest:
        """Validate a pending approval locally without recording it."""

        with self._immediate_transaction() as connection:
            return self._approval_candidate(
                connection,
                request_id,
                payload_hash,
                expected_class,
            )

    def confirm(self, request_id: str, payload_hash: str) -> PreparedRequest:
        """Compatibility helper for the v0.2 local tool surface."""

        request = self.get(request_id)
        return self.approve(request_id, payload_hash, request.mutation_class)

    @repository_locked
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

    @repository_locked
    def require_ready(self, request_id: str) -> PreparedRequest:
        with self._immediate_transaction() as connection:
            request = self._expire_if_needed(connection, self._fetch(connection, request_id))
            self._require_state(request, RequestState.READY_TO_EXECUTE)
            return request

    @repository_locked
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

    @repository_locked
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

    @repository_locked
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

    @repository_locked
    def resolve_outcome_unknown(
        self,
        request_id: str,
        outcome: str,
        response_summary: dict[str, Any] | None = None,
    ) -> PreparedRequest:
        states = {
            "succeeded": RequestState.SUCCEEDED,
            "failed": RequestState.FAILED,
        }
        state = states.get(outcome)
        if state is None:
            raise RequestStateError("invalid_resolution_outcome")
        with self._immediate_transaction() as connection:
            request = self._fetch(connection, request_id)
            self._require_state(request, RequestState.OUTCOME_UNKNOWN)
            updated = self._updated(
                request,
                state=state,
                failure_reason="execution_failed" if state is RequestState.FAILED else None,
                response_summary=response_summary or {},
            )
            self._store(connection, updated)
            return updated

    @repository_locked
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
            clauses = ["state IN (?, ?, ?)"]
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

    @repository_locked
    def assert_replay_allowed(self, payload_hash: str) -> None:
        if not _payload_hash(payload_hash):
            raise RequestStateError("invalid_payload_hash")
        connection = self._connect()
        try:
            self._assert_replay_allowed(connection, payload_hash)
        finally:
            self._close_connection(connection)

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if not isinstance(version, int) or version > _SCHEMA_VERSION:
                raise RequestStateError("request_store_version_unsupported")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            has_requests_table = any(
                _normalized_sqlite_identifier(name) == "requests" for name in tables
            )
            if version == 0 and tables:
                raise RequestStateError("request_store_schema_invalid")
            if version > 0 and not has_requests_table:
                raise RequestStateError("request_store_schema_invalid")
            if version < _SCHEMA_VERSION and has_requests_table:
                archive_name = self._next_schema_name(connection, _V1_ARCHIVE_NAME)
                connection.execute(
                    f'ALTER TABLE "requests" RENAME TO "{archive_name}"'
                )
            self._create_v2_schema(connection)
            self._validate_v2_schema(connection)
            self._ensure_replay_blocking_index(connection)
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
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
            self._close_connection(connection)

    @staticmethod
    def _create_v2_schema(connection: sqlite3.Connection) -> None:
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

    @staticmethod
    def _validate_v2_schema(connection: sqlite3.Connection) -> None:
        table_info = tuple(
            (
                row[0],
                _normalized_sqlite_identifier(row[1]),
                row[2],
                _sqlite_affinity(row[2]),
                bool(row[3]),
                row[4],
                row[5],
            )
            for row in connection.execute('PRAGMA table_info("requests")').fetchall()
        )
        table_xinfo = tuple(
            (
                row[0],
                _normalized_sqlite_identifier(row[1]),
                row[2],
                _sqlite_affinity(row[2]),
                bool(row[3]),
                row[4],
                row[5],
                row[6],
            )
            for row in connection.execute('PRAGMA table_xinfo("requests")').fetchall()
        )
        definitions = [
            row[2]
            for row in connection.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            if _normalized_sqlite_identifier(row[0]) == "requests"
            and _normalized_sqlite_identifier(row[1]) == "requests"
        ]
        triggers = [
            row[0]
            for row in connection.execute(
                "SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
            if _normalized_sqlite_identifier(row[1]) == "requests"
        ]
        if (
            table_info != _V2_REQUEST_TABLE_CONTRACT
            or table_xinfo != _V2_REQUEST_TABLE_XINFO_CONTRACT
            or len(definitions) != 1
            or not _matches_v2_request_table_ddl(definitions[0])
            or triggers
        ):
            raise RequestStateError("request_store_schema_invalid")

    @classmethod
    def _ensure_replay_blocking_index(cls, connection: sqlite3.Connection) -> None:
        for row in connection.execute('PRAGMA index_list("requests")').fetchall():
            name = row[1]
            if (
                isinstance(name, str)
                and _SQLITE_SCHEMA_NAME.fullmatch(name) is not None
                and name.casefold().startswith(_REPLAY_INDEX_NAME)
                and not cls._is_replay_blocking_index(connection, row)
            ):
                connection.execute(f'DROP INDEX "{name}"')
        if cls._has_replay_blocking_index(connection):
            return
        index_name = cls._next_schema_name(connection, _REPLAY_INDEX_NAME)
        connection.execute(
            f"""
            CREATE UNIQUE INDEX "{index_name}"
            ON "requests" ("payload_hash")
            WHERE "state" IN ('executing', 'succeeded', 'outcome_unknown')
            """
        )
        if not cls._has_replay_blocking_index(connection):
            raise RequestStateError("request_store_schema_invalid")

    @staticmethod
    def _next_schema_name(connection: sqlite3.Connection, base: str) -> str:
        names = {
            name
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name IS NOT NULL"
            ).fetchall()
            if (name := _normalized_sqlite_identifier(row[0])) is not None
        }
        if base.casefold() not in names:
            return base
        suffix = 2
        while f"{base}_{suffix}".casefold() in names:
            suffix += 1
        return f"{base}_{suffix}"

    @classmethod
    def _has_replay_blocking_index(cls, connection: sqlite3.Connection) -> bool:
        index_rows = connection.execute('PRAGMA index_list("requests")').fetchall()
        return any(cls._is_replay_blocking_index(connection, row) for row in index_rows)

    @staticmethod
    def _is_replay_blocking_index(
        connection: sqlite3.Connection,
        row: sqlite3.Row | tuple[Any, ...],
    ) -> bool:
        name = row[1]
        normalized_name = _normalized_sqlite_identifier(name)
        if (
            not isinstance(name, str)
            or _SQLITE_SCHEMA_NAME.fullmatch(name) is None
            or normalized_name is None
            or not normalized_name.startswith(_REPLAY_INDEX_NAME)
            or row[2] != 1
            or len(row) < 5
            or row[4] != 1
        ):
            return False
        columns = tuple(
            _normalized_sqlite_identifier(item[2])
            for item in connection.execute(f'PRAGMA index_info("{name}")').fetchall()
        )
        if columns != ("payload_hash",):
            return False
        definition = connection.execute(
            "SELECT tbl_name, sql FROM sqlite_master "
            "WHERE type = 'index' AND name = ?",
            (name,),
        ).fetchone()
        if (
            definition is None
            or _normalized_sqlite_identifier(definition[0]) != "requests"
        ):
            return False
        sql = definition[1]
        if not isinstance(sql, str):
            return False
        normalized = re.sub(r'["`\[\]]', "", sql)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return _REPLAY_INDEX_PATTERN.fullmatch(normalized) is not None

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
            self._close_connection(connection)

    def _connect(self) -> sqlite3.Connection:
        self._validate_storage_path()
        if os.name != "posix":
            return self._connect_path()

        cache_fd = self._open_cache_directory()
        database_fd = -1
        sidecar_fds: dict[str, int] = {}
        connection: sqlite3.Connection | None = None
        try:
            database_fd = self._open_store_file(cache_fd, _DATABASE_NAME, create=True)
            retained = os.fstat(database_fd)
            sidecar_fds = {
                name: self._open_store_file(cache_fd, name, create=True)
                for name in _SIDECAR_NAMES
            }
            retained_sidecars = {
                name: os.fstat(file_fd) for name, file_fd in sidecar_fds.items()
            }
            connection = self._connect_path()
            self._verify_entry_identity(cache_fd, _DATABASE_NAME, retained)
            for name, sidecar_state in retained_sidecars.items():
                self._verify_entry_identity(cache_fd, name, sidecar_state)
            self._enforce_sidecar_modes(cache_fd)
            return connection
        except ValueError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise RequestStateError("request_store_unavailable") from exc
        finally:
            if database_fd >= 0:
                os.close(database_fd)
            for sidecar_fd in sidecar_fds.values():
                os.close(sidecar_fd)
            os.close(cache_fd)

    def _connect_path(self) -> sqlite3.Connection:
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

    def _close_connection(self, connection: sqlite3.Connection) -> None:
        try:
            connection.close()
        finally:
            self._validate_storage_path()

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
        if os.name != "posix":
            self._validate_database_path_fallback()
            return
        cache_fd = self._open_cache_directory()
        try:
            for name in (_DATABASE_NAME, *_SIDECAR_NAMES):
                file_fd = self._open_store_file(cache_fd, name, create=False)
                if file_fd is not None:
                    os.close(file_fd)
        finally:
            os.close(cache_fd)

    def _validate_database_path_fallback(self) -> None:
        try:
            mode = self._database.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("invalid_request_store_path") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError("invalid_request_store_path")

    def _open_cache_directory(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            directory_fd = os.open(self._context.cache_dir, flags)
            state = os.fstat(directory_fd)
            if not stat.S_ISDIR(state.st_mode) or state.st_uid != os.getuid():
                raise ValueError("invalid_request_store_path")
            os.fchmod(directory_fd, 0o700)
            state = os.fstat(directory_fd)
            if stat.S_IMODE(state.st_mode) != 0o700:
                raise ValueError("invalid_request_store_path")
            return directory_fd
        except ValueError:
            if "directory_fd" in locals():
                os.close(directory_fd)
            raise
        except OSError as exc:
            raise ValueError("invalid_request_store_path") from exc

    def _open_store_file(
        self,
        cache_fd: int,
        name: str,
        *,
        create: bool,
    ) -> int | None:
        flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        if create:
            flags |= os.O_CREAT
        try:
            file_fd = os.open(name, flags, 0o600, dir_fd=cache_fd)
        except FileNotFoundError:
            if not create:
                return None
            raise ValueError("invalid_request_store_path") from None
        except OSError as exc:
            raise ValueError("invalid_request_store_path") from exc
        try:
            state = os.fstat(file_fd)
            if (
                not stat.S_ISREG(state.st_mode)
                or state.st_uid != os.getuid()
                or state.st_nlink != 1
            ):
                raise ValueError("invalid_request_store_path")
            os.fchmod(file_fd, 0o600)
            state = os.fstat(file_fd)
            if stat.S_IMODE(state.st_mode) != 0o600:
                raise ValueError("invalid_request_store_path")
            self._verify_entry_identity(cache_fd, name, state)
            return file_fd
        except Exception:
            os.close(file_fd)
            raise

    def _enforce_sidecar_modes(self, cache_fd: int) -> None:
        for name in _SIDECAR_NAMES:
            file_fd = self._open_store_file(cache_fd, name, create=False)
            if file_fd is not None:
                os.close(file_fd)

    @staticmethod
    def _verify_entry_identity(
        cache_fd: int,
        name: str,
        retained: os.stat_result,
    ) -> None:
        try:
            current = os.stat(name, dir_fd=cache_fd, follow_symlinks=False)
        except OSError as exc:
            raise ValueError("invalid_request_store_path") from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != (retained.st_dev, retained.st_ino)
        ):
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

    def _approval_candidate(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        payload_hash: str,
        expected_class: MutationClass,
    ) -> PreparedRequest:
        request = self._fetch(connection, request_id)
        if not isinstance(payload_hash, str) or not secrets.compare_digest(
            payload_hash,
            request.payload_hash,
        ):
            raise RequestStateError("payload_hash_mismatch")
        request = self._expire_if_needed(connection, request)
        self._require_state(request, RequestState.AWAITING_CONFIRMATION)
        if (
            not isinstance(expected_class, MutationClass)
            or request.mutation_class is not expected_class
        ):
            raise RequestStateError("mutation_class_mismatch")
        return request

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
            "SELECT * FROM requests WHERE state IN (?, ?, ?)", _PENDING_STATES
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


def _sqlite_affinity(declared_type: object) -> str:
    if not isinstance(declared_type, str):
        return "invalid"
    normalized = declared_type.upper()
    if "INT" in normalized:
        return "INTEGER"
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in normalized or not normalized:
        return "BLOB"
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _payload_hash(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))
