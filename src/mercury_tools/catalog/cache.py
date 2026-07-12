import json
import sqlite3
import stat
from pathlib import Path

from pydantic import ValidationError

from mercury_tools.catalog.identity import canonical_json, validate_action_identity
from mercury_tools.catalog.local_store import merge_actions
from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.local.repository import RepositoryContext

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_actions (
    action_id TEXT PRIMARY KEY NOT NULL,
    version_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_metadata (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);
"""


class CatalogCache:
    def __init__(self, context: RepositoryContext) -> None:
        self._context = context

    def replace_global(self, actions: list[CatalogAction], etag: str | None) -> None:
        rows = self._rows_for_actions(actions)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM catalog_actions")
            connection.executemany(
                "INSERT INTO catalog_actions (action_id, version_id, payload) VALUES (?, ?, ?)",
                rows,
            )
            if etag:
                connection.execute(
                    "INSERT INTO catalog_metadata (key, value) VALUES ('etag', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (etag,),
                )
            else:
                connection.execute("DELETE FROM catalog_metadata WHERE key = 'etag'")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_global(self) -> list[CatalogAction]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT action_id, version_id, payload FROM catalog_actions ORDER BY action_id"
            ).fetchall()
            connection.commit()
        finally:
            connection.close()

        actions: list[CatalogAction] = []
        seen_action_ids: set[str] = set()
        seen_version_ids: set[str] = set()
        for action_id, version_id, payload in rows:
            action = _decode_row(action_id, version_id, payload)
            if action.action_id in seen_action_ids or action.version_id in seen_version_ids:
                raise ValueError("catalog_cache_row_invalid")
            seen_action_ids.add(action.action_id)
            seen_version_ids.add(action.version_id)
            actions.append(action)
        return merge_actions((), actions)

    def conditional_headers(self) -> dict[str, str]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT value FROM catalog_metadata WHERE key = 'etag'"
            ).fetchone()
        finally:
            connection.close()
        if row is None or not row[0]:
            return {}
        return {"If-None-Match": row[0]}

    def _connect(self) -> sqlite3.Connection:
        path = self._database_path()
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript(_SCHEMA)
            return connection
        except Exception:
            connection.close()
            raise

    def _database_path(self) -> Path:
        context = self._context
        if context.cache_dir != context.mercury_dir / "cache":
            raise ValueError("catalog_cache_path_escape")
        if not context.cache_dir.is_relative_to(context.mercury_dir):
            raise ValueError("catalog_cache_path_escape")
        _require_real_directory(context.root)
        _require_real_directory(context.mercury_dir)
        _require_real_directory(context.cache_dir)
        path = context.cache_dir / "catalog.sqlite"
        if path.exists() or path.is_symlink():
            try:
                status = path.lstat()
            except FileNotFoundError:
                return path
            if stat.S_ISLNK(status.st_mode):
                raise ValueError("catalog_cache_symlink")
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("catalog_cache_path_invalid")
        return path

    @staticmethod
    def _rows_for_actions(actions: list[CatalogAction]) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        seen_action_ids: set[str] = set()
        seen_version_ids: set[str] = set()
        for action in actions:
            try:
                validated = revalidate_catalog_action(action)
            except (AttributeError, TypeError, ValidationError, ValueError):
                raise ValueError("catalog_cache_action_invalid") from None
            if (
                validated.action_id in seen_action_ids
                or validated.version_id in seen_version_ids
            ):
                raise ValueError("catalog_cache_duplicate")
            seen_action_ids.add(validated.action_id)
            seen_version_ids.add(validated.version_id)
            rows.append(
                (
                    validated.action_id,
                    validated.version_id,
                    canonical_json(validated.model_dump(mode="json")),
                )
            )
        return rows


def _decode_row(action_id: str, version_id: str, payload: str) -> CatalogAction:
    try:
        data = json.loads(payload)
        action = CatalogAction.model_validate(data)
        validate_action_identity(action)
        if action.action_id != action_id or action.version_id != version_id:
            raise ValueError("catalog_cache_row_invalid")
        if payload != canonical_json(action.model_dump(mode="json")):
            raise ValueError("catalog_cache_row_invalid")
        return action
    except (ValidationError, ValueError, TypeError):
        raise ValueError("catalog_cache_row_invalid") from None


def _require_real_directory(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise ValueError("catalog_cache_path_invalid") from error
    if stat.S_ISLNK(status.st_mode):
        raise ValueError("catalog_cache_symlink")
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError("catalog_cache_path_invalid")
