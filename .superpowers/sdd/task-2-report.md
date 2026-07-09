# Task 2 Report: Gated Connector Setup State Machine

## Scope
- Added connector setup state primitives in `src/mercury_tools/connectors/setup.py`.
- Added connector setup helper in `SupabaseProductStore.start_connector_setup` with manifest-based validation and setup metadata seeding.
- Added `tests/test_connector_setup.py` for the new state machine and setup helper behavior.

## Changes
- Added:
  - `ConnectorSetupStatus` union type with ordered states:
    `not_started`, `program_selected`, `environment_selected`, `awaiting_credentials`, `credentials_received`, `validation_failed`, `connected_read_only`, `ready`.
  - `CONNECTOR_SETUP_STATES`.
  - `required_missing_fields(manifest, credentials)`.
  - `next_setup_state(has_environment, missing_fields)`.
- Added `SupabaseProductStore.start_connector_setup(...)`:
  - validates `connector_id` using `connector_by_id`.
  - validates `environment` against manifest environments.
  - calls `set_connector_profile(...)` with metadata:
    - `setup_state: awaiting_credentials`
    - `required_secret_fields`
    - `preset`
    - `capabilities`
- Added tests:
  - ordered setup state constants
  - missing fields from manifest
  - setup state transitions for environment/credential presence
  - setup helper metadata persisted in returned profile
  - `ValueError` on unknown connector and unsupported environment.

## Validation
- TDD step 1/2 check (expected failure before implementation):
  - `uv run pytest tests/test_connector_setup.py -v`
  - observed: `ModuleNotFoundError: No module named 'mercury_tools.connectors.setup'`
- Final validation:
  - `uv run pytest tests/test_connector_setup.py tests/test_product_fallback.py -v`
  - `14 passed`
- Lint:
  - `uv run ruff check src/mercury_tools/connectors/setup.py src/mercury_tools/db/product.py tests/test_connector_setup.py tests/test_product_fallback.py`
  - `All checks passed!`

## Notes
- Kept edits strictly inside the requested implementation/test scope.

## Review Fix
- Fixed resume/idempotent setup behavior so `start_connector_setup(...)` preserves
  `company_name=None` instead of coercing it to an empty string.
- Updated product-table upsert payload construction to omit `company_name` when
  no new label is provided, allowing existing labels to survive merge upserts.
- Updated audit fallback profile setup to preserve an existing connector profile
  company label when a later setup resume omits `company_name`.
- Added `resolve_setup_state(...)` and made `next_setup_state(...)` delegate to it.
- Made `start_connector_setup(...)` derive setup metadata state through
  `resolve_setup_state(...)` instead of hard-coding `awaiting_credentials`.
- Added focused coverage for all declared setup states and the
  resume-without-company-name case.

## Review Fix Validation
- `uv run pytest tests/test_connector_setup.py tests/test_product_fallback.py -v`
  - `16 passed`
- `uv run ruff check src/mercury_tools/connectors/setup.py src/mercury_tools/db/product.py tests/test_connector_setup.py tests/test_product_fallback.py`
  - `All checks passed!`
