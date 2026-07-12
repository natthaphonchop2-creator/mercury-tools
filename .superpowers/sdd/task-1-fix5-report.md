# Task 1 Fix 5: Remaining Repository Security Hardening

## Scope

Closed the five remaining validated repository-state findings while preserving
the public interfaces, atomic writes, and centralized connector record validation.

## RED

Added regression coverage first, then ran:

```bash
uv run pytest tests/test_local_repository.py -q
```

Result: `17 failed, 65 passed`.

The failures proved that decoded URL path credentials, missing or extra top-level
config fields, coerced schema versions, legacy IPv4 numeric aliases, and later
gitignore negations were still accepted. Safe path and canonical public IPv4
controls passed in the same run.

## GREEN

- Config loading now requires exactly `schema_version`, `trusted_hosts`, and
  `connectors`; `schema_version` must have exact Python type `int` and value `1`.
- Endpoint validation decodes each path segment and rejects existing credential
  prefixes or sensitive credential markers while preserving `/v1`, token endpoint
  paths, and ordinary percent-encoded paths.
- Trusted-host validation rejects single-label decimal and hexadecimal IPv4 aliases
  plus non-canonical dotted numeric forms before DNS-label acceptance.
- Gitignore maintenance removes prior exact managed rules and exact negations, then
  writes the required credentials, cache, and audit rules as the final block. Broad
  Mercury negations and unrelated user lines are preserved; a second call is stable.
- Both configured and loaded connector records continue through
  `_normalize_connector_record`; config and gitignore writes remain atomic.

## Verification

```text
uv run pytest tests/test_local_repository.py -q
82 passed in 0.16s

uv run pytest -m 'not integration' -q
324 passed, 1 deselected, 1 warning in 1.01s

uv run ruff check .
All checks passed!

git diff --check
clean
```

The warning is the existing Starlette `httpx` deprecation warning in
`tests/test_connector_mcp_tools.py` and is unrelated to this change.
