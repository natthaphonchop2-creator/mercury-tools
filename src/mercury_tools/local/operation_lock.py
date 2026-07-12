"""Repository-scoped serialization for local ERP state mutations."""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar, cast

from mercury_tools.local.repository import RepositoryContext

try:  # pragma: no cover - platform import
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None

try:  # pragma: no cover - platform import
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - non-Windows fallback
    _msvcrt = None

_LOCK_NAME = "operation.lock"
_FALLBACK_GUARD_NAME = "operation.lock.guard"
_FALLBACK_TIMEOUT_SECONDS = 5.0
_FALLBACK_POLL_SECONDS = 0.025


@dataclass
class _LockState:
    thread_lock: threading.RLock = field(default_factory=threading.RLock)
    depth: int = 0
    file_descriptor: int = -1
    fallback_guard: Path | None = None


_registry_lock = threading.Lock()
_registry_pid = os.getpid()
_states: dict[Path, _LockState] = {}


def _reset_after_fork() -> None:
    global _registry_lock, _registry_pid, _states
    for state in _states.values():
        if state.file_descriptor >= 0:
            with suppress(OSError):
                os.close(state.file_descriptor)
    _registry_lock = threading.Lock()
    _registry_pid = os.getpid()
    _states = {}


if hasattr(os, "register_at_fork"):  # pragma: no branch - POSIX registration
    os.register_at_fork(after_in_child=_reset_after_fork)


@contextmanager
def repository_operation_lock(context: RepositoryContext) -> Iterator[None]:
    """Hold the repository mutation lock, safely supporting nested local calls."""

    cache_dir = _validated_cache_directory(context)
    state = _state_for(cache_dir)
    with state.thread_lock:
        if state.depth == 0:
            file_descriptor = _open_lock_file(cache_dir)
            try:
                state.fallback_guard = _acquire_process_lock(file_descriptor, cache_dir)
            except Exception:
                os.close(file_descriptor)
                raise
            state.file_descriptor = file_descriptor
        state.depth += 1
        try:
            yield
        finally:
            state.depth -= 1
            if state.depth == 0:
                try:
                    _release_process_lock(
                        state.file_descriptor,
                        state.fallback_guard,
                    )
                finally:
                    os.close(state.file_descriptor)
                    state.file_descriptor = -1
                    state.fallback_guard = None


_Return = TypeVar("_Return")


def repository_locked(
    method: Callable[..., _Return],
) -> Callable[..., _Return]:
    """Serialize an instance method whose owner has a repository context."""

    @wraps(method)
    def wrapped(owner: Any, *args: Any, **kwargs: Any) -> _Return:
        context = getattr(owner, "_context", None)
        if not isinstance(context, RepositoryContext):
            raise ValueError("invalid_repository_context")
        with repository_operation_lock(context):
            return method(owner, *args, **kwargs)

    return cast(Callable[..., _Return], wrapped)


def _state_for(cache_dir: Path) -> _LockState:
    global _registry_pid
    if _registry_pid != os.getpid():
        _reset_after_fork()
    with _registry_lock:
        return _states.setdefault(cache_dir, _LockState())


def _validated_cache_directory(context: RepositoryContext) -> Path:
    if not isinstance(context, RepositoryContext):
        raise ValueError("invalid_repository_context")
    try:
        root = context.root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("invalid_operation_lock_path") from exc
    expected_mercury = root / ".mercury"
    expected_cache = expected_mercury / "cache"
    if (
        context.root != root
        or context.mercury_dir != expected_mercury
        or context.cache_dir != expected_cache
    ):
        raise ValueError("invalid_operation_lock_path")
    for path in (root, expected_mercury, expected_cache):
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ValueError("invalid_operation_lock_path") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError("invalid_operation_lock_path")
    return expected_cache


def _open_lock_file(cache_dir: Path) -> int:
    if os.name == "posix":
        return _open_lock_file_posix(cache_dir)
    path = cache_dir / _LOCK_NAME
    try:
        file_descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        retained = os.fstat(file_descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(retained.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (retained.st_dev, retained.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ValueError("invalid_operation_lock_path")
        return file_descriptor
    except Exception:
        if "file_descriptor" in locals():
            os.close(file_descriptor)
        raise


def _open_lock_file_posix(cache_dir: Path) -> int:
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_fd = -1
    file_descriptor = -1
    try:
        directory_fd = os.open(cache_dir, directory_flags)
        directory_state = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_state.st_mode) or directory_state.st_uid != os.getuid():
            raise ValueError("invalid_operation_lock_path")
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_descriptor = os.open(_LOCK_NAME, flags, 0o600, dir_fd=directory_fd)
        retained = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(retained.st_mode)
            or retained.st_uid != os.getuid()
            or retained.st_nlink != 1
        ):
            raise ValueError("invalid_operation_lock_path")
        os.fchmod(file_descriptor, 0o600)
        current = os.stat(_LOCK_NAME, dir_fd=directory_fd, follow_symlinks=False)
        if (
            stat.S_ISLNK(current.st_mode)
            or (retained.st_dev, retained.st_ino) != (current.st_dev, current.st_ino)
            or stat.S_IMODE(os.fstat(file_descriptor).st_mode) != 0o600
        ):
            raise ValueError("invalid_operation_lock_path")
        result = file_descriptor
        file_descriptor = -1
        return result
    except OSError as exc:
        raise ValueError("invalid_operation_lock_path") from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


def _acquire_process_lock(file_descriptor: int, cache_dir: Path) -> Path | None:
    if _fcntl is not None:
        try:
            _fcntl.flock(file_descriptor, _fcntl.LOCK_EX)
            return None
        except OSError as exc:
            raise ValueError("operation_lock_unavailable") from exc
    if _msvcrt is not None:  # pragma: no cover - Windows fallback
        try:
            if os.fstat(file_descriptor).st_size == 0:
                os.write(file_descriptor, b"\0")
                os.fsync(file_descriptor)
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            _msvcrt.locking(file_descriptor, _msvcrt.LK_LOCK, 1)
            return None
        except OSError as exc:
            raise ValueError("operation_lock_unavailable") from exc

    guard = cache_dir / _FALLBACK_GUARD_NAME
    deadline = time.monotonic() + _FALLBACK_TIMEOUT_SECONDS
    while True:  # pragma: no cover - rare standard-library fallback
        try:
            os.mkdir(guard, 0o700)
            return guard
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError("operation_lock_unavailable") from None
            time.sleep(_FALLBACK_POLL_SECONDS)
        except OSError as exc:
            raise ValueError("operation_lock_unavailable") from exc


def _release_process_lock(file_descriptor: int, fallback_guard: Path | None) -> None:
    if _fcntl is not None:
        _fcntl.flock(file_descriptor, _fcntl.LOCK_UN)
        return
    if _msvcrt is not None:  # pragma: no cover - Windows fallback
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        _msvcrt.locking(file_descriptor, _msvcrt.LK_UNLCK, 1)
        return
    if fallback_guard is not None:  # pragma: no cover - rare fallback
        try:
            fallback_guard.rmdir()
        except OSError as exc:
            raise ValueError("operation_lock_unavailable") from exc
