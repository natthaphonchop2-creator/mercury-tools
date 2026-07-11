# Task 6 Fix Cycle 1 Report

## Scope

- `src/mercury_tools/catalog/search.py`
- `tests/test_catalog_search.py`

## RED Evidence

Added regression coverage before changing production code, then ran:

```text
.venv/bin/python -m pytest -q tests/test_catalog_search.py::test_top_k_one_preserves_ambiguity_from_the_full_ranked_top_two tests/test_catalog_search.py::test_bucket_four_gap_of_exactly_point_zero_five_is_not_ambiguous tests/test_catalog_search.py::test_bucket_four_gap_below_point_zero_five_is_ambiguous tests/test_catalog_search.py::test_top_k_limits_the_returned_match_count
2 failed, 2 passed in 0.06s
```

The two failures demonstrated the requested defects:

- `top_k=1` discarded the second ranked candidate before ambiguity was evaluated.
- Float subtraction interpreted the mathematical `17/20 - 16/20 == 0.05`
  boundary as below `0.05`.

The below-threshold and output-length tests passed before the implementation
change because those already-described behaviors were present; they remain
regression coverage for the complete contract.

## Implementation

- Evaluate ambiguity from the full ranked list before truncating returned
  `matches` to `top_k`.
- Keep token-overlap data as internal `Fraction` values and compare the bucket
  four gap against exact `Fraction(1, 20)`.
- Convert overlap values to `float` only when assigning public
  `CatalogMatch.score`, preserving the existing public score type.

## GREEN Evidence

Focused tests:

```text
.venv/bin/python -m pytest -q tests/test_catalog_search.py
27 passed in 0.03s
```

Full non-integration suite, lint, and whitespace check:

```text
.venv/bin/python -m pytest -q -m "not integration"
667 passed, 1 deselected, 1 warning in 2.48s

.venv/bin/python -m ruff check .
All checks passed!

git diff --check
passed
```

The warning is the pre-existing Starlette `httpx` TestClient deprecation
warning from `tests/test_connector_mcp_tools.py`.

## Commit

`fec3b5e` - `fix: preserve catalog search ambiguity`
