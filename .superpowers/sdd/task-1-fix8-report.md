# Task 1 Fix 8 RED/GREEN Report

## RED

Command:

```text
uv run pytest tests/test_local_repository.py -k 'expanded_metadata_ipv6 or ipv4_mapped_metadata'
```

Result: 8 failed, 4 passed, 103 deselected. The failures were the expected
`DID NOT RAISE ValueError` results for expanded `fd00:ec2::254` spellings and
IPv4-mapped forms of the forbidden `100.100.100.200` endpoint.

## GREEN

Command:

```text
uv run pytest tests/test_local_repository.py -k 'expanded_metadata_ipv6 or ipv4_mapped_metadata'
```

Result: 12 passed, 103 deselected.

## Verification

- `uv run pytest tests/test_local_repository.py`: 115 passed.
- `uv run pytest -m 'not integration'`: 357 passed, 1 deselected.
- `uv run ruff check .`: passed.
- `git diff --check`: passed.

## Scope

Forbidden metadata IPs are stored as `ipaddress` objects. Candidate IPs are
parsed before comparison, and IPv4-mapped IPv6 candidates are also checked via
their mapped IPv4 address. Metadata remains forbidden with private-network
access enabled for both `local` and `gateway` environments.

Concern: the non-integration suite emits one existing Starlette/httpx
deprecation warning from `tests/test_connector_mcp_tools.py`.
