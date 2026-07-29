"""Cancellation-preserving finalization for non-critical runtime cleanup."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from typing import Any

import anyio


async def await_cleanup(*operations: Awaitable[Any] | Any) -> None:
    """Finish all cleanup while preserving the caller's primary cancellation.

    Cleanup errors are intentionally non-authoritative. A native asyncio or AnyIO
    cancellation is retained until every supplied operation has settled.
    """

    cancellation: asyncio.CancelledError | None = None
    for operation in operations:
        if operation is None:
            continue
        if not inspect.isawaitable(operation):
            continue
        task = asyncio.ensure_future(operation)
        with anyio.CancelScope(shield=True):
            while True:
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as error:
                    if task.cancelled():
                        if cancellation is not None:
                            raise cancellation from error
                        raise
                    if cancellation is None:
                        cancellation = error
                    continue
                except Exception:
                    break
                break
    if cancellation is not None:
        raise cancellation
    await anyio.lowlevel.checkpoint_if_cancelled()


__all__ = ["await_cleanup"]
