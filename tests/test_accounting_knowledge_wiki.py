from pathlib import Path

import pytest

from mercury_tools.rag.chunking import chunk_document, document_from_markdown

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "wiki"
STANDARDS = WIKI / "standards" / "th"

EXPECTED_STANDARDS = {
    "tfrs-15-revenue.md": "TFRS 15",
    "tfrs-16-leases.md": "TFRS 16",
    "tfrs-9-financial-instruments.md": "TFRS 9",
    "tas-2-inventories.md": "TAS 2",
    "tas-7-cash-flows.md": "TAS 7",
    "tas-12-income-taxes.md": "TAS 12",
    "tas-16-property-plant-equipment.md": "TAS 16",
    "tfrs-for-npaes-overview.md": "TFRS for NPAEs",
}

REQUIRED_STANDARD_HEADINGS = {
    "## Purpose",
    "## Core Accounting Model",
    "## Required Data And Evidence",
    "## Mercury Review Checks",
    "## Limitations",
    "## Official References",
}


def test_accounting_standard_pages_are_source_checked() -> None:
    for filename, standard_id in EXPECTED_STANDARDS.items():
        path = STANDARDS / filename
        document = document_from_markdown(path, root=WIKI)
        text = path.read_text(encoding="utf-8")

        assert document.doc_type == "accounting_standard"
        assert document.jurisdiction == "TH"
        assert document.review_status == "reviewed"
        assert document.source_url and document.source_url.startswith("https://")
        assert document.metadata["standard_id"] == standard_id
        assert document.metadata["source_verified_at"] == "2026-07-10"
        assert document.metadata["professional_review_required"] is True
        assert document.document_uri.startswith("mercury://wiki/standards/th/")
        assert all(heading in text for heading in REQUIRED_STANDARD_HEADINGS)
        assert chunk_document(document)


def test_thai_tax_pages_are_source_checked() -> None:
    for filename in ("th-input-vat-basics.md", "th-withholding-tax-basics.md"):
        path = WIKI / "tax" / filename
        document = document_from_markdown(path, root=WIKI)
        text = path.read_text(encoding="utf-8")

        assert document.doc_type == "tax"
        assert document.jurisdiction == "TH"
        assert document.review_status == "reviewed"
        assert document.source_url and document.source_url.startswith("https://www.rd.go.th/")
        assert document.metadata["source_verified_at"] == "2026-07-10"
        assert document.metadata["professional_review_required"] is True
        assert "## Evidence Checklist" in text
        assert "## Reconciliation Checks" in text
        assert "## Escalate To Accountant" in text
        assert chunk_document(document)


def test_wiki_index_lists_accounting_knowledge() -> None:
    index = (WIKI / "index.md").read_text(encoding="utf-8")

    for standard_id in EXPECTED_STANDARDS.values():
        assert standard_id in index
    assert "Thai VAT And Tax Invoice Basics" in index
    assert "Thai Withholding Tax Basics" in index


def test_reviewed_regulated_page_requires_source_verification(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text(
        """---
title: Bad standard
doc_type: accounting_standard
review_status: reviewed
jurisdiction: TH
source_url: https://example.com
---
# Bad
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source_verified_at"):
        document_from_markdown(path, root=tmp_path)
