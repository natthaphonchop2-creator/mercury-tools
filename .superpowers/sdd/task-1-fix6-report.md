# Task 1 Fix 6 Report

## Scope

- `src/mercury_tools/local/repository.py`
- `tests/test_local_repository.py`

## RED Evidence

Initial focused run:

```text
uv run pytest tests/test_local_repository.py -q
7 failed, 82 passed in 0.33s
```

The failures proved these missing boundaries:

- `.mercury/.gitignore` left local negations effective.
- Atomic replacement reset existing file modes.
- Dotted hexadecimal and mixed legacy IPv4 aliases were accepted.
- Invalid JSON and duplicate object members did not consistently return the fail-closed invalid-config error.

Additional invalid UTF-8 regression before its implementation change:

```text
uv run pytest tests/test_local_repository.py::test_load_repository_config_rejects_invalid_utf8_without_echoing_bytes -q
1 failed in 0.11s
```

## GREEN Evidence

```text
uv run pytest tests/test_local_repository.py -q
90 passed in 0.33s

uv run pytest -m 'not integration' -q
332 passed, 1 deselected, 1 warning in 1.47s

uv run ruff check src/mercury_tools/local/repository.py tests/test_local_repository.py
All checks passed!

git diff --check
exit 0
```

The suite warning is an existing Starlette `httpx` deprecation warning from
`tests/test_connector_mcp_tools.py`; it is outside this task's ownership.

## Delivered Boundaries

- Config decoding rejects duplicate JSON keys at every object level and converts
  JSON syntax, duplicate-key, and UTF-8 decoding failures to
  `ValueError("invalid_repository_config")` without exposing config values.
- Canonical IPv4 addresses remain accepted before legacy fallback. Single-value,
  dotted decimal, dotted hexadecimal, and mixed legacy numeric aliases are rejected.
- Root and nested `.mercury/.gitignore` rules are both idempotently managed, with
  final credential/cache/audit ignores taking precedence over exact local negations.
- POSIX atomic replacement preserves an existing file mode. Newly created
  `.gitignore` files use mode `0644`; `.mercury` remains enforced as `0700`.

## Residual Concern

The legacy alias boundary intentionally rejects all-dotted numeric or `0x`
component hostnames after canonical IP parsing. This conservative policy avoids
resolver-dependent IPv4 interpretation at the cost of disallowing unusual
numeric-label DNS names.
