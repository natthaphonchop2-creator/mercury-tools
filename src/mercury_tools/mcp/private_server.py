"""Authenticated MCP tools for private FlowAccount journal writes."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from mcp.server.fastmcp import FastMCP

from mercury_tools.config import load_settings
from mercury_tools.connectors.flowaccount_journal import FlowAccountJournalError
from mercury_tools.db.journal_writes import SupabaseJournalWriteStore
from mercury_tools.db.product import SupabaseProductStore
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.journals.models import JournalValidationError
from mercury_tools.journals.service import FlowAccountJournalService
from mercury_tools.safety.redaction import redact_json

private_mcp = FastMCP("Mercury Finance Private")


def _journal_service() -> FlowAccountJournalService:
    settings = load_settings()
    return FlowAccountJournalService(
        product_store=SupabaseProductStore(settings),
        write_store=SupabaseJournalWriteStore(settings),
    )


def _audit_private(
    tool_name: str,
    input_summary: dict[str, Any],
    output_summary: dict[str, Any],
) -> None:
    with suppress(Exception):
        SupabaseRagStore(load_settings()).record_audit_event(
            {
                "tool_name": tool_name,
                "input": redact_json(input_summary),
                "output_summary": redact_json(output_summary),
                "status": str(output_summary.get("status") or "ok"),
                "metadata": {"runtime": "private-mcp", "connector": "flowaccount"},
            }
        )


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, JournalValidationError):
        return redact_json(
            {
                "status": "validation_error",
                "code": exc.code,
                "message": str(exc),
                "details": exc.details,
            }
        )
    if isinstance(exc, FlowAccountJournalError):
        return redact_json(
            {
                "status": exc.code,
                "message": str(exc),
                "http_status": exc.status_code,
                "retry_allowed": False,
            }
        )
    return redact_json({"status": "error", "message": str(exc)})


@private_mcp.tool()
def preview_flowaccount_journal(
    workspace_id: str,
    document_date: str,
    reference: str,
    description: str,
    lines: list[dict[str, Any]],
    note: str | None = None,
    remarks: str | None = None,
) -> dict[str, Any]:
    """Resolve accounts and preview one balanced FlowAccount JV without writing."""

    try:
        payload = _journal_service().preview(
            workspace_id=workspace_id,
            document_date=document_date,
            reference=reference,
            description=description,
            lines=lines,
            note=note,
            remarks=remarks,
        )
    except (JournalValidationError, FlowAccountJournalError, RuntimeError, ValueError) as exc:
        payload = _error_payload(exc)
    _audit_private(
        "preview_flowaccount_journal",
        {
            "workspace_id": workspace_id,
            "document_date": document_date,
            "reference_present": bool(reference.strip()),
            "line_count": len(lines),
        },
        {"status": payload.get("status"), "preview_id": payload.get("preview_id")},
    )
    return payload


@private_mcp.tool()
def create_flowaccount_journal_draft(
    workspace_id: str,
    preview_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create one FlowAccount draft from an unexpired confirmed preview."""

    try:
        payload = _journal_service().create_draft(
            workspace_id=workspace_id,
            preview_id=preview_id,
            confirm=confirm,
        )
    except (FlowAccountJournalError, RuntimeError, ValueError) as exc:
        payload = _error_payload(exc)
    _audit_private(
        "create_flowaccount_journal_draft",
        {
            "workspace_id": workspace_id,
            "preview_id": preview_id,
            "confirmed": confirm is True,
        },
        {
            "status": payload.get("status"),
            "record_id": payload.get("record_id"),
            "document_serial": payload.get("document_serial"),
        },
    )
    return payload


@private_mcp.tool()
def approve_flowaccount_journal(
    workspace_id: str,
    record_id: int,
    confirm: bool = False,
) -> dict[str, Any]:
    """Approve one Mercury-created FlowAccount draft after a new confirmation."""

    try:
        payload = _journal_service().approve(
            workspace_id=workspace_id,
            record_id=record_id,
            confirm=confirm,
        )
    except (FlowAccountJournalError, RuntimeError, ValueError) as exc:
        payload = _error_payload(exc)
    _audit_private(
        "approve_flowaccount_journal",
        {
            "workspace_id": workspace_id,
            "record_id": int(record_id),
            "confirmed": confirm is True,
        },
        {"status": payload.get("status"), "record_id": payload.get("record_id")},
    )
    return payload
