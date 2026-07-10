from mercury_tools.rag.routing import (
    apply_knowledge_routing,
    infer_knowledge_domain,
)


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
