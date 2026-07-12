# Task 12 Report: FlowAccount and PEAK Global Catalog

## Status

DONE_WITH_CONCERNS

## Changed Files

- `scripts/build_builtin_catalog.py`
- `catalog/global/flowaccount/source.json`
- `catalog/global/flowaccount/actions.json`
- `catalog/global/peak/source.json`
- `catalog/global/peak/actions.json`
- `src/mercury_tools/rag/chunking.py`
- `tests/test_builtin_action_catalog.py`
- `tests/test_chunking.py`
- `wiki/connectors/flowaccount-endpoint-dictionary.md`
- `wiki/connectors/peak-endpoint-dictionary.md`

## RED Evidence

1. Initial catalog and chunk-routing tests produced `8 failed`: neither built-in
   catalog existed and endpoint chunks did not carry an `action_id`.
2. The first FlowAccount build failed closed with
   `catalog_credentials_unsafe`; a credential field description had been built
   after source sanitization instead of being relocated as safe schema metadata.
3. The first PEAK build failed with `method_risk_tier_invalid`; GET payment
   method routes had been classified by a broad payment substring.
4. The numeric-path regression failed on `/products/12851240`; supplied example
   record IDs were still embedded in six FlowAccount routes.
5. The high-risk route regression failed on provider compound spelling
   `sharedocument`; two share actions lacked Tier 2 semantics.
6. The journal-draft regression failed because `Draft Payment Voucher` was
   classified as a payment mutation instead of a journal draft.
7. The Wiki safety regression found one email and two phone-number examples in
   the pre-existing FlowAccount endpoint dictionary.

## Delivered

- Deterministic, sanitized global catalogs with exactly 190 FlowAccount actions
  and 64 PEAK actions, fixed source timestamps, stable sorting, and valid
  immutable action/source identities.
- FlowAccount repeated method/path variants derive unique variant IDs from the
  documented operation plus canonical request schema.
- Stable capability, Thai/English alias, risk, confirmation, side-effect, and
  environment metadata for each action. Payment, approval, void, email, share,
  invitation, and delete mutations receive Tier 2 handling without
  misclassifying payment-method master data or draft payment vouchers.
- Source outputs retain schemas and citations but omit request examples,
  credential values, personal examples, absolute source paths, and auth headers.
- Numeric example record IDs are normalized to `{recordId}`.
- Endpoint dictionaries contain one generated block per immutable action and
  describe Cloud knowledge as read-only while local ERP execution remains
  preview- and confirmation-gated.
- RAG chunk metadata extracts exactly one `act_<24 hex>` identity from an action
  block for semantic catalog routing.

## GREEN Evidence

- Focused catalog, chunking, and connector Wiki suite:
  `uv run pytest tests/test_builtin_action_catalog.py tests/test_chunking.py tests/test_accounting_knowledge_wiki.py tests/test_peak_wiki_content.py -q`
  -> `19 passed in 0.52s`.
- Full non-integration suite:
  `uv run pytest -q -m 'not integration'`
  -> `1100 passed, 1 deselected, 1 warning in 5.65s`.
- `uv run ruff check .` -> `All checks passed!`.
- `git diff --check` -> passed with no output.
- Rebuilding both providers produced byte-identical SHA-256 hashes for both
  catalogs and both endpoint dictionaries.
- Credential, email, Thai tax ID, phone, bearer, local absolute-path, and known
  secret-pattern scans returned no matches in generated catalog/Wiki outputs.

## Concern

- The full suite retains the pre-existing Starlette `httpx` deprecation warning
  in `tests/test_connector_mcp_tools.py`; Task 12 adds no warning or failure.
