# Task 3 Sanitizer Fix 2 Report

## Scope

- Starting commit: `c30545b916bc303e43b5a09c382ab6bceb361ce4`
- Production: `src/mercury_tools/catalog/identity.py`
- Regression coverage: `tests/test_action_catalog_models.py`

## Root Cause

The URI sanitizer redacted authority userinfo before calling `urlsplit`. The
`[REDACTED]` marker looks like an invalid bracketed host to the parser, so it
raised `ValueError` and the sanitizer returned with sensitive query values
unchanged. This affected absolute and scheme-relative URIs containing both
userinfo and sensitive query parameters.

## Fix

- Parse original URI-like values before replacing authority userinfo; redact
  query values and userinfo only after parsing.
- Preserve a narrow fallback sanitizer for parser-rejected URI-like strings.
- Redact scalar values inside credential/authentication containers while
  retaining only explicit parameter-name metadata fields.
- Recognize Cookie, Proxy-Authorization, X-Auth-Token, X-Access-Token,
  X-Client-Secret, X-Amz-Security-Token, and related sensitive headers.
- Add ingestion and direct-action tests for absolute, scheme-relative,
  relative, templated, and relative-authority URI-like values.

## Verification

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_stores.py -q
74 passed in 0.11s

uv run pytest -m 'not integration' -q
487 passed, 1 deselected, 1 warning in 1.37s

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The remaining warning is the pre-existing Starlette/httpx deprecation warning
from `tests/test_connector_mcp_tools.py`, outside this task's ownership.
