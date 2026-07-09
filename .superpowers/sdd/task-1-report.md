# Task 1 Report: Connector Catalog Module

## Scope
- Implemented connector catalog module extraction and product catalog binding.
- Kept FlowAccount as the first live connector and left PEAK/Express in `setup_target` state.

## Changes
- Added `src/mercury_tools/connectors/catalog.py` with:
  - `ConnectorValidation` and `ConnectorManifest` dataclasses.
  - `CONNECTOR_CATALOG` entries for `flowaccount`, `peak`, and `express`.
  - `connector_by_id()` lookup helper.
  - `list_connector_summaries()` helper that emits serialized connector rows.
- Added `src/mercury_tools/connectors/__init__.py` re-exporting catalog interfaces.
- Updated `src/mercury_tools/db/product.py` to:
  - Use catalog imports instead of local `CONNECTOR_CATALOG` and `connector_by_id` definitions.
  - Resolve connectors via manifest access in profile/credential flows.
  - Return serialized connector summaries when exposing connector lists.
- Added `tests/test_connector_catalog.py` with 3 catalog contract tests.

## Validation
- Expected initial fail state observed:
  - `pytest tests/test_connector_catalog.py -v` -> `ModuleNotFoundError: No module named 'mercury_tools.connectors'` (before module creation).
- Final pass:
  - `pytest tests/test_connector_catalog.py tests/test_product_fallback.py -v` (12 passed).
  - `ruff check src/mercury_tools/connectors/catalog.py src/mercury_tools/connectors/__init__.py src/mercury_tools/db/product.py tests/test_connector_catalog.py tests/test_product_fallback.py` (passed).

## Notes / Self-review
- No functional regression observed in fallback product flows covered by existing tests.
- No additional changes were made outside the requested scope.

## Post-review Fixes
- Addressed reviewer critical issue around canonical connector IDs:
  - `set_connector_profile` and `set_connector_credentials` now resolve `connector_id` via `connector_by_id` once and persist/use the canonical `connector.connector_id` for profile keys, stored metadata, DB payloads, vault keys, and event summaries.
  - This prevents non-canonical inputs like `" FlowAccount "` or `"FLOWACCOUNT"` from creating duplicate connector profiles/keys.
- Addressed reviewer important issue around serialization paths:
  - Replaced all product-response connector list serializations with `list_connector_summaries()` in `product.py`.
  - This removes duplicate/manual serialization paths and keeps connector summary contract centralized.
- Added/updated tests to validate:
  - `connector_by_id` normalizes case/whitespace (`" FlowAccount "`, `"FLOWACCOUNT"`).
  - Audit fallback no longer creates separate connector profile keys for normalized variants (`flowaccount` canonical only) while setting profile/credentials.
