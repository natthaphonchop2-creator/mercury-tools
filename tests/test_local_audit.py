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
        assert stat.S_IMODE(ledger.index_path.stat().st_mode) == 0o600


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


def test_audit_ledger_never_returns_partial_match_from_corrupted_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        "not-json\n"
        '{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa","authorization":"Bearer token"}\n'
    )
    ledger = AuditLedger(path)

    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    with pytest.raises(ValueError, match="^audit_ledger_corrupt$"):
        ledger.get("evt_aaaaaaaaaaaaaaaaaaaaaaaa")
    assert ledger.get("evt_not-an-event-id") is None


def test_audit_ledger_strict_allowlists_drop_dynamic_keys(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa",'
        '"connector_id":"flowaccount",'
        '"person@example.com":"secret",'
        '"cus_9f83ab12":"record",'
        '"response_summary":{"status_class":"2xx",'
        '"invoice_number":"INV-001","0105559999999":"tax"}}\n'
    )
    ledger = AuditLedger(path)

    row = ledger.get("evt_aaaaaaaaaaaaaaaaaaaaaaaa")

    assert row == {
        "event_id": "evt_aaaaaaaaaaaaaaaaaaaaaaaa",
        "connector_id": "flowaccount",
        "response_summary": {"status_class": "2xx"},
    }
    assert "person@example.com" not in str(row)


def test_audit_ledger_rejects_non_scalar_allowlisted_values_without_recursing(
    tmp_path: Path,
) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError, match="^invalid_audit_event$"):
        ledger.record({"event": cyclic})
    with pytest.raises(ValueError, match="^invalid_audit_event$"):
        ledger.record({"response_summary": {"status": cyclic}})


def test_audit_ledger_drops_unknown_cyclic_values_without_inspecting_them(
    tmp_path: Path,
) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    event_id = ledger.record({"event": "confirmed", "unknown": cyclic})

    assert ledger.get(event_id)["event"] == "confirmed"  # type: ignore[index]


@pytest.mark.parametrize(
    "event",
    [
        {"event": "Ada Lovelace"},
        {"method": "post"},
        {"payload_hash": "not-a-hash"},
        {"confirmation_count": -1},
        {"request_id": "invoice-customer-Ada"},
        {"response_summary": {"http_status": 999}},
    ],
)
def test_audit_ledger_strictly_validates_known_fields(
    tmp_path: Path,
    event: dict[str, object],
) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")

    with pytest.raises(ValueError, match="^invalid_audit_event$"):
        ledger.record(event)


def test_audit_ledger_bounds_known_scalar_and_top_level_key_counts(
    tmp_path: Path,
) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")

    with pytest.raises(ValueError, match="^audit_event_too_large$"):
        ledger.record({"event": "x" * (audit.MAX_AUDIT_SCALAR_BYTES + 1)})
    with pytest.raises(ValueError, match="^audit_event_too_large$"):
        ledger.record(
            {
                f"unknown_{index}": index
                for index in range(audit.MAX_AUDIT_TOP_LEVEL_KEYS + 1)
            }
        )


def test_audit_ledger_only_retains_opaque_mercury_artifact_references(
    tmp_path: Path,
) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    opaque = "artifacts/art_" + "a" * 24 + ".json"

    kept = ledger.get(ledger.record({"event": "completed", "artifact_path": opaque}))
    redacted = ledger.get(
        ledger.record(
            {
                "event": "completed",
                "artifact_path": "/Customers/Ada Lovelace/INV-001.pdf",
            }
        )
    )

    assert kept is not None and kept["artifact_path"] == opaque
    assert redacted is not None and redacted["artifact_path"] == "[REDACTED]"


def test_audit_ledger_duplicate_event_ids_are_stable_corruption(tmp_path: Path) -> None:
    event_id = "evt_" + "a" * 24
    row = json.dumps({"event_id": event_id, "event": "confirmed"}).encode() + b"\n"
    path = tmp_path / "audit.jsonl"
    path.write_bytes(row + row)
    ledger = AuditLedger(path)

    with pytest.raises(ValueError, match="^audit_ledger_corrupt$"):
        ledger.get(event_id)


def test_audit_ledger_stale_index_rebuilds_from_append_only_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(path)
    ledger.record({"event": "preview_created"})
    appended_id = "evt_" + "b" * 24
    with path.open("ab") as handle:
        handle.write(
            json.dumps({"event_id": appended_id, "event": "confirmed"}).encode()
            + b"\n"
        )

    assert ledger.get(appended_id) == {
        "event": "confirmed",
        "event_id": appended_id,
    }


def test_audit_ledger_stale_index_detects_appended_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(path)
    event_id = ledger.record({"event": "preview_created"})
    with path.open("ab") as handle:
        handle.write(
            json.dumps({"event_id": event_id, "event": "confirmed"}).encode()
            + b"\n"
        )

    with pytest.raises(ValueError, match="^audit_ledger_corrupt$"):
        ledger.get(event_id)


def test_audit_ledger_growth_beyond_old_scan_limit_remains_readable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    rows: list[bytes] = []
    for index in range(145):
        event_id = f"evt_{index:024x}"
        row = (
            json.dumps(
                {
                    "event_id": event_id,
                    "event": "confirmed",
                    "unknown_padding": "x" * 60_000,
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )
        assert len(row) <= audit.MAX_AUDIT_LINE_BYTES
        rows.append(row)
    path.write_bytes(b"".join(rows))
    assert path.stat().st_size > 8 * 1024 * 1024
    ledger = AuditLedger(path)

    assert ledger.get("evt_000000000000000000000090") == {
        "event": "confirmed",
        "event_id": "evt_000000000000000000000090",
    }


def test_audit_ledger_rejects_oversized_line_without_partial_result(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_bytes(
        b'{"event_id":"evt_aaaaaaaaaaaaaaaaaaaaaaaa","event":"confirmed"}\n'
        + b"x" * (audit.MAX_AUDIT_LINE_BYTES + 1)
        + b"\n"
    )
    ledger = AuditLedger(path)

    with pytest.raises(ValueError, match="^audit_ledger_corrupt$"):
        ledger.get("evt_aaaaaaaaaaaaaaaaaaaaaaaa")
