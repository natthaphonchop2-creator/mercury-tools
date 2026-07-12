# Task 1 Fix 4: Central Connector Record Validation

## Scope

Centralized connector record validation so `configure_connector` and
`load_repository_config` enforce the same secret-free persistence boundary.

## RED

Before the implementation, ran:

```bash
uv run pytest tests/test_local_repository.py -q
```

Result: `17 failed, 43 passed`.

The failures demonstrated that configure accepted credential-like OAuth scopes
and connector identifiers, while load accepted hand-edited connector records
with unsafe URLs, unsupported auth metadata, invalid grant types, unknown
record keys, and missing or extra trusted hosts.

## GREEN

Implemented a pure `_normalize_connector_record` path used by both configure
and load. It validates identifiers, record keys, network policy, URLs, and
auth metadata; computes the base and optional token host set; and returns the
normalized persisted record.

Load now validates the connector and trusted-host maps together. Each stored
record must have exactly the host set computed from its base URL and optional
token URL, and all connector/environment pairs must be represented on both
sides. Unknown record, auth-setting, and network-policy keys are rejected.

OAuth scope validation now checks each scope token with
`_looks_like_credential_material`, in addition to the existing syntax and
sensitive-marker checks. Connector IDs, environments, and driver IDs use the
same bounded identifier validation and reject credential-like values.

Valid configure-generated records remain accepted, including the FlowAccount
`flowaccount-api` scope, `client_credentials` grant type, parameter names, and
local or gateway private-network opt-in.

## Verification

```text
uv run pytest tests/test_local_repository.py -q
60 passed in 0.12s

uv run pytest -m 'not integration' -q
302 passed, 1 deselected, 1 warning in 0.96s

uv run ruff check .
All checks passed!
```

The sole test-suite warning is an existing Starlette deprecation warning in
`tests/test_connector_mcp_tools.py`; it is unrelated to this change.
