# Mercury Finance v0.2.1

Release candidate status: Task 15 prepares the reviewed package, plugin,
workflows, verifiers, and public documentation. This document does not assert that the tag, assets, deployment, or visibility change exists. Those operations remain manual and are bound to an exact reviewed `main` SHA.

Mercury Tools is an independent open-source project and is not affiliated with Mercury Technologies, Inc.

## Release Identity

- Python package: `0.2.1`
- Codex plugin: `0.2.1+codex.20260713`
- Git release tag: `v0.2.1`
- Launcher: `git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.1`
- Local surface: one `mercury-finance` stdio MCP with exactly 19 local tools
- Hosted surface: separate Streamable HTTP MCP with exactly 20 hosted tools

## Required Gates

The manual release workflow accepts one reviewed 40-character `main` SHA and
requires these gates in order before asset publication:

1. Full history secret scans with checksum-verified Gitleaks 8.24.3 and TruffleHog 3.88.32, Ruff, full pytest JUnit, exact skip waivers, catalog validation, plugin validation, and local MCP smoke.
2. Supabase migration verification against an isolated environment.
3. Sanitized FlowAccount 190-action sandbox coverage with the required live read test and no provider values in logs.
4. PEAK 64-action contract coverage with `http_attempts=0`.
5. Reproducible wheel, sdist, plugin, and source artifacts plus SHA-256 manifest, built through the pinned Linux/amd64 release platform.
6. History-free public staging verification.
7. Tagged marketplace installation with one MCP and 19 local tools.
8. Render verification requiring `/healthz`, exact version, exact deployment commit, 254 catalog actions, 20 hosted tools, cited RAG retrieval, and scanned logs.
9. Release asset publication only after all preceding jobs pass.

The post-public workflow then performs anonymous clone, release asset digest,
marketplace installation, MCP startup, and judge quickstart verification.

## Security And Data Boundary

ERP credentials, request payloads, previews, confirmations, execution state,
and the redacted local audit ledger remain repository-local. Cloud stores
catalog, RAG, and audit metadata only. Release jobs pass secret values through
environment names and never print raw scanner, provider, Render, or Supabase
responses.

Every release output is created under an owner-controlled mode-`0700` parent.
The reproducible release platform is the immutable Linux/amd64 image recorded
in `release-toolchain/platform.json`; unsupported local platforms fail closed.

The reviewed scanner false positives are exact, expiring fingerprints documented
in `docs/release/v0.2.1-secret-scan-review.md`; no whole path, commit, detector,
or repository is excluded.

## Operator Constraints

Do not recreate or move an existing tag. Do not publish assets until every
workflow dependency has passed for the reviewed SHA. Do not substitute
`/api/status` for `/healthz`. A post-public discrepancy is handled as a security
incident and corrected in a new version; the published tag remains immutable.
