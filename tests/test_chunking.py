from pathlib import Path

from mercury_tools.rag.chunking import chunk_document, document_from_markdown


def test_chunking_keeps_metadata_and_citation(tmp_path: Path) -> None:
    path = tmp_path / "vat.md"
    path.write_text(
        """---
title: VAT Test
doc_type: tax
jurisdiction: TH
connector: flowaccount
review_status: reviewed
source_url: https://example.test/vat
source_verified_at: "2026-07-10"
---

# VAT Test

Input VAT content.
""",
        encoding="utf-8",
    )

    document = document_from_markdown(path, root=tmp_path)
    chunks = chunk_document(document)

    assert document.document_uri == "mercury://wiki/vat"
    assert document.jurisdiction == "TH"
    assert chunks[0].citation["source_title"] == "VAT Test"
    assert chunks[0].citation["source_url"] == "https://example.test/vat"
    assert chunks[0].metadata["connector"] == "flowaccount"


def test_endpoint_dictionary_chunk_carries_exact_action_id(tmp_path: Path) -> None:
    path = tmp_path / "endpoints.md"
    path.write_text(
        """---
title: Endpoint Dictionary
doc_type: endpoint_dictionary
connector: flowaccount
review_status: reviewed
---

## Create invoice

action_id: act_1234567890abcdef12345678
method: POST
path: /invoices
""",
        encoding="utf-8",
    )

    document = document_from_markdown(path, root=tmp_path)
    chunk = chunk_document(document)[0]

    assert chunk.metadata["action_id"] == "act_1234567890abcdef12345678"
