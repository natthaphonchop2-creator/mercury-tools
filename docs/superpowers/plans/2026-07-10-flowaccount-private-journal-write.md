# FlowAccount Private Journal Write Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated private MCP workflow that previews, creates, and separately approves one balanced multi-line FlowAccount General Journal Voucher without changing the public read-only contest MCP.

**Architecture:** Keep the existing public FastMCP server at `/mcp`, add a second FastMCP tool registry at `/private-mcp`, and protect only the private route with a dedicated bearer token. A journal domain module validates and resolves accounts, a FlowAccount adapter owns HTTP, a Supabase write-request store owns encrypted previews and idempotency, and a service coordinates the three private MCP tools.

**Tech Stack:** Python 3.11, FastMCP 1.26, Starlette, httpx, cryptography/Fernet, Supabase Postgres/PostgREST, pytest, Ruff, Render.

## Global Constraints

- The existing public `/mcp` endpoint remains read-only and must not list private write tools.
- Private write calls require `MERCURY_PRIVATE_MCP_TOKEN`; the value never enters Git, Supabase, MCP output, or audit records.
- v1 supports FlowAccount only and General Journal Voucher `documentType=51` only.
- Debit maps to `debitCredit=1`; credit maps to `debitCredit=3`.
- Production previews require `document_date`, `reference`, description, and balanced positive lines.
- Account selection is exact by code or exact unique name; fuzzy matches are suggestions only.
- Draft creation and approval are separate tool calls with separate explicit confirmation.
- Draft creation uses `POST /journal-entries/draft`; approval uses `POST /journal-entries/{id}/approve`.
- Payment, delete, void, attachment, email, share, arbitrary status reset, PEAK writes, and arbitrary connector paths remain blocked.
- A timeout, disconnect, or HTTP 5xx after write dispatch becomes `outcome_unknown` and is never retried automatically.
- CI never creates a live FlowAccount journal.

---

### Task 1: Journal Validation And Account Resolution

**Files:**
- Create: `src/mercury_tools/journals/__init__.py`
- Create: `src/mercury_tools/journals/models.py`
- Test: `tests/test_journal_models.py`

**Interfaces:**
- Consumes: FlowAccount chart rows shaped as `{"id", "code", "nameLocal", "nameForeign"}`.
- Produces: `JournalValidationError`, `PreparedJournal`, and `prepare_general_journal(...) -> PreparedJournal`.
- `PreparedJournal.flowaccount_payload` is consumed by Tasks 3 and 4.
- `PreparedJournal.input_hash(...)` is consumed by the write-request store in Task 3.

- [ ] **Step 1: Write failing model tests**

```python
# tests/test_journal_models.py
from mercury_tools.journals.models import JournalValidationError, prepare_general_journal


ACCOUNTS = [
    {"id": 501, "code": "52010", "nameLocal": "ค่าขนส่ง", "nameForeign": "Shipping expense"},
    {"id": 601, "code": "11379.01", "nameLocal": "ร้านค้าออนไลน์ - TikTok Shop", "nameForeign": "TikTok Shop"},
    {"id": 604, "code": "11379.04", "nameLocal": "ร้านค้าออนไลน์ - Shopee", "nameForeign": "Shopee"},
]


def example_lines():
    return [
        {"side": "debit", "account_name": "ค่าขนส่ง", "amount": "4236"},
        {"side": "credit", "account_code": "11379.01", "amount": "2844"},
        {"side": "credit", "account_code": "11379.04", "amount": "1392"},
    ]


def test_prepare_marketplace_shipping_journal() -> None:
    journal = prepare_general_journal(
        document_date="2026-07-10",
        reference="MARKETPLACE-SHIPPING-2026-07-10",
        description="Marketplace shipping expense",
        lines=example_lines(),
        accounts=ACCOUNTS,
        environment="production",
    )

    assert journal.total_debit == "4236.00"
    assert journal.total_credit == "4236.00"
    assert journal.flowaccount_payload["documentType"] == 51
    assert journal.flowaccount_payload["bookOfAccounts"] == [
        {"debitCredit": 1, "chartOfAccountId": 501, "value": 4236, "description": None},
        {"debitCredit": 3, "chartOfAccountId": 601, "value": 2844, "description": None},
        {"debitCredit": 3, "chartOfAccountId": 604, "value": 1392, "description": None},
    ]


def test_prepare_rejects_unbalanced_journal() -> None:
    lines = example_lines()
    lines[-1]["amount"] = "1300"

    try:
        prepare_general_journal(
            document_date="2026-07-10",
            reference="REF-1",
            description="Unbalanced",
            lines=lines,
            accounts=ACCOUNTS,
            environment="production",
        )
    except JournalValidationError as exc:
        assert exc.code == "unbalanced_journal"
    else:
        raise AssertionError("expected JournalValidationError")


def test_prepare_stops_on_ambiguous_account_name() -> None:
    accounts = [*ACCOUNTS, {"id": 502, "code": "52011", "nameLocal": "ค่าขนส่ง", "nameForeign": "Freight"}]

    try:
        prepare_general_journal(
            document_date="2026-07-10",
            reference="REF-2",
            description="Ambiguous",
            lines=example_lines(),
            accounts=accounts,
            environment="production",
        )
    except JournalValidationError as exc:
        assert exc.code == "ambiguous_account"
        assert len(exc.details["candidates"]) == 2
    else:
        raise AssertionError("expected JournalValidationError")


def test_prepare_requires_reference_in_production() -> None:
    try:
        prepare_general_journal(
            document_date="2026-07-10",
            reference="",
            description="Missing reference",
            lines=example_lines(),
            accounts=ACCOUNTS,
            environment="production",
        )
    except JournalValidationError as exc:
        assert exc.code == "reference_required"
    else:
        raise AssertionError("expected JournalValidationError")
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `uv run pytest tests/test_journal_models.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'mercury_tools.journals'`.

- [ ] **Step 3: Implement the journal domain module**

```python
# src/mercury_tools/journals/models.py
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

MONEY_QUANTUM = Decimal("0.01")
SIDE_CODES = {"debit": 1, "credit": 3}


class JournalValidationError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class PreparedJournal:
    document_date: str
    reference: str
    description: str
    total_debit: str
    total_credit: str
    preview_lines: list[dict[str, Any]]
    flowaccount_payload: dict[str, Any]

    def input_hash(self, *, workspace_id: str, connector_profile_id: str, environment: str) -> str:
        canonical = json.dumps(
            {
                "workspace_id": workspace_id,
                "connector_profile_id": connector_profile_id,
                "environment": environment,
                "payload": self.flowaccount_payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise JournalValidationError("invalid_amount", "Journal amounts must be decimal values.") from exc
    if amount <= 0:
        raise JournalValidationError("invalid_amount", "Journal amounts must be greater than zero.")
    return amount


def _json_amount(amount: Decimal) -> int | float:
    integral = amount.to_integral_value()
    return int(integral) if amount == integral else float(amount)


def _resolve_account(line: dict[str, Any], accounts: list[dict[str, Any]]) -> dict[str, Any]:
    requested_code = _normalize_text(line.get("account_code"))
    requested_name = _normalize_text(line.get("account_name"))
    if not requested_code and not requested_name:
        raise JournalValidationError("account_required", "Each journal line requires account_code or account_name.")

    if requested_code:
        matches = [row for row in accounts if _normalize_text(row.get("code")) == requested_code]
    else:
        matches = [
            row
            for row in accounts
            if requested_name
            in {_normalize_text(row.get("nameLocal")), _normalize_text(row.get("nameForeign"))}
        ]
    if not matches:
        raise JournalValidationError(
            "account_resolution_required",
            "No exact chart-of-account match was found.",
            details={"account_code": line.get("account_code"), "account_name": line.get("account_name")},
        )
    if len(matches) != 1:
        raise JournalValidationError(
            "ambiguous_account",
            "More than one chart-of-account match was found.",
            details={
                "candidates": [
                    {"code": str(row.get("code") or ""), "name": str(row.get("nameLocal") or row.get("nameForeign") or "")}
                    for row in matches
                ]
            },
        )
    return matches[0]


def prepare_general_journal(
    *,
    document_date: str,
    reference: str,
    description: str,
    lines: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    environment: str,
    note: str | None = None,
    remarks: str | None = None,
) -> PreparedJournal:
    try:
        normalized_date = date.fromisoformat(str(document_date)).isoformat()
    except ValueError as exc:
        raise JournalValidationError("invalid_document_date", "document_date must use YYYY-MM-DD.") from exc
    clean_reference = str(reference or "").strip()
    clean_description = str(description or "").strip()
    if environment == "production" and not clean_reference:
        raise JournalValidationError("reference_required", "reference is required for production journals.")
    if not clean_description:
        raise JournalValidationError("description_required", "description is required.")
    if len(lines) < 2:
        raise JournalValidationError("insufficient_lines", "A journal requires at least two lines.")

    debit = Decimal("0.00")
    credit = Decimal("0.00")
    body_lines: list[dict[str, Any]] = []
    preview_lines: list[dict[str, Any]] = []
    for line in lines:
        side = str(line.get("side") or "").strip().lower()
        if side not in SIDE_CODES:
            raise JournalValidationError("invalid_side", "side must be debit or credit.")
        amount = _money(line.get("amount"))
        account = _resolve_account(line, accounts)
        debit += amount if side == "debit" else Decimal("0.00")
        credit += amount if side == "credit" else Decimal("0.00")
        line_description = str(line.get("description") or "").strip() or None
        body_lines.append(
            {
                "debitCredit": SIDE_CODES[side],
                "chartOfAccountId": int(account["id"]),
                "value": _json_amount(amount),
                "description": line_description,
            }
        )
        preview_lines.append(
            {
                "side": side,
                "account_code": str(account.get("code") or ""),
                "account_name": str(account.get("nameLocal") or account.get("nameForeign") or ""),
                "amount": f"{amount:.2f}",
                "description": line_description,
            }
        )
    if not debit or not credit or debit != credit:
        raise JournalValidationError(
            "unbalanced_journal",
            "Total debit must equal total credit.",
            details={"total_debit": f"{debit:.2f}", "total_credit": f"{credit:.2f}"},
        )
    payload = {
        "documentType": 51,
        "documentDate": normalized_date,
        "contactId": None,
        "contactName": "",
        "description": clean_description,
        "note": str(note).strip() if note else None,
        "remarks": str(remarks).strip() if remarks else None,
        "reference": clean_reference or None,
        "bookOfAccounts": body_lines,
    }
    return PreparedJournal(
        document_date=normalized_date,
        reference=clean_reference,
        description=clean_description,
        total_debit=f"{debit:.2f}",
        total_credit=f"{credit:.2f}",
        preview_lines=preview_lines,
        flowaccount_payload=payload,
    )
```

Export these names from `src/mercury_tools/journals/__init__.py`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_journal_models.py -q`

Expected: `4 passed`.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/mercury_tools/journals tests/test_journal_models.py
git commit -m "Add FlowAccount journal validation"
```

---

### Task 2: FlowAccount Journal HTTP Adapter

**Files:**
- Create: `src/mercury_tools/connectors/flowaccount_journal.py`
- Test: `tests/test_flowaccount_journal_client.py`

**Interfaces:**
- Consumes: FlowAccount environment preset and stored `client_id`/`client_secret`.
- Produces: `FlowAccountJournalClient`, `FlowAccountJournalError`, and `FlowAccountOutcomeUnknown`.
- `list_chart_accounts()`, `create_draft(payload)`, and `approve_draft(record_id)` are consumed by Task 4.

- [ ] **Step 1: Write failing adapter tests using `httpx.MockTransport`**

```python
# tests/test_flowaccount_journal_client.py
import httpx
import pytest

from mercury_tools.connectors.flowaccount_journal import (
    FlowAccountJournalClient,
    FlowAccountOutcomeUnknown,
)


def make_client(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return FlowAccountJournalClient(
        api_base_url="https://openapi.flowaccount.com/v1",
        token_url="https://openapi.flowaccount.com/v1/token",
        client_id="client-id",
        client_secret="client-secret",
        http_client=http,
    )


def test_client_reads_chart_and_creates_then_approves_draft() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url.path == "/v1/chart-of-accounts/accounts":
            assert request.headers["Authorization"] == "Bearer access-token"
            return httpx.Response(200, json={"status": True, "data": {"accounts": [{"id": 501, "code": "52010"}]}})
        if request.url.path == "/v1/journal-entries/draft":
            return httpx.Response(200, json={"status": True, "data": {"recordId": 9001, "documentSerial": "JV2026070001", "status": 1, "debit": 4236, "credit": 4236}})
        if request.url.path == "/v1/journal-entries/9001/approve":
            return httpx.Response(200, json={"status": True, "data": {"recordId": 9001, "status": 5}})
        raise AssertionError(request.url)

    client = make_client(handler)
    assert client.list_chart_accounts()[0]["id"] == 501
    assert client.create_draft({"documentType": 51})["recordId"] == 9001
    assert client.approve_draft(9001)["status"] == 5
    assert calls == [
        ("POST", "/v1/token"),
        ("GET", "/v1/chart-of-accounts/accounts"),
        ("POST", "/v1/token"),
        ("POST", "/v1/journal-entries/draft"),
        ("POST", "/v1/token"),
        ("POST", "/v1/journal-entries/9001/approve"),
    ]


def test_write_5xx_is_outcome_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        return httpx.Response(503, json={"message": "temporary failure"})

    with pytest.raises(FlowAccountOutcomeUnknown):
        make_client(handler).create_draft({"documentType": 51})
```

- [ ] **Step 2: Run adapter tests and verify RED**

Run: `uv run pytest tests/test_flowaccount_journal_client.py -q`

Expected: import fails because `flowaccount_journal.py` does not exist.

- [ ] **Step 3: Implement the focused adapter**

```python
# src/mercury_tools/connectors/flowaccount_journal.py
from __future__ import annotations

from typing import Any

import httpx

from mercury_tools.safety.redaction import redact_json


class FlowAccountJournalError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int | None = None):
        super().__init__(str(redact_json(message))[:300])
        self.code = code
        self.status_code = status_code


class FlowAccountOutcomeUnknown(FlowAccountJournalError):
    pass


class FlowAccountJournalClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        grant_type: str = "client_credentials",
        scope: str = "flowaccount-api",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.grant_type = grant_type
        self.scope = scope
        self.http = http_client or httpx.Client(timeout=60)

    def _access_token(self) -> str:
        try:
            response = self.http.post(
                self.token_url,
                data={
                    "grant_type": self.grant_type,
                    "scope": self.scope,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise FlowAccountJournalError("authentication_failed", "FlowAccount token request failed.") from exc
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        token = str(payload.get("access_token") or "")
        if response.status_code >= 300 or not token:
            message = str(payload.get("error_description") or payload.get("message") or "FlowAccount token request failed.")
            raise FlowAccountJournalError("authentication_failed", message, status_code=response.status_code)
        return token

    def _request(self, method: str, path: str, *, write: bool, json: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._access_token()
        try:
            response = self.http.request(
                method,
                f"{self.api_base_url}/{path.lstrip('/')}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=json,
            )
        except httpx.HTTPError as exc:
            error = FlowAccountOutcomeUnknown if write else FlowAccountJournalError
            raise error("outcome_unknown" if write else "connector_unavailable", "FlowAccount request did not return a response.") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if write and response.status_code >= 500:
            raise FlowAccountOutcomeUnknown("outcome_unknown", "FlowAccount returned a server error after dispatch.", status_code=response.status_code)
        if response.status_code >= 300 or payload.get("status") is False:
            message = str(payload.get("message") or "FlowAccount rejected the request.")
            raise FlowAccountJournalError("rejected", message, status_code=response.status_code)
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def list_chart_accounts(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/chart-of-accounts/accounts", write=False)
        accounts = data.get("accounts") or []
        if not isinstance(accounts, list):
            raise FlowAccountJournalError("invalid_response", "FlowAccount chart response is invalid.")
        return [row for row in accounts if isinstance(row, dict)]

    def create_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/journal-entries/draft", write=True, json=payload)

    def approve_draft(self, record_id: int) -> dict[str, Any]:
        return self._request("POST", f"/journal-entries/{int(record_id)}/approve", write=True)
```

- [ ] **Step 4: Run focused tests and all connector tests**

Run: `uv run pytest tests/test_flowaccount_journal_client.py tests/test_connector_setup.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/mercury_tools/connectors/flowaccount_journal.py tests/test_flowaccount_journal_client.py
git commit -m "Add FlowAccount journal adapter"
```

---

### Task 3: Supabase Write Request Ledger And Private Connector Context

**Files:**
- Create: `supabase/migrations/0005_flowaccount_private_journal_writes.sql`
- Create: `src/mercury_tools/db/journal_writes.py`
- Modify: `src/mercury_tools/db/product.py:1296-1346`
- Test: `tests/test_journal_write_store.py`
- Test: `tests/test_journal_migration.py`

**Interfaces:**
- Produces: `SupabaseJournalWriteStore.create_preview`, `load_request`, `claim_preview`, `find_blocking_duplicate`, `record_draft`, `load_draft_by_record_id`, `claim_draft_for_approval`, `record_approved`, and `record_failure`.
- Produces: `SupabaseProductStore.get_private_connector_context(workspace_id, connector_id) -> dict` containing workspace UUID/key, connector-profile UUID, environment, preset, and decrypted credentials.
- Consumes: `PreparedJournal` from Task 1.

- [ ] **Step 1: Write failing migration and store tests**

```python
# tests/test_journal_migration.py
from pathlib import Path


def test_private_journal_migration_is_service_role_only() -> None:
    sql = Path("supabase/migrations/0005_flowaccount_private_journal_writes.sql").read_text()
    assert "create table if not exists public.connector_write_requests" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.connector_write_requests from anon, authenticated" in sql
    assert "grant all on table public.connector_write_requests to service_role" in sql
    assert "where status in ('executing', 'draft_created', 'approved', 'outcome_unknown')" in sql
```

```python
# tests/test_journal_write_store.py
from datetime import UTC, datetime, timedelta

from mercury_tools.config import Settings
from mercury_tools.db.journal_writes import SupabaseJournalWriteStore


def settings() -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role",
        openai_api_key="",
        connect_signing_secret="vault-secret",
    )


def test_preview_payload_is_encrypted_and_can_be_loaded(monkeypatch) -> None:
    store = SupabaseJournalWriteStore(settings())
    captured = {}

    def fake_request(method, path, **kwargs):
        if method == "POST":
            captured.update(kwargs["json"][0])
            return [{**captured, "id": "00000000-0000-0000-0000-000000000001"}]
        return [{**captured, "id": "00000000-0000-0000-0000-000000000001"}]

    monkeypatch.setattr(store, "_request", fake_request)
    row = store.create_preview(
        workspace_uuid="10000000-0000-0000-0000-000000000001",
        connector_profile_id="20000000-0000-0000-0000-000000000001",
        workspace_key="demo-workspace",
        environment="production",
        input_hash="a" * 64,
        payload={"flowaccount_payload": {"reference": "REF-1"}, "preview": {"total_debit": "4236.00"}},
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=10),
    )

    assert row["request_key"].startswith("mjp_")
    assert "REF-1" not in captured["encrypted_payload"]
    loaded = store.load_request(
        request_key=row["request_key"],
        workspace_uuid="10000000-0000-0000-0000-000000000001",
        workspace_key="demo-workspace",
    )
    assert loaded["payload"]["flowaccount_payload"]["reference"] == "REF-1"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_journal_migration.py tests/test_journal_write_store.py -q`

Expected: missing migration and module failures.

- [ ] **Step 3: Add the migration**

```sql
-- supabase/migrations/0005_flowaccount_private_journal_writes.sql
create table if not exists public.connector_write_requests (
  id uuid primary key default gen_random_uuid(),
  request_key text not null unique,
  workspace_id uuid not null references public.mercury_workspaces(id) on delete cascade,
  connector_profile_id uuid not null references public.mercury_connector_profiles(id) on delete cascade,
  connector_id text not null default 'flowaccount' check (connector_id = 'flowaccount'),
  environment text not null check (environment in ('production', 'sandbox')),
  operation text not null default 'journal.create' check (operation = 'journal.create'),
  input_hash text not null,
  encrypted_payload text not null,
  payload_version integer not null default 1,
  status text not null default 'previewed' check (
    status in ('previewed', 'executing', 'draft_created', 'approved', 'failed', 'outcome_unknown', 'expired', 'cancelled')
  ),
  flowaccount_record_id bigint,
  document_serial text,
  response_summary jsonb not null default '{}'::jsonb,
  expires_at timestamptz not null,
  executed_at timestamptz,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists connector_write_requests_workspace_idx
  on public.connector_write_requests (workspace_id, created_at desc);

create index if not exists connector_write_requests_record_idx
  on public.connector_write_requests (workspace_id, flowaccount_record_id)
  where flowaccount_record_id is not null;

create unique index if not exists connector_write_requests_dedupe_idx
  on public.connector_write_requests (workspace_id, connector_profile_id, operation, input_hash)
  where status in ('executing', 'draft_created', 'approved', 'outcome_unknown');

alter table public.connector_write_requests enable row level security;
revoke all on table public.connector_write_requests from anon, authenticated;
grant all on table public.connector_write_requests to service_role;
```

- [ ] **Step 4: Implement the store and private connector context**

Implement `src/mercury_tools/db/journal_writes.py` with Fernet encryption derived from the existing `vault_key(settings, workspace_key)`. Use `secrets.token_urlsafe(18)` for `request_key`, PostgREST `Prefer: return=representation`, conditional PATCH filters for state transitions, and fail closed on all schema errors. Never use the existing audit fallback for writes.

Add this exact server-only interface to `SupabaseProductStore`:

```python
def get_private_connector_context(self, workspace_id: str, connector_id: str) -> dict[str, Any]:
    token_payload = public_workspace_token_payload(workspace_id)
    context = self.workspace_for_token(token_payload)
    if not context:
        raise ValueError("Workspace was not found.")
    rows = self._request(
        "GET",
        "mercury_connector_profiles",
        params={
            "workspace_id": f"eq.{context['workspace']['id']}",
            "connector_id": f"eq.{connector_id}",
            "select": "id,connector_id,environment,status,metadata",
            "order": "updated_at.desc",
        },
    )
    ready = [
        row
        for row in rows or []
        if str((row.get("metadata") or {}).get("setup_state") or "").lower() == "ready"
        and isinstance((row.get("metadata") or {}).get("server_vault"), dict)
    ]
    if len(ready) != 1:
        raise ValueError("Exactly one ready FlowAccount connector profile is required.")
    profile = ready[0]
    credentials = decrypt_connector_credentials(
        self.settings,
        workspace_key_value=context["workspace"]["workspace_key"],
        vault_record=profile["metadata"]["server_vault"],
    )
    return {
        "workspace_uuid": context["workspace"]["id"],
        "workspace_key": context["workspace"]["workspace_key"],
        "connector_profile_id": profile["id"],
        "connector_id": connector_id,
        "environment": profile["environment"],
        "preset": connector_by_id(connector_id).preset_for_environment(profile["environment"]),
        "credentials": credentials,
    }
```

Store state methods must use these legal transitions:

```text
previewed -> executing -> draft_created -> executing -> approved
previewed -> expired
executing -> failed
executing -> outcome_unknown
```

- [ ] **Step 5: Run store, connector setup, and migration tests**

Run: `uv run pytest tests/test_journal_migration.py tests/test_journal_write_store.py tests/test_connector_setup.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add supabase/migrations/0005_flowaccount_private_journal_writes.sql src/mercury_tools/db/journal_writes.py src/mercury_tools/db/product.py tests/test_journal_migration.py tests/test_journal_write_store.py tests/test_connector_setup.py
git commit -m "Persist private journal write requests"
```

---

### Task 4: Preview, Draft, And Approval Service

**Files:**
- Create: `src/mercury_tools/journals/service.py`
- Modify: `src/mercury_tools/journals/__init__.py`
- Test: `tests/test_journal_service.py`

**Interfaces:**
- Consumes: `prepare_general_journal`, `FlowAccountJournalClient`, `SupabaseProductStore`, and `SupabaseJournalWriteStore`.
- Produces: `FlowAccountJournalService.preview`, `create_draft`, and `approve` returning redacted dictionaries for MCP tools.

- [ ] **Step 1: Write failing orchestration tests**

Create fakes for product context, write store, and client. Cover these exact behaviors:

```python
def test_preview_returns_balanced_three_line_marketplace_journal():
    payload = service.preview(
        workspace_id="mw_publiccontestworkspace001",
        document_date="2026-07-10",
        reference="MARKETPLACE-SHIPPING-2026-07-10",
        description="Marketplace shipping expense",
        lines=example_lines(),
    )
    assert payload["status"] == "awaiting_confirmation"
    assert payload["total_debit"] == "4236.00"
    assert payload["total_credit"] == "4236.00"
    assert payload["preview_id"].startswith("mjp_")


def test_create_draft_requires_confirm_true():
    assert service.create_draft(workspace_id=WORKSPACE, preview_id=PREVIEW, confirm=False)["status"] == "confirmation_required"
    assert fake_client.create_calls == []


def test_consumed_preview_cannot_create_second_draft():
    first = service.create_draft(workspace_id=WORKSPACE, preview_id=PREVIEW, confirm=True)
    second = service.create_draft(workspace_id=WORKSPACE, preview_id=PREVIEW, confirm=True)
    assert first["status"] == "draft_created"
    assert second["status"] == "duplicate_blocked"
    assert len(fake_client.create_calls) == 1


def test_approval_requires_mercury_created_draft_and_new_confirmation():
    assert service.approve(workspace_id=WORKSPACE, record_id=9001, confirm=False)["status"] == "confirmation_required"
    approved = service.approve(workspace_id=WORKSPACE, record_id=9001, confirm=True)
    assert approved["status"] == "approved"
    assert fake_client.approve_calls == [9001]


def test_write_timeout_becomes_outcome_unknown_without_retry():
    fake_client.create_error = FlowAccountOutcomeUnknown("outcome_unknown", "timeout")
    result = service.create_draft(workspace_id=WORKSPACE, preview_id=PREVIEW, confirm=True)
    assert result["status"] == "outcome_unknown"
    assert len(fake_client.create_calls) == 1
```

- [ ] **Step 2: Run service tests and verify RED**

Run: `uv run pytest tests/test_journal_service.py -q`

Expected: import fails because `journals/service.py` does not exist.

- [ ] **Step 3: Implement `FlowAccountJournalService`**

Use constructor injection so tests never touch the network:

```python
class FlowAccountJournalService:
    def __init__(self, *, product_store, write_store, client_factory, now=None):
        self.product_store = product_store
        self.write_store = write_store
        self.client_factory = client_factory
        self.now = now or (lambda: datetime.now(tz=UTC))
```

Implement the public methods with this flow:

```python
def preview(self, *, workspace_id, document_date, reference, description, lines, note=None, remarks=None):
    context = self.product_store.get_private_connector_context(workspace_id, "flowaccount")
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
    row = self.write_store.create_preview(
        workspace_uuid=context["workspace_uuid"],
        connector_profile_id=context["connector_profile_id"],
        workspace_key=context["workspace_key"],
        environment=context["environment"],
        input_hash=input_hash,
        payload={"flowaccount_payload": prepared.flowaccount_payload, "preview": prepared.preview_lines},
        expires_at=self.now() + timedelta(minutes=10),
    )
    return redact_json({
        "status": "awaiting_confirmation",
        "preview_id": row["request_key"],
        "environment": context["environment"],
        "document_type": "JV",
        "document_date": prepared.document_date,
        "reference": prepared.reference,
        "description": prepared.description,
        "total_debit": prepared.total_debit,
        "total_credit": prepared.total_credit,
        "lines": prepared.preview_lines,
        "expires_at": row["expires_at"],
        "next_tool": "create_flowaccount_journal_draft",
    })
```

`create_draft` must call `claim_preview` before dispatch, check `find_blocking_duplicate`, and then call the adapter exactly once. On success store `recordId`, `documentSerial`, `status`, `debit`, and `credit`. On `FlowAccountOutcomeUnknown`, store `outcome_unknown`; on a definitive `FlowAccountJournalError`, store `failed`.

`approve` must find the draft by workspace UUID and `record_id`, atomically move `draft_created -> executing`, call `approve_draft` once, and record `approved`, `failed`, or `outcome_unknown`.

- [ ] **Step 4: Run service and lower-layer tests**

Run: `uv run pytest tests/test_journal_service.py tests/test_journal_models.py tests/test_flowaccount_journal_client.py tests/test_journal_write_store.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/mercury_tools/journals tests/test_journal_service.py
git commit -m "Orchestrate private journal writes"
```

---

### Task 5: Authenticated Private MCP Surface

**Files:**
- Modify: `src/mercury_tools/config.py:11-45,80-130`
- Create: `src/mercury_tools/mcp/private_server.py`
- Modify: `src/mercury_tools/mcp/server.py:7-18,64-92,2685-2764`
- Test: `tests/test_private_mcp.py`
- Modify: `tests/test_http_app.py`

**Interfaces:**
- Produces private MCP tools: `preview_flowaccount_journal`, `create_flowaccount_journal_draft`, `approve_flowaccount_journal`.
- Produces settings: `private_mcp_path`, `private_mcp_bearer_token`, `private_mcp_configured`.
- Public MCP tool registry remains unchanged.

- [ ] **Step 1: Write failing auth and tool-contract tests**

```python
# tests/test_private_mcp.py
import pytest
from starlette.testclient import TestClient

from mercury_tools.mcp.private_server import private_mcp
from mercury_tools.mcp.server import create_http_app, mcp


@pytest.mark.asyncio
async def test_public_and_private_tool_registries_are_separate() -> None:
    public_names = {tool.name for tool in await mcp.list_tools()}
    private_names = {tool.name for tool in await private_mcp.list_tools()}
    expected = {
        "preview_flowaccount_journal",
        "create_flowaccount_journal_draft",
        "approve_flowaccount_journal",
    }
    assert expected.isdisjoint(public_names)
    assert private_names == expected


def test_private_mcp_requires_dedicated_bearer(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_PRIVATE_MCP_TOKEN", "private-token")
    monkeypatch.setenv("MERCURY_PRIVATE_MCP_PATH", "/private-mcp")
    client = TestClient(create_http_app(require_auth=False), raise_server_exceptions=False)

    assert client.get("/mcp").status_code != 401
    assert client.get("/private-mcp").status_code == 401
    assert client.get("/private-mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/private-mcp", headers={"Authorization": "Bearer private-token"}).status_code != 401
```

Add a tool-function test that monkeypatches `_journal_service` and verifies `JournalValidationError` is returned as `{status, message, details}` with no traceback or secret.

- [ ] **Step 2: Run private MCP tests and verify RED**

Run: `uv run pytest tests/test_private_mcp.py -q`

Expected: import fails because `private_server.py` does not exist.

- [ ] **Step 3: Add private settings and private MCP tools**

Add to `Settings` and `load_settings`:

```python
private_mcp_path: str = "/private-mcp"
private_mcp_bearer_token: str = ""

@property
def private_mcp_configured(self) -> bool:
    return bool(self.private_mcp_bearer_token)
```

Read `MERCURY_PRIVATE_MCP_PATH` and `MERCURY_PRIVATE_MCP_TOKEN` from the environment.

In `private_server.py`, create `private_mcp = FastMCP("Mercury Finance Private")`, a `_journal_service()` factory, and the three decorated tool functions. Catch only expected `JournalValidationError`, `FlowAccountJournalError`, `PermissionError`, `RuntimeError`, and `ValueError`; return redacted structured errors and write audit summaries through the existing audit store.

- [ ] **Step 4: Mount both MCP apps with independent lifespans**

Refactor `create_http_app` to build a parent Starlette app. Keep the public MCP route at `settings.mcp_path`. When `private_mcp_configured` is true, add the private MCP route at `settings.private_mcp_path` and compose both child lifespan contexts:

```python
@asynccontextmanager
async def lifespan(_app):
    async with public_app.router.lifespan_context(public_app):
        if private_app is None:
            yield
        else:
            async with private_app.router.lifespan_context(private_app):
                yield

routes = [*public_app.routes]
if private_app is not None:
    routes.extend(private_app.routes)
app = Starlette(routes=routes, lifespan=lifespan)
```

Add root, `/api/status`, `/healthz`, and optional legacy routes to the parent app exactly as before. Add `PrivateBearerAuthMiddleware` only for `settings.private_mcp_path`; compare only `settings.private_mcp_bearer_token` with `hmac.compare_digest`. Do not accept public Mercury client tokens on the private route.

Expose only these safe health fields:

```json
{
  "private_mcp": "enabled",
  "private_mcp_path": "/private-mcp"
}
```

Never expose whether a supplied token matched or any token fingerprint.

- [ ] **Step 5: Run HTTP and MCP contract tests**

Run: `uv run pytest tests/test_private_mcp.py tests/test_http_app.py tests/test_mcp_contract.py -q`

Expected: all tests pass; public route remains unauthenticated when configured that way and private route returns 401 without its dedicated token.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/mercury_tools/config.py src/mercury_tools/mcp/private_server.py src/mercury_tools/mcp/server.py tests/test_private_mcp.py tests/test_http_app.py
git commit -m "Expose authenticated private journal MCP"
```

---

### Task 6: Private Codex Plugin And Gated Journal Skill

**Files:**
- Create: `plugins/mercury-finance-private/.codex-plugin/plugin.json`
- Create: `plugins/mercury-finance-private/.mcp.json`
- Create: `plugins/mercury-finance-private/skills/flowaccount-journal-posting-th/SKILL.md`
- Modify: `.agents/plugins/marketplace.json`
- Modify: `src/mercury_tools/db/product.py:27-121`
- Create: `supabase/migrations/20260710_add_flowaccount_journal_skill.sql`
- Modify: `tests/test_plugin_package.py`

**Interfaces:**
- Produces plugin `mercury-finance-private` with `Interactive`, `Read`, and `Write` capabilities.
- Produces skill `flowaccount-journal-posting-th` that enforces preview, draft confirmation, and separate approval confirmation.

- [ ] **Step 1: Write failing plugin package tests**

```python
def test_private_plugin_declares_write_capability_and_bearer_env() -> None:
    root = ROOT / "plugins/mercury-finance-private"
    plugin = json.loads((root / ".codex-plugin/plugin.json").read_text())
    mcp = json.loads((root / ".mcp.json").read_text())
    server = mcp["mcpServers"]["mercury-finance-private"]

    assert plugin["name"] == "mercury-finance-private"
    assert plugin["interface"]["capabilities"] == ["Interactive", "Read", "Write"]
    assert server["url"] == "https://mercury-tools-mcp.onrender.com/private-mcp"
    assert server["bearer_token_env_var"] == "MERCURY_PRIVATE_MCP_TOKEN"
    assert "private-token" not in json.dumps(mcp)


def test_private_skill_stops_between_preview_draft_and_approval() -> None:
    skill = (ROOT / "plugins/mercury-finance-private/skills/flowaccount-journal-posting-th/SKILL.md").read_text()
    ordered = [
        "preview_flowaccount_journal",
        "wait for explicit confirmation",
        "create_flowaccount_journal_draft",
        "wait for a new explicit confirmation",
        "approve_flowaccount_journal",
    ]
    positions = [skill.index(item) for item in ordered]
    assert positions == sorted(positions)
```

- [ ] **Step 2: Run plugin tests and verify RED**

Run: `uv run pytest tests/test_plugin_package.py -q`

Expected: missing private plugin files.

- [ ] **Step 3: Create the private plugin and skill**

Use this MCP configuration with no embedded value:

```json
{
  "mcpServers": {
    "mercury-finance-private": {
      "type": "http",
      "url": "https://mercury-tools-mcp.onrender.com/private-mcp",
      "bearer_token_env_var": "MERCURY_PRIVATE_MCP_TOKEN"
    }
  }
}
```

The skill must be under 80 lines and state:

1. Use when the user asks to post or approve a FlowAccount journal.
2. Collect missing `document_date`, `reference`, description, and lines.
3. Never infer an ambiguous account.
4. Call `preview_flowaccount_journal` and render a Dr/Cr table.
5. Stop and wait for explicit confirmation.
6. Call `create_flowaccount_journal_draft` only after confirmation.
7. Show record ID/document serial.
8. Stop and wait for a new explicit confirmation.
9. Call `approve_flowaccount_journal` only after the second confirmation.
10. Never retry `outcome_unknown`.

Add the plugin to `.agents/plugins/marketplace.json` as an `AVAILABLE` Finance plugin with `authentication: ON_INSTALL`. Add the skill to `SKILL_CATALOG_SEED` and to an idempotent Supabase catalog migration.

- [ ] **Step 4: Run plugin validation and tests**

Run: `uv run pytest tests/test_plugin_package.py -q`

Run: `python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance-private`

Expected: tests pass and validator prints `Plugin validation passed`.

- [ ] **Step 5: Commit Task 6**

```bash
git add plugins/mercury-finance-private .agents/plugins/marketplace.json src/mercury_tools/db/product.py supabase/migrations/20260710_add_flowaccount_journal_skill.sql tests/test_plugin_package.py
git commit -m "Add private FlowAccount journal plugin"
```

---

### Task 7: Deployment Configuration, Documentation, And Verification

**Files:**
- Modify: `.env.example`
- Modify: `render.yaml`
- Create: `docs/PRIVATE_JOURNAL_MCP.md`
- Modify: `docs/JUDGE_QUICKSTART.md`
- Modify: `README.md`
- Test: full suite and live MCP protocol smoke

**Interfaces:**
- Produces operator setup instructions and Render secret configuration.
- Does not change the judge's public plugin install path or public MCP URL.

- [ ] **Step 1: Write failing documentation assertions**

Extend `tests/test_plugin_package.py`:

```python
def test_private_journal_docs_keep_secrets_out_of_git() -> None:
    text = (ROOT / "docs/PRIVATE_JOURNAL_MCP.md").read_text()
    assert "MERCURY_PRIVATE_MCP_TOKEN" in text
    assert "preview_flowaccount_journal" in text
    assert "create_flowaccount_journal_draft" in text
    assert "approve_flowaccount_journal" in text
    assert "POST /journal-entries/draft" in text
    assert "POST /journal-entries/{id}/approve" in text
    assert "actual-token" not in text
```

- [ ] **Step 2: Run the documentation test and verify RED**

Run: `uv run pytest tests/test_plugin_package.py::test_private_journal_docs_keep_secrets_out_of_git -q`

Expected: missing documentation file.

- [ ] **Step 3: Add deployment settings and operator guide**

Add to `.env.example` and `render.yaml`:

```yaml
- key: MERCURY_PRIVATE_MCP_PATH
  value: /private-mcp
- key: MERCURY_PRIVATE_MCP_TOKEN
  sync: false
```

Document these exact operator steps without a real token:

```bash
export MERCURY_PRIVATE_MCP_TOKEN="<company-private-token>"
codex mcp add mercury-finance-private \
  --url https://mercury-tools-mcp.onrender.com/private-mcp \
  --bearer-token-env-var MERCURY_PRIVATE_MCP_TOKEN
```

State explicitly that the public contest MCP remains read-only, that a Draft JV has no financial-statement impact until approval, and that production execution requires the final date, reference, resolved accounts, amounts, and separate confirmations.

- [ ] **Step 4: Run local quality gates**

Run: `uv run pytest`

Expected: all unit tests pass; the existing live integration test may skip without Supabase environment variables.

Run: `uv run ruff check .`

Expected: `All checks passed!`

Run both plugin validators:

```bash
python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance
python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance-private
```

Expected: both pass.

- [ ] **Step 5: Apply Supabase migrations**

Apply `0005_flowaccount_private_journal_writes.sql` and `20260710_add_flowaccount_journal_skill.sql` to the linked Supabase project. Verify with service-role access that `connector_write_requests` exists and with anonymous access that SELECT and INSERT are denied.

Expected: table exists, RLS is enabled, and no anonymous/authenticated DML succeeds.

- [ ] **Step 6: Configure Render and deploy without exposing the token**

Generate the token locally, save it directly to the Render secret environment and the operator's local secret manager, and do not print it in logs or commit it:

```bash
openssl rand -base64 48
```

Deploy the tested commit to `srv-d978tk37uimc73ej52mg` with Render CLI and wait for `status: live`.

- [ ] **Step 7: Run live transport and tool-list smoke tests**

Verify:

1. `GET /healthz` returns 200 with `private_mcp=enabled`.
2. Unauthenticated `/private-mcp` returns 401.
3. Authenticated MCP initialization succeeds.
4. Private tool list contains exactly the three journal tools.
5. Public MCP tool list contains none of the three journal tools.
6. `preview_flowaccount_journal` performs only token and Chart of Accounts reads and returns a balanced preview.

Do not call draft creation until the user supplies and confirms:

- final `document_date`;
- unique production `reference`;
- exact resolved shipping-expense account;
- TikTok credit `11379.01` for `2,844.00`;
- Shopee credit `11379.04` for `1,392.00`.

- [ ] **Step 8: Record deployment evidence and commit**

Update `docs/PRIVATE_JOURNAL_MCP.md` with the tested commit, Render deploy ID, test count, and smoke-test statuses. Do not include credentials, full journal payloads, tax IDs, or account IDs.

```bash
git add .env.example render.yaml README.md docs/PRIVATE_JOURNAL_MCP.md docs/JUDGE_QUICKSTART.md tests/test_plugin_package.py
git commit -m "Document private journal deployment"
git push origin mercury-public-mcp-contest
```

Expected: PR #2 CI is green and the branch remains available for review.

