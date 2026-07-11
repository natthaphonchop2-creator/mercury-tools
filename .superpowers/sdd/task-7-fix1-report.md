# Task 7 Fix 1 Report: Generic ERP Driver Hardening

## Scope

Hardened the Task 7 generic connector driver contract without changing the
existing `register(driver)` or `get(connector_id)` registry path. The code and
tests are recorded separately in `214f592 fix: harden generic ERP connector drivers`.

## RED Evidence

The focused Task 7 suite was run after adding adversarial tests and before the
implementation changes:

```text
uv run pytest tests/test_connector_driver_contract.py tests/test_generic_drivers.py -q
18 failed, 7 passed
```

The failures covered AuthContext credential-bearing repr output, shallow public
model freezing, fake `summary` environments, unchecked credential bundles,
probe credential echoes, plaintext body exposure, JSON `null` handling,
terminal wildcard redaction, multipart type/root/symlink handling, and the
missing factory lookup/create API.

The direct list-terminal wildcard regression was also verified against the old
branch before restoring the fix:

```text
uv run pytest tests/test_generic_drivers.py::test_terminal_wildcard_redaction_replaces_every_child_value -q
1 failed
```

The OAuth environment-completeness case was added independently and verified
RED before its implementation:

```text
uv run pytest tests/test_generic_drivers.py::test_oauth_credential_fields_require_a_token_url_for_the_environment -q
1 failed
```

## GREEN Evidence

```text
uv run pytest tests/test_connector_driver_contract.py tests/test_generic_drivers.py -q
26 passed

uv run pytest tests/test_local_credentials.py -q
53 passed

uv run pytest -m "not integration" -q
693 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The remaining warning is the pre-existing Starlette/httpx deprecation warning
from `tests/test_connector_mcp_tools.py`.

## Finding Coverage

1. `AuthContext.__repr__` now exposes header/query names and expiry metadata,
   never their values.
2. Generic probes replace exact submitted credential echoes in company names
   and return only bounded probe metadata; plaintext response summaries are the
   fixed descriptor `plaintext_response`.
3. Terminal wildcard redaction replaces every mapping or sequence child.
4. `ConnectionProbe.details` and `ConnectorResult.data` are recursively frozen:
   mappings are immutable JSON-serializable dictionaries and sequences are tuples.
5. Credential schemas validate configured environments. Exact bundles reject
   missing, blank, undeclared, and invalid values with stable safe codes.
6. The built-in registry now stores explicit generic driver factories and
   credential schemas. `get_factory()` and `create()` produce configured drivers
   without empty-environment placeholders or provider-module imports.
7. Multipart validation accepts only `multipart/form-data` with optional
   parameters, requires existing directory roots, and normalizes symlink-loop
   failures to stable path-free configuration errors.
8. A decode-failure sentinel preserves the distinction between valid JSON `null`
   and malformed plaintext responses.
