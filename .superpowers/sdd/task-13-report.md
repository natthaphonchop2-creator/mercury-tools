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

## Fix Round 4: Structural Final Fix

### Status

DONE_WITH_CONCERNS

This structural fix started from reviewed Task 13 head `0fcf621a02d6cf41bcffb555e928f51a117b6d08`. The concurrent Task 14 worker committed `c3ef61a0485749a4d5fcb9d21b6bc80abe7b3cbd` while this work was in progress. No Task 14 flow source, template, model, test, or report path was edited, staged, reverted, or included in the Task 13 fix commit.

### Structural Changes

1. Added `src/mercury_tools/cloud/models.py` as the shared fail-closed public boundary. Strict Pydantic models forbid extra fields and validate exact connector, skill, skill-detail, search, citation, and document response shapes. They also enforce canonical identifiers and Wiki URIs, finite scores, JSON-safe values, sanitized idempotent public text, safe public URLs, and requested skill/document identity.
2. `CloudBrainClient` now validates every 200 response from `list_connectors`, `list_skills`, `get_skill`, `search_knowledge`, and `get_document` through those models. Any malformed response raises only `ValueError("cloud_public_response_invalid")`; raw payloads are never returned. Catalog path templates are admitted before model construction and before cache replacement.
3. Skill IDs now use a dedicated grammar without colon or assignment syntax on both API and client boundaries. Action IDs and document identifiers retain their canonical path-specific validators. Sensitive skill IDs are rejected before URL construction or loader access.
4. Skill loading, type checking, sanitization, strict projection, and serialization preparation now remain inside one ordinary dependency-error boundary. Runtime, malformed type, and projection errors return the constant 503 response while `BaseException` remains uncaught.
5. Path handling now classifies raw and bounded percent-decoded representations instead of adding endpoint-specific substitutions. It covers repeated leading slashes, file URIs, Windows drives, UNC paths, and single/double encoded variants with explicit depth and byte limits. Safe HTTP(S), Mercury URIs, API templates, and ordinary encoded web paths remain intact.
6. API path templates now share one server/client admission rule. It requires a single-leading-slash relative API path and rejects local roots, repeated slashes, file/drive/UNC forms, credentials, query/fragment data, traversal, backslashes, encoded local paths, unsafe representations, and unresolved decoding beyond the supported bound.
7. Shared text redaction now classifies bounded percent-encoded credential representations, including quoted JSON, Authorization, Cookie, and double-encoded forms. Sensitive encoded segments fail closed as one constant placeholder.
8. Shared structured redaction now recognizes recursive header descriptor mappings such as `{name, value}` and `{name, values}`. Authorization and complete Cookie/Set-Cookie values are removed, mixed placeholder-plus-secret values fail closed, and only complete documented placeholder values are preserved.
9. Repeated-slash, file URI, encoded secret, structured header, and local path matrices prove unsafe values do not reach the RAG query spy and do not leave catalog, skill, search, or document projections.
10. All previous Task 13 guarantees remain covered: exactly seven read-only routes, canonical reviewed Wiki membership, forced reviewed filters, immutable canonical skills, async cache/store calls off-loop, ETag fallback policy, no outbound credentials/repository/ERP payloads, and lazy Supabase dependencies.

### Changed Files

- `.superpowers/sdd/task-13-report.md`
- `src/mercury_tools/cloud/api.py`
- `src/mercury_tools/cloud/client.py`
- `src/mercury_tools/cloud/models.py` (new shared strict public schema)
- `src/mercury_tools/safety/redaction.py`
- `tests/test_cloud_api.py`
- `tests/test_cloud_client.py`
- `tests/test_redaction.py`

### RED Evidence

The required endpoint, client, and redaction regressions were added before production changes:

```text
$ uv run python -m py_compile tests/test_cloud_api.py tests/test_cloud_client.py tests/test_redaction.py
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py tests/test_redaction.py -q
69 failed, 218 passed in 1.37s
```

The failures reproduced the permissive 200 client responses, sensitive skill identifier grammar, skill projection boundary escape, malformed loader coercion, unsafe path templates, repeated/file/UNC path leakage, percent-encoded credential leakage, and unrecognized structured header descriptors.

A self-review RED cycle then covered documented placeholders in descriptor-shaped headers and credential-like keys nested inside citation sections:

```text
$ uv run pytest \
    tests/test_redaction.py::test_json_redaction_recognizes_nested_header_descriptor_shapes \
    tests/test_cloud_client.py::test_client_rejects_every_malformed_public_200_with_constant_error -q
2 failed, 24 passed in 0.31s
```

The first full-suite pass also exposed an overbroad global `credential` key policy:

```text
$ uv run pytest -m 'not integration' -q
2 failed, 1381 passed, 1 deselected, 1 warning in 6.37s
```

The representation-only credential classifier was separated from legacy JSON container redaction, preserving safe internal `credentials` containers and `credentials_configured` metadata.

### GREEN Evidence

The final focused Cloud/API/client/redaction/hosted/RAG set passed:

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_redaction.py tests/test_http_app.py tests/test_mcp_rag_routing.py -q
316 passed, 1 warning in 1.64s
```

The complete non-integration suite passed:

```text
$ uv run pytest -m 'not integration' -q
1384 passed, 1 deselected, 1 warning in 5.90s
```

The warning is the pre-existing Starlette `TestClient` deprecation warning.

Static and whitespace verification:

```text
$ uv run ruff check .
All checks passed!

$ git diff --check
<no output>
```

### Commit

- Reviewed Task 13 base: `0fcf621a02d6cf41bcffb555e928f51a117b6d08`
- Concurrent Task 14 parent: `c3ef61a0485749a4d5fcb9d21b6bc80abe7b3cbd`
- Subject: `fix: enforce strict Cloud public boundary`
- Final hash: reported in the completion response because a commit cannot contain its own hash.

### Residual Concern

- `match_knowledge_chunks` still lacks a database RPC URI-prefix parameter. The accepted mitigation remains forced `review_status="reviewed"` plus strict canonical `mercury://wiki/...` validation after fetch and before public projection. Moving that restriction into query execution remains a future database/RPC change outside Task 13.

## Fix Round 5: Bounded Acceptance Fix

### Status

DONE_WITH_CONCERNS

This bounded acceptance fix starts from `f18de2a` with the concurrent Task 14
flow commit and uncommitted local MCP work present in the shared worktree. No
Task 14 flow, local runtime, local server, local MCP test, or MCP contract path
was edited, staged, reverted, or included in this Task 13 commit.

### Findings Addressed

1. HTTP(S) URLs are no longer trusted solely from scheme and authority. Shared
   redaction now inspects bounded single/double decoded path, query names,
   query values, and fragment components for local filesystem paths and
   credential syntax. Unsafe inbound URLs are redacted before the RAG call and
   unsafe outbound `source_url` values fail the public schema. Safe URLs,
   encoded web paths, Mercury URIs, and API path templates remain supported.
2. `PublicCitation` now exposes only explicit scalar fields. `heading`,
   `section`, titles, and URIs are strings or null; `chunk_index` and `page`
   are strict integers or null. Nested mappings/lists and coercive scalar types
   are rejected by both API projection and the exact client response model.
3. Catalog JSON is validated with exact raw types before `CatalogAction`
   normalization. Canonical action, connector, operation, variant, preflight,
   and API path identities are enforced on server and client. The client also
   applies every public projection and source rule before atomic cache
   replacement, so malformed 200 responses preserve the previous snapshot.
4. Search result `metadata.review_status` must be a string. Missing, boolean,
   numeric, mapping, or list values return the constant 503 response; valid
   non-reviewed strings remain filtered from the successful response.
5. All previous Task 13 read-only route, reviewed Wiki membership, redaction,
   ETag/cache fallback, async boundary, exact response, and constant-error
   guarantees remain covered. The concurrent Task 14 tests are included in the
   full non-integration verification.

### Changed Files

- `.superpowers/sdd/task-13-report.md`
- `src/mercury_tools/cloud/api.py`
- `src/mercury_tools/cloud/client.py`
- `src/mercury_tools/cloud/models.py`
- `src/mercury_tools/safety/redaction.py`
- `tests/test_cloud_api.py`
- `tests/test_cloud_client.py`
- `tests/test_redaction.py`

### RED Evidence

The first URL component and citation schema cycle failed as expected:

```text
$ uv run pytest -q <url-citation-focused-tests>
18 failed, 28 passed in 0.46s
```

The strict catalog admission and review metadata cycle then failed:

```text
$ uv run pytest -q <catalog-review-focused-tests>
13 failed, 5 passed in 0.34s
```

Self-review added encoded local paths in query parameter names and reproduced
the remaining bypass across shared redaction, API, and client boundaries:

```text
$ uv run pytest -q <query-name-focused-tests>
4 failed, 44 passed in 0.38s
```

Total expected RED failures observed across the three TDD cycles: 35.

### GREEN Evidence

The final focused Cloud/API/client/models/redaction/hosted/RAG set passed:

```text
$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_redaction.py tests/test_http_app.py \
    tests/test_mcp_rag_routing.py -q
359 passed, 1 warning in 2.29s
```

The complete non-integration suite, including concurrent Task 14 tests, passed:

```text
$ uv run pytest -m 'not integration' -q
1456 passed, 1 deselected, 1 warning in 8.81s
```

Static verification passed:

```text
$ uv run ruff check .
All checks passed!
```

The warning is the pre-existing Starlette `TestClient` deprecation warning.

### Commit

- Parent: `7668c43a8d1b8c76c5c217d43e369c163d0e12f5`
- Subject: `fix: close Task 13 bounded acceptance gaps`
- Final hash: reported in the completion response because a commit cannot
  contain its own hash.

### Residual Concern

- `match_knowledge_chunks` still lacks a database RPC URI-prefix parameter.
  The accepted mitigation remains forced `review_status="reviewed"` plus strict
  canonical `mercury://wiki/...` validation after fetch and before public
  projection. Moving the URI restriction into query execution remains a future
  database/RPC change outside Task 13.

## Fix Round 6: Executable Catalog Compatibility

### Status

DONE_WITH_CONCERNS

This compatibility fix starts from `77e5b1a`. It changes only the owned Cloud
API/client/models tests and this report. No flow, local MCP, plugin, catalog
generator, or generated catalog file was edited.

### Findings Addressed

1. Replaced the lossy server-side catalog projection with admission of the
   original canonical `CatalogAction`. Public-safe `action_id`, `version_id`,
   executable input schema, idempotency metadata, success/error rules,
   response-redaction selectors, source URI, and source hash now cross the
   Cloud boundary unchanged. The server no longer recomputes a projected
   version ID.
2. Added one shared executable-contract validator used by the API and client.
   It strictly allowlists parameter, body, file, idempotency, response-rule,
   redaction-selector, and public source shapes. Unsupported schema composition,
   references, defaults, examples, unknown keys, malformed status codes,
   traversal selectors, unsafe paths/URLs, and credential-bearing values fail
   closed.
3. Kept schema field names such as `client_secret`, `password`, and
   `access_token` valid as declarations or redaction selectors while rejecting
   credential values. This distinction preserves the generated FlowAccount and
   PEAK contracts without exposing credentials.
4. The client validates exact raw JSON container and scalar types before
   `CatalogAction` coercion, revalidates canonical identity, applies the shared
   public validator, and only then atomically replaces the previous cache.
5. Added an end-to-end client test proving a fetched global POST action can be
   passed to `build_request`, validates path/query/body inputs, derives the
   idempotency header, and retains the canonical version ID.
6. Added a parameterized validator regression over every generated builtin
   action: 190 FlowAccount actions and 64 PEAK actions.
7. Existing catalog redaction tests now assert fail-closed 503 behavior for an
   unsafe canonical action. Other skill, RAG, and document projections continue
   to redact and return successful public responses.

### RED Evidence

The inherited RED tests reproduced the compatibility gap exactly:

```text
$ .venv/bin/pytest -q tests/test_cloud_api.py tests/test_cloud_client.py
10 failed, 281 passed in 1.26s
```

The failures covered schema/version loss, executable contract loss, unsupported
shape admission, examples being stripped instead of rejected, and the local
client rejecting a canonical executable action.

The added builtin validator test was also observed RED before the shared
validator existed:

```text
$ .venv/bin/pytest -q \
    tests/test_cloud_api.py::test_public_catalog_validator_accepts_every_builtin_action
2 failed in 0.29s
```

### GREEN Evidence

Focused Cloud API/client tests:

```text
$ .venv/bin/pytest -q tests/test_cloud_api.py tests/test_cloud_client.py
293 passed in 2.09s
```

Focused Cloud API/client/redaction/hosted/RAG verification after cleanup:

```text
$ .venv/bin/pytest -q tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_redaction.py tests/test_http_app.py tests/test_mcp_rag_routing.py
370 passed, 1 warning in 2.02s
```

Complete non-integration verification:

```text
$ .venv/bin/pytest -q -m 'not integration'
1467 passed, 1 deselected, 1 warning in 7.50s
```

Direct builtin validation:

```text
flowaccount: 190 validated
peak: 64 validated
```

Static verification:

```text
$ .venv/bin/ruff check .
All checks passed!
```

The warning remains the pre-existing Starlette `TestClient` deprecation warning.

### Commit

- Parent: `77e5b1a453e5241a2d0280796ae59cade8373eae`
- Subject: `fix: preserve executable Cloud catalog actions`
- Final hash: reported in the completion response because a commit cannot
  contain its own hash.

### Residual Concern

- The allowlist intentionally fails closed when a future generator introduces a
  new schema or rule keyword. The two current generator quirks accepted for
  canonical compatibility are nested object schemas represented as
  `{"properties": ...}` without an explicit object type and API paths with a
  trailing slash. Any broader schema evolution requires an explicit validator
  and request-builder compatibility update.

## Fix Round 7: Final JSON, Encoding, and Catalog Review

### Status

DONE_WITH_CONCERNS

This final review started from Cloud head `fa91562`. The concurrent Task 14
worker committed its owned flow/local MCP changes as `9277e09` before the full
suite. This patch changes only shared redaction, Cloud API/models, Cloud and
redaction tests, and this Task 13 report. Cloud client envelope/cache behavior
is unchanged.

### Findings Addressed

1. Whole bounded JSON object/array strings are now parsed before plain-text
   regex projection. Recursive `redact_json` handling covers compact,
   whitespace, escaped JSON-string, and nested-array forms, including
   `header`/`name` descriptors for Authorization, Cookie, and Set-Cookie.
   Unsafe parse/projection, depth, node, wrapper, input-byte, or output-byte
   states fail closed to `[REDACTED]`. Safe JSON retains its original text and
   formatting so repeated sanitization is deterministic.
2. Percent decoding now uses one bounded iterative path with a maximum depth of
   8 and a 4 KiB token budget. Depths 1, 2, 3, and 8 are inspected; values that
   still change at depth 9 fail closed. Triple-encoded Authorization values are
   removed from both inbound RAG queries and outbound public RAG text. Safe
   nested URL text is preserved through the bound, while ambiguous over-bound
   URL text is rejected.
3. Catalog loading now rejects duplicate canonical `action_id` or `version_id`
   rows before sorting, filtering, payload construction, or ETag calculation.
   Both cases return the constant 503 envelope without an ETag.
4. Generic JSON redaction is no longer applied to the complete executable
   catalog payload during admission because schema property declarations such
   as `access_token` and `client_secret` are names, not credential values.
   Public scalar/list text is validated directly and executable schema/rule
   content remains under the existing strict structural validators. All 190
   FlowAccount and 64 PEAK builtins retain their canonical version and
   executable request contract.

### Changed Files

- `.superpowers/sdd/task-13-report.md`
- `src/mercury_tools/cloud/api.py`
- `src/mercury_tools/cloud/models.py`
- `src/mercury_tools/safety/redaction.py`
- `tests/test_cloud_api.py`
- `tests/test_redaction.py`

### RED Evidence

The direct JSON regression cycle failed as expected:

```text
$ .venv/bin/pytest -q tests/test_redaction.py -k 'whole_json or unsafe_json_projection'
6 failed, 1 passed, 64 deselected
```

The iterative percent-decoding cycle failed at depth 3 and above-bound cases:

```text
$ .venv/bin/pytest -q tests/test_redaction.py -k '<percent-depth-tests>'
4 failed, 7 passed, 60 deselected
```

The route-level and duplicate-catalog cycle failed as expected:

```text
$ .venv/bin/pytest -q tests/test_cloud_api.py -k '<json-percent-duplicate-tests>'
4 failed, 207 deselected
```

Total expected RED failures observed: 14.

### GREEN Evidence

Direct shared-redaction verification:

```text
$ .venv/bin/pytest -q tests/test_redaction.py
71 passed
```

Focused Cloud API/client/redaction/hosted/RAG verification:

```text
$ .venv/bin/pytest -q tests/test_cloud_api.py tests/test_cloud_client.py \
    tests/test_redaction.py tests/test_http_app.py tests/test_mcp_rag_routing.py
392 passed, 1 warning
```

Explicit executable compatibility checks:

```text
$ .venv/bin/pytest -q \
    tests/test_cloud_api.py::test_public_catalog_validator_accepts_every_builtin_action
2 passed  # FlowAccount 190 + PEAK 64

$ .venv/bin/pytest -q \
    tests/test_cloud_client.py::test_client_preserves_global_executable_contract_for_request_validation
1 passed
```

Complete non-integration verification after Task 14 stabilized:

```text
$ .venv/bin/pytest -q -m 'not integration'
1511 passed, 1 deselected, 1 warning

$ .venv/bin/ruff check .
All checks passed!

$ git diff --check
# no output
```

The warning remains the pre-existing Starlette `TestClient` deprecation warning.

### Commit

- Cloud base: `fa9156296e52801209d0f5b1bcbd00e42f1c88ad`
- Current parent after concurrent Task 14: `9277e09f54025fd3ba021bb66e90308dc09d4e03`
- Subject: `fix: close final Cloud redaction gaps`
- Final hash: reported in the completion response because a commit cannot
  contain its own hash.

### Residual Concern

- The public boundary intentionally fails closed beyond 8 percent decodes, 4
  escaped JSON-string wrappers, 32 JSON levels, 8,192 JSON nodes, or 64 KiB of
  JSON text. Legitimate content beyond those limits is redacted rather than
  projected. No known in-scope credential or duplicate-catalog bypass remains.

## Task 13-14 Acceptance Fix B: OpenAPI Required Semantics

### Status

IMPLEMENTED

This acceptance fix starts from `af6f80bc3247cb6ec5f39c7b2a5d957db3caddfd` and
keeps changes inside catalog import, executable request construction, Cloud
catalog validation/client tests, builtin compatibility tests, and this report.
Concurrent flow and local-MCP changes were not modified or staged.

### Canonical Contract

- Path, query, header, and file declarations may carry `required: <bool>`.
  Importers emit the key only when the upstream declaration is `true`; absence
  remains the backward-compatible optional representation.
- The whole request body uses root-only `x-mercury-required: <bool>` on the
  body schema. JSON Schema `required: [property, ...]` remains independently
  scoped to object properties.
- OpenAPI path parameters must explicitly declare `required: true`. Required
  cookie, unknown-location, non-list parameter containers, unresolved request
  body references, and non-boolean required values fail closed.
- Multipart binary properties move to `input_schema.files`; required files use
  the parameter marker, while required non-file properties remain in the body
  schema and required request-body presence uses the body marker.

Cloud admission accepts these markers only at their canonical locations and
requires actual booleans. String booleans, nested body markers, and unsupported
required keys remain invalid. Existing marker-free catalog actions retain the
same canonical identities and versions.

### RED Evidence

The initial focused reproductions failed as expected:

```text
OpenAPI required/path/cookie/multipart reproduction: 6 failed
Request builder/imported preview reproduction:       6 failed
Cloud executable round-trip reproduction:            2 failed, 3 strict-invalid passed
Optional body vs property-required reproduction:     1 failed
Non-list parameter-container reproduction:           1 failed
Unresolved requestBody reference reproduction:       1 failed
```

The failures showed required markers missing after import, absent body being
collapsed into `{}`, missing required inputs reaching network-policy validation,
and Cloud rejecting the new valid executable contract.

### GREEN Evidence

```text
$ uv run pytest tests/test_catalog_importers.py tests/test_action_catalog_models.py \
    tests/test_builtin_action_catalog.py -q
192 passed

$ uv run pytest tests/test_erp_executor.py tests/test_request_store.py \
    tests/test_execution_policy.py -q
115 passed

$ uv run pytest tests/test_cloud_api.py tests/test_cloud_client.py -q
300 passed

$ uv run pytest -m 'not integration' -q
1547 passed, 1 deselected, 1 warning

$ uv run ruff check .
All checks passed!

$ git diff --check
# no output
```

The builtin acceptance sweep revalidated and called `build_request` for all 190
FlowAccount and 64 PEAK actions using synthetic path and required inputs. All
254 actions built without network dispatch.

### Generated Catalogs

Both builtin builders were run twice from the documented source files. Each run
reported 190 FlowAccount and 64 PEAK actions, and the first and second generated
sets were byte-identical, including both endpoint wiki files. The current
sanitizer recalculated unrelated redaction-report counts, which would churn every
source hash and action version despite no required metadata change. Those
unrelated generated changes were excluded so canonical versions remain stable.

Changed generated files: none.

The branch advanced concurrently to parent
`0fb05d98ffad2cec265864da7dd7a907da571592` before this fix was committed. The
full verification above was rerun on that parent; its flow/local-MCP commit is
not part of this fix's staged diff.

### Residual

- OpenAPI component references are not resolved by this importer. Parameter
  references already fail validation, and request-body references now fail
  closed instead of silently losing required semantics.
- The full suite retains the pre-existing Starlette `TestClient` deprecation
  warning. No in-scope test or lint failure remains.

## Acceptance Fix C: Multipart Required Semantics

### Status

IMPLEMENTED

This acceptance fix started while Task 15 changes were uncommitted on
`d3a2d95b89d3737a46f77254075b7f72d9272d1d`. Task 15 stabilized independently
as `59a0b13d8e078988c0314e01fdfb9ba89fd958a7`, which is the parent for this fix.
No plugin, marketplace, product, runtime-Skill, or Task 15 report file was
modified or staged by this work.

### Findings Addressed

1. Request rendering now follows the catalog action media type rather than the
   presence of bound files. A multipart action with only body fields is encoded
   as multipart form fields even when `files` is absent or empty. Files-only and
   body-plus-files requests use the same multipart path.
2. `httpx` owns multipart boundary generation. Catalog media-type parameters
   are used only to recognize multipart and are never copied as a hard-coded
   boundary header. Empty optional multipart input remains an empty request with
   no fabricated boundary or dummy form field.
3. `application/json` retains JSON encoding and
   `application/x-www-form-urlencoded` uses form encoding.
4. OpenAPI import now validates every object-schema `required` contract before
   multipart properties can be split into body and file declarations. Required
   names must be a unique list of non-empty canonical strings and an exact
   subset of declared properties. Regular OpenAPI and Swagger body schemas are
   checked recursively under object properties and array items.
5. Valid required body fields, required multipart file markers, and the
   root-only `x-mercury-required` whole-body marker remain unchanged. A valid
   empty `required: []` list is preserved as the empty subset even when the
   schema omits `properties`.
6. Cloud public catalog admission already enforced canonical schema names,
   unique required lists, recursive subset membership, and root-only whole-body
   markers. New raw-validator and client cache-poisoning regressions prove the
   invariant, so no Cloud production change was required.
7. Missing required multipart bodies and fields fail during preview request
   construction before credential loading, network validation, preview record
   creation, or provider dispatch. A complete body-only multipart write reaches
   the provider as form data.

### Changed Files

- `.superpowers/sdd/task-13-report.md`
- `src/mercury_tools/catalog/importers/openapi.py`
- `src/mercury_tools/execution/request_builder.py`
- `tests/test_catalog_importers.py`
- `tests/test_cloud_api.py`
- `tests/test_cloud_client.py`
- `tests/test_erp_executor.py`

The generic driver required no change because it only validates and prepares
file paths; request media encoding is owned by `RequestTemplate`.

### RED Evidence

Importer malformed-required reproduction before implementation:

```text
$ uv run pytest -q tests/test_catalog_importers.py \
    -k 'malformed_object_required_contracts'
7 failed, 3 passed, 94 deselected
```

Request builder and executor multipart reproduction before implementation:

```text
$ uv run pytest -q tests/test_erp_executor.py \
    -k 'body_only_multipart or files_only_and_body_plus_files or \
        empty_optional_multipart or preserves_json_and_urlencoded or \
        required_multipart_fails or complete_body_only_multipart'
3 failed, 4 passed, 39 deselected
```

The failures were exactly body-only multipart and complete multipart dispatch
using `application/json`, plus urlencoded bodies using JSON. The Cloud
validator/client regressions passed before implementation (`10 passed`),
confirming that layer already failed closed.

Self-review then caught an over-strict empty-subset interpretation before the
adjustment. The importer and request-builder tests each failed once on valid
`required: []` without `properties`, then each passed after missing properties
were treated as an empty mapping. Explicit non-mapping properties still fail
closed.

### Multipart Wire Evidence

An imported body-only OpenAPI action was passed through `build_request` and
`RequestTemplate.to_httpx_request`. The generated boundary was normalized only
for this report:

```text
content-type: multipart/form-data; boundary=<BOUNDARY>
--<BOUNDARY>
Content-Disposition: form-data; name="caption"

Invoice
--<BOUNDARY>--
```

The wire body contains no JSON media marker or JSON object wrapper.

### GREEN Evidence

```text
$ uv run pytest -q tests/test_catalog_importers.py tests/test_erp_executor.py \
    tests/test_generic_drivers.py tests/test_cloud_api.py tests/test_cloud_client.py
552 passed in 2.40s

$ uv run pytest -q \
    tests/test_builtin_action_catalog.py::test_every_builtin_action_is_cloud_valid_and_request_buildable
1 passed in 0.67s
```

The builtin acceptance test validated Cloud admission and `build_request` for
all 190 FlowAccount and 64 PEAK actions, for 254 actions total.

```text
$ uv run pytest -q -m 'not integration'
1571 passed, 1 deselected, 1 warning in 8.23s
```

The final commands below are rerun after every production and report edit before
the single owned-scope commit:

```text
$ uv run ruff check .
All checks passed!

$ git diff --check
# no output

$ git diff --name-only -- .agents plugins src/mercury_tools/db/product.py \
    tests/test_plugin_package.py tests/test_private_mcp.py \
    tests/test_runtime_skills.py .superpowers/sdd/task-15-report.md
# no output
```

### Residual

- Empty optional multipart input intentionally emits no body and no
  `Content-Type`; inventing an empty part would change provider semantics.
- OpenAPI component references remain unsupported by this importer and continue
  to fail closed under the earlier acceptance fix.
- The full suite retains the pre-existing Starlette/httpx deprecation warning
  from `tests/test_connector_mcp_tools.py`.

## Acceptance Fix D: Canonical Multipart Request Boundaries

### Status

IMPLEMENTED

This final acceptance fix started from `11ed193`. Task 16 concurrently owns the
plugin manifests, packaging metadata and lockfile, embeddings, release docs,
and plugin package tests. None of those files was edited or staged by this fix.

### Findings Addressed

1. Model-supplied `Content-Type` headers are rejected case-insensitively even
   when an action schema declares that header. `httpx` therefore remains the
   sole owner of multipart boundary generation and cannot emit a body whose
   boundary disagrees with a user-controlled header.
2. OpenAPI import and executable request validation now use the same canonical
   schema-name predicate as Cloud admission. Property and required names must
   be non-empty, trimmed, bounded, sanitizer-stable names without path,
   assignment, URI, control-character, or encoding ambiguity.
3. A body-schema `required` list is valid only on an explicit object schema. It
   must be a canonical, unique subset of declared properties. A scalar schema
   carrying `required`, including `required: []`, fails closed.
4. Optional multipart `body=None` is normalized to the same absent body as an
   omitted body. It emits no dummy form part and no content type when no files
   exist; with files, only the declared file parts reach the wire. A whole-body
   `x-mercury-required` marker still rejects `None` as `required_body_missing`.
5. Existing JSON and form-urlencoded paths are unchanged. Multipart media-type
   parameters remain recognition-only and are never copied into the rendered
   request header.
6. Cloud already enforced the required-shape behavior. Its canonical name
   predicate was moved to the shared executable schema contract without
   changing public admission behavior, and client cache-poisoning regressions
   cover scalar required lists and noncanonical property names.

### Changed Files

- `.superpowers/sdd/task-13-report.md`
- `src/mercury_tools/catalog/schema_contract.py`
- `src/mercury_tools/catalog/importers/openapi.py`
- `src/mercury_tools/cloud/models.py`
- `src/mercury_tools/drivers/generic.py`
- `src/mercury_tools/execution/request_builder.py`
- `tests/test_catalog_importers.py`
- `tests/test_cloud_client.py`
- `tests/test_erp_executor.py`
- `tests/test_generic_drivers.py`

The generic API-key driver now rejects `Content-Type` as a header credential
name. Request rendering also removes a driver-authored transport content type
before `httpx` selects the canonical encoding and boundary.

### RED Evidence

The focused acceptance tests were added before production changes:

```text
$ uv run pytest -q \
    tests/test_erp_executor.py::test_request_builder_rejects_user_content_type_overrides \
    tests/test_erp_executor.py::test_request_builder_rejects_noncanonical_body_required_contracts \
    tests/test_erp_executor.py::test_request_builder_treats_optional_multipart_none_body_as_absent \
    tests/test_erp_executor.py::test_request_builder_rejects_none_for_required_multipart_body \
    tests/test_catalog_importers.py::test_openapi_rejects_noncanonical_body_required_contracts \
    tests/test_cloud_client.py::test_client_rejects_nonpublic_catalog_projection_without_poisoning_cache
10 failed, 13 passed in 0.40s
```

The failures were the three `Content-Type` casings, three runtime schema
contracts, optional and required multipart `None`, and two importer contracts.
The Cloud cases passed before implementation, confirming that Cloud already
held the intended canonical boundary.

### GREEN Evidence

```text
$ uv run pytest -q tests/test_catalog_importers.py tests/test_erp_executor.py \
    tests/test_generic_drivers.py tests/test_cloud_api.py tests/test_cloud_client.py
570 passed in 2.56s
```

```text
$ uv run pytest -q \
    tests/test_erp_executor.py::test_request_builder_rejects_user_content_type_overrides \
    tests/test_erp_executor.py::test_request_builder_replaces_auth_content_type_for_multipart_rendering \
    tests/test_erp_executor.py::test_request_builder_encodes_body_only_multipart_with_client_boundary \
    tests/test_erp_executor.py::test_request_builder_encodes_files_only_and_body_plus_files_as_multipart \
    tests/test_erp_executor.py::test_request_builder_leaves_empty_optional_multipart_unencoded \
    tests/test_erp_executor.py::test_request_builder_treats_optional_multipart_none_body_as_absent \
    tests/test_erp_executor.py::test_request_builder_omits_none_body_from_multipart_file_wire \
    tests/test_erp_executor.py::test_request_builder_rejects_none_for_required_multipart_body \
    tests/test_erp_executor.py::test_request_builder_preserves_json_and_urlencoded_body_encodings \
    tests/test_erp_executor.py::test_complete_body_only_multipart_dispatches_form_data
13 passed in 0.13s
```

```text
$ uv run pytest -q \
    tests/test_builtin_action_catalog.py::test_every_builtin_action_is_cloud_valid_and_request_buildable
1 passed in 0.82s
```

The builtin sweep validates Cloud admission and calls `build_request` for all
190 FlowAccount and 64 PEAK actions, preserving all 254 builtins.

During concurrent Task 16 work, the full non-integration run excluded only its
two active package-test files:

```text
$ uv run pytest -q -m 'not integration' \
    --ignore=tests/test_plugin_package.py \
    --ignore=tests/test_plugin_clean_install.py
1552 passed, 1 deselected, 1 warning in 8.61s
```

The count changed because the concurrent Cloud-secret removal worker deleted
legacy private-journal tests and updated the remaining connector contracts. No
file from that change is part of this commit.

```text
$ uv run ruff check \
    src/mercury_tools/catalog/importers/openapi.py \
    src/mercury_tools/catalog/schema_contract.py \
    src/mercury_tools/cloud/models.py \
    src/mercury_tools/drivers/generic.py \
    src/mercury_tools/execution/request_builder.py \
    tests/test_catalog_importers.py tests/test_cloud_client.py \
    tests/test_erp_executor.py tests/test_generic_drivers.py
All checks passed!

$ git diff --check && git diff --cached --check
# no output
```

### Residual

- The full suite retains the pre-existing Starlette/httpx deprecation warning
  from `tests/test_connector_mcp_tools.py`.
- Task 16 package tests remained excluded from the final non-integration run.
  A direct package run completed 27 tests before the clean-wheel case timed out
  after 120 seconds while `uvx --no-cache` downloaded dependencies; later
  retries overlapped Task 16's own clean-install worker and were not used as
  acceptance evidence.
- Full-repository Ruff currently reports four errors only in the concurrent,
  unstaged `scripts/purge_cloud_erp_secrets.py` and
  `tests/test_cloud_secret_removal.py`. Owned-scope Ruff and both staged and
  unstaged diff checks pass.
