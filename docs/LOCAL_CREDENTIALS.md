# Repository-Local Credentials

This is the sole guide for entering API-driver credentials. It applies only to the
separately connected advanced local Mercury MCP; the hosted one-click plugin never asks
for, receives, stores, or tests ERP credentials.

## Terminal-only input

Run setup from the repository that owns the ERP connection, or pass its path with
`--repo-root`. The CLI prompts for every credential through hidden terminal input (echo
disabled). Do not put values on a command line, in a file, in an MCP argument, or in chat.

```bash
uv run mercury credentials setup flowaccount --env production --repo-root .
uv run mercury credentials setup peak --env uat --repo-root .
uv run mercury credentials status --repo-root .
```

The status command reports only required, present, and missing field names. It never
prints credential values.

## Location and boundary

Credential commands initialize `.mercury/` in the selected repository:

- `.mercury/credentials.env` contains repository-local credential profiles.
- `.mercury/audit/audit.jsonl` contains redacted, append-only audit events.
- `.mercury/cache/requests.sqlite` keeps immutable local preview and outcome state.

On POSIX systems the credential and audit files use owner-only permissions. Do not commit
`.mercury/credentials.env`. `.env` is not the local ERP trust boundary; the local runtime
reads connector values only through the `mercury credentials` command family.

## Safe connection probes

After hidden-input setup is complete, validate authentication with a safe read-only probe:

```bash
uv run mercury credentials test flowaccount --env production --repo-root .
uv run mercury credentials test peak --env uat --repo-root .
```

These probes make safe GET requests, record only non-secret validation metadata locally,
and never create, update, approve, or delete provider records.

## Clear local state

Remove one configured profile:

```bash
uv run mercury credentials clear flowaccount --env production --repo-root .
```

Remove every profile from the active repository:

```bash
uv run mercury credentials clear --all --repo-root .
```

`clear --all` unlinks the local credential file, invalidates pending local requests, and
removes local validation records. It does not guarantee forensic erasure from operating
system caches, backups, snapshots, or storage media. Rotate credentials after a suspected
compromise.

## Live test guard

The default integration suite uses fake Cloud and ERP transports. Live credential checks
are opt-in and use the same safe probes above; they never execute ERP writes.

```bash
MERCURY_LIVE_FLOWACCOUNT=1 uv run pytest tests/integration/test_local_erp_mcp.py -q
MERCURY_LIVE_PEAK=1 uv run pytest tests/integration/test_local_erp_mcp.py -q
```
