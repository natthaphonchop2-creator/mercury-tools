from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mercury_tools.local.operation_lock import repository_operation_lock
from mercury_tools.local.repository import RepositoryContext


def test_repository_operation_lock_is_reentrant_and_owner_only(
    repository_context: RepositoryContext,
) -> None:
    with (
        repository_operation_lock(repository_context),
        repository_operation_lock(repository_context),
    ):
        lock_path = repository_context.cache_dir / "operation.lock"
        assert lock_path.is_file()

    if os.name == "posix":
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow behavior")
def test_repository_operation_lock_rejects_symlink(
    repository_context: RepositoryContext,
    tmp_path: Path,
) -> None:
    target = tmp_path / "lock-target"
    target.touch()
    (repository_context.cache_dir / "operation.lock").symlink_to(target)

    with (
        pytest.raises(ValueError, match="^invalid_operation_lock_path$"),
        repository_operation_lock(repository_context),
    ):
        pass
