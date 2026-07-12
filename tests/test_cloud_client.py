from __future__ import annotations

import json
import threading

import httpx
import pytest

from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.catalog.identity import build_version_id
from mercury_tools.cloud.client import CloudBrainClient
from mercury_tools.config import DEFAULT_CLOUD_BASE_URL, load_settings


class ThreadTrackingCache:
    def __init__(self, delegate: CatalogCache) -> None:
        self.delegate = delegate
        self.calls: dict[str, list[int]] = {
            "conditional_headers": [],
            "replace_global": [],
            "list_global": [],
        }

    def conditional_headers(self):
        self.calls["conditional_headers"].append(threading.get_ident())
        return self.delegate.conditional_headers()

    def replace_global(self, actions, etag):
        self.calls["replace_global"].append(threading.get_ident())
        return self.delegate.replace_global(actions, etag)

    def list_global(self):
        self.calls["list_global"].append(threading.get_ident())
        return self.delegate.list_global()


def _read_action(action_factory, **overrides):
    values = {
        "method": "GET",
        "path_template": "/company",
        "operation_id": "getCompany",
        "capability": "company.info.read",
        "risk_tier": 0,
        "required_confirmations": 0,
        "side_effects": (),
        "input_schema": {
            "path": {},
            "query": {},
            "headers": {},
            "body": {},
            "files": {},
        },
        "examples": (),
        "idempotency": {},
        "success_rules": {},
        "error_rules": {},
        "response_redaction": (),
    }
    values.update(overrides)
    return action_factory(**values)


@pytest.mark.asyncio
async def test_client_uses_cached_catalog_when_cloud_is_unavailable(
    repository_context, action_factory
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"catalog-v1"')

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(fail),
    )
    try:
        result = await client.list_actions(connector="flowaccount")
    finally:
        await client.aclose()

    assert result.source == "cache"
    assert result.actions == (action,)


@pytest.mark.asyncio
async def test_client_caches_cloud_catalog_by_etag_without_auth_header(
    repository_context, action_factory
) -> None:
    action = _read_action(action_factory)
    peak_action = _read_action(
        action_factory,
        connector_id="peak",
        path_template="/contacts",
        operation_id="listContacts",
        capability="contacts.read",
    )
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            headers={"ETag": '"catalog-v2"'},
            json={
                "actions": [
                    action.model_dump(mode="json"),
                    peak_action.model_dump(mode="json"),
                ]
            },
        )

    cache = CatalogCache(repository_context)
    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.list_actions(connector="flowaccount", method="GET")
    finally:
        await client.aclose()

    assert result.source == "cloud"
    assert result.actions == (action,)
    assert cache.list_global() == [action, peak_action]
    assert cache.conditional_headers() == {"If-None-Match": '"catalog-v2"'}
    assert dict(seen_requests[0].url.params) == {}
    assert "authorization" not in seen_requests[0].headers


@pytest.mark.asyncio
async def test_client_raises_for_identity_invalid_catalog_without_overwriting_snapshot(
    repository_context, action_factory
) -> None:
    cached_action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([cached_action], etag='"trusted"')
    invalid = cached_action.model_copy(update={"version_id": "av_invalid"})

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"ETag": '"untrusted"'},
                json={"actions": [invalid.model_dump(mode="json")]},
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="catalog_action_version_invalid"):
            await client.list_actions()
    finally:
        await client.aclose()

    assert cache.list_global() == [cached_action]
    assert cache.conditional_headers() == {"If-None-Match": '"trusted"'}


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_client_raises_for_cloud_4xx_without_mutating_cache(
    repository_context, action_factory, status_code
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"trusted"')
    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code, json={"error": "rejected"})
        ),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.list_actions()
    finally:
        await client.aclose()

    assert cache.list_global() == [action]
    assert cache.conditional_headers() == {"If-None-Match": '"trusted"'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"actions": "not-a-list"},
        {"actions": [{"not": "an-action"}]},
    ],
)
async def test_client_raises_for_malformed_200_without_mutating_cache(
    repository_context, action_factory, payload
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"trusted"')
    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
    )
    try:
        with pytest.raises((KeyError, TypeError, ValueError)):
            await client.list_actions()
    finally:
        await client.aclose()

    assert cache.list_global() == [action]
    assert cache.conditional_headers() == {"If-None-Match": '"trusted"'}


@pytest.mark.asyncio
async def test_client_raises_for_duplicate_catalog_without_mutating_cache(
    repository_context, action_factory
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"trusted"')
    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"ETag": '"duplicate"'},
                json={
                    "actions": [
                        action.model_dump(mode="json"),
                        action.model_dump(mode="json"),
                    ]
                },
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="catalog_cache_duplicate"):
            await client.list_actions()
    finally:
        await client.aclose()

    assert cache.list_global() == [action]
    assert cache.conditional_headers() == {"If-None-Match": '"trusted"'}


@pytest.mark.asyncio
async def test_client_uses_cache_for_5xx_without_mutation(
    repository_context, action_factory
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"trusted"')
    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(lambda _request: httpx.Response(503)),
    )
    try:
        result = await client.list_actions()
    finally:
        await client.aclose()

    assert result.source == "cache"
    assert result.actions == (action,)
    assert cache.conditional_headers() == {"If-None-Match": '"trusted"'}


@pytest.mark.asyncio
async def test_client_rejects_repository_path_identifier_before_network(
    repository_context,
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=CatalogCache(repository_context),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ValueError, match="cloud_identifier_invalid"):
            await client.get_document("/Users/operator/private/document.md")
    finally:
        await client.aclose()

    assert requests == []


@pytest.mark.asyncio
async def test_client_uses_etag_cache_on_not_modified(
    repository_context, action_factory
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"catalog-v2"')

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"catalog-v2"'
        return httpx.Response(304)

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.list_actions()
    finally:
        await client.aclose()

    assert result.source == "cache"
    assert result.actions == (action,)


@pytest.mark.asyncio
async def test_client_get_action_uses_detail_route_and_falls_back_to_cache(
    repository_context, action_factory
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"catalog-v2"')
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"action": action.model_dump(mode="json")})
        raise httpx.ConnectError("offline", request=request)

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    try:
        cloud_action = await client.get_action(action.action_id)
        cached_action = await client.get_action(action.action_id)
    finally:
        await client.aclose()

    assert cloud_action == cached_action == action
    assert requests[0].url.path == f"/api/cloud/v1/catalog/actions/{action.action_id}"
    assert "authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_client_redacts_sensitive_search_text_before_network(repository_context) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"results": []})

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=CatalogCache(repository_context),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.search_knowledge(
            "VAT person@example.com 0105559999999 api_key=private-value",
            filters={"jurisdiction": "TH"},
            top_k=4,
        )
    finally:
        await client.aclose()

    assert result == ()
    serialized = json.dumps(captured)
    assert "person@example.com" not in serialized
    assert "0105559999999" not in serialized
    assert "private-value" not in serialized


def test_cloud_base_url_defaults_and_loads_from_environment(monkeypatch) -> None:
    monkeypatch.delenv("MERCURY_CLOUD_BASE_URL", raising=False)
    assert load_settings().cloud_base_url == DEFAULT_CLOUD_BASE_URL

    monkeypatch.setenv("MERCURY_CLOUD_BASE_URL", "https://cloud.example.test/root/")
    assert load_settings().cloud_base_url == "https://cloud.example.test/root"


@pytest.mark.asyncio
async def test_client_uses_configured_cloud_base_url_when_not_explicit(
    monkeypatch, repository_context
) -> None:
    monkeypatch.setenv("MERCURY_CLOUD_BASE_URL", "https://configured.example.test/root/")

    client = CloudBrainClient(cache=CatalogCache(repository_context))
    try:
        assert str(client.client.base_url) == "https://configured.example.test/root/"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "input_schema",
            {
                "path": {},
                "query": {"secret": {"type": "string"}},
                "headers": {},
                "body": {},
                "files": {},
            },
        ),
        ("examples", ({"body": {"amount": 100}},)),
        ("idempotency", {"header": "Idempotency-Key"}),
        ("success_rules", {"status": [200]}),
        ("error_rules", {"400": "bad request"}),
        ("response_redaction", ("$.secret",)),
        ("source_uri", "file:///Users/operator/private/openapi.json"),
        ("source_uri", "mercury://wiki/../../private"),
        (
            "description",
            "SUPABASE_SERVICE_ROLE_KEY=sb_secret_TASK13_EXAMPLE",
        ),
    ],
)
async def test_client_rejects_nonpublic_catalog_projection_without_poisoning_cache(
    repository_context, action_factory, field, value
) -> None:
    cached_action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([cached_action], etag='"trusted"')
    malicious = cached_action.model_copy(update={field: value})
    malicious = malicious.model_copy(update={"version_id": build_version_id(malicious)})
    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"ETag": '"malicious"'},
                json={"actions": [malicious.model_dump(mode="json")]},
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="cloud_catalog_projection_invalid"):
            await client.list_actions()
    finally:
        await client.aclose()

    assert cache.list_global() == [cached_action]
    assert cache.conditional_headers() == {"If-None-Match": '"trusted"'}


@pytest.mark.asyncio
@pytest.mark.parametrize("etag", [None, "unquoted", '"unterminated'])
async def test_client_rejects_missing_or_malformed_etag_without_mutating_cache(
    repository_context, action_factory, etag
) -> None:
    action = _read_action(action_factory)
    cache = CatalogCache(repository_context)
    cache.replace_global([action], etag='"trusted"')
    headers = {} if etag is None else {"ETag": etag}
    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers=headers,
                json={"actions": [action.model_dump(mode="json")]},
            )
        ),
    )
    try:
        with pytest.raises(ValueError, match="cloud_catalog_etag_invalid"):
            await client.list_actions()
    finally:
        await client.aclose()

    assert cache.list_global() == [action]
    assert cache.conditional_headers() == {"If-None-Match": '"trusted"'}


@pytest.mark.asyncio
async def test_client_rejects_sensitive_filters_before_network(repository_context) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=CatalogCache(repository_context),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ValueError, match="cloud_search_invalid"):
            await client.search_knowledge(
                "VAT",
                filters={
                    "connector": "SUPABASE_SERVICE_ROLE_KEY=sb_secret_TASK13_EXAMPLE"
                },
            )
    finally:
        await client.aclose()

    assert requests == []


@pytest.mark.asyncio
async def test_client_offloads_catalog_etag_read_and_successful_replacement(
    repository_context, action_factory
) -> None:
    old_action = _read_action(action_factory)
    new_action = _read_action(
        action_factory,
        path_template="/contacts",
        operation_id="listContacts",
        capability="contacts.read",
    )
    delegate = CatalogCache(repository_context)
    delegate.replace_global([old_action], etag='"old"')
    cache = ThreadTrackingCache(delegate)
    event_loop_thread = threading.get_ident()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"old"'
        return httpx.Response(
            200,
            headers={"ETag": '"new"'},
            json={"actions": [new_action.model_dump(mode="json")]},
        )

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.list_actions()
    finally:
        await client.aclose()

    assert result.actions == (new_action,)
    assert cache.calls["conditional_headers"]
    assert cache.calls["replace_global"]
    assert all(
        thread_id != event_loop_thread
        for method in ("conditional_headers", "replace_global")
        for thread_id in cache.calls[method]
    )
    assert delegate.list_global() == [new_action]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["304", "5xx", "transport"])
async def test_client_offloads_catalog_cache_reads_for_every_fallback(
    repository_context, action_factory, failure_mode
) -> None:
    action = _read_action(action_factory)
    delegate = CatalogCache(repository_context)
    delegate.replace_global([action], etag='"trusted"')
    cache = ThreadTrackingCache(delegate)
    event_loop_thread = threading.get_ident()

    def handler(request: httpx.Request) -> httpx.Response:
        if failure_mode == "transport":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(304 if failure_mode == "304" else 503)

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.list_actions()
    finally:
        await client.aclose()

    assert result.source == "cache"
    assert result.actions == (action,)
    assert cache.calls["conditional_headers"]
    assert cache.calls["list_global"]
    assert all(
        thread_id != event_loop_thread
        for method in ("conditional_headers", "list_global")
        for thread_id in cache.calls[method]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["5xx", "transport"])
async def test_client_offloads_detail_cache_lookup_for_get_action_fallback(
    repository_context, action_factory, failure_mode
) -> None:
    action = _read_action(action_factory)
    delegate = CatalogCache(repository_context)
    delegate.replace_global([action], etag='"trusted"')
    cache = ThreadTrackingCache(delegate)
    event_loop_thread = threading.get_ident()

    def handler(request: httpx.Request) -> httpx.Response:
        if failure_mode == "transport":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(503)

    client = CloudBrainClient(
        base_url="https://cloud.example.test",
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.get_action(action.action_id)
    finally:
        await client.aclose()

    assert result == action
    assert cache.calls["list_global"]
    assert all(
        thread_id != event_loop_thread for thread_id in cache.calls["list_global"]
    )
