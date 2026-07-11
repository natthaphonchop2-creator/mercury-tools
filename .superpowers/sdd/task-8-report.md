# Task 8 Report: FlowAccount and PEAK Connector Drivers

## Scope

Ported the FlowAccount and PEAK setup authentication and read-only healthcheck
behavior into provider-specific async drivers. The synchronous setup function
now delegates to the async path only when no event loop is active. The existing
private FlowAccount journal client was not changed.

## RED Evidence

Provider contract tests were added before the provider modules existed:

```text
uv run pytest tests/test_flowaccount_driver.py tests/test_peak_driver.py -q
ModuleNotFoundError: No module named 'mercury_tools.drivers.flowaccount'
ModuleNotFoundError: No module named 'mercury_tools.drivers.peak'
```

The repository-config validation test was then added before its guard:

```text
uv run pytest \
  tests/test_flowaccount_driver.py::test_registry_for_repository_rejects_untrusted_mixed_and_malformed_records -q
1 failed, 3 passed
Failed: DID NOT RAISE DriverConfigurationError
```

## Implementation

- Added `FlowAccountDriver` for exact production `/v1/token` and sandbox
  `/test/token` OAuth client-credentials flows, with fixed grant and scope,
  expiry handling, company-info probes, provider body failure handling, and
  credential-aware company-name redaction.
- Added `PeakDriver` for HMAC-SHA1 ClientToken authentication, all manifest
  environments, required application/user/client-token headers, `GET /user`
  probes, and `resCode == "200"` response semantics.
- Added `DriverRegistry.for_repository(config)` with lazy provider imports,
  built-in FlowAccount and PEAK drivers, five generic factories, trusted-host
  enforcement, and stable rejection of malformed or mixed repository records.
- Replaced provider logic in `connectors/setup.py` with async driver delegation.
  The sync wrapper raises `connector_healthcheck_async_required` rather than
  nesting `asyncio.run` inside an active event loop.
- Migrated setup and MCP regression seams to `httpx.MockTransport`; no provider
  test uses live credentials or a provider network call.

## Security Coverage

- Credential bundles are exact; unknown, missing, and blank fields fail closed.
- Credentials and provider tokens are not retained on drivers or exposed in
  reprs, error codes, probes, compatibility output, or public result data.
- Literal and reversibly URL-encoded values are redacted from FlowAccount company
  display names. Provider response bodies are not returned by setup validation.
- Registry summaries remain immutable and use an explicit JSON boundary.
- `build_generic_registry()` retains provider-import isolation; provider modules
  are loaded only by `for_repository()`.

## Verification

```text
uv run pytest tests/test_flowaccount_driver.py tests/test_peak_driver.py \
  tests/test_connector_setup.py tests/test_flowaccount_journal_client.py -q
56 passed

uv run pytest -m "not integration" -q
805 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The remaining warning is the pre-existing Starlette/httpx deprecation warning
from `tests/test_connector_mcp_tools.py`.

## Commits

- `8f55ea9 feat: port FlowAccount and PEAK connector drivers`
