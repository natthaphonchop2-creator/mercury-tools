"""Private FlowAccount journal preview and write orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mercury_tools.connectors.flowaccount_journal import (
    FlowAccountJournalClient,
    FlowAccountJournalError,
    FlowAccountOutcomeUnknown,
)
from mercury_tools.journals.models import prepare_general_journal
from mercury_tools.safety.redaction import redact_json

ClientFactory = Callable[[dict[str, Any]], FlowAccountJournalClient]


def flowaccount_client_from_context(context: dict[str, Any]) -> FlowAccountJournalClient:
    preset = context["preset"]
    credentials = context["credentials"]
    return FlowAccountJournalClient(
        api_base_url=str(preset["api_base_url"]),
        token_url=str(preset["token_url"]),
        grant_type=str(preset.get("grant_type") or "client_credentials"),
        scope=str(preset.get("scope") or "flowaccount-api"),
        client_id=str(credentials["client_id"]),
        client_secret=str(credentials["client_secret"]),
    )


def _response_summary(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "recordId",
        "documentSerial",
        "status",
        "debit",
        "credit",
        "documentDate",
        "documentType",
    }
    return redact_json({key: payload[key] for key in allowed if key in payload})


def _connector_error_payload(exc: FlowAccountJournalError) -> dict[str, Any]:
    return {
        "status": exc.code,
        "message": str(exc),
        "http_status": exc.status_code,
        "retry_allowed": False,
    }


class FlowAccountJournalService:
    """Enforce preview, one-time draft creation, and separate approval."""

    def __init__(
        self,
        *,
        product_store: Any,
        write_store: Any,
        client_factory: ClientFactory = flowaccount_client_from_context,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.product_store = product_store
        self.write_store = write_store
        self.client_factory = client_factory
        self.now = now or (lambda: datetime.now(tz=UTC))

    def _context(self, workspace_id: str) -> dict[str, Any]:
        return self.product_store.get_private_connector_context(
            workspace_id,
            "flowaccount",
        )

    def preview(
        self,
        *,
        workspace_id: str,
        document_date: str,
        reference: str,
        description: str,
        lines: list[dict[str, Any]],
        note: str | None = None,
        remarks: str | None = None,
    ) -> dict[str, Any]:
        context = self._context(workspace_id)
        client = self.client_factory(context)
        prepared = prepare_general_journal(
            document_date=document_date,
            reference=reference,
            description=description,
            lines=lines,
            accounts=client.list_chart_accounts(),
            environment=context["environment"],
            note=note,
            remarks=remarks,
        )
        input_hash = prepared.input_hash(
            workspace_id=context["workspace_uuid"],
            connector_profile_id=context["connector_profile_id"],
            environment=context["environment"],
        )
        preview_payload = {
            "document_type": "JV",
            "document_date": prepared.document_date,
            "reference": prepared.reference,
            "description": prepared.description,
            "total_debit": prepared.total_debit,
            "total_credit": prepared.total_credit,
            "lines": prepared.preview_lines,
        }
        row = self.write_store.create_preview(
            workspace_uuid=context["workspace_uuid"],
            connector_profile_id=context["connector_profile_id"],
            workspace_key=context["workspace_key"],
            environment=context["environment"],
            input_hash=input_hash,
            payload={
                "flowaccount_payload": prepared.flowaccount_payload,
                "preview": preview_payload,
            },
            expires_at=self.now() + timedelta(minutes=10),
        )
        return redact_json(
            {
                "status": "awaiting_confirmation",
                "preview_id": row["request_key"],
                "environment": context["environment"],
                **preview_payload,
                "expires_at": row["expires_at"],
                "next_tool": "create_flowaccount_journal_draft",
                "confirmation_required": True,
            }
        )

    def create_draft(
        self,
        *,
        workspace_id: str,
        preview_id: str,
        confirm: bool,
    ) -> dict[str, Any]:
        if confirm is not True:
            return {
                "status": "confirmation_required",
                "preview_id": preview_id,
                "message": "Confirm the journal preview before creating a draft.",
            }

        context = self._context(workspace_id)
        row = self.write_store.load_request(
            request_key=preview_id,
            workspace_uuid=context["workspace_uuid"],
            workspace_key=context["workspace_key"],
        )
        if row is None:
            return {"status": "not_found", "preview_id": preview_id}
        if (
            row.get("connector_profile_id") != context["connector_profile_id"]
            or row.get("environment") != context["environment"]
        ):
            return {
                "status": "connector_context_changed",
                "preview_id": preview_id,
                "message": "Create a new preview for the active connector context.",
            }
        if row.get("status") in {
            "executing",
            "draft_created",
            "approved",
            "outcome_unknown",
        }:
            return self._duplicate_payload(row)

        duplicate = self.write_store.find_blocking_duplicate(
            workspace_uuid=context["workspace_uuid"],
            connector_profile_id=context["connector_profile_id"],
            input_hash=row["input_hash"],
            exclude_request_key=preview_id,
        )
        if duplicate:
            return self._duplicate_payload(duplicate)

        claimed = self.write_store.claim_preview(
            request_key=preview_id,
            workspace_uuid=context["workspace_uuid"],
            now=self.now(),
        )
        if not claimed:
            return {
                "status": "expired_or_consumed",
                "preview_id": preview_id,
                "message": "Create a new journal preview.",
            }

        try:
            response = self.client_factory(context).create_draft(
                row["payload"]["flowaccount_payload"]
            )
            record_id = int(response.get("recordId") or 0)
            if not record_id:
                raise FlowAccountOutcomeUnknown(
                    "outcome_unknown",
                    "FlowAccount returned no journal record ID after dispatch.",
                )
            document_serial = str(response.get("documentSerial") or "") or None
            self.write_store.record_draft(
                request_key=preview_id,
                workspace_uuid=context["workspace_uuid"],
                record_id=record_id,
                document_serial=document_serial,
                response_summary=_response_summary(response),
            )
        except FlowAccountOutcomeUnknown as exc:
            self.write_store.record_failure(
                request_key=preview_id,
                workspace_uuid=context["workspace_uuid"],
                status="outcome_unknown",
                response_summary=_connector_error_payload(exc),
            )
            return _connector_error_payload(exc)
        except FlowAccountJournalError as exc:
            self.write_store.record_failure(
                request_key=preview_id,
                workspace_uuid=context["workspace_uuid"],
                status="failed",
                response_summary=_connector_error_payload(exc),
            )
            return _connector_error_payload(exc)

        preview = row["payload"].get("preview") or {}
        return redact_json(
            {
                "status": "draft_created",
                "preview_id": preview_id,
                "record_id": record_id,
                "document_serial": document_serial,
                "flowaccount_status": response.get("status"),
                "total_debit": str(response.get("debit") or preview.get("total_debit")),
                "total_credit": str(
                    response.get("credit") or preview.get("total_credit")
                ),
                "next_tool": "approve_flowaccount_journal",
                "approval_confirmation_required": True,
            }
        )

    def approve(
        self,
        *,
        workspace_id: str,
        record_id: int,
        confirm: bool,
    ) -> dict[str, Any]:
        if confirm is not True:
            return {
                "status": "confirmation_required",
                "record_id": int(record_id),
                "message": "Confirm the existing FlowAccount draft before approval.",
            }

        context = self._context(workspace_id)
        row = self.write_store.load_draft_by_record_id(
            workspace_uuid=context["workspace_uuid"],
            record_id=int(record_id),
        )
        if row is None:
            return {
                "status": "not_found",
                "record_id": int(record_id),
                "message": "No Mercury-created draft is available for approval.",
            }
        if (
            row.get("connector_profile_id") != context["connector_profile_id"]
            or row.get("environment") != context["environment"]
        ):
            return {
                "status": "connector_context_changed",
                "record_id": int(record_id),
            }

        claimed = self.write_store.claim_draft_for_approval(
            request_key=row["request_key"],
            workspace_uuid=context["workspace_uuid"],
            now=self.now(),
        )
        if not claimed:
            return {
                "status": "already_processed",
                "record_id": int(record_id),
            }

        try:
            response = self.client_factory(context).approve_draft(int(record_id))
            self.write_store.record_approved(
                request_key=row["request_key"],
                workspace_uuid=context["workspace_uuid"],
                approved_at=self.now(),
                response_summary=_response_summary(response),
            )
        except FlowAccountOutcomeUnknown as exc:
            self.write_store.record_failure(
                request_key=row["request_key"],
                workspace_uuid=context["workspace_uuid"],
                status="outcome_unknown",
                response_summary=_connector_error_payload(exc),
            )
            return _connector_error_payload(exc)
        except FlowAccountJournalError as exc:
            self.write_store.record_failure(
                request_key=row["request_key"],
                workspace_uuid=context["workspace_uuid"],
                status="failed",
                response_summary=_connector_error_payload(exc),
            )
            return _connector_error_payload(exc)

        return redact_json(
            {
                "status": "approved",
                "record_id": int(record_id),
                "document_serial": row.get("document_serial"),
                "flowaccount_status": response.get("status"),
            }
        )

    @staticmethod
    def _duplicate_payload(row: dict[str, Any]) -> dict[str, Any]:
        return redact_json(
            {
                "status": "duplicate_blocked",
                "existing_status": row.get("status"),
                "record_id": row.get("flowaccount_record_id"),
                "document_serial": row.get("document_serial"),
                "message": "Inspect the existing FlowAccount result before retrying.",
            }
        )
