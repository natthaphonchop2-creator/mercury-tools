# Repository-Local Credentials

Mercury Finance stores ERP credentials per repository. The local executor reads
them only when it prepares a request; it does not upload them to Mercury Cloud.

## Location And Boundary

Running a credential command initializes `.mercury/` in the selected repository:

- `.mercury/credentials.env` contains the repository-local credential profiles.
- `.mercury/audit/audit.jsonl` contains redacted, append-only audit events.
- `.mercury/cache/requests.sqlite` keeps local immutable preview and outcome state.

On POSIX systems the credential and audit files use owner-only permissions. Do
not commit `.mercury/credentials.env`, paste values into MCP prompts, or add
them to the plugin manifest.

`.env` is not the trust boundary for local ERP credentials. It can configure
developer process settings, but the local executor obtains connector values
from `.mercury/credentials.env` through `mercury credentials`. This avoids
accidental use of shell or dotenv values from another repository.

## Setup And Status

Run all commands from the repository that owns the ERP connection, or pass its
path with `--repo-root`.

```bash
uv run mercury credentials setup flowaccount --env production --repo-root .
uv run mercury credentials setup peak --env uat --repo-root .
uv run mercury credentials status --repo-root .
```

The setup command prompts locally. Status returns required, present, and
missing field names without printing credential values.

## Safe Connection Probes

Credential tests validate authentication then make a safe GET request only:

```bash
uv run mercury credentials test flowaccount --env production --repo-root .
uv run mercury credentials test peak --env uat --repo-root .
```

FlowAccount probes `GET /company/info`. PEAK probes `GET /user`. A successful
result records non-secret validation metadata locally. These commands do not
create, update, approve, or delete provider records.

## Clear Semantics

Remove one configured profile:

```bash
uv run mercury credentials clear flowaccount --env production --repo-root .
```

Remove every profile from the active repository:

```bash
uv run mercury credentials clear --all --repo-root .
```

`clear --all` unlinks the local credential file, invalidates pending local
requests, and removes local validation records. It does not promise a forensic
secure erase from operating-system caches, backups, snapshots, or storage
media. Rotate provider credentials when a compromise is suspected.

## Live Test Guard

The repository's default integration test uses fake Cloud and fake ERP
transports. Live credential checks are intentionally opt-in:

```bash
MERCURY_LIVE_FLOWACCOUNT=1 uv run pytest tests/integration/test_local_erp_mcp.py -q
MERCURY_LIVE_PEAK=1 uv run pytest tests/integration/test_local_erp_mcp.py -q
```

Those optional tests only execute the same credential validation and safe GET
probes above. They never execute ERP writes.
