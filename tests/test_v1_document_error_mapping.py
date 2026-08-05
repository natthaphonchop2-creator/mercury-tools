from __future__ import annotations

import pytest

from mercury_tools.execution.hosted.operation_service import DocumentOperationError
from mercury_tools.execution.hosted.store import HostedPreviewError
from mercury_tools.mcp.v1_errors import public_error_code


@pytest.mark.parametrize(
    ("internal", "public"),
    [
        ("preview_expired", "preview_expired"),
        ("preview_binding_changed", "preview_binding_mismatch"),
        ("preview_state_stale", "preview_state_changed"),
        ("preview_state_invalid", "preview_state_changed"),
        ("document_payload_invalid", "validation_failed"),
        ("document_schema_invalid", "validation_failed"),
        ("duplicate_provider_call", "duplicate_batch_item"),
        ("operation_state_stale", "operation_in_progress"),
        ("operation_conflict", "operation_in_progress"),
    ],
)
def test_hosted_preview_errors_map_to_actionable_v1_codes(
    internal: str,
    public: str,
) -> None:
    assert public_error_code(HostedPreviewError(internal)) == public


@pytest.mark.parametrize(
    ("internal", "public"),
    [
        ("audit_write_failed", "capability_unavailable"),
        ("retry_payload_unavailable", "manual_review_required"),
        ("operation_invalid", "validation_failed"),
    ],
)
def test_document_operation_errors_map_without_internal_detail(
    internal: str,
    public: str,
) -> None:
    assert public_error_code(DocumentOperationError(internal)) == public
