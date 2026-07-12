from pathlib import Path

from mercury_tools.rag.chunking import chunk_document, document_from_markdown

ROOT = Path(__file__).resolve().parents[1]


def test_peak_endpoint_dictionary_is_indexed_for_rag() -> None:
    path = ROOT / "wiki/connectors/peak-endpoint-dictionary.md"
    document = document_from_markdown(path, root=ROOT / "wiki")
    text = path.read_text(encoding="utf-8")

    assert document.document_uri == "mercury://wiki/connectors/peak-endpoint-dictionary"
    assert document.connector == "peak"
    assert document.doc_type == "endpoint_dictionary"
    assert document.review_status == "reviewed"
    assert {
        chunk.source_path for chunk in chunk_document(document)
    } == {"wiki/connectors/peak-endpoint-dictionary.md"}
    assert "PEAK API Data Dictionary" in text
    assert "Total endpoints | 64" in text
    assert "POST | `/clienttoken`" in text
    assert "POST | `/invoices`" in text
    assert "GET | `/paymentmethods`" in text
    assert "documents.invoice.payment.create" in text
    assert "daily_journal.create" in text
    assert "connect_key" in text
    assert "must not store ConnectId" in text
