# Task 3 Fix 3 Report: Lifecycle Hardening

## Status

Implemented and verified.

## Delivered

- Sanitized legacy HTTP typed-body validation failures so rejected values are
  never serialized, and added migration metadata to success and every handled
  error response.
- Reconstructed fallback state from a server-filtered, fully paginated,
  deterministic workspace event stream so late unlink tombstones cannot be
  omitted by the 500-row page size.
- Added `user_supplied` to the public connector environment and allowed
  catalog-declared discovered safe-read capabilities to validate for
  `generic_mcp` without changing fixed-catalog alias canonicalization.
- Made every exact profile relink clear capability states, evidence source,
  evidence reference, and validation timestamp in both fallback and product
  table storage.
- Preserved the exact seven public connector lifecycle tools and the canonical
  capability evidence behavior introduced by `cbb851f`.

## TDD Evidence

The focused regressions failed before the implementation changes:

```text
uv run pytest -q tests/test_http_app.py -k 'legacy_connector_setup or product_mutation_requires_supabase'
5 failed, 33 deselected, 1 warning in 0.56s

uv run pytest -q tests/test_product_fallback.py -k 'paginates_past_late_unlink or exact_relink or generic_mcp_user_supplied'
4 failed, 20 deselected in 0.20s

uv run pytest -q tests/test_connector_mcp_tools.py tests/test_connector_catalog.py tests/test_mcp_contract.py -k 'generic_mcp_user_supplied or public_connector_environment or public_mcp_tool_schemas_are_explicit or canonicalizes_alias or duplicate_and_conflicting_alias or public_connector_lifecycle_contract'
3 failed, 3 passed, 56 deselected, 1 warning in 0.65s
```

After implementation, the same slices passed:

```text
uv run pytest -q tests/test_http_app.py -k 'legacy_connector_setup or product_mutation_requires_supabase'
5 passed, 33 deselected, 1 warning in 0.46s

uv run pytest -q tests/test_product_fallback.py -k 'paginates_past_late_unlink or exact_relink or generic_mcp_user_supplied'
4 passed, 20 deselected in 0.09s

uv run pytest -q tests/test_connector_mcp_tools.py tests/test_connector_catalog.py tests/test_mcp_contract.py -k 'generic_mcp_user_supplied or public_connector_environment or public_mcp_tool_schemas_are_explicit or canonicalizes_alias or duplicate_and_conflicting_alias or public_connector_lifecycle_contract'
6 passed, 56 deselected, 1 warning in 0.62s
```

## Final Verification

```text
uv run pytest -q tests/test_http_app.py tests/test_product_fallback.py tests/test_connector_mcp_tools.py tests/test_connector_catalog.py tests/test_mcp_contract.py
124 passed, 1 warning in 1.10s

uv run pytest -q tests/test_connector_setup.py -k 'connector_profile or start_connector_setup'
7 passed, 19 deselected in 0.14s

uv run ruff check src/mercury_tools/mcp/server.py src/mercury_tools/mcp/schemas.py src/mercury_tools/db/product.py tests/test_http_app.py tests/test_product_fallback.py tests/test_connector_mcp_tools.py tests/test_connector_catalog.py tests/test_mcp_contract.py
All checks passed!

git diff --check
Passed
```

## Concerns

The focused HTTP and MCP tests still emit the existing Starlette TestClient
deprecation warning for the installed `httpx` compatibility layer. No new
concerns found.
