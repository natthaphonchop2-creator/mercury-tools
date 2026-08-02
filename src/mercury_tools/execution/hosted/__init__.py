"""Hosted Mercury execution services."""

from __future__ import annotations

from typing import Any

__all__ = ["HostedReadService", "ProviderReadEnvelope"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from mercury_tools.execution.hosted.read_service import (
            HostedReadService,
            ProviderReadEnvelope,
        )

        return {
            "HostedReadService": HostedReadService,
            "ProviderReadEnvelope": ProviderReadEnvelope,
        }[name]
    raise AttributeError(name)
