# Task 7 Fix Cycle 3 Report: URL Closure and Immutable Public Mappings

## Scope

This fix cycle is recorded separately from implementation commit
`6fa6926 fix: close driver URL and immutability gaps`. The implementation is
limited to `src/mercury_tools/drivers/` and the two Task 7 test modules.
`progress.md` was not changed.

## RED Evidence

The regression tests were written before the production changes and then run
against the prior implementation:

```text
.venv/bin/pytest -q tests/test_connector_driver_contract.py tests/test_generic_drivers.py
33 failed, 79 passed
```

The failures showed that direct constructors and factories accepted URLs with
embedded ASCII whitespace, backslashes, malformed percent escapes, and a
trailing empty port. They also showed that `FrozenDict` remained a `dict`, so
the public-mapping contract could still be bypassed with unbound `dict`
methods. The prior models had neither `to_jsonable` nor the required explicit
`public_dict()` serialization boundaries.

## GREEN Evidence

After the implementation and final formatting pass:

```text
.venv/bin/pytest -q tests/test_connector_driver_contract.py tests/test_generic_drivers.py
112 passed

.venv/bin/pytest -q tests/test_local_credentials.py
53 passed

.venv/bin/pytest -m "not integration" -q
779 passed, 1 deselected, 1 warning

.venv/bin/ruff check src/mercury_tools/drivers tests/test_connector_driver_contract.py tests/test_generic_drivers.py
All checks passed!

git diff --check
exit 0
```

The one full-suite warning is the pre-existing Starlette/httpx deprecation
warning from `tests/test_connector_mcp_tools.py`.

## Finding Coverage

1. Generic driver direct constructors and registry factories now fail closed
   with the stable `driver_url_invalid` code for malformed URL text, including
   ASCII whitespace/control characters, backslashes, malformed percent escapes,
   empty explicit ports, malformed authority/path syntax, unsupported schemes,
   missing hosts, userinfo, fragments, and invalid ports. The validation keeps
   valid HTTP local/gateway URLs, HTTPS URLs, IPv6 hosts with valid ports, and
   valid percent escapes. OAuth token URLs use the same validation path.
2. Public mappings now use `MappingProxyType` over an unreferenced,
   recursively frozen copy rather than a `dict` subclass. Tests prove that
   `dict.__setitem__`, `dict.update`, `dict.__init__`, and nested assignment
   cannot mutate these values or alter later serialized output. `to_jsonable`,
   `ConnectionProbe.public_dict()`, `ConnectorResult.public_dict()`, and
   `DriverRegistry.public_summaries()` provide the supported JSON boundary;
   `AuthContext` intentionally has no public serialization API. This preserves
   the Python tradeoff explicitly: a strictly immutable `Mapping` cannot be
   passed directly to stdlib `json.dumps`, so callers must serialize the plain
   data returned by the explicit boundary instead of weakening immutability.
