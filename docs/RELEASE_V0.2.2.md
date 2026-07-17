# Mercury Finance v0.2.2

Release candidate status: under protected review. This document does not assert that the tag, assets, deployment, or visibility change exists.

Mercury Tools is an independent open-source project and is not affiliated with Mercury Technologies, Inc.

## Release Identity

- Python package: `0.2.2`
- Codex plugin: `0.2.2+codex.20260717`
- Git tag and launcher: `v0.2.2`
- Local surface: one repository-local `mercury-finance` stdio MCP with 19 tools
- Hosted surface: a separate public Streamable HTTP MCP with 20 tools

## Trust Boundary

Mercury source jobs are secretless and have `contents: read`. The independently
reviewed public `mercury-release-control-v2` repository owns provider secrets,
history-free staging, hosted evidence, and final publication. Mercury accepts a
strict `TrustedAttestationV2`, rebuilds wheel, sdist, plugin, and source archives
offline, and emits an attempt-bound schema-v3 handoff. It cannot create or
replace a Git tag or GitHub Release.

The attestation binds the reviewed repository ID and SHA, release-control
repository ID and commit, workflow run and attempt, PublicTreeV1 digest,
history-free staging tag, Render deployment, Supabase schema/RAG state,
FlowAccount sandbox probe, and eight sanitized public-surface receipts.

## Product Boundary

ERP credentials and write state stay in `.mercury/` inside the selected local
repository. Supabase stores catalog, RAG, and sanitized audit metadata only.
Production ERP writes require preview, explicit confirmation, and a cataloged
action; Mercury never retries an unknown write outcome automatically.

## Verification

Run the release workflow only for the exact reviewed `main` SHA. The external
publisher independently re-downloads the original attestation, handoff, and
release artifact bundle, checks every ID and digest, enables immutable releases,
and publishes `v0.2.2` without force or overwrite behavior. After publication,
`.github/workflows/post-public-verify.yml` performs anonymous tag, asset, plugin,
MCP, and quickstart smoke tests.

Required gates include Gitleaks 8.24.3 and TruffleHog 3.88.32 full-history
scans, an ephemeral Supabase stack, FlowAccount exact 190-action coverage,
PEAK 64-action provider-free coverage, one `mercury-finance` stdio MCP with
19 local tools, and 20 hosted tools. The Render exact commit check binds
`/healthz` to the reviewed SHA. Artifacts are produced in a secretless, networkless, read-only candidate container, and the handoff carries exact artifact IDs and digests into release-control before any post-public check.

See [JUDGE_QUICKSTART.md](JUDGE_QUICKSTART.md),
[LOCAL_CREDENTIALS.md](LOCAL_CREDENTIALS.md), and
[v0.2.2-secret-scan-review.md](release/v0.2.2-secret-scan-review.md).
