from mercury_tools.prompts import get_prompt
from mercury_tools.rag.embeddings import HashEmbeddingProvider


def test_mcp_server_imports_and_exposes_server() -> None:
    from mercury_tools.mcp.server import mcp

    assert mcp.name == "Mercury Tools"


def test_prompt_templates_exist() -> None:
    assert "VAT" in get_prompt("vat_summary_th")
    assert "โปรแกรมบัญชี" in get_prompt("connector_setup_guide_th")


def test_hash_embedding_dimension() -> None:
    vector = HashEmbeddingProvider(dimensions=1536).embed_query("vat")

    assert len(vector) == 1536
    assert all(isinstance(value, float) for value in vector)


def test_mcp_flow_tools_validate_and_dry_run() -> None:
    from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
    from mercury_tools.mcp.server import check_flow_syntax, flow_cheat_sheet, run_flow

    assert "Mercury Flow" in flow_cheat_sheet()["cheat_sheet"]

    syntax = check_flow_syntax(COMPANY_HEALTH_TEMPLATE)
    assert syntax["status"] == "ok"
    assert syntax["flow"]["command_count"] == 3

    result = run_flow(COMPANY_HEALTH_TEMPLATE, dry_run=True)
    assert result["status"] == "planned"
    assert result["steps"][0]["command"] == "connectorStatus"
