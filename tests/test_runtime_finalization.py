from __future__ import annotations

import asyncio

import pytest

from mercury_tools.providers.finalization import await_cleanup


@pytest.mark.asyncio
async def test_cleanup_finishes_all_operations_after_repeated_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed: list[str] = []

    async def first() -> None:
        started.set()
        await release.wait()
        completed.append("first")

    async def second() -> None:
        completed.append("second")

    task = asyncio.create_task(await_cleanup(first(), second()))
    await started.wait()
    task.cancel()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed == ["first", "second"]


@pytest.mark.asyncio
async def test_cleanup_failure_never_replaces_later_required_cleanup() -> None:
    completed: list[str] = []

    async def failing_close() -> None:
        raise RuntimeError("close_failed")

    async def required_audit() -> None:
        completed.append("audit")

    await await_cleanup(failing_close(), required_audit())
    assert completed == ["audit"]
