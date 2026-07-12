# Task 7 Fix Cycle 2 Report: Generic Driver Boundary Validation

## Scope

Recorded the second Task 7 fix cycle separately from the implementation
commit `4b57051 fix: validate generic driver boundaries`. The implementation
changes are limited to `src/mercury_tools/drivers/` and the two Task 7 test
modules. `progress.md` was not changed.

## RED Evidence

Each reviewer finding was encoded as a failing test before the corresponding
production change:

```text
uv run pytest \
  tests/test_generic_drivers.py::test_probe_replaces_exact_credential_echoes_and_omits_provider_details \
  tests/test_generic_drivers.py::test_probe_fully_redacts_literal_and_reversibly_encoded_credential_echoes \
  tests/test_generic_drivers.py::test_probe_fully_redacts_reversibly_encoded_auth_value_echoes -q
8 failed
```

The failures showed partial literal redaction and unredacted percent-encoded,
`quote_plus`, mixed-case percent, double-encoded, and encoded Basic auth
echoes in `company_name`.

```text
uv run pytest \
  tests/test_generic_drivers.py::test_direct_driver_constructor_rejects_invalid_environment_maps_without_echoing_urls \
  tests/test_generic_drivers.py::test_factory_rejects_invalid_environment_maps_at_construction \
  tests/test_generic_drivers.py::test_oauth_constructor_validates_token_urls_and_requires_exact_environment_sets \
  tests/test_generic_drivers.py::test_api_key_factory_does_not_treat_an_explicit_blank_key_name_as_default -q
23 failed, 1 passed
```

```text
uv run pytest \
  tests/test_generic_drivers.py::test_probe_url_construction_errors_are_returned_as_safe_probe_failures \
  tests/test_generic_drivers.py::test_oauth_token_url_construction_errors_are_returned_as_safe_auth_failures -q
2 failed
```

These failures demonstrated deferred configuration validation, unsafe factory
fallback behavior, missing OAuth environment-set validation, and uncaught URL
construction/request errors.

```text
uv run pytest \
  tests/test_connector_driver_contract.py::test_connector_driver_protocol_does_not_require_credential_schema \
  tests/test_connector_driver_contract.py::test_registry_summaries_accept_a_driver_with_only_the_planned_protocol \
  tests/test_connector_driver_contract.py::test_registry_distinguishes_factory_recipes_from_connector_entries_with_same_name -q
3 failed
```

```text
uv run pytest \
  tests/test_connector_driver_contract.py::test_public_driver_response_models_reject_non_json_values_with_safe_stable_errors \
  tests/test_connector_driver_contract.py::test_public_driver_response_models_accept_strict_json_data -q
9 failed, 6 passed
```

The contract failures covered a required `credential_schema`, fake summary
metadata assumptions, ambiguous factory/connector records, non-string mapping
keys, non-finite floats, bytes-like values, sets, and custom objects.

## GREEN Evidence

After the implementation and final formatting pass:

```text
uv run pytest tests/test_connector_driver_contract.py tests/test_generic_drivers.py -q
76 passed

uv run pytest tests/test_local_credentials.py -q
53 passed

uv run pytest -m "not integration" -q
743 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The remaining warning is the pre-existing Starlette/httpx deprecation warning
from `tests/test_connector_mcp_tools.py`.

## Finding Coverage

1. Probe company names now return `[REDACTED]` in full whenever a literal,
   URL-decoded, or `quote_plus`-decoded credential or auth value is present
   within two reversible decoding rounds.
2. Direct drivers and factory-created drivers validate non-empty environment
   maps and safe absolute `http`/`https` URLs at construction. Userinfo,
   fragments, missing hosts, and malformed ports return stable configuration
   codes without echoing the input URL. OAuth token URLs use the same checks
   and must have the exact same environment keys as base URLs.
3. Probe and OAuth token dispatch convert URL construction/request failures
   into safe `probe_request_failed` and `oauth_token_failed` results.
4. `ConnectorDriver` no longer requires `credential_schema`; registry summaries
   use optional safe schema metadata without fake environment calls.
5. Summary records are explicitly typed as `connector` or `factory`.
   Factories omit `connector_id`; `get()` remains connector-only and
   `get_factory()`/`create()` remain recipe-only, including when names match.
6. `ConnectionProbe.details` and `ConnectorResult.data` now accept only
   recursively immutable, strict JSON-compatible values: string-key mappings,
   lists/tuples, JSON scalars, and finite floats. Unsupported values raise the
   stable `public_data_invalid` code.
7. Prior Task 7 safeguards remain covered by the focused suite, including
   safe `AuthContext` repr, exact credential bundles, terminal wildcard
   redaction, plaintext descriptors, multipart controls, JSON `null`, and
   provider import isolation.
