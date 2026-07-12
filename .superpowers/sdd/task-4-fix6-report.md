# Task 4 Fix 6 Report

## RED evidence

Added the model regression cases before changing production code for encoded
field names resolving to `path`, multi-layer `path`, `path_template`,
`endpoint`, `route`, and `url` with nested `raw`. Each value contains a
credential prefix or assignment. Added a multi-layer encoded `path` field-name
case through the OpenAPI importer.

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
7 failed, 156 passed
```

The six direct model failures did not raise `ValidationError`; the importer
failure did not raise `ValueError`. This demonstrated that percent-encoded
field names bypassed path classification.

## Implementation

- Added permissive bounded decoding for mapping-key classification. Invalid
  UTF-8 returns the raw key and does not raise during classification.
- Uses the decoded field name only for path-field classification and the
  decoded `url` parent context for nested `raw` values.
- Treats `endpoint` and `route` as path value fields.
- Leaves structural path-key validation on its existing raw-key flow.

## Verification

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
163 passed

uv run pytest -m "not integration" -q
608 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The full suite warning is the existing Starlette deprecation warning in
`tests/test_connector_mcp_tools.py`.

## Commit

`34e74fc8828b9dc466083e600ed3df6c518e0239 fix: classify encoded catalog path fields`

## Residual risk

Classification decoding is intentionally bounded by the original key length
and returns raw field names on malformed escapes or invalid UTF-8. This avoids
rejecting unrelated metadata while retaining the existing stricter handling
for values already classified as paths.
