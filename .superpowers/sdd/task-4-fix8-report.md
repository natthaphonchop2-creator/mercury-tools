# Task 4 Fix 8 Report

## RED evidence

Added model and importer regressions before changing production code. Both use
a multi-layer encoded `%2575rl` field with a list item containing a
credential-bearing `raw` endpoint path. The rejection errors must remain
constant and must not echo the secret.

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
2 failed, 168 passed
```

The list traversal dropped the decoded `url` parent key. Consequently, the
`raw` field in each list item was treated as ordinary metadata instead of an
endpoint path.

## Implementation

The sequence branch of `_inspect_credential_paths` now forwards its existing
`parent_key` to each item. No recursive path-context propagation was added.
Nested mappings still replace their parent key with their own decoded key, so
metadata remains ordinary metadata.

Acceptance coverage in both model and importer tests proves a list item under
encoded `url` accepts `raw: /safe` and retains nested
`ghp_documentation_field` and `client_secret_like_text` metadata.

## Verification

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
170 passed

uv run pytest -m "not integration" -q
615 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The full-suite warning is the existing Starlette deprecation warning from
`tests/test_connector_mcp_tools.py`.

## Commit

Recorded after the verification commands in this report.

## Residual risk

List items intentionally inherit only the immediate parent field name. A
nested mapping below a list item receives its own key as parent, preserving the
direct-`raw`-under-`url` rule without classifying deeper metadata as a path.
