# Task 4 Fix 7 Report

## RED evidence

Added direct model and importer regressions before changing production code:

- A malformed mixed-escape field name, `p%61th%`, with a credential-looking
  path value must remain ordinary metadata and retain its value.
- A multi-layer encoded `url` field with direct `raw: /safe` and nested
  metadata keys beginning with `ghp_` or containing `client_secret` text must
  be accepted.
- A multi-layer encoded `url` field with direct credential-bearing `raw` must
  still be rejected.

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
4 failed, 164 passed
```

The malformed field was partially decoded to `path%` and classified as `path`.
The encoded URL cases propagated path context to nested metadata and rejected
the `ghp_documentation_field` key.

## Implementation

- Field-name decoding now returns the original raw key if any decoding round
  contains an incomplete or non-hex percent escape. Invalid UTF-8 continues to
  return raw without exposing an exception chain.
- Removed recursive path-context propagation. Structural mapping keys that
  decode to leading `/` remain independently checked, as do direct path-field
  values and direct `raw` children of decoded `url` fields.

## Verification

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
168 passed

uv run pytest -m "not integration" -q
613 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The full suite warning is the existing Starlette deprecation warning in
`tests/test_connector_mcp_tools.py`.

## Commit

`b3ade1ba83e810d26f5d1fee44a370b751fb77d5 fix: constrain catalog path field classification`

## Residual risk

Field-name classification intentionally fails neutral when a malformed percent
escape appears in any decoding round. This avoids treating malformed metadata
as a path field, while separately decoded structural keys and direct path
values continue to receive strict credential-path validation.
