from __future__ import annotations

import pytest

from mercury_tools.journals.models import JournalValidationError, prepare_general_journal

ACCOUNTS = [
    {
        "id": 501,
        "code": "52010",
        "nameLocal": "ค่าขนส่ง",
        "nameForeign": "Shipping expense",
    },
    {
        "id": 601,
        "code": "11379.01",
        "nameLocal": "ร้านค้าออนไลน์ - TikTok Shop",
        "nameForeign": "TikTok Shop",
    },
    {
        "id": 604,
        "code": "11379.04",
        "nameLocal": "ร้านค้าออนไลน์ - Shopee",
        "nameForeign": "Shopee",
    },
]


def example_lines() -> list[dict[str, str]]:
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
        {
            "debitCredit": 1,
            "chartOfAccountId": 501,
            "value": 4236,
            "description": None,
        },
        {
            "debitCredit": 3,
            "chartOfAccountId": 601,
            "value": 2844,
            "description": None,
        },
        {
            "debitCredit": 3,
            "chartOfAccountId": 604,
            "value": 1392,
            "description": None,
        },
    ]


def test_prepare_rejects_unbalanced_journal() -> None:
    lines = example_lines()
    lines[-1]["amount"] = "1300"

    with pytest.raises(JournalValidationError) as caught:
        prepare_general_journal(
            document_date="2026-07-10",
            reference="REF-1",
            description="Unbalanced",
            lines=lines,
            accounts=ACCOUNTS,
            environment="production",
        )

    assert caught.value.code == "unbalanced_journal"
    assert caught.value.details == {
        "total_debit": "4236.00",
        "total_credit": "4144.00",
    }


def test_prepare_stops_on_ambiguous_account_name() -> None:
    accounts = [
        *ACCOUNTS,
        {
            "id": 502,
            "code": "52011",
            "nameLocal": "ค่าขนส่ง",
            "nameForeign": "Freight",
        },
    ]

    with pytest.raises(JournalValidationError) as caught:
        prepare_general_journal(
            document_date="2026-07-10",
            reference="REF-2",
            description="Ambiguous",
            lines=example_lines(),
            accounts=accounts,
            environment="production",
        )

    assert caught.value.code == "ambiguous_account"
    assert len(caught.value.details["candidates"]) == 2


def test_prepare_requires_reference_in_production() -> None:
    with pytest.raises(JournalValidationError) as caught:
        prepare_general_journal(
            document_date="2026-07-10",
            reference="",
            description="Missing reference",
            lines=example_lines(),
            accounts=ACCOUNTS,
            environment="production",
        )

    assert caught.value.code == "reference_required"


def test_prepare_allows_reference_to_be_omitted_in_sandbox() -> None:
    journal = prepare_general_journal(
        document_date="2026-07-10",
        reference="",
        description="Sandbox journal",
        lines=example_lines(),
        accounts=ACCOUNTS,
        environment="sandbox",
    )

    assert journal.flowaccount_payload["reference"] is None


def test_input_hash_binds_workspace_profile_environment_and_payload() -> None:
    journal = prepare_general_journal(
        document_date="2026-07-10",
        reference="REF-3",
        description="Hash journal",
        lines=example_lines(),
        accounts=ACCOUNTS,
        environment="production",
    )

    first = journal.input_hash(
        workspace_id="workspace-a",
        connector_profile_id="profile-a",
        environment="production",
    )
    same = journal.input_hash(
        workspace_id="workspace-a",
        connector_profile_id="profile-a",
        environment="production",
    )
    different = journal.input_hash(
        workspace_id="workspace-a",
        connector_profile_id="profile-a",
        environment="sandbox",
    )

    assert first == same
    assert first != different
    assert len(first) == 64
