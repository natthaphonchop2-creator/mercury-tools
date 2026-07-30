# Wave D Pre-Review Format Remediation

Status: formatter applied to exactly the five files listed in the brief.

Diff: formatting-only Ruff output; no manual or semantic edits.

Checks:

- `uv run ruff format --check`: PASS, 5 files already formatted.
- `uv run ruff check`: FAIL, two formatter-produced `E501` violations in `tests/test_flowaccount_provider_oauth.py` at lines 688 and 773.
- `uv run pytest -q`: PASS, 167 passed, 1 warning.
- `git diff --check`: PASS.

Concern: the repository's formatter output conflicts with its 100-character lint limit for the two reported function definitions. No manual changes were made because the brief limits remediation to formatter output.

Changed paths:

- `src/mercury_tools/providers/oauth.py`
- `src/mercury_tools/providers/store.py`
- `tests/integration/test_postgres_task4_provider_connections.py`
- `tests/test_flowaccount_provider_oauth.py`
- `tests/test_workspace_bootstrap.py`

## E501 Follow-Up

Renamed the two affected tests without changing their bodies or behavior:

- `test_disconnect_revokes_supported_flowaccount_authorization_before_returning`
- `test_disconnect_records_revocation_obligation_after_remote_failure_idempotently`

Exact results:

- `uv run ruff format`: PASS, `1 file reformatted, 4 files left unchanged`
- `uv run ruff format --check`: PASS, `5 files already formatted`
- `uv run ruff check`: PASS, `All checks passed!`
- `uv run pytest -q`: PASS, `167 passed, 1 warning in 17.86s`
- `git diff --check`: PASS, no output

Concern: the existing `StarletteDeprecationWarning` remains; there are no Ruff findings.
