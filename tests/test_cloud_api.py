from __future__ import annotations

import json
import threading

import httpx
import pytest
import pytest_asyncio
from starlette.applications import Starlette

from mercury_tools.catalog.identity import build_version_id
from mercury_tools.cloud import api as cloud_api
from mercury_tools.cloud.api import CloudDependencies, cloud_routes
from mercury_tools.db.product import SKILL_CATALOG_SEED
from mercury_tools.rag.models import SearchResult
from mercury_tools.safety.redaction import redact_json


class CatalogStoreSpy:
    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self.last_filters = None
        self.thread_id = None
        self.calls = 0

    def list_active_actions(self, filters=None):
        self.calls += 1
        self.last_filters = filters
        self.thread_id = threading.get_ident()
        return list(self.actions)


class RagStoreSpy:
    def __init__(self) -> None:
        self.last_query = ""
        self.last_filters = None
        self.last_document_id = None
        self.thread_id = None
        self.search_calls = 0

    def search_knowledge(self, **kwargs):
        self.search_calls += 1
        self.last_query = kwargs["query"]
        self.last_filters = kwargs["filters"]
        self.thread_id = threading.get_ident()
        return [
            SearchResult(
                chunk_id="legacy-chunk",
                document_id="legacy-document",
                document_uri="mercury://workspace/private-vat",
                chunk_uri="mercury://workspace/private-vat#chunk-1",
                text="Legacy workspace content",
                score=0.99,
                source_title="Legacy workspace",
                source_uri="mercury://workspace/private-vat",
                source_url=None,
                source_path="/Users/operator/private/workspace.md",
                citation={"heading": "Private"},
                metadata={"review_status": "reviewed"},
            ),
            SearchResult(
                chunk_id="chunk-1",
                document_id="document-1",
                document_uri="mercury://wiki/vat-input-tax",
                chunk_uri="mercury://wiki/vat-input-tax#chunk-1",
                text="Contact person@example.com about VAT input tax.",
                score=0.91,
                source_title="VAT input tax",
                source_uri="mercury://wiki/vat-input-tax",
                source_url="https://example.test/vat",
                source_path="/Users/operator/private/wiki.md",
                citation={"heading": "VAT", "source_path": "/Users/operator/private/wiki.md"},
                metadata={
                    "api_key": "private-value",
                    "jurisdiction": "TH",
                    "review_status": "reviewed",
                },
            ),
            SearchResult(
                chunk_id="draft-chunk",
                document_id="draft-document",
                document_uri="mercury://wiki/draft-vat",
                chunk_uri="mercury://wiki/draft-vat#chunk-1",
                text="Draft wiki content",
                score=0.90,
                source_title="Draft VAT",
                source_uri="mercury://wiki/draft-vat",
                source_url=None,
                source_path=None,
                citation={"heading": "Draft"},
                metadata={"review_status": "draft"},
            )
        ]

    def get_document(self, document_id):
        self.last_document_id = document_id
        self.thread_id = threading.get_ident()
        if document_id == "22222222-2222-4222-8222-222222222222":
            return {
                "id": document_id,
                "document_uri": "mercury://workspace/private-vat",
                "title": "Private VAT",
                "body": "Private workspace body",
                "knowledge_sources": {
                    "source_uri": "mercury://workspace/private-vat",
                    "review_status": "reviewed",
                },
            }
        return {
            "id": document_id,
            "document_uri": "mercury://wiki/vat-input-tax",
            "title": "VAT input tax",
            "body": "Email person@example.com with bearer secret-token-value.",
            "sha256": "a" * 64,
            "metadata": {"repository_path": "/Users/operator/private/wiki.md"},
            "knowledge_sources": {
                "title": "Mercury Wiki",
                "source_uri": "mercury://wiki/vat-input-tax",
                "source_url": "https://example.test/vat",
                "source_path": "/Users/operator/private/wiki.md",
                "review_status": "reviewed",
            },
        }


class SkillLoaderSpy:
    def __init__(self) -> None:
        self.calls = []
        self.markdown = "# VAT\nContact person@example.com."

    def __call__(self, skill_id):
        self.calls.append(skill_id)
        return self.markdown


@pytest.fixture
def cloud_dependencies(action_factory):
    read_action = action_factory(
        method="GET",
        path_template="/company",
        operation_id="getCompany",
        capability="company.info.read",
        risk_tier=0,
        required_confirmations=0,
        side_effects=(),
        description="Get company",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object", "properties": {"erp_payload": {"type": "string"}}},
            "files": {},
        },
        examples=({"body": {"erp_payload": "must-not-leak"}},),
        source_uri="/Users/operator/private/openapi.json",
    )
    write_action = action_factory()
    rag_store = RagStoreSpy()
    skill_loader = SkillLoaderSpy()
    catalog_store = CatalogStoreSpy([read_action, write_action])
    dependencies = CloudDependencies(
        catalog_store=catalog_store,
        rag_store=rag_store,
        skills=(
            {
                "skill_id": "vat-summary-th",
                "title": "VAT Summary TH",
                "category": "tax",
                "summary": "VAT summary",
                "status": "available",
                "version": "0.1.0",
                "required_connectors": ["flowaccount"],
                "tags": ["vat"],
                "repository_path": "/Users/operator/private/skill",
            },
        ),
        skill_loader=skill_loader,
    )
    return dependencies, read_action, rag_store, catalog_store, skill_loader


@pytest_asyncio.fixture
async def client(cloud_dependencies):
    dependencies, *_ = cloud_dependencies
    app = Starlette(routes=cloud_routes(dependencies))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://cloud.example.test",
    ) as http:
        yield http


@pytest.mark.asyncio
async def test_cloud_api_exposes_only_read_catalog_routes(
    client, cloud_dependencies
) -> None:
    _, read_action, *_ = cloud_dependencies

    response = await client.get(
        "/api/cloud/v1/catalog/actions?connector=flowaccount&method=GET"
    )

    assert response.status_code == 200
    assert [item["action_id"] for item in response.json()["actions"]] == [
        read_action.action_id
    ]
    assert response.headers["etag"]
    serialized = json.dumps(response.json())
    assert "must-not-leak" not in serialized
    assert "erp_payload" not in serialized
    assert "/Users/" not in serialized
    write_attempt = await client.post("/api/cloud/v1/catalog/actions", json={})
    assert write_attempt.status_code == 405

    write_metadata = await client.get("/api/cloud/v1/catalog/actions?method=POST")
    assert write_metadata.status_code == 200
    assert len(write_metadata.json()["actions"]) == 1
    assert write_metadata.json()["actions"][0]["method"] == "POST"


@pytest.mark.asyncio
async def test_cloud_catalog_etag_supports_conditional_fetch(client) -> None:
    first = await client.get("/api/cloud/v1/catalog/actions")
    second = await client.get(
        "/api/cloud/v1/catalog/actions",
        headers={"If-None-Match": first.headers["etag"]},
    )

    assert first.status_code == 200
    assert second.status_code == 304
    assert second.content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "unknown=value",
        "connector=flowaccount&connector=peak",
        "method=get",
        "method=TRACE",
    ],
)
async def test_cloud_catalog_rejects_unknown_duplicate_and_invalid_query_keys(
    client, query
) -> None:
    response = await client.get(f"/api/cloud/v1/catalog/actions?{query}")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cloud_search_redacts_personal_data_and_private_fields(
    client, cloud_dependencies
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={
            "query": (
                "VAT for person@example.com tax id 0105559999999 "
                "Bearer arbitrary-sensitive-material"
            ),
            "filters": {"jurisdiction": "TH", "review_status": "draft"},
            "top_k": 4,
        },
    )

    serialized = json.dumps(response.json())
    assert response.status_code == 200
    assert response.json()["results"][0]["citation"]
    assert "person@example.com" not in rag_store.last_query
    assert "0105559999999" not in rag_store.last_query
    assert "arbitrary-sensitive-material" not in rag_store.last_query
    assert rag_store.last_filters.review_status == "reviewed"
    assert len(response.json()["results"]) == 1
    assert response.json()["results"][0]["document_uri"].startswith("mercury://wiki/")
    assert "person@example.com" not in serialized
    assert "private-value" not in serialized
    assert "/Users/" not in serialized
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"query": "x" * 2001, "top_k": 4},
        {"query": "VAT", "top_k": 0},
        {"query": "VAT", "top_k": 21},
        {"query": "VAT", "top_k": True},
        {"query": "VAT", "top_k": 1.5},
        {"query": "VAT", "top_k": "4"},
        {"query": "VAT", "top_k": None},
        {"query": "VAT", "filters": {"unknown": "value"}},
    ],
)
async def test_cloud_search_rejects_invalid_limits_and_filters(client, payload) -> None:
    response = await client.post("/api/cloud/v1/knowledge/search", json=payload)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_cloud_search_accepts_exact_query_and_top_k_boundaries(client) -> None:
    low = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "x" * 2_000, "top_k": 1},
    )
    high = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 20},
    )

    assert low.status_code == high.status_code == 200


@pytest.mark.asyncio
async def test_cloud_skills_and_documents_expose_only_public_fields(client) -> None:
    skills = await client.get("/api/cloud/v1/skills")
    skill = await client.get("/api/cloud/v1/skills/vat-summary-th")
    document = await client.get(
        "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
    )

    serialized = json.dumps(
        {"skills": skills.json(), "skill": skill.json(), "document": document.json()}
    )
    assert skills.status_code == skill.status_code == document.status_code == 200
    assert skill.json()["markdown"].startswith("# VAT")
    assert "/Users/" not in serialized
    assert "person@example.com" not in serialized
    assert "repository_path" not in serialized


@pytest.mark.asyncio
async def test_cloud_document_rejects_injection_before_store_and_blocks_private_corpus(
    client, cloud_dependencies
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies

    injected = await client.get(
        "/api/cloud/v1/documents/id%29%2Cdocument_uri.eq.mercury%3A%2F%2Fworkspace%2Fprivate"
    )

    assert injected.status_code == 400
    assert rag_store.last_document_id is None

    private = await client.get(
        "/api/cloud/v1/documents/22222222-2222-4222-8222-222222222222"
    )
    assert private.status_code == 404
    assert "Private workspace body" not in private.text


@pytest.mark.asyncio
async def test_cloud_action_rejects_encoded_traversal_before_catalog_store(
    client, cloud_dependencies
) -> None:
    _, _, _, catalog_store, _ = cloud_dependencies
    calls_before = catalog_store.calls

    traversal = await client.get(
        "/api/cloud/v1/catalog/actions/..%2F..%2Fprivate"
    )
    injection = await client.get(
        "/api/cloud/v1/catalog/actions/act_bad%29%2Cor%3D%28id.eq.secret"
    )

    assert traversal.status_code in {400, 404}
    assert injection.status_code == 400
    assert catalog_store.calls == calls_before


@pytest.mark.asyncio
async def test_cloud_skill_rejects_unknown_and_traversal_ids_before_loader(
    client, cloud_dependencies
) -> None:
    _, _, _, _, skill_loader = cloud_dependencies

    unknown = await client.get("/api/cloud/v1/skills/not-in-seed")
    traversal = await client.get("/api/cloud/v1/skills/..%2F..%2Fprivate")

    assert unknown.status_code == 404
    assert traversal.status_code in {400, 404}
    assert skill_loader.calls == []


@pytest.mark.asyncio
async def test_cloud_skill_injected_metadata_cannot_expand_seed_allowlist(
    client, cloud_dependencies
) -> None:
    dependencies, _, _, _, skill_loader = cloud_dependencies
    dependencies.skills = (
        *dependencies.skills,
        {
            "skill_id": "not-in-seed",
            "title": "Injected",
            "category": "private",
            "summary": "Injected skill",
            "status": "available",
            "version": "1.0.0",
            "required_connectors": [],
            "tags": [],
        },
    )

    response = await client.get("/api/cloud/v1/skills/not-in-seed")

    assert response.status_code == 404
    assert skill_loader.calls == []


@pytest.mark.asyncio
async def test_cloud_store_calls_run_outside_async_event_loop(
    client, cloud_dependencies
) -> None:
    _, _, rag_store, catalog_store, _ = cloud_dependencies
    event_loop_thread = threading.get_ident()

    catalog = await client.get("/api/cloud/v1/catalog/actions")
    search = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 4},
    )

    assert catalog.status_code == search.status_code == 200
    assert catalog_store.thread_id != event_loop_thread
    assert rag_store.thread_id != event_loop_thread


def test_cloud_routes_have_exact_read_only_method_matrix(cloud_dependencies) -> None:
    dependencies, *_ = cloud_dependencies
    routes = cloud_routes(dependencies)

    assert [(route.path, route.methods) for route in routes] == [
        ("/api/cloud/v1/catalog/actions", {"GET", "HEAD"}),
        ("/api/cloud/v1/catalog/actions/{action_id}", {"GET", "HEAD"}),
        ("/api/cloud/v1/connectors", {"GET", "HEAD"}),
        ("/api/cloud/v1/skills", {"GET", "HEAD"}),
        ("/api/cloud/v1/skills/{skill_id}", {"GET", "HEAD"}),
        ("/api/cloud/v1/knowledge/search", {"POST"}),
        ("/api/cloud/v1/documents/{document_id:path}", {"GET", "HEAD"}),
    ]


@pytest.mark.asyncio
async def test_cloud_wrong_methods_are_405(client, cloud_dependencies) -> None:
    _, read_action, *_ = cloud_dependencies
    paths = (
        "/api/cloud/v1/catalog/actions",
        f"/api/cloud/v1/catalog/actions/{read_action.action_id}",
        "/api/cloud/v1/connectors",
        "/api/cloud/v1/skills",
        "/api/cloud/v1/skills/vat-summary-th",
        "/api/cloud/v1/knowledge/search",
        "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111",
    )

    for path in paths:
        response = await (
            client.get(path)
            if path == "/api/cloud/v1/knowledge/search"
            else client.patch(path, json={})
        )
        assert response.status_code == 405


@pytest.mark.asyncio
async def test_cloud_responses_use_exact_public_projection_keys(
    client, cloud_dependencies
) -> None:
    _, read_action, *_ = cloud_dependencies
    catalog = await client.get("/api/cloud/v1/catalog/actions?method=GET")
    action = await client.get(
        f"/api/cloud/v1/catalog/actions/{read_action.action_id}"
    )
    connectors = await client.get("/api/cloud/v1/connectors")
    skills = await client.get("/api/cloud/v1/skills")
    skill = await client.get("/api/cloud/v1/skills/vat-summary-th")
    search = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 4},
    )
    document = await client.get(
        "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
    )

    assert set(catalog.json()) == {"actions"}
    assert set(catalog.json()["actions"][0]) == {
        "action_id",
        "version_id",
        "connector_id",
        "environments",
        "method",
        "path_template",
        "operation_id",
        "variant_id",
        "content_type",
        "aliases_th",
        "aliases_en",
        "capability",
        "input_schema",
        "examples",
        "risk_tier",
        "required_confirmations",
        "side_effects",
        "preflight_action_ids",
        "idempotency",
        "success_rules",
        "error_rules",
        "response_redaction",
        "source_uri",
        "source_hash",
        "confidence",
        "observed_state",
        "description",
    }
    assert set(action.json()) == {"action"}
    assert set(connectors.json()) == {"connectors"}
    assert set(connectors.json()["connectors"][0]) == {
        "connector_id",
        "capabilities",
        "environments",
    }
    assert set(skills.json()) == {"skills"}
    assert set(skills.json()["skills"][0]) == {
        "skill_id",
        "title",
        "category",
        "summary",
        "status",
        "version",
        "required_connectors",
        "tags",
    }
    assert set(skill.json()) == set(skills.json()["skills"][0]) | {"markdown"}
    assert set(search.json()) == {"results"}
    assert set(search.json()["results"][0]) == {
        "chunk_id",
        "document_id",
        "document_uri",
        "chunk_uri",
        "text",
        "score",
        "source_title",
        "source_uri",
        "source_url",
        "citation",
    }
    assert set(search.json()["results"][0]["citation"]) == {"heading"}
    assert set(document.json()) == {
        "id",
        "document_uri",
        "title",
        "body",
        "sha256",
        "source",
    }
    assert set(document.json()["source"]) == {
        "title",
        "source_uri",
        "source_url",
    }


@pytest.mark.parametrize(
    "uri",
    [
        "mercury://wiki/../../private",
        "mercury://wiki//private",
        "mercury://wiki/%2e%2e/private",
        "mercury://wiki/private/",
        "mercury://wiki/private\\document",
        "MERCURY://wiki/private",
        "mercury://WIKI/private",
        "mercury://wiki/private?source=legacy",
        "mercury://wiki/private#chunk-0",
    ],
)
def test_public_wiki_uri_validator_rejects_noncanonical_values(uri) -> None:
    assert cloud_api.is_canonical_public_wiki_uri(uri) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "mercury://wiki/../../private",
        "mercury://wiki//private",
        "mercury://wiki/%2e%2e/private",
        "mercury://wiki/private/",
    ],
)
async def test_cloud_search_rejects_malformed_reviewed_wiki_rows(
    client, cloud_dependencies, uri
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    rag_store.search_knowledge = lambda **_kwargs: [
        SearchResult(
            chunk_id="malformed-chunk",
            document_id="malformed-document",
            document_uri=uri,
            chunk_uri=f"{uri}#chunk-0",
            text="must not escape",
            score=0.99,
            source_title="Malformed",
            source_uri=uri,
            source_url=None,
            source_path=None,
            citation={"heading": "Malformed"},
            metadata={"review_status": "reviewed"},
        )
    ]

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 4},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}
    assert "must not escape" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "mercury://wiki/../../private",
        "mercury://wiki//private",
        "mercury://wiki/%2e%2e/private",
    ],
)
async def test_cloud_document_rejects_malformed_public_membership(
    client, cloud_dependencies, uri
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    rag_store.get_document = lambda document_id: {
        "id": document_id,
        "document_uri": uri,
        "title": "Malformed",
        "body": "private document body",
        "sha256": "a" * 64,
        "knowledge_sources": {
            "title": "Malformed",
            "source_uri": uri,
            "source_url": None,
            "review_status": "reviewed",
        },
    }

    response = await client.get(
        "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}
    assert "private document body" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jurisdiction", "person@example.com"),
        ("connector", "0105559999999"),
        ("doc_type", "Bearer arbitrary-sensitive-material"),
        ("review_status", "api_key=private-value"),
        ("connector", "SUPABASE_SERVICE_ROLE_KEY=sb_secret_TASK13_EXAMPLE"),
        ("doc_type", "/Users/operator/private/wiki.md"),
        ("effective_date", "2026-02-30"),
    ],
)
async def test_cloud_search_rejects_sensitive_or_noncanonical_filters_before_rag(
    client, cloud_dependencies, field, value
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    calls_before = rag_store.search_calls

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "filters": {field: value}, "top_k": 4},
    )

    assert response.status_code == 400
    assert rag_store.search_calls == calls_before
    assert value not in rag_store.last_query


@pytest.mark.asyncio
async def test_cloud_search_sanitizes_filters_and_forces_reviewed_before_rag(
    client, cloud_dependencies
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={
            "query": "VAT",
            "filters": {
                "jurisdiction": "TH",
                "connector": "flowaccount",
                "doc_type": "tax",
                "review_status": "draft",
                "effective_date": "2026-07-12",
            },
            "top_k": 4,
        },
    )

    assert response.status_code == 200
    assert rag_store.last_filters.jurisdiction == "TH"
    assert rag_store.last_filters.connector == "flowaccount"
    assert rag_store.last_filters.doc_type == "tax"
    assert rag_store.last_filters.review_status == "reviewed"
    assert rag_store.last_filters.effective_date == "2026-07-12"


@pytest.mark.asyncio
async def test_cloud_redacts_supabase_secrets_across_every_public_projection(
    client, cloud_dependencies
) -> None:
    dependencies, read_action, rag_store, catalog_store, skill_loader = cloud_dependencies
    secret = "sb_secret_TASK13_EXAMPLE"
    assignment = f"SUPABASE_SERVICE_ROLE_KEY={secret}"
    tampered = read_action.model_copy(update={"description": assignment})
    tampered = tampered.model_copy(update={"version_id": build_version_id(tampered)})
    catalog_store.actions = [tampered]
    skill_loader.markdown = f"# VAT\nservice_role_key: {secret}"
    rag_store.search_knowledge = lambda **_kwargs: [
        SearchResult(
            chunk_id="chunk-secret",
            document_id="document-secret",
            document_uri="mercury://wiki/vat-secret",
            chunk_uri="mercury://wiki/vat-secret#chunk-0",
            text=f"Search text {assignment}",
            score=0.9,
            source_title="VAT",
            source_uri="mercury://wiki/vat-secret",
            source_url=None,
            source_path=None,
            citation={"heading": f"service_role_key={secret}"},
            metadata={"review_status": "reviewed"},
        )
    ]
    rag_store.get_document = lambda document_id: {
        "id": document_id,
        "document_uri": "mercury://wiki/vat-secret",
        "title": "VAT",
        "body": f"Document {secret}",
        "sha256": "a" * 64,
        "knowledge_sources": {
            "title": "Wiki",
            "source_uri": "mercury://wiki/vat-secret",
            "source_url": None,
            "review_status": "reviewed",
        },
    }

    catalog = await client.get("/api/cloud/v1/catalog/actions")
    skill = await client.get("/api/cloud/v1/skills/vat-summary-th")
    search = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": assignment, "top_k": 4},
    )
    document = await client.get(
        "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
    )

    serialized = json.dumps(
        {
            "catalog": catalog.json(),
            "skill": skill.json(),
            "search": search.json(),
            "document": document.json(),
        }
    )
    assert catalog.status_code == skill.status_code == 200
    assert search.status_code == document.status_code == 200
    assert secret not in rag_store.last_query
    assert secret not in serialized
    assert assignment not in serialized
    assert dependencies.skill_loader.calls == ["vat-summary-th"]


@pytest.mark.asyncio
async def test_cloud_skills_ignore_injected_replacement_for_seeded_id(
    client, cloud_dependencies
) -> None:
    dependencies, _, _, _, skill_loader = cloud_dependencies
    canonical = next(
        item for item in SKILL_CATALOG_SEED if item["skill_id"] == "vat-summary-th"
    )
    dependencies.skills = (
        {
            **canonical,
            "title": "Injected replacement",
            "summary": "Injected summary",
            "version": "999.0.0",
        },
    )

    listing = await client.get("/api/cloud/v1/skills")
    detail = await client.get("/api/cloud/v1/skills/vat-summary-th")

    listed = next(
        item for item in listing.json()["skills"] if item["skill_id"] == "vat-summary-th"
    )
    assert listed["title"] == canonical["title"]
    assert listed["summary"] == canonical["summary"]
    assert listed["version"] == canonical["version"]
    assert detail.json()["title"] == canonical["title"]
    assert detail.json()["version"] == canonical["version"]
    assert skill_loader.calls == ["vat-summary-th"]


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [None, "not-a-list", {"results": []}])
async def test_cloud_search_malformed_upstream_returns_constant_503(
    client, cloud_dependencies, malformed
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    rag_store.search_knowledge = lambda **_kwargs: malformed

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 4},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_value",
    [
        {"metadata": None},
        {"citation": None},
        {"score": float("nan")},
    ],
)
async def test_cloud_search_malformed_result_returns_constant_503(
    client, cloud_dependencies, field_value
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    values = {
        "chunk_id": "chunk-1",
        "document_id": "document-1",
        "document_uri": "mercury://wiki/vat",
        "chunk_uri": "mercury://wiki/vat#chunk-0",
        "text": "VAT",
        "score": 0.9,
        "source_title": "VAT",
        "source_uri": "mercury://wiki/vat",
        "source_url": None,
        "source_path": None,
        "citation": {"heading": "VAT"},
        "metadata": {"review_status": "reviewed"},
    }
    values.update(field_value)
    rag_store.search_knowledge = lambda **_kwargs: [SearchResult(**values)]

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 4},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        {"knowledge_sources": "not-a-source"},
        {"body": {"unexpected": "object"}},
        {"knowledge_sources": {"review_status": ["reviewed"]}},
    ],
)
async def test_cloud_document_malformed_upstream_returns_constant_503(
    client, cloud_dependencies, document
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    payload = {
        "id": "11111111-1111-4111-8111-111111111111",
        "document_uri": "mercury://wiki/vat",
        "title": "VAT",
        "body": "VAT body",
        "sha256": "a" * 64,
        "knowledge_sources": {
            "title": "Wiki",
            "source_uri": "mercury://wiki/vat",
            "source_url": None,
            "review_status": "reviewed",
        },
    }
    payload.update(document)
    rag_store.get_document = lambda _document_id: payload

    response = await client.get(
        "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}


@pytest.mark.asyncio
async def test_cloud_catalog_etag_identifies_filtered_representation(client) -> None:
    read = await client.get("/api/cloud/v1/catalog/actions?method=GET")
    write = await client.get("/api/cloud/v1/catalog/actions?method=POST")

    assert read.status_code == write.status_code == 200
    assert read.json() != write.json()
    assert read.headers["etag"] != write.headers["etag"]


@pytest.mark.asyncio
async def test_cloud_filtered_etag_only_returns_304_for_same_representation(client) -> None:
    read = await client.get("/api/cloud/v1/catalog/actions?method=GET")
    different = await client.get(
        "/api/cloud/v1/catalog/actions?method=POST",
        headers={"If-None-Match": read.headers["etag"]},
    )
    same = await client.get(
        "/api/cloud/v1/catalog/actions?method=GET",
        headers={"If-None-Match": read.headers["etag"]},
    )

    assert different.status_code == 200
    assert same.status_code == 304


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        KeyError("malformed loader"),
        TypeError("malformed loader"),
        ValueError("malformed loader"),
        OSError("loader unavailable"),
    ],
)
async def test_cloud_skill_loader_errors_return_constant_503(
    client, cloud_dependencies, error
) -> None:
    dependencies, _, _, _, _ = cloud_dependencies

    def fail_loader(_skill_id):
        raise error

    dependencies.skill_loader = fail_loader
    response = await client.get("/api/cloud/v1/skills/vat-summary-th")

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}
    assert "malformed loader" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["search", "document"])
@pytest.mark.parametrize(
    "error",
    [
        KeyError("malformed store"),
        TypeError("malformed store"),
        ValueError("malformed store"),
        OSError("store unavailable"),
    ],
)
async def test_cloud_store_errors_return_constant_503(
    client, cloud_dependencies, route, error
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies

    def fail_store(*_args, **_kwargs):
        raise error

    if route == "search":
        rag_store.search_knowledge = fail_store
        response = await client.post(
            "/api/cloud/v1/knowledge/search",
            json={"query": "VAT", "top_k": 4},
        )
    else:
        rag_store.get_document = fail_store
        response = await client.get(
            "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
        )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}
    assert "malformed store" not in response.text


class KeyErrorMapping(dict):
    def __contains__(self, _key):
        raise KeyError("malformed projection")

    def get(self, _key, _default=None):
        raise KeyError("malformed projection")


@pytest.mark.asyncio
async def test_cloud_search_projection_key_error_returns_constant_503(
    client, cloud_dependencies
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    rag_store.search_knowledge = lambda **_kwargs: [
        SearchResult(
            chunk_id="chunk-1",
            document_id="document-1",
            document_uri="mercury://wiki/vat",
            chunk_uri="mercury://wiki/vat#chunk-0",
            text="VAT",
            score=0.9,
            source_title="VAT",
            source_uri="mercury://wiki/vat",
            source_url=None,
            source_path=None,
            citation=KeyErrorMapping({"heading": "VAT"}),
            metadata={"review_status": "reviewed"},
        )
    ]

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 4},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}


@pytest.mark.asyncio
async def test_cloud_document_projection_key_error_returns_constant_503(
    client, cloud_dependencies
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    rag_store.get_document = lambda document_id: {
        "id": document_id,
        "document_uri": "mercury://wiki/vat",
        "title": "VAT",
        "body": "VAT",
        "sha256": "a" * 64,
        "knowledge_sources": KeyErrorMapping(
            {
                "title": "Wiki",
                "source_uri": "mercury://wiki/vat",
                "source_url": None,
                "review_status": "reviewed",
            }
        ),
    }

    response = await client.get(
        "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}


def test_public_sanitizer_preserves_documented_auth_cookie_placeholders() -> None:
    value = "Authorization: Bearer <token> Cookie: session=<cookie>"

    assert cloud_api.sanitize_public_text(value) == value


def test_shared_json_redaction_preserves_documented_header_placeholders() -> None:
    payload = {
        "authorization": "Bearer <token>",
        "cookie": "session=<cookie>",
    }

    assert redact_json(payload) == payload
    assert redact_json(
        {"authorization": "opaque-secret", "cookie": "session=opaque-secret"}
    ) == {"authorization": "[REDACTED]", "cookie": "[REDACTED]"}


@pytest.mark.asyncio
async def test_cloud_redacts_auth_and_cookie_values_before_rag(
    client, cloud_dependencies
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    query = (
        "Authorization: opaque-auth Proxy-Authorization: proxy-auth "
        "Cookie: session=opaque-cookie Set-Cookie: sid=opaque-set-cookie"
    )

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": query, "top_k": 4},
    )

    assert response.status_code == 200
    for secret in ("opaque-auth", "proxy-auth", "opaque-cookie", "opaque-set-cookie"):
        assert secret not in rag_store.last_query


@pytest.mark.asyncio
async def test_cloud_redacts_auth_and_cookie_values_from_every_projection(
    client, cloud_dependencies
) -> None:
    _, read_action, rag_store, catalog_store, skill_loader = cloud_dependencies
    tampered = read_action.model_copy(
        update={"description": "Authorization: opaque-catalog-auth"}
    )
    tampered = tampered.model_copy(update={"version_id": build_version_id(tampered)})
    catalog_store.actions = [tampered]
    skill_loader.markdown = "# VAT\nCookie: session=opaque-skill-cookie"
    rag_store.search_knowledge = lambda **_kwargs: [
        SearchResult(
            chunk_id="chunk-1",
            document_id="document-1",
            document_uri="mercury://wiki/vat",
            chunk_uri="mercury://wiki/vat#chunk-0",
            text="Proxy-Authorization: opaque-search-auth",
            score=0.9,
            source_title="VAT",
            source_uri="mercury://wiki/vat",
            source_url=None,
            source_path=None,
            citation={"heading": "Cookie: session=opaque-citation-cookie"},
            metadata={"review_status": "reviewed"},
        )
    ]
    rag_store.get_document = lambda document_id: {
        "id": document_id,
        "document_uri": "mercury://wiki/vat",
        "title": "VAT",
        "body": "Set-Cookie: sid=opaque-document-cookie",
        "sha256": "a" * 64,
        "knowledge_sources": {
            "title": "Wiki",
            "source_uri": "mercury://wiki/vat",
            "source_url": None,
            "review_status": "reviewed",
        },
    }

    responses = [
        await client.get("/api/cloud/v1/catalog/actions"),
        await client.get("/api/cloud/v1/skills/vat-summary-th"),
        await client.post(
            "/api/cloud/v1/knowledge/search",
            json={"query": "VAT", "top_k": 4},
        ),
        await client.get(
            "/api/cloud/v1/documents/11111111-1111-4111-8111-111111111111"
        ),
    ]

    serialized = json.dumps([response.json() for response in responses])
    assert all(response.status_code == 200 for response in responses)
    for secret in (
        "opaque-catalog-auth",
        "opaque-skill-cookie",
        "opaque-search-auth",
        "opaque-citation-cookie",
        "opaque-document-cookie",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunk_id", "Authorization: opaque-secret"),
        ("document_id", "/opt/mercury/repo/private.md"),
    ],
)
async def test_cloud_rejects_malicious_search_result_identifiers(
    client, cloud_dependencies, field, value
) -> None:
    _, _, rag_store, _, _ = cloud_dependencies
    values = {
        "chunk_id": "chunk-1",
        "document_id": "document-1",
        "document_uri": "mercury://wiki/vat",
        "chunk_uri": "mercury://wiki/vat#chunk-0",
        "text": "VAT",
        "score": 0.9,
        "source_title": "VAT",
        "source_uri": "mercury://wiki/vat",
        "source_url": None,
        "source_path": None,
        "citation": {"heading": "VAT"},
        "metadata": {"review_status": "reviewed"},
    }
    values[field] = value
    rag_store.search_knowledge = lambda **_kwargs: [SearchResult(**values)]

    response = await client.post(
        "/api/cloud/v1/knowledge/search",
        json={"query": "VAT", "top_k": 4},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "service_unavailable"}
    assert value not in response.text


@pytest.mark.parametrize(
    "value",
    [
        "Read /opt/mercury/repo/private.md",
        "Read /workspace/project/private.json",
        "Read /mnt/secrets/config.toml",
        r"Read C:\mercury\private\config.json",
        "Read C:/mercury/private/config.json",
    ],
)
def test_public_sanitizer_redacts_cross_platform_absolute_paths(value) -> None:
    sanitized = cloud_api.sanitize_public_text(value)

    assert "[REDACTED_PATH]" in sanitized
    assert "private" not in sanitized


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/opt/mercury/docs",
        "mercury://wiki/opt/mercury/docs",
        "Use debit/credit for this entry",
    ],
)
def test_public_sanitizer_preserves_safe_uri_and_slash_text(value) -> None:
    assert cloud_api.sanitize_public_text(value) == value


@pytest.mark.asyncio
async def test_cloud_catalog_preserves_endpoint_path_templates(
    client, cloud_dependencies, action_factory
) -> None:
    _, _, _, catalog_store, _ = cloud_dependencies
    action = action_factory(
        method="GET",
        path_template="/workspace/items/{item_id}",
        operation_id="getWorkspaceItem",
        capability="workspace.items.read",
        risk_tier=0,
        required_confirmations=0,
        side_effects=(),
    )
    catalog_store.actions = [action]

    response = await client.get("/api/cloud/v1/catalog/actions")

    assert response.status_code == 200
    assert response.json()["actions"][0]["path_template"] == action.path_template
