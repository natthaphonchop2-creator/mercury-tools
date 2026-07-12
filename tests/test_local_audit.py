from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

from mercury_tools.local import audit
from mercury_tools.local.audit import AuditLedger


def test_audit_ledger_redacts_credentials_personal_fields_and_request_inputs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(path)

    event_id = ledger.record(
        {
            "connector_id": "flowaccount",
            "authorization": "Bearer token",
            "email": "person@example.com",
            "tax_id": "0105559999999",
            "customer_name": "Ada Lovelace",
            "request_inputs": {"body": {"invoice_number": "INV-001"}},
        }
    )

    text = path.read_text()
    row = ledger.get(event_id)
    assert re.fullmatch(r"evt_[0-9a-f]{24}", event_id)
    assert "Bearer token" not in text
    assert "person@example.com" not in text
    assert "0105559999999" not in text
    assert "Ada Lovelace" not in text
    assert "INV-001" not in text
    assert row is not None
    assert row["event_id"] == event_id
    assert row["connector_id"] == "flowaccount"
    assert "request_inputs" not in row
    assert "authorization" not in row
    assert "email" not in row
    assert "tax_id" not in row
    assert "customer_name" not in row


def test_audit_ledger_appends_jsonl_and_enforces_owner_only_mode(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(path)
    first = ledger.record({"event": "preview_created", "payload_hash": "a" * 64})
    second = ledger.record({"event": "confirmed", "payload_hash": "a" * 64})

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event_id"] for row in rows] == [first, second]
    assert all("recorded_at" in row for row in rows)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_audit_ledger_keeps_response_classification_without_provider_record_values(
    tmp_path: Path,
) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    event_id = ledger.record(
        {
            "event": "execution_completed",
            "response_summary": {
                "status_class": "2xx",
                "http_status": 201,
                "invoice_number": "INV-0001",
                "customer": "Ada Lovelace",
            },
        }
    )

    row = ledger.get(event_id)

    assert row is not None
    assert row["response_summary"]["status_class"] == "2xx"
    assert row["response_summary"]["http_status"] == 201
    assert "INV-0001" not in str(row)
    assert "Ada Lovelace" not in str(row)


def test_audit_ledger_get_is_safe_for_tampered_rows(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        "not-json\n"
        '{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa","authorization":"Bearer token"}\n'
    )
    ledger = AuditLedger(path)

    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    row = ledger.get("evt_aaaaaaaaaaaaaaaaaaaaaaaa")

    assert row is not None
    assert "authorization" not in row
    assert ledger.get("evt_not-an-event-id") is None


def test_audit_ledger_strict_allowlists_drop_dynamic_keys(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa",'
        '"connector_id":"flowaccount",'
        '"person@example.com":"secret",'
        '"cus_9f83ab12":"record",'
        '"artifact_path":{"person@example.com":"nested"},'
        '"response_summary":{"status_class":"2xx",'
        '"invoice_number":"INV-001","0105559999999":"tax"}}\n'
    )
    ledger = AuditLedger(path)

    row = ledger.get("evt_aaaaaaaaaaaaaaaaaaaaaaaa")

    assert row == {
        "event_id": "evt_aaaaaaaaaaaaaaaaaaaaaaaa",
        "connector_id": "flowaccount",
        "artifact_path": "[REDACTED]",
        "response_summary": {"status_class": "2xx"},
    }
    assert "person@example.com" not in str(row)


def test_audit_ledger_get_fails_without_partial_result_on_oversized_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_bytes(
        b'{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa","event":"confirmed"}\n'
        + b"x" * (audit.MAX_AUDIT_LINE_BYTES + 1)
        + b"\n"
    )
    ledger = AuditLedger(path)

    with pytest.raises(ValueError, match="^audit_scan_limit_exceeded$"):
        ledger.get("evt_aaaaaaaaaaaaaaaaaaaaaaaa")


@pytest.mark.parametrize(
    ("constant", "limit", "content"),
    [
        (
            "MAX_AUDIT_SCAN_BYTES",
            32,
            b'{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa"}\n',
        ),
        (
            "MAX_AUDIT_SCAN_LINES",
            1,
            b'{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa"}\n{}\n',
        ),
    ],
)
def test_audit_ledger_get_enforces_total_scan_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    limit: int,
    content: bytes,
) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_bytes(content)
    ledger = AuditLedger(path)
    monkeypatch.setattr(audit, constant, limit)

    with pytest.raises(ValueError, match="^audit_scan_limit_exceeded$"):
        ledger.get("evt_aaaaaaaaaaaaaaaaaaaaaaaa")
