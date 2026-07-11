# Task 7 Report: Generic ERP Connector Drivers

## Scope

Implemented the repository-local connector driver contract, generic auth drivers,
and deterministic driver registry. No provider credentials, live provider services,
or provider-specific driver modules were used.

## RED Evidence

1. Initial contract tests were written before production modules existed.

   ```text
   uv run pytest tests/test_connector_driver_contract.py tests/test_generic_drivers.py -q
   ModuleNotFoundError: No module named 'mercury_tools.drivers.base'
   ModuleNotFoundError: No module named 'mercury_tools.drivers.generic'
   2 errors during collection
   ```

2. The immutable public-summary test was tightened to require JSON serialization.
   Before the registry change, it failed because `mappingproxy` was not JSON serializable.

   ```text
   TypeError: Object of type mappingproxy is not JSON serializable
   1 failed, 8 passed
   ```

## GREEN Evidence

```text
uv run pytest tests/test_connector_driver_contract.py tests/test_generic_drivers.py -q
9 passed in 0.06s

uv run pytest -m "not integration" -q
676 passed, 1 deselected, 1 warning in 1.64s

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The non-integration suite retains one pre-existing Starlette/httpx deprecation warning
from `tests/test_connector_mcp_tools.py`.

## Security Coverage

- Auth values are held only in per-operation `AuthContext` objects.
- Driver summaries, probes, and public exceptions do not expose credential values.
- Unknown environments fail closed.
- OAuth client-credentials token failures expose only stable error codes.
- Response JSON is redacted using action-specific paths plus the shared redactor.
- Body-level error rules can mark an HTTP 200 response as failed.
- Non-JSON response summaries are redacted and limited to 1024 characters.
- Multipart input paths must be declared regular files within active MCP roots after
  symlink resolution.
- Registry summaries are sorted, immutable, and JSON serializable; duplicate and
  unknown connector lookups use stable errors.

## Commits

- `b5dfb5e feat: add generic ERP connector drivers`
