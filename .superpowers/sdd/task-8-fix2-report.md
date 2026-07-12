# Task 8 Fix Cycle 2 Report: Fail-Closed Connector Edges

## Scope

This cycle hardens the Task 8 repository configuration, generic OAuth,
FlowAccount, and PEAK driver boundaries. `progress.md` and the private
FlowAccount journal client were not changed.

## RED Evidence

New regression coverage was written before the implementation. The initial
focused run against the prior implementation produced:

```text
45 failed, 22 passed, 247 deselected
```

The failures showed that repository normalization accepted malformed percent
escapes, trailing authority colons, whitespace, backslashes, and control
characters before provider construction; generic OAuth accepted colliding form
names and credential-equivalent access tokens; non-finite expiry values could
raise; FlowAccount accepted structured provider codes; and PEAK traversal
failed open at its depth and node bounds.

An additional top-level PEAK provider-row regression was added during review:

```text
1 failed, 1 passed
```

It proved that a root `resCode=200` response still descended into normal rows,
which would incorrectly fail a large successful provider payload.

## Implementation

- Repository endpoint validation now rejects unsafe raw URL syntax with the
  stable `invalid_endpoint_url` error before registry provider construction.
  The same checks apply to base and OAuth token endpoints.
- Generic OAuth rejects case-insensitive collisions between client field names,
  `grant_type`, and `scope` at direct-constructor, factory, and repository
  registry paths. It also rejects credential-equivalent issued access tokens
  after reversible URL decoding.
- Generic and FlowAccount now ignore non-finite or out-of-range `expires_in`
  values instead of raising or constructing unsafe datetimes.
- FlowAccount fails closed for every structured or nonnumeric provider
  `code`/`resCode`, while retaining success for `None`, `False`, numeric zero,
  and string zero.
- PEAK reports failure whenever bounded response traversal is truncated. It
  stops beneath a nested result-code node so large successful result rows do
  not consume the traversal budget. A root response envelope inspects only
  direct code-bearing provider nodes, preserving the prior nested-failure
  behavior without treating normal rows as provider responses.

## Verification

```text
uv run pytest -q \
  tests/test_flowaccount_driver.py tests/test_peak_driver.py \
  tests/test_connector_setup.py tests/test_flowaccount_journal_client.py \
  tests/test_generic_drivers.py tests/test_connector_driver_contract.py \
  tests/test_local_repository.py
380 passed

uv run pytest -m "not integration" -q
899 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The full suite warning is the existing Starlette/httpx deprecation warning in
`tests/test_connector_mcp_tools.py`.

## Commits

- `b8afa87 fix: fail closed on connector edge cases` (source and tests)
