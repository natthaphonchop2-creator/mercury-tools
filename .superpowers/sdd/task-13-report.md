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

## Fix Round: Cloud Boundary Hardening

### Status

DONE_WITH_CONCERNS

This fix round addresses the consolidated Task 13 architecture and adversarial findings without adding Task 14 behavior or changing the seven-route read-only surface.

### Findings Addressed

1. Replaced `startswith("mercury://wiki/")` trust decisions with one strict canonical wiki URI validator. The same validator covers public document request URIs, returned document/source URIs, and search document/source/chunk URIs. It rejects traversal segments, empty/doubled segments, trailing separators, percent-encoded ambiguity, backslashes, query strings, unexpected fragments, and noncanonical scheme/authority casing.
2. Extended shared redaction for modern `sb_secret_...` values, `SUPABASE_SERVICE_ROLE_KEY`, `service_role_key`, and generic sensitive key/value assignments. The JSON-key policy remains narrow enough to preserve credential containers and nonsecret status metadata.
3. Added canonical search-filter validation before `SearchFilters`: selector fields use the existing strict selector grammar, `effective_date` must be a canonical ISO date, sensitive values are rejected after defense-in-depth sanitization, and `review_status` is always overwritten with `reviewed` before RAG.
4. Snapshotted canonical skill metadata from `SKILL_CATALOG_SEED`. Injected `skills` data can no longer replace a seeded title, summary, or version, and only canonical seeded IDs reach the markdown loader.
5. Added client-side public catalog projection admission. A valid immutable `CatalogAction` is still rejected before cache mutation when it contains ERP schemas/examples, idempotency or success/error rules, response redaction paths, unsafe source URIs, unsafe text, or invalid public projection fields. Existing cached rows are validated before fallback use.
6. A 200 catalog response now requires a syntactically valid ETag. Missing or malformed ETags raise and preserve the last good snapshot and conditional ETag.
7. Search and document upstream rows now pass explicit shape, finite-number, canonical URI, public membership, and JSON-serializability checks. Malformed metadata, citations, values, or containers return the constant `503 {"error":"service_unavailable"}` response.
8. Preserved all prior guarantees: exactly seven read-only routes, wrong methods return 405, lazy Supabase setup, no client Authorization header/repository path/ERP payload/secret, one unfiltered global catalog snapshot with local filters, no stale fallback for 4xx or malformed 200, and fallback only for transport errors, 5xx, and 304.

### Changed Files

- `src/mercury_tools/cloud/api.py`
- `src/mercury_tools/cloud/client.py`
- `src/mercury_tools/safety/redaction.py`
- `tests/test_cloud_api.py`
- `tests/test_cloud_client.py`
- `.superpowers/sdd/task-13-report.md`

### RED Evidence

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q
46 failed, 46 passed in 1.17s
```

The failures covered all mandatory fix findings: canonical public URI bypasses, unsanitized filters and Supabase secrets, injected seeded-skill replacement, malformed upstream projection failures, untrusted catalog projection admission, and missing/malformed ETag handling.

The first full non-integration run also caught an overbroad shared JSON-key redaction regression:

```text
$ uv run pytest -m 'not integration' -q
2 failed, 1191 passed, 1 deselected, 1 warning in 5.72s
```

The JSON-key matcher was narrowed while retaining text-assignment and service-role redaction. The two affected regressions and the new end-to-end secret test then passed together.

### GREEN Evidence

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q
92 passed in 0.42s
```

```text
$ uv run pytest tests/test_redaction.py -q
15 passed in 0.02s
```

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_http_app.py tests/test_mcp_rag_routing.py tests/test_redaction.py -q
131 passed, 1 warning in 0.84s
```

```text
$ uv run pytest \
    tests/test_generic_drivers.py::test_terminal_wildcard_redaction_replaces_every_child_value \
    tests/test_product_fallback.py::test_product_store_audit_fallback_encrypts_connector_credentials \
    tests/test_cloud_api.py::test_cloud_redacts_supabase_secrets_across_every_public_projection \
    tests/test_redaction.py -q
18 passed in 0.15s
```

```text
$ uv run pytest -m 'not integration' -q
1193 passed, 1 deselected, 1 warning in 6.75s
```

```text
$ uv run ruff check .
All checks passed!
```

```text
$ git diff --check
<no output>
```

### Commit

- Parent: `85d0867f156d234f9edbc570e8d85f0319b2b016`
- Subject: `fix: harden Mercury Cloud Brain boundaries`
- Final hash: reported in the completion response because a commit cannot contain its own hash.

### Residual Concerns

- `match_knowledge_chunks` still has no source-URI-prefix parameter. This fix retains the forced `review_status="reviewed"` store filter and now applies strict canonical `mercury://wiki/...` membership validation after fetch. No legacy or malformed row can be projected, but a future database/RPC change would be needed to move the URI boundary into query execution.
- The pre-existing Starlette `TestClient` deprecation warning remains. Integration tests were intentionally excluded by the required full non-integration command.

## Fix Round 2: Async Cache and Projection Boundaries

### Status

DONE_WITH_CONCERNS

This second fix round addresses every mandatory re-review finding while preserving the existing seven-route read-only surface and prior Task 13 hardening.

### Findings Addressed

1. Moved every synchronous `CatalogCache` access used by `CloudBrainClient` behind `anyio.to_thread.run_sync`, including conditional ETag reads, successful global replacement, 304/5xx/transport fallback reads, and action-detail fallback reads. The client still fetches and caches one unfiltered global snapshot and applies connector/method filters locally.
2. Added explicit ordinary-exception boundaries for skill loaders and search/document dependency and projection failures. `KeyError`, `TypeError`, `ValueError`, and `OSError` now produce the constant `503 {"error":"service_unavailable"}` response where applicable without catching cancellation or other `BaseException` subclasses.
3. Extended shared text and JSON redaction for `Authorization`, `Proxy-Authorization`, `Cookie`, and `Set-Cookie` values while preserving documented placeholders. Inbound queries and every public catalog, skill, search, and document projection are covered by regression tests.
4. Added strict canonical validation for returned search `chunk_id` and `document_id` values. Identifiers containing headers, paths, whitespace, or sanitizer-sensitive material fail closed with the constant 503 response instead of being string-cast into output.
5. Centralized local absolute-path redaction for common POSIX roots including `/opt`, `/workspace`, and `/mnt`, plus Windows drive paths. Safe HTTP URLs, canonical Mercury URIs, endpoint path templates, and ordinary slash-separated prose remain unchanged.
6. Changed catalog response ETags to hash the actual filtered response representation. Different filter results now have different ETags, and `If-None-Match` returns 304 only for the matching representation. The local client continues to request only the unfiltered representation for its global cache.
7. Preserved prior guarantees, including strict public Wiki URI post-fetch enforcement, forced `review_status="reviewed"`, lazy dependencies, exact route/method behavior, catalog projection admission, and fail-closed cache semantics.

### Changed Files

- `src/mercury_tools/cloud/api.py`
- `src/mercury_tools/cloud/client.py`
- `src/mercury_tools/safety/redaction.py`
- `tests/test_cloud_api.py`
- `tests/test_cloud_client.py`
- `.superpowers/sdd/task-13-report.md`

### RED Evidence

The required regression tests were added before implementation:

```text
$ uv run python -m py_compile tests/test_cloud_api.py tests/test_cloud_client.py && uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q
27 failed, 101 passed in 3.63s
```

The failures covered event-loop cache access, ordinary dependency exceptions escaping as 500, authorization/cookie leakage, malformed search identifiers, uncovered absolute paths, and ETags shared across filtered representations.

A placeholder-preservation test added during self-review failed before the JSON redaction adjustment:

```text
$ uv run pytest tests/test_cloud_api.py::test_shared_json_redaction_preserves_documented_header_placeholders -q
1 failed in 0.18s
```

The first full non-integration run then exposed one overbroad generic Bearer match in existing product YAML:

```text
$ uv run pytest -m 'not integration' -q
1 failed, 1229 passed, 1 deselected, 1 warning in 9.68s
```

The matcher was narrowed to preserve the ordinary documentation phrase `bearer tokens` while retaining generic credential redaction.

### GREEN Evidence

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q
128 passed in 0.95s
```

```text
$ uv run pytest tests/test_cloud_api.py::test_shared_json_redaction_preserves_documented_header_placeholders -q
1 passed in 0.13s
```

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_http_app.py tests/test_mcp_rag_routing.py tests/test_redaction.py -q
168 passed, 1 warning in 1.24s
```

The focused set was rerun with the full-suite regression included after narrowing the Bearer matcher:

```text
$ uv run pytest \
    tests/test_product_fallback.py::test_product_store_audit_fallback_records_workspace_flow \
    tests/test_cloud_api.py tests/test_cloud_client.py tests/test_http_app.py \
    tests/test_mcp_rag_routing.py tests/test_redaction.py -q
169 passed, 1 warning in 1.07s
```

```text
$ uv run pytest -m 'not integration' -q
1230 passed, 1 deselected, 1 warning in 7.60s
```

```text
$ uv run ruff check src/mercury_tools/cloud/api.py \
    src/mercury_tools/cloud/client.py src/mercury_tools/safety/redaction.py \
    tests/test_cloud_api.py tests/test_cloud_client.py
All checks passed!
```

```text
$ git diff --check
<no output>
```

### Commit

- Parent: `a672d8fb1699d50ddd0a4ea63598325984019167`
- Subject: `fix: close Cloud Brain review gaps`
- Final hash: reported in the completion response because a commit cannot contain its own hash.

### Residual Concerns

- `match_knowledge_chunks` still has no source-URI-prefix parameter. The API therefore retains the accepted boundary of forcing `review_status="reviewed"` and applying strict canonical `mercury://wiki/...` membership validation after fetch. Moving this restriction into query execution requires a future RPC/database change outside Task 13.
- The pre-existing Starlette `TestClient` deprecation warning remains. The required full suite excluded the single integration test.

## Fix Round 3: Final Hardening

### Status

DONE_WITH_CONCERNS

This final hardening round closes all four mandatory findings from HEAD `55bc60fb98dee9cda90fb812aea0af64f588ae3a` without changing Task 14 files or the existing seven-route read-only Cloud surface.

### Findings Addressed

1. Public citation projection now sends every nested mapping through the shared recursive JSON key-redaction policy before recursively sanitizing text and paths. Permitted citation fields, nested mappings, nested lists, and JSON-safe output are preserved, while `cookie`, `set-cookie`, `authorization`, `password`, `token`, and `secret` values become constant placeholders.
2. Added one `_ORDINARY_DEPENDENCY_ERRORS` tuple containing `httpx.HTTPError`, `KeyError`, `TypeError`, `ValueError`, `OSError`, `RuntimeError`, and `OverflowError`. Catalog list/detail, connector projection, skill catalog/loading, search, document, and post-fetch projection boundaries use this tuple consistently and return the exact `503 {"error":"service_unavailable"}` body. A dedicated regression proves `BaseException` is not caught; cancellation, `KeyboardInterrupt`, and `SystemExit` remain outside the tuple.
3. Cookie text redaction now consumes the complete line-delimited `Cookie` or `Set-Cookie` value, including semicolon-delimited pairs and attributes. Only a whole safe documented placeholder value is preserved; placeholder-plus-secret input is fully redacted. Shared JSON, inbound RAG query, and outbound public projection regressions cover the policy.
4. Local absolute-path redaction now checks single and repeated percent encodings case-insensitively, with a maximum decode depth of 3 and a 4,096-byte token bound. It fails closed for ambiguous encoded absolute roots and Windows drive paths while preserving valid HTTP URLs, Mercury URIs, endpoint templates, relative encoded URL paths, and deeply encoded relative URL paths.
5. All previous Task 13 adversarial API/client/hosted/RAG/redaction tests remain green. No Task 14 file was modified.

### Changed Files

- `src/mercury_tools/cloud/api.py`
- `src/mercury_tools/safety/redaction.py`
- `tests/test_cloud_api.py`
- `tests/test_redaction.py`
- `.superpowers/sdd/task-13-report.md`

### RED Evidence

All mandatory endpoint and shared-redaction regressions were added before production changes:

```text
$ uv run pytest tests/test_cloud_api.py tests/test_redaction.py -q
38 failed, 136 passed in 2.20s
```

The failures covered nested citation mappings/lists, malformed catalog `None`, ordinary dependency and projection exceptions, skill-loader `RuntimeError`, semicolon cookie tails, and percent-encoded local paths crossing inbound and outbound boundaries.

A second RED cycle tightened safe relative URL-path preservation at the decode bound:

```text
$ uv run pytest tests/test_redaction.py::test_absolute_path_redaction_preserves_safe_public_paths -q
1 failed, 5 passed in 0.03s
```

### GREEN Evidence

The mandatory API and redaction set passed after the implementation:

```text
$ uv run pytest tests/test_cloud_api.py tests/test_redaction.py -q
174 passed in 0.38s
```

The final focused Cloud/API/client/redaction/hosted/RAG set, including bounded decoding and `BaseException` regressions, passed:

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_redaction.py tests/test_http_app.py tests/test_mcp_rag_routing.py -q
240 passed, 1 warning in 1.64s
```

The warning is the pre-existing Starlette `TestClient` deprecation warning.

The complete non-integration suite passed:

```text
$ uv run pytest -m 'not integration' -q
1302 passed, 1 deselected, 1 warning in 6.08s
```

Static and whitespace verification passed:

```text
$ uv run ruff check .
All checks passed!

$ git diff --check
<no output>
```

### Commit

- Parent: `55bc60fb98dee9cda90fb812aea0af64f588ae3a`
- Subject: `fix: finalize Task 13 Cloud hardening`
- Final hash: reported in the completion response because a commit cannot contain its own hash.

### Residual Concern

- `match_knowledge_chunks` still has no database RPC URI-prefix parameter. The accepted mitigation remains forced `review_status="reviewed"` plus strict canonical `mercury://wiki/...` URI checks after fetch and before public projection. Moving the URI restriction into query execution requires a future database/RPC change outside Task 13.
