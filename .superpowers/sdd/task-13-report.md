# Task 13 Implementation Report

## Status

DONE_WITH_CONCERNS

Task 13 is implemented on top of commit `77dd6fc`. The Cloud Brain HTTP surface is read-only, mounted independently of legacy HTTP APIs, and backed by lazy Supabase dependencies. The local async client keeps one immutable global catalog snapshot and applies filters locally.

## Changed Files

- `src/mercury_tools/cloud/__init__.py`
- `src/mercury_tools/cloud/api.py`
- `src/mercury_tools/cloud/client.py`
- `src/mercury_tools/config.py`
- `src/mercury_tools/mcp/server.py`
- `tests/test_cloud_api.py`
- `tests/test_cloud_client.py`
- `tests/test_http_app.py`
- `.superpowers/sdd/task-13-report.md`

No files outside Task 13 ownership and this required report were modified.

## RED Evidence

Initial RED:

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q
ERROR tests/test_cloud_api.py
ERROR tests/test_cloud_client.py
ModuleNotFoundError: No module named 'mercury_tools.cloud'
2 errors in 0.09s
```

The first GREEN attempt exposed test-harness issues and one client assertion, then passed after correcting the async fixture declaration and QueryParams comparison:

```text
13 passed in 0.14s
```

Terra/Luna adversarial RED was then recorded before the architecture fixes:

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_http_app.py::test_public_app_mounts_cloud_reads_without_cloud_write_or_legacy_routes -q
20 failed, 21 passed, 1 warning in 0.78s
```

Those failures covered public-corpus enforcement, strict IDs, query validation, async thread isolation, exact route shape, one-snapshot local filtering, identity validation, 4xx/malformed failure policy, and repository-path rejection.

Two additional RED tests were recorded during self-review:

```text
$ uv run pytest \
    tests/test_cloud_api.py::test_cloud_skill_injected_metadata_cannot_expand_seed_allowlist \
    tests/test_cloud_client.py::test_client_uses_configured_cloud_base_url_when_not_explicit -q
2 failed in 0.21s
```

## Delivered Behavior

- Added exactly seven Cloud Brain routes:
  - `GET /api/cloud/v1/catalog/actions`
  - `GET /api/cloud/v1/catalog/actions/{action_id}`
  - `GET /api/cloud/v1/connectors`
  - `GET /api/cloud/v1/skills`
  - `GET /api/cloud/v1/skills/{skill_id}`
  - `POST /api/cloud/v1/knowledge/search`
  - `GET /api/cloud/v1/documents/{document_id}`
- The API has no ERP execution or mutation route. Catalog metadata still describes GET, POST, PUT, PATCH, and DELETE actions as required.
- Catalog output is a credential-safe public projection. ERP request examples and schemas, local source paths, response rules, and payload-bearing metadata are removed; public projection identities are revalidated.
- Catalog query keys are unique and allowlisted. Connector selectors and uppercase HTTP methods are validated strictly.
- Added deterministic global catalog ETags and conditional `304` responses.
- Added `CloudBrainClient` with typed catalog results, direct detail reads, async lifecycle support, no Authorization header, and no repository/credential/ERP payload transmission.
- The client always fetches one unfiltered catalog snapshot, validates each immutable identity with `revalidate_catalog_action`, atomically replaces `catalog.sqlite`, and filters connector/method locally.
- `304`, transport errors, and 5xx responses use the existing cache without changing its ETag. HTTP 4xx, malformed 200 responses, duplicate actions, and invalid identities raise without changing the snapshot.
- Knowledge search accepts 1-2,000 query characters and strict integer `top_k` 1-20. Generic Bearer material, API-key assignments, emails, Thai tax IDs, URL credentials, and absolute paths are redacted before the RAG store call.
- Public search forces `review_status="reviewed"` and only projects results whose document URI, source URI, and chunk metadata remain inside reviewed `mercury://wiki/` content.
- Document IDs are validated as canonical UUIDs or strict `mercury://wiki/` URIs before the PostgREST-backed lookup. Returned documents must match the requested identity and reviewed public source membership.
- Skill detail loading is restricted to IDs present in `SKILL_CATALOG_SEED`; injected, unknown, absolute, and traversal-like IDs never reach `skill_markdown`.
- Catalog, RAG, document, and skill file calls cross a worker-thread boundary so synchronous stores do not block the async event loop.
- Cloud routes are mounted unconditionally before the legacy toggle. Missing Supabase configuration returns constant `503` payloads without breaking app creation. Legacy writes stay `404`, wrong Cloud methods return `405`, and `/mcp` remains mounted.
- Added `MERCURY_CLOUD_BASE_URL`, defaulting to `https://mercury-tools-mcp.onrender.com`, and made the client honor the configured value when no explicit base URL is supplied.

## Design Review Response

### Terra

1. Public corpus boundary: forced reviewed filtering before RAG plus reviewed `mercury://wiki/` membership checks before response projection.
2. Document safety: strict pre-store ID/URI validation, returned-identity check, reviewed source membership, fixed output whitelist, and constant service errors.
3. Skill safety: seed allowlist is checked before the worker-thread call to `skill_markdown`.
4. Catalog cache: one unfiltered snapshot and ETag, local filters, atomic replacement, and no mutation on 304/failure/invalid payloads.
5. App wiring: unconditional route mount with lazy dependencies; missing Supabase produces `503` at request time.
6. Projection safety: fixed top-level and nested output keys; no raw metadata, source paths, settings, upstream errors, ERP examples, or raw query echo.
7. Redaction: generic Bearer handling supplements shared token redaction and runs before the RAG call.
8. Async safety: all synchronous store/file calls use `run_in_threadpool`.

### Luna

- Added tests for the exact seven-route/method matrix, wrong methods, legacy isolation, and MCP compatibility.
- Added encoded traversal/injection tests for action, skill, and document identifiers before loader/store calls.
- Added duplicate/unknown catalog query key tests, strict method tests, exact query/top_k boundaries, bool/float/string/null rejection, and filter allowlisting.
- Added exact response-key projection tests and credential/path/metadata omission checks.
- Added client tests for unfiltered requests, local filtering, ETag persistence, 304 immutability, transport/5xx fallback, strict 4xx behavior, malformed/duplicate/identity-invalid payloads, and outbound safety.
- Preserved catalog metadata for all ERP action methods while exposing no execution route.

## GREEN Evidence

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q
44 passed in 0.29s
```

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_http_app.py tests/test_mcp_rag_routing.py -q
68 passed, 1 warning in 0.71s
```

```text
$ uv run pytest tests/test_catalog_stores.py tests/test_catalog_publisher.py \
    tests/test_action_catalog_models.py tests/test_search_filters.py \
    tests/test_runtime_skills.py -q
121 passed in 0.17s
```

```text
$ uv run pytest -q
1145 passed, 1 skipped, 1 warning in 5.50s
```

The warning is the existing Starlette `TestClient` deprecation warning recommending `httpx2`.

## Concerns

- The existing `match_knowledge_chunks` RPC has no source-URI-prefix parameter. Task 13 therefore enforces the public boundary by forcing `review_status="reviewed"` in the store call and then requiring reviewed `mercury://wiki/` document/source metadata before any result is returned. Legacy candidates cannot appear in the API response, but adding a source-prefix argument to a future RPC would move the same boundary into the database query.
- The suite still reports one pre-existing Starlette `TestClient` deprecation warning. It is unrelated to Task 13 behavior.
