from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from starlette.requests import Request

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
AUTH_USER_ID = UUID("33333333-3333-4333-8333-333333333333")


def _require_attribute(module_name: str, attribute: str):
    module = __import__(module_name, fromlist=[attribute])
    assert hasattr(module, attribute), f"{module_name}.{attribute} is not implemented"
    return getattr(module, attribute)


def _authenticated_context() -> SimpleNamespace:
    from mercury_tools.auth.models import MercuryPrincipal

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer test.token.value")],
        }
    )
    request.state.mercury_principal = MercuryPrincipal(
        subject=AUTH_USER_ID,
        client_id="task-11-test",
        scopes=frozenset({"openid"}),
        token_id="task-11-token",
    )
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _workspace_service_type(*, checked: list[bool] | None = None) -> type[object]:
    from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

    class WorkspaceService:
        def require_workspace(self, *_args: object) -> WorkspaceMembership:
            if checked is not None:
                checked.append(True)
            return WorkspaceMembership(
                tenant_id=TENANT_ID,
                tenant_display_name="Mercury",
                workspace_id=WORKSPACE_ID,
                workspace_display_name="Task 11",
                role=WorkspaceRole.MEMBER,
            )

    return WorkspaceService


def _search_result():
    from mercury_tools.rag.models import SearchResult

    return SearchResult(
        chunk_id="44444444-4444-4444-8444-444444444444",
        document_id="55555555-5555-4555-8555-555555555555",
        document_uri="mercury://knowledge/global/vat",
        chunk_uri="mercury://knowledge/global/vat#chunk-0",
        text="Reviewed VAT evidence",
        score=0.75,
        source_title="VAT guidance",
        source_uri="mercury://knowledge/global/vat",
        source_url="https://example.test/vat",
        source_path=None,
        citation={"heading": "VAT"},
        metadata={
            "source_id": "66666666-6666-4666-8666-666666666666",
            "jurisdiction": "TH",
            "provider": "flowaccount",
            "doc_type": "tax",
            "review_status": "reviewed",
            "effective_on": "2026-07-01",
        },
    )


def test_v1_knowledge_filters_are_exact_and_unknown_keys_fail_closed() -> None:
    KnowledgeFiltersInput = _require_attribute(
        "mercury_tools.mcp.v1_schemas",
        "KnowledgeFiltersInput",
    )
    normalize = _require_attribute(
        "mercury_tools.rag.routing",
        "normalize_v1_knowledge_filters",
    )
    values = {
        "jurisdiction": "TH",
        "provider": "flowaccount",
        "doc_type": "tax",
        "review_status": "reviewed",
        "effective_on": "2026-07-30",
        "source_id": "66666666-6666-4666-8666-666666666666",
        "capability_version": "a" * 64,
    }

    filters = KnowledgeFiltersInput.model_validate(values)

    assert normalize(filters.model_dump(mode="json")) == values
    assert set(filters.model_json_schema()["properties"]) == set(values)
    with pytest.raises(ValidationError):
        KnowledgeFiltersInput.model_validate({**values, "connector": "flowaccount"})
    with pytest.raises(ValueError, match="^knowledge_filters_invalid$"):
        normalize({**values, "raw_response": "must-not-echo"})


def test_v1_search_modes_are_fts_backed_and_exclude_vector_only_mode() -> None:
    validate_mode = _require_attribute(
        "mercury_tools.rag.routing",
        "validate_v1_search_mode",
    )

    assert validate_mode("keyword") == "keyword"
    assert validate_mode("hybrid") == "hybrid"
    with pytest.raises(ValueError, match="^knowledge_search_mode_invalid$"):
        validate_mode("vector")


def test_service_role_search_passes_exact_identity_and_no_embedding(monkeypatch) -> None:
    from mercury_tools.db.supabase import SupabaseRagStore

    method = getattr(SupabaseRagStore, "search_workspace_knowledge", None)
    assert callable(method), "service-role workspace predicate is not implemented"
    captured: dict[str, object] = {}
    store = object.__new__(SupabaseRagStore)

    def request(http_method: str, path: str, **kwargs: object) -> list[dict[str, object]]:
        captured.update(http_method=http_method, path=path, payload=kwargs["json"])
        return []

    monkeypatch.setattr(store, "_request", request)

    result = method(
        store,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        query="invoice evidence",
        filters={"provider": "flowaccount", "review_status": "reviewed"},
        top_k=7,
        mode="hybrid",
    )

    assert result == []
    assert captured == {
        "http_method": "POST",
        "path": "rpc/search_mercury_v1_knowledge",
        "payload": {
            "p_tenant_id": str(TENANT_ID),
            "p_workspace_id": str(WORKSPACE_ID),
            "p_auth_user_id": str(AUTH_USER_ID),
            "query_text": "invoice evidence",
            "match_count": 7,
            "search_mode": "hybrid",
            "filter_jurisdiction": None,
            "filter_provider": "flowaccount",
            "filter_doc_type": None,
            "filter_review_status": "reviewed",
            "filter_effective_on": None,
            "filter_source_id": None,
            "filter_capability_version": None,
        },
    }
    assert "embedding" not in str(captured).lower()


@pytest.mark.asyncio
async def test_search_handler_authorizes_before_service_role_application_query() -> None:
    search_knowledge = _require_attribute(
        "mercury_tools.mcp.v1_tools",
        "search_knowledge",
    )
    membership_checks: list[bool] = []
    calls: list[dict[str, object]] = []
    audit_events: list[dict[str, object]] = []

    class Store:
        def search_workspace_knowledge(self, **kwargs: object):
            assert membership_checks == [True]
            calls.append(kwargs)
            return [_search_result()]

    result = await search_knowledge(
        _authenticated_context(),
        workspace_id=WORKSPACE_ID,
        query="VAT evidence",
        filters={"jurisdiction": "TH", "review_status": "reviewed"},
        top_k=5,
        mode="keyword",
        service_factory=_workspace_service_type(checked=membership_checks),
        store_factory=Store,
        audit_recorder=audit_events.append,
    )

    assert result.status == "ok"
    assert result.workspace_id == WORKSPACE_ID
    assert len(result.data) == 1
    assert calls == [
        {
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "auth_user_id": AUTH_USER_ID,
            "query": "VAT evidence",
            "filters": {
                "jurisdiction": "TH",
                "review_status": "reviewed",
            },
            "top_k": 5,
            "mode": "keyword",
        }
    ]
    assert len(audit_events) == 1
    assert audit_events[0]["tool_name"] == "search_knowledge"
    assert audit_events[0]["output_summary"] == {"result_count": 1}
    assert "VAT evidence" not in str(audit_events[0])


@pytest.mark.asyncio
async def test_empty_workspace_search_returns_insufficient_evidence() -> None:
    MercuryV1ToolError = _require_attribute(
        "mercury_tools.mcp.v1_errors",
        "MercuryV1ToolError",
    )
    search_knowledge = _require_attribute(
        "mercury_tools.mcp.v1_tools",
        "search_knowledge",
    )

    class Store:
        def search_workspace_knowledge(self, **_kwargs: object):
            return []

    with pytest.raises(MercuryV1ToolError, match="^insufficient_evidence$"):
        await search_knowledge(
            _authenticated_context(),
            workspace_id=WORKSPACE_ID,
            query="missing fact",
            service_factory=_workspace_service_type(),
            store_factory=Store,
        )


@pytest.mark.asyncio
async def test_v1_knowledge_tools_publish_workspace_bound_closed_contracts() -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Task 11 knowledge")
    configure_v1_tools(server, enabled=True)
    tools = {tool.name: tool for tool in await server.list_tools()}

    for name in ("search_knowledge", "retrieve_context_pack"):
        schema = tools[name].inputSchema
        assert schema["properties"]["workspace_id"]["format"] == "uuid"
        assert "workspace_id" in schema["required"]
        filters = schema["properties"]["filters"]
        if "$ref" in filters:
            filters = schema["$defs"][filters["$ref"].rsplit("/", 1)[-1]]
        assert set(filters["properties"]) == {
            "jurisdiction",
            "provider",
            "doc_type",
            "review_status",
            "effective_on",
            "source_id",
            "capability_version",
        }
        assert filters["additionalProperties"] is False
        assert tools[name].outputSchema is not None

    modes = tools["search_knowledge"].inputSchema["properties"]["mode"]["enum"]
    assert modes == ["keyword", "hybrid"]
