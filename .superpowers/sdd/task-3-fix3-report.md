# Task 3 Sanitizer Fix 3 Report

## Scope

- Starting HEAD: `77440dd8fc1bbb07b1668bfa0e381d930db398ed`
- Production: `src/mercury_tools/catalog/identity.py`
- Regression coverage: `tests/test_action_catalog_models.py`

## RED

Added parser-rejected URI regressions using an invalid bracketed authority,
percent-encoded `access_token` key, repeated query separators, and a fragment.

```text
uv run pytest -q tests/test_action_catalog_models.py -k 'parser_rejected_uri'
2 failed, 55 deselected
```

The failures showed source ingestion retained the raw token and direct
`CatalogAction` validation accepted it.

## Fix

- Added a malformed-URI fallback that separates fragment and query without URI
  parsing, preserves query ordering and `&`/`;` separators, and classifies keys
  only after percent-decoding them.
- Replaced sensitive query values with `[REDACTED]` while preserving non-sensitive
  components and the original fragment.
- Applied the existing userinfo fallback after reconstructing the URI-like string.
- Treated a percent-encoded existing redaction marker as already sanitized so
  repeated validation remains idempotent and existing valid-URI behavior holds.
- Added source-ingestion and direct-action rejection tests; the latter confirms
  validation errors do not echo the raw value.

## Verification

```text
uv run pytest -q tests/test_action_catalog_models.py -k 'parser_rejected_uri'
2 passed, 55 deselected

uv run pytest -q tests/test_action_catalog_models.py tests/test_catalog_stores.py
76 passed in 0.14s

uv run pytest -m 'not integration' -q
489 passed, 1 deselected, 1 warning in 3.00s

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The only suite warning is the pre-existing Starlette/httpx deprecation warning
from `tests/test_connector_mcp_tools.py`, outside this task's ownership.
