# Task 3 Fix 4 Report: Sanitized Evidence and Generic MCP Discovery

## Status

Implemented and verified.

## Delivered

- `validate_connector_connection` now catches Pydantic `ValidationError` before
  general value errors, returns the fixed message `Connector validation evidence
  is invalid.`, and audits that fixed output only. A regression submits a
  forbidden `provider_body` marker and proves it is absent from both result and
  captured audit data.
- Generic MCP `discovered_tools` evidence now preserves exact host-observed
  capability names without parsing segments such as `delete` as a grant or deny
  rule. It remains evidence-only and reports
  `provider_called_by_mercury=false`.
- Product-store validation and status now allow Generic MCP discovered evidence
  with mutation-looking names to become `ready_read_only`; fixed-catalog modes
  remain catalog-bound and reject an unknown capability.

## Verification

```text
uv run pytest -q tests/test_connector_mcp_tools.py tests/test_product_fallback.py tests/test_mcp_contract.py -k 'typed_evidence_input or mutation_named_discovered or unknown_fixed_catalog or fixed_catalog_rejects or generic_mcp_user_supplied or canonicalizes_alias or duplicate_and_conflicting_alias or public_connector_lifecycle_contract'
8 passed, 73 deselected, 1 warning in 0.57s

uv run pytest -q tests/test_connector_mcp_tools.py tests/test_product_fallback.py tests/test_mcp_contract.py
81 passed, 1 warning in 0.80s

uv run ruff check src/mercury_tools/mcp/server.py src/mercury_tools/db/product.py tests/test_connector_mcp_tools.py tests/test_product_fallback.py tests/test_mcp_contract.py
All checks passed!

git diff --check
Passed
```

## Concerns

The focused tests retain the existing Starlette TestClient deprecation warning
for the installed `httpx` compatibility layer. No new concerns found.
