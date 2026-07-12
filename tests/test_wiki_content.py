from pathlib import Path

from mercury_tools.rag.chunking import chunk_document, document_from_markdown

ROOT = Path(__file__).resolve().parents[1]


def test_flowaccount_endpoint_dictionary_is_indexed_for_rag() -> None:
    path = ROOT / "wiki/connectors/flowaccount-endpoint-dictionary.md"
    document = document_from_markdown(path, root=ROOT / "wiki")
    index = (ROOT / "wiki/index.md").read_text(encoding="utf-8")
    text = path.read_text(encoding="utf-8")

    assert document.document_uri == "mercury://wiki/connectors/flowaccount-endpoint-dictionary"
    assert document.connector == "flowaccount"
    assert document.doc_type == "endpoint_dictionary"
    assert document.review_status == "reviewed"
    chunks = chunk_document(document)
    assert {
        chunk.source_path for chunk in chunks
    } == {"wiki/connectors/flowaccount-endpoint-dictionary.md"}
    assert "FlowAccount Endpoint Data Dictionary" in index
    assert "Total endpoints | 190" in text
    assert "POST | /token" in text
    assert "POST | /tax-invoices" in text
    assert "GET | /products" in text
    assert "client_secret" in text
    assert "[redacted]" in text
