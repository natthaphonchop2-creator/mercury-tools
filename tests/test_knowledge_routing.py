from mercury_tools.rag.models import SearchResult
from mercury_tools.rag.routing import apply_knowledge_routing, infer_knowledge_domain
from mercury_tools.rag.service import MIN_RELEVANCE_SCORE, RagService


def _search_result(score: float) -> SearchResult:
    return SearchResult(
        chunk_id=f"chunk-{score}",
        document_id="document-1",
        document_uri="mercury://wiki/standards/th/example",
        chunk_uri=f"mercury://wiki/standards/th/example#chunk-{score}",
        text="Accounting standard context",
        score=score,
        source_title="Accounting standard",
        source_uri="mercury://wiki/standards/th/example",
        source_url="https://example.com",
        source_path="wiki/standards/th/example.md",
        citation={"heading": "Core Accounting Model"},
        metadata={"doc_type": "accounting_standard"},
    )


class _FakeStore:
    def __init__(self, scores: list[float]):
        self.scores = scores

    def search_knowledge(self, **kwargs):
        del kwargs
        return [_search_result(score) for score in self.scores]


class _FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        del text
        return [0.0]


def test_infers_accounting_standard_domain() -> None:
    assert infer_knowledge_domain("TFRS 15 การรับรู้รายได้") == "accounting_standard"
    assert infer_knowledge_domain("TAS 2 สินค้าคงเหลือ") == "accounting_standard"
    assert infer_knowledge_domain("IFRS 16 lease accounting") == "accounting_standard"


def test_infers_tax_domain() -> None:
    assert infer_knowledge_domain("สรุปภาษีซื้อ VAT เดือนนี้") == "tax"
    assert infer_knowledge_domain("ตรวจภาษีหัก ณ ที่จ่าย WHT") == "tax"


def test_infers_connector_endpoint_domain() -> None:
    assert infer_knowledge_domain("FlowAccount invoice list endpoint") == "connector_endpoint"
    assert infer_knowledge_domain("PEAK API /invoices/list") == "connector_endpoint"


def test_infers_workflow_and_general_domains() -> None:
    assert infer_knowledge_domain("สร้าง management report context pack") == "workflow"
    assert infer_knowledge_domain("บริษัทเป็นอย่างไร") == "general"


def test_standard_query_does_not_apply_inferred_connector_to_standard_docs() -> None:
    filters, connector, domain = apply_knowledge_routing(
        "FlowAccount ใช้ TFRS 15 รับรู้รายได้อย่างไร",
        None,
    )

    assert filters == {"doc_type": "accounting_standard"}
    assert connector == "flowaccount"
    assert domain == "accounting_standard"


def test_endpoint_query_applies_connector_and_doc_type() -> None:
    filters, connector, domain = apply_knowledge_routing(
        "FlowAccount invoice list endpoint",
        {"review_status": "reviewed"},
    )

    assert filters == {
        "review_status": "reviewed",
        "doc_type": "endpoint_dictionary",
        "connector": "flowaccount",
    }
    assert connector == "flowaccount"
    assert domain == "connector_endpoint"


def test_explicit_filters_win_without_mutating_input() -> None:
    original = {"connector": "flowaccount", "doc_type": "tax"}

    filters, connector, domain = apply_knowledge_routing("VAT FlowAccount", original)

    assert filters == original
    assert filters is not original
    assert connector is None
    assert domain is None


def test_rag_service_drops_results_below_minimum_score() -> None:
    service = RagService(store=_FakeStore([0.19, 0.05]), embedder=_FakeEmbedder())

    assert service.search("unknown standard") == []


def test_rag_service_keeps_results_at_threshold() -> None:
    service = RagService(store=_FakeStore([0.31, 0.20]), embedder=_FakeEmbedder())

    assert [row.score for row in service.search("TFRS 15")] == [0.31, 0.20]
    assert MIN_RELEVANCE_SCORE == 0.20
