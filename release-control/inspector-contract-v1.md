# Release-Control Inspector Interface v1

The external release-control repository must contain an independently reviewed
executable at `bin/mercury-release-control-inspector`. Its SHA-256 is recorded
in `policy-v0.2.1.json`; `run_pinned_inspector.py` rejects a missing, symlinked,
group/world-writable, non-executable, or digest-mismatched file.

## Invocation

The wrapper invokes the executable without a shell:

```text
mercury-release-control-inspector
  --interface-version 1
  --policy <trusted-policy-json>
  --reviewed-sha <40-lowercase-hex>
  --staging-ref <immutable-v0.2.1-prerelease-ref>
  --manifest <untrusted-public-surface-manifest-json>
  --allowlist <untrusted-secret-scan-allowlist-json>
  --output <new-hosted-evidence-v1-json>
```

Only the environment names listed in `run_pinned_inspector.py` are forwarded.
`SUPABASE_DB_URL` is an environment-scoped protected secret for a direct
PostgreSQL or Supabase session-pooler connection. The wrapper requires it to be
non-empty and may forward it only after it has verified the pinned inspector
path, permissions, and SHA-256; it must not log, parse, persist, or pass it to
any other process. The executable must treat the manifest and allowlist as
untrusted data, must not execute or import Mercury candidate code, and must
never emit raw provider responses, credentials, business payloads, logs, or
secret values.

The accepted allowlist enums must exactly match Mercury's release model:
classifications `documentation_placeholder` and `non_secret_fixture`, with
reviewer roles `release_reviewer` and `security_reviewer`.

`MERCURY_TARGET_REPOSITORY_READ_TOKEN` is the only target-repository token the
wrapper may forward to the inspector, and it is limited to target-repository
content and Actions reads. `MERCURY_TARGET_REPOSITORY_TOKEN` is write-capable,
reserved exclusively for final tag/release publication steps, and the wrapper
must never forward it to the inspector or use it for preflight.
`MERCURY_TARGET_WORKFLOW_DISPATCH_TOKEN` is not forwarded to the inspector; it
is limited to `actions:write` for dispatching the target `release.yml` and must
not have Contents write permission or be used for tag/release publication.

## Supabase Database Inspection

The inspector must use `SUPABASE_DB_URL` only through a PostgreSQL driver. It
must require `sslmode=verify-full`, validate the server certificate and hostname
against a trusted CA, and reject plaintext, `sslmode=require`, and every other
TLS mode. It must not send database metadata requests through `SUPABASE_URL`,
PostgREST, REST/RPC endpoints, or any proxy that exposes system schemas.

Before querying `supabase_migrations.schema_migrations` or calling
`pg_get_functiondef`, the inspector must verify the connected database/project
identity against `policy.supabase.project_ref` without logging the credential.
For a direct connection, the verified TLS hostname must be exactly
`db.<project_ref>.supabase.co`; for a session-pooler connection, the verified
TLS hostname must be a Supabase pooler hostname and the authenticated role must
be exactly `postgres.<project_ref>`. In either case, inside a read-only
transaction, it must verify `current_database()` is the expected database and
`current_user` is the expected authenticated role before the migration or
function-definition queries. Any URL, TLS, hostname, role, database, or project
identity mismatch must fail the inspection before those queries run.

## Required Inspection

The inspector must fail nonzero unless all of the following are complete:

- Gitleaks 8.24.3 and TruffleHog 3.88.32 scan all Git refs plus pull-request
  refs, releases/assets, Actions logs/artifacts/caches, packages, Pages, and
  wiki content. No update or remote verification mode is allowed. Every
  allowlisted scanner finding must match the exact relative path, rule, and a
  canonical fingerprint that includes a SHA-256 of the scanner's secret/raw
  evidence; a file-and-rule-only fingerprint is invalid.
- Marketplace staging is the exact policy repository/ref, contains one MCP
  with exactly 19 local tools, is an annotated single-commit history-free ref,
  and yields the exact candidate tree SHA-256.
- Approved production validation knowledge contains exactly the 190
  FlowAccount and 64 PEAK action/version identities parsed from the exact
  reviewed `catalog/global/*/actions.json` tree. Reviewed endpoint-validation
  RAG contains one document and chunk for each of those 254 identities.
- FlowAccount has exactly 190 terminal action records and the required live
  sandbox read passes.
- Render build/runtime logs are clean; `/healthz`, `/api/status`, MCP
  initialize/list, exact version `0.2.1`, exact reviewed deployment commit, and
  exactly 20 hosted tools pass.
- Public MCP sampled responses are sanitized, contain no provider secrets, and
  return connector-bound reviewed endpoint-validation citations for both
  FlowAccount and PEAK.
- Production Supabase project ref, ordered 17-table inventory, empty bucket
  inventory, migration history (including `20260716100000`), ordered function
  signatures/definition digests including the runtime search and validation
  resolver RPCs, and derived schema digest exactly equal the protected policy.

## Output

The output is one UTF-8 JSON object, at most 2 MiB, with exactly these keys:

```text
schema_version, reviewed_repository, reviewed_commit_sha,
public_surface_manifest_sha256, secret_scan_allowlist_sha256,
flowaccount, staging, render, supabase, surfaces, completed_at
```

`schema_version` is `1`. `surfaces` is ordered exactly as follows and excludes
the local artifact surface, which Mercury scans inside its isolated container:

```text
git_all_refs
github_pull_request_refs
github_releases_and_assets
github_actions_logs_artifacts_caches
github_packages_pages_wiki
marketplace_snapshot
render_build_and_runtime_logs
supabase_knowledge_and_storage
public_mcp_responses
```

Every surface must be `passed`, have zero findings/blockers, use scanner
versions `1.0.0,3.88.32,8.24.3` for Git/history surfaces and `1.0.0` otherwise,
contain only SHA-256 evidence hashes, and expose only integer exit codes. The
exact nested schema and all cross-field bindings are enforced by
`assemble_trusted_attestation.py`; any omitted/extra field or mismatch blocks
artifact creation.
