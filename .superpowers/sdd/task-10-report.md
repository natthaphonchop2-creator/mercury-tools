# Task 10 Report: OpenAI Submission and MCP Review Contract

## Status

Complete from task base `adf2ae9`. The hosted MCP contract, OpenAI submission
assets, public Skills, review cases, and deterministic Skill bundle are aligned.
Task 9 packaging remains valid, and no Task 11 version or release identity was
changed.

## TDD Evidence

### RED

1. Initial linter test:
   `uv run pytest -q tests/test_mcp_review_contract.py` -> `14 failed` because
   `scripts/review_mcp_contract.py` did not exist.
2. First hosted introspection after implementing the linter -> `1 failed,
   13 passed`; all 24 hosted root schemas omitted
   `additionalProperties=false`.
3. Submission and description contract:
   `uv run pytest -q tests/test_mcp_review_contract.py
   tests/test_openai_plugin_submission.py` -> `7 failed, 17 passed` for stale
   tool names/annotations, vendor-oriented copy, old Skills/lifecycle, old test
   cases, and underspecified tool descriptions.
4. Narrow no-argument exemption regression -> `1 failed`; an empty root with a
   phantom required argument was not rejected.
5. Unknown-field sanitization regression -> `1 failed`; Pydantic included the
   supplied marker in the strict-extra validation error.

### GREEN

- `uv run pytest -q tests/test_mcp_review_contract.py` -> `17 passed`.
- `uv run pytest -q tests/test_openai_plugin_submission.py` -> `9 passed`.
- Combined MCP review, OpenAI submission, and hosted MCP contract suites ->
  `146 passed, 1 existing Starlette/httpx deprecation warning`.
- Task 9 package and clean-install regressions -> `81 passed`.

## Delivered

- Added `scripts/review_mcp_contract.py`, which introspects hosted
  `mcp.list_tools()` and returns stable tool/argument paths for strict object,
  required workspace, scalar environment enum, bounded typed array,
  multi-source, behavior annotation, and credential-field findings.
- Added a strict hosted FastMCP registration boundary. Root schemas advertise
  `additionalProperties=false`, unknown keys are rejected at runtime, and
  `hide_input_in_errors=true` prevents rejected values from being echoed.
- Rewrote hosted tool descriptions to state state changes, external contacts,
  and omitted-option behavior. Audited reads remain read-only.
- Aligned `chatgpt-app-submission.json` with all 24 hosted tools and the actual
  annotation matrix. Both submission manifests now describe the
  connector-neutral hosted core, sanitized profile/audit metadata, no ERP
  credentials, and reviewed advanced-local execution after host approval.
- Updated all six public Skills to call hosted tools only. Connector onboarding
  uses the exact lifecycle:
  `list_connectors -> get_connector_setup -> link_connector_profile ->
  host/provider OAuth or local handoff -> validate_connector_connection ->
  connector_status`.
- Replaced the review cases with five positives (native MCP read-only, PEAK API
  driver, Express Local Bridge, portable Skill routing, cited knowledge) and
  three negatives (secret in chat, unavailable provider write, ambiguous
  multi-profile selection).
- Strengthened deterministic bundle coverage to build two independent ZIPs and
  compare bytes, SHA-256, sorted entries, fixed timestamps, and source content.
  The Task 9 builder already met the required behavior, so its implementation
  did not need to change.

## Verification

```text
uv run python scripts/review_mcp_contract.py
Mercury MCP review: 0 unclear arguments; annotations verified

bundle run 1 sha256
7350346eee6d636467006be4fc67045387e3f08811cf0b603150f309acbee64f

bundle run 2 sha256
7350346eee6d636467006be4fc67045387e3f08811cf0b603150f309acbee64f

uv run ruff check .
All checks passed!

git diff --check
no output; exit 0

Gitleaks 8.24.3 focused Task 10 source/submission/tests/generated ZIP
no leaks found
```

## Scope And Risks

- `pyproject.toml` remains at `0.2.2`; Task 11 release files are untouched.
- The full broad non-integration suite was stopped after the user prioritized
  focused completion. The directly affected hosted, submission, and Task 9
  packaging suites are green.
- An unfiltered working-directory Gitleaks scan also saw eight pre-existing
  findings in ignored `.superpowers/sdd/review-task-9*.diff` and Python bytecode.
  None are in the Task 10 diff, submission tree, tests, or generated ZIP; the
  focused Task 10 scan is clean.
