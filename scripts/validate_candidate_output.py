#!/usr/bin/env python3
"""Fail-closed validation for a bounded candidate output tree."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path

DEFAULT_MAX_FILES = 100
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
HARD_MAX_FILES = 100
HARD_MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_DEPTH = 32
MAX_DIRECTORIES = 100
MAX_ENTRIES = 400
MAX_NAME_BYTES = 255
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ValidationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise argparse.ArgumentError(None, message)


def _error(code: str) -> ValidationError:
    return ValidationError(code)


def _check_mode(mode: int) -> None:
    if mode & 0o022:
        raise _error("permissions")


def _check_name(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name)
    if (
        name != normalized
        or name in {".", ".."}
        or len(os.fsencode(name)) > MAX_NAME_BYTES
        or _SAFE_NAME.fullmatch(name) is None
    ):
        raise _error("unsafe_name")
    return normalized.casefold()


def _validate_limits(max_files: int, max_bytes: int) -> None:
    if not 1 <= max_files <= HARD_MAX_FILES:
        raise _error("arguments")
    if not 1 <= max_bytes <= HARD_MAX_BYTES:
        raise _error("arguments")


def validate_root(root: Path, *, max_files: int, max_bytes: int) -> tuple[int, int]:
    """Return regular-file count and total bytes after validating the tree."""

    _validate_limits(max_files, max_bytes)
    try:
        root_stat = os.lstat(root)
    except OSError as exc:
        raise _error("root_invalid") from exc
    if stat.S_ISLNK(root_stat.st_mode):
        raise _error("symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise _error("root_invalid")
    _check_mode(root_stat.st_mode)

    files = 0
    total_bytes = 0
    directories = 0
    entries_seen = 0
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise _error("io_error") from exc
        canonical_names: set[str] = set()
        for entry in entries:
            entries_seen += 1
            if entries_seen > MAX_ENTRIES:
                raise _error("count_overflow")
            canonical_name = _check_name(entry.name)
            if canonical_name in canonical_names:
                raise _error("duplicate_name")
            canonical_names.add(canonical_name)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _error("io_error") from exc
            mode = entry_stat.st_mode
            if stat.S_ISLNK(mode):
                raise _error("symlink")
            if stat.S_ISDIR(mode):
                _check_mode(mode)
                if depth + 1 > MAX_DEPTH:
                    raise _error("depth_overflow")
                directories += 1
                if directories > MAX_DIRECTORIES:
                    raise _error("count_overflow")
                pending.append((Path(entry.path), depth + 1))
                continue
            if not stat.S_ISREG(mode):
                raise _error("special_file")
            _check_mode(mode)
            if entry_stat.st_nlink != 1:
                raise _error("hardlink")
            files += 1
            if files > max_files:
                raise _error("count_overflow")
            size = entry_stat.st_size
            if size < 0 or size > max_bytes or total_bytes > max_bytes - size:
                raise _error("bytes_overflow")
            total_bytes += size
    return files, total_bytes


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("root")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    try:
        return parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit) as exc:
        raise _error("arguments") from exc


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        files, total_bytes = validate_root(
            Path(args.root), max_files=args.max_files, max_bytes=args.max_bytes
        )
    except ValidationError as exc:
        _emit({"status": "error", "code": exc.code, "files": 0, "bytes": 0})
        return 1
    except Exception:
        _emit({"status": "error", "code": "io_error", "files": 0, "bytes": 0})
        return 1
    _emit({"status": "ok", "files": files, "bytes": total_bytes})
    return 0


if __name__ == "__main__":
    sys.exit(main())
