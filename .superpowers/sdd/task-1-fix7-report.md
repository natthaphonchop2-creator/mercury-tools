# Task 1 Fix 7 Report

## Scope

- Block known cloud metadata IPs and hostnames before private-network policy.
- Preserve valid HTTP private-network acceptance for both `local` and `gateway`.

## RED Evidence

Focused command before implementation:

```text
uv run pytest tests/test_local_repository.py -k 'internet_urls_require_https_unless_private_network_is_enabled or metadata_targets_even_with_private_network_allowed'
8 failed, 6 passed, 89 deselected
```

The failures included `fd00:ec2::254`, `100.100.100.200`,
`metadata.goog`, and `instance-data.ec2.internal`; `169.254.169.254` and
`metadata.google.internal` remained covered by the existing deny behavior.

## Fix

- Added explicit normalized `_FORBIDDEN_METADATA_IPS` and
  `_FORBIDDEN_METADATA_HOSTNAMES` sets for all required targets.
- Kept generic IP link-local blocking and avoided DNS resolution.
- Parameterized the valid private-network acceptance test over `local` and
  `gateway`.

## GREEN Evidence

```text
uv run pytest tests/test_local_repository.py -k 'internet_urls_require_https_unless_private_network_is_enabled or metadata_targets_even_with_private_network_allowed'
14 passed, 89 deselected

uv run pytest -m 'not integration'
345 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
clean
```

## Concern

The full suite retains the existing `StarletteDeprecationWarning` from
`tests/test_connector_mcp_tools.py`; it is unrelated to this change.

Commit: see the final git commit for this report's immutable commit identity.
