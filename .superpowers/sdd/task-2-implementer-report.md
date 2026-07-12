# Task 2: Atomic Repository Credential Store

## Scope

- Added `CredentialField` and `CredentialStatus` driver models.
- Added a repository-bound `CredentialStore` that persists only to
  `<repo>/.mercury/credentials.env`.
- Added lifecycle, safety, and redaction tests in `tests/test_local_credentials.py`.

## TDD Evidence

### RED

1. Before any production credential files existed:

   ```bash
   test ! -e src/mercury_tools/drivers && test ! -e src/mercury_tools/local/credentials.py && uv run pytest tests/test_local_credentials.py -q
   ```

   Output: collection failed with `ModuleNotFoundError: No module named 'mercury_tools.drivers'`.

2. Security hardening tests added after the first GREEN:

   ```bash
   uv run pytest tests/test_local_credentials.py -q
   ```

   Output: 2 failures, proving dotenv interpolation read process environment and a dangling
   credential symlink was not rejected.

3. Repository-bound path test:

   ```bash
   uv run pytest tests/test_local_credentials.py -q
   ```

   Output: 1 failure, proving a forged `RepositoryContext.credentials_path` was accepted.

### GREEN

```bash
uv run pytest tests/test_local_credentials.py tests/test_redaction.py -q
```

Output: `24 passed in 0.08s`.

## Final Verification

```bash
uv run ruff check src/mercury_tools/drivers src/mercury_tools/local/credentials.py tests/test_local_credentials.py
uv run pytest -m "not integration" -q
git diff --check
```

Output:

- Ruff: `All checks passed!`
- Non-integration suite: `378 passed, 1 deselected, 1 warning in 1.60s`
- `git diff --check`: clean

The warning is the existing Starlette/httpx deprecation warning from
`tests/test_connector_mcp_tools.py:6`.

## Files

- `src/mercury_tools/drivers/__init__.py`
- `src/mercury_tools/drivers/models.py`
- `src/mercury_tools/local/credentials.py`
- `tests/test_local_credentials.py`

## Self-Review

- Store construction rejects any context path other than the repository-local credentials file.
- Parsing uses `dotenv_values(..., interpolate=False)` and never loads values into `os.environ`.
- Reads are uncached; writes are same-directory temporary files with file fsync, POSIX `0600`,
  and `os.replace` after symlink rejection.
- Serialization is quoted and lexically sorted. Duplicate normalized field names and duplicate
  stored keys are rejected without including values in errors.
- Status, repr checks, exceptions, and tests expose only names and presence state, never values.
- `save` accepts only declared fields; partial input is represented deterministically as missing
  required fields.

## Concerns

- No unresolved Task 2 concerns. The only verification warning is the pre-existing Starlette
  deprecation warning noted above.
