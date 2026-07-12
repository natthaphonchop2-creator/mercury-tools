# Task 4 Fix 5 Report

## RED evidence

Added regression coverage before changing production code, then ran:

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
4 failed, 152 passed
```

The expected failing cases were:

- Invalid percent-decoded UTF-8 exposed a `UnicodeDecodeError` through `__cause__`.
- `/rates/100%25` was rejected after decoding to a literal percent.
- Unrelated mapping keys `rate%` and `rate%25` were rejected as unsafe paths.

The added deep structural-key tests cover four encoding layers directly through
`CatalogSource` and through the OpenAPI importer. They passed before the production
change because the prior bounded decoder already reached four layers; they remain
regression coverage for the required behavior.

## Changed files

- `src/mercury_tools/catalog/identity.py`
  - Decodes only while a valid `%HH` escape remains.
  - Returns literal percent text unchanged after decoding.
  - Uses a UTF-8 decode sentinel so the public `ValueError` is raised outside an
    active exception handler.
  - Restricts decoded mapping-key inspection to structural paths or explicit
    path-field context.
- `tests/test_action_catalog_models.py`
  - Covers exception type/message/chain, literal percent stability, non-path
    mapping keys, and deep encoded structural keys.
- `tests/test_catalog_importers.py`
  - Covers deep encoded structural-key rejection through OpenAPI import.

## Verification

```text
uv run pytest tests/test_action_catalog_models.py tests/test_catalog_importers.py -q
156 passed

uv run pytest -m "not integration" -q
601 passed, 1 deselected, 1 warning

uv run ruff check .
All checks passed!

git diff --check
exit 0
```

The full suite warning is the existing Starlette warning about `httpx` usage in
`tests/test_connector_mcp_tools.py`.

## Commit

`0263f2c808d3913973bb2c20bfe3706058f19a03 fix: safely decode catalog path escapes`

## Residual risk

Percent-decoding is intentionally limited to a finite number of passes based on
the original input length. The test suite covers valid multi-layer encodings,
invalid UTF-8, literal percent output, path context, and importer propagation;
unusual malformed percent text outside explicit path contexts remains ordinary
mapping data by design.
