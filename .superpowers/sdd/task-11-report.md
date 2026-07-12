# Task 11 Report: Network Boundary and Generic ERP Executor

## Status

DONE_WITH_CONCERNS

## Changed Files

- `src/mercury_tools/safety/network.py`
- `src/mercury_tools/safety/__init__.py`
- `src/mercury_tools/execution/request_builder.py`
- `src/mercury_tools/execution/executor.py`
- `src/mercury_tools/execution/store.py`
- `src/mercury_tools/execution/__init__.py`
- `src/mercury_tools/local/audit.py`
- `tests/test_network_policy.py`
- `tests/test_erp_executor.py`

## RED Evidence

1. `uv run pytest tests/test_erp_executor.py tests/test_network_policy.py -q`
   failed during collection because `mercury_tools.execution.executor` did not
   exist.
2. `uv run pytest tests/test_network_policy.py -q` produced `8 failed`; the
   runtime network policy had no base/request validation APIs and no explicit
   private-network boundary.
3. The first executor green attempt produced `6 failed, 17 passed`; immutable
   request input reconstruction could not thaw the stored request body.
4. Adversarial idempotency, peer-rebinding, and duplicate tests produced
   `3 failed, 17 passed`; the builder did not derive an idempotency header,
   response-peer mismatch was classified as a definitive failure, and duplicate
   preflight returned only a generic failure.
5. Audit fail-closed and encoded-base-path tests produced `2 failed, 21 passed`;
   a dispatch audit failure left the request executing and encoded traversal in
   a base path was accepted.
6. Dynamic-summary, preflight-audit, and confirmation-audit tests produced
   `3 failed, 25 passed`; dynamic body keys were model-visible, preflight calls
   lacked evidence, and an unaudited confirmation remained executable.
7. Auth-time action-version and target changes produced `2 failed`; neither
   binding was rechecked at the final pre-dispatch linearization point.
8. A preflight transport failure test produced `1 failed`; the mutation was
   blocked, but the failed cataloged preflight call had no audit row.

## Delivered

- Fresh DNS resolution for every configured base URL and every API/token
  request, exact trusted-host checks, metadata/link-local blocking, explicit
  local/gateway private-network handling, no redirects, and response-peer
  verification against the just-resolved address set.
- Catalog/schema-bound request construction for path, query, header, body, and
  file inputs. Path traversal, auth overrides, undeclared fields, unsafe base
  paths, and files outside active MCP roots fail before credential access.
- Immutable canonical bindings include repository, connector, environment,
  action/version, method/path, query/header/body, target, effective risk, and
  descriptor-pinned relative file hashes without credentials or absolute file
  paths.
- Catalog-derived idempotency headers and read-only duplicate preflights.
- Read execution plus preview, confirmation, dispatch, status, and
  outcome-unknown resolution for writes. Credentials are loaded only after
  catalog, schema, target, network, and confirmation checks.
- Mutations are sent once. Auth/connect failure before dispatch is definitive;
  timeout, disconnect, 5xx, and post-response peer mismatch become
  `outcome_unknown` and block replay until a cataloged status GET resolves them.
- Active action version and connector target are rechecked immediately before
  the request-store transition to executing.
- Audit failures invalidate previews or fail closed before network dispatch;
  failed preflight calls retain evidence, and model-visible request summaries
  expose only aggregate shape, not dynamic business keys or values.

## GREEN Evidence

- Focused executor, network, state, driver, importer, and audit suite:
  `uv run pytest tests/test_network_policy.py tests/test_erp_executor.py tests/test_request_store.py tests/test_generic_drivers.py tests/test_flowaccount_driver.py tests/test_peak_driver.py tests/test_catalog_importers.py tests/test_local_audit.py -q`
  -> `406 passed in 3.53s`.
- Full non-integration suite:
  `uv run pytest -q -m 'not integration'`
  -> `1087 passed, 1 deselected, 1 warning in 6.64s`.
- `uv run ruff check .` -> `All checks passed!`.
- `git diff --check` -> passed with no output.

## Concern

- The full suite retains the pre-existing Starlette `httpx` deprecation warning
  in `tests/test_connector_mcp_tools.py`; Task 11 adds no warning or failure.
