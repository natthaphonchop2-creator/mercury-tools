"""Execution policy and local preview primitives for ERP actions."""

from __future__ import annotations

from typing import Any

from mercury_tools.canonical import canonical_payload_hash

__all__ = [
    "ERPExecutor",
    "ExecutionPolicyError",
    "LocalRequestStore",
    "PreparedRequest",
    "RequestBuildError",
    "RequestState",
    "RequestStateError",
    "RequestTemplate",
    "RiskDecision",
    "build_request",
    "canonical_payload_hash",
    "effective_risk",
]


def __getattr__(name: str) -> Any:
    if name in {"ERPExecutor", "ExecutionPolicyError"}:
        from mercury_tools.execution.executor import ERPExecutor, ExecutionPolicyError

        return {"ERPExecutor": ERPExecutor, "ExecutionPolicyError": ExecutionPolicyError}[name]
    if name in {"PreparedRequest", "RequestState"}:
        from mercury_tools.execution.models import PreparedRequest, RequestState

        return {"PreparedRequest": PreparedRequest, "RequestState": RequestState}[name]
    if name in {"RiskDecision", "effective_risk"}:
        from mercury_tools.execution.policy import RiskDecision, effective_risk

        return {"RiskDecision": RiskDecision, "effective_risk": effective_risk}[name]
    if name in {"RequestBuildError", "RequestTemplate", "build_request"}:
        from mercury_tools.execution.request_builder import (
            RequestBuildError,
            RequestTemplate,
            build_request,
        )

        return {
            "RequestBuildError": RequestBuildError,
            "RequestTemplate": RequestTemplate,
            "build_request": build_request,
        }[name]
    if name in {"LocalRequestStore", "RequestStateError"}:
        from mercury_tools.execution.store import LocalRequestStore, RequestStateError

        return {"LocalRequestStore": LocalRequestStore, "RequestStateError": RequestStateError}[
            name
        ]
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
