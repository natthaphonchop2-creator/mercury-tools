# Task 3 Fix 5 Report: FastMCP Evidence Validation Boundary

## Status

Implemented and verified.

## Delivered

- `validate_connector_connection` now exposes `ConnectorValidationEvidence`'s
  complete strict JSON Schema while using Pydantic `SkipValidation` at the
  FastMCP argument boundary. The handler continues to validate the raw value
  with `ConnectorValidationEvidence.model_validate`, so its fixed sanitized
  `ValidationError` response and audit path handle invalid evidence.
- The lifecycle schema test now compares the real generated MCP evidence schema
  and nested capability schema against `ConnectorValidationEvidence`.
- A real async `mcp.call_tool` regression submits an extra `provider_body`
  marker and verifies the fixed safe result plus absence of the marker and
  Pydantic `input_value` in the MCP output and captured audit event.
- The valid direct Python-call regression now passes a typed
  `ConnectorValidationEvidence` instance.

## Verification

```text
uv run pytest -q tests/test_connector_mcp_tools.py tests/test_mcp_contract.py -k 'hides_typed_evidence_input or records_host_observed_evidence or public_connector_lifecycle_contract_is_exact_and_secretless or sanitizes_invalid_evidence_through_fastmcp'
4 passed, 53 deselected, 1 warning in 0.55s

uv run ruff check src/mercury_tools/mcp/schemas.py src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py tests/test_mcp_contract.py
All checks passed!

git diff --check
Passed
```

## Concerns

The focused test run retains the existing Starlette TestClient deprecation
warning for the installed `httpx` compatibility layer. No new concerns found.
