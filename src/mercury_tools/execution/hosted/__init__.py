"""Hosted Mercury execution services."""

from __future__ import annotations

from typing import Any

__all__ = [
    "BatchOperationService",
    "DocumentCreateConfirmation",
    "DocumentOperationError",
    "HostedReadService",
    "NativeBatchQualification",
    "OperationService",
    "ProviderCreateRejected",
    "ProviderReadEnvelope",
    "ReconciliationError",
    "ReconciliationService",
    "RecordedLookupBinding",
]


def __getattr__(name: str) -> Any:
    if name in {"HostedReadService", "ProviderReadEnvelope"}:
        from mercury_tools.execution.hosted.read_service import (
            HostedReadService,
            ProviderReadEnvelope,
        )

        return {
            "HostedReadService": HostedReadService,
            "ProviderReadEnvelope": ProviderReadEnvelope,
        }[name]
    if name in {
        "DocumentCreateConfirmation",
        "DocumentOperationError",
        "OperationService",
        "ProviderCreateRejected",
    }:
        from mercury_tools.execution.hosted.operation_service import (
            DocumentCreateConfirmation,
            DocumentOperationError,
            OperationService,
            ProviderCreateRejected,
        )

        return {
            "DocumentCreateConfirmation": DocumentCreateConfirmation,
            "DocumentOperationError": DocumentOperationError,
            "OperationService": OperationService,
            "ProviderCreateRejected": ProviderCreateRejected,
        }[name]
    if name in {"BatchOperationService", "NativeBatchQualification"}:
        from mercury_tools.execution.hosted.batch_service import (
            BatchOperationService,
            NativeBatchQualification,
        )

        return {
            "BatchOperationService": BatchOperationService,
            "NativeBatchQualification": NativeBatchQualification,
        }[name]
    if name in {"ReconciliationError", "ReconciliationService", "RecordedLookupBinding"}:
        from mercury_tools.execution.hosted.reconciliation_service import (
            ReconciliationError,
            ReconciliationService,
            RecordedLookupBinding,
        )

        return {
            "ReconciliationError": ReconciliationError,
            "ReconciliationService": ReconciliationService,
            "RecordedLookupBinding": RecordedLookupBinding,
        }[name]
    raise AttributeError(name)
