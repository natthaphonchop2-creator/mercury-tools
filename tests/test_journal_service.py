from __future__ import annotations

from datetime import UTC, datetime

from mercury_tools.connectors.flowaccount_journal import FlowAccountOutcomeUnknown
from mercury_tools.journals.service import FlowAccountJournalService

WORKSPACE = "mw_publiccontestworkspace001"
WORKSPACE_UUID = "10000000-0000-0000-0000-000000000001"
PROFILE_UUID = "20000000-0000-0000-0000-000000000001"
FIXED_NOW = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)

ACCOUNTS = [
    {"id": 501, "code": "52010", "nameLocal": "ค่าขนส่ง", "nameForeign": "Shipping"},
    {"id": 601, "code": "11379.01", "nameLocal": "TikTok Shop", "nameForeign": "TikTok"},
    {"id": 604, "code": "11379.04", "nameLocal": "Shopee", "nameForeign": "Shopee"},
]


def example_lines() -> list[dict[str, str]]:
    return [
        {"side": "debit", "account_name": "ค่าขนส่ง", "amount": "4236"},
        {"side": "credit", "account_code": "11379.01", "amount": "2844"},
        {"side": "credit", "account_code": "11379.04", "amount": "1392"},
    ]


class FakeProductStore:
    def get_private_connector_context(self, workspace_id, connector_id):
        assert workspace_id == WORKSPACE
        assert connector_id == "flowaccount"
        return {
            "workspace_uuid": WORKSPACE_UUID,
            "workspace_key": "demo-workspace",
            "connector_profile_id": PROFILE_UUID,
            "connector_id": "flowaccount",
            "environment": "production",
            "preset": {
                "api_base_url": "https://openapi.flowaccount.com/v1",
                "token_url": "https://openapi.flowaccount.com/v1/token",
                "grant_type": "client_credentials",
                "scope": "flowaccount-api",
            },
            "credentials": {
                "client_id": "demo-id",
                "client_secret": "demo-secret",
            },
        }


class FakeClient:
    def __init__(self):
        self.create_calls: list[dict] = []
        self.approve_calls: list[int] = []
        self.create_error: Exception | None = None
        self.approve_error: Exception | None = None

    def list_chart_accounts(self):
        return ACCOUNTS

    def create_draft(self, payload):
        self.create_calls.append(payload)
        if self.create_error:
            raise self.create_error
        return {
            "recordId": 9001,
            "documentSerial": "JV2026070001",
            "status": 1,
            "debit": 4236,
            "credit": 4236,
        }

    def approve_draft(self, record_id):
        self.approve_calls.append(record_id)
        if self.approve_error:
            raise self.approve_error
        return {"recordId": record_id, "status": 5}


class FakeWriteStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.counter = 0

    def create_preview(self, **kwargs):
        self.counter += 1
        key = f"mjp_preview_{self.counter}"
        row = {
            "request_key": key,
            "workspace_id": kwargs["workspace_uuid"],
            "connector_profile_id": kwargs["connector_profile_id"],
            "environment": kwargs["environment"],
            "input_hash": kwargs["input_hash"],
            "payload": kwargs["payload"],
            "status": "previewed",
            "expires_at": kwargs["expires_at"].isoformat(),
        }
        self.rows[key] = row
        return row

    def load_request(self, *, request_key, workspace_uuid, workspace_key):
        row = self.rows.get(request_key)
        if not row or row["workspace_id"] != workspace_uuid:
            return None
        return row

    def find_blocking_duplicate(
        self,
        *,
        workspace_uuid,
        connector_profile_id,
        input_hash,
        exclude_request_key,
    ):
        for key, row in self.rows.items():
            if key == exclude_request_key:
                continue
            if (
                row["workspace_id"] == workspace_uuid
                and row["connector_profile_id"] == connector_profile_id
                and row["input_hash"] == input_hash
                and row["status"]
                in {"executing", "draft_created", "approved", "outcome_unknown"}
            ):
                return row
        return None

    def claim_preview(self, *, request_key, workspace_uuid, now):
        row = self.rows.get(request_key)
        if not row or row["workspace_id"] != workspace_uuid:
            return None
        if row["status"] != "previewed" or datetime.fromisoformat(row["expires_at"]) <= now:
            return None
        row["status"] = "executing"
        return row

    def record_draft(
        self,
        *,
        request_key,
        workspace_uuid,
        record_id,
        document_serial,
        response_summary,
    ):
        row = self.rows[request_key]
        row.update(
            status="draft_created",
            flowaccount_record_id=record_id,
            document_serial=document_serial,
            response_summary=response_summary,
        )
        return row

    def record_failure(
        self,
        *,
        request_key,
        workspace_uuid,
        status,
        response_summary,
    ):
        row = self.rows[request_key]
        row.update(status=status, response_summary=response_summary)
        return row

    def load_draft_by_record_id(self, *, workspace_uuid, record_id):
        for row in self.rows.values():
            if (
                row["workspace_id"] == workspace_uuid
                and row.get("flowaccount_record_id") == record_id
                and row["status"] == "draft_created"
            ):
                return row
        return None

    def claim_draft_for_approval(self, *, request_key, workspace_uuid, now):
        row = self.rows.get(request_key)
        if not row or row["status"] != "draft_created":
            return None
        row["status"] = "executing"
        return row

    def record_approved(
        self,
        *,
        request_key,
        workspace_uuid,
        approved_at,
        response_summary,
    ):
        row = self.rows[request_key]
        row.update(status="approved", response_summary=response_summary)
        return row


def make_service():
    client = FakeClient()
    write_store = FakeWriteStore()
    service = FlowAccountJournalService(
        product_store=FakeProductStore(),
        write_store=write_store,
        client_factory=lambda context: client,
        now=lambda: FIXED_NOW,
    )
    return service, client, write_store


def make_preview(service: FlowAccountJournalService) -> dict:
    return service.preview(
        workspace_id=WORKSPACE,
        document_date="2026-07-10",
        reference="MARKETPLACE-SHIPPING-2026-07-10",
        description="Marketplace shipping expense",
        lines=example_lines(),
    )


def test_preview_returns_balanced_three_line_marketplace_journal() -> None:
    service, client, write_store = make_service()

    payload = make_preview(service)

    assert payload["status"] == "awaiting_confirmation"
    assert payload["total_debit"] == "4236.00"
    assert payload["total_credit"] == "4236.00"
    assert payload["preview_id"].startswith("mjp_")
    assert payload["next_tool"] == "create_flowaccount_journal_draft"
    assert len(write_store.rows) == 1
    assert client.create_calls == []


def test_create_draft_requires_confirm_true() -> None:
    service, client, _ = make_service()
    preview = make_preview(service)

    payload = service.create_draft(
        workspace_id=WORKSPACE,
        preview_id=preview["preview_id"],
        confirm=False,
    )

    assert payload["status"] == "confirmation_required"
    assert client.create_calls == []


def test_consumed_preview_cannot_create_second_draft() -> None:
    service, client, _ = make_service()
    preview = make_preview(service)

    first = service.create_draft(
        workspace_id=WORKSPACE,
        preview_id=preview["preview_id"],
        confirm=True,
    )
    second = service.create_draft(
        workspace_id=WORKSPACE,
        preview_id=preview["preview_id"],
        confirm=True,
    )

    assert first["status"] == "draft_created"
    assert first["record_id"] == 9001
    assert first["document_serial"] == "JV2026070001"
    assert second["status"] == "duplicate_blocked"
    assert len(client.create_calls) == 1


def test_approval_requires_mercury_created_draft_and_new_confirmation() -> None:
    service, client, _ = make_service()
    preview = make_preview(service)
    service.create_draft(
        workspace_id=WORKSPACE,
        preview_id=preview["preview_id"],
        confirm=True,
    )

    not_confirmed = service.approve(
        workspace_id=WORKSPACE,
        record_id=9001,
        confirm=False,
    )
    approved = service.approve(
        workspace_id=WORKSPACE,
        record_id=9001,
        confirm=True,
    )

    assert not_confirmed["status"] == "confirmation_required"
    assert approved["status"] == "approved"
    assert client.approve_calls == [9001]


def test_approval_rejects_untracked_record() -> None:
    service, client, _ = make_service()

    payload = service.approve(
        workspace_id=WORKSPACE,
        record_id=9999,
        confirm=True,
    )

    assert payload["status"] == "not_found"
    assert client.approve_calls == []


def test_write_timeout_becomes_outcome_unknown_without_retry() -> None:
    service, client, write_store = make_service()
    preview = make_preview(service)
    client.create_error = FlowAccountOutcomeUnknown("outcome_unknown", "timeout")

    payload = service.create_draft(
        workspace_id=WORKSPACE,
        preview_id=preview["preview_id"],
        confirm=True,
    )

    assert payload["status"] == "outcome_unknown"
    assert len(client.create_calls) == 1
    assert write_store.rows[preview["preview_id"]]["status"] == "outcome_unknown"
