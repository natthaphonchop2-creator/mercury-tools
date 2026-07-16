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

The manual Mercury workflow accepts one reviewed 40-character `main` SHA plus
the exact run/attempt/artifact identities of a separately pinned
release-control attestation. It requires these gates before handing artifacts
back to release-control:

1. Full history secret scans with checksum-verified Gitleaks 8.24.3 and TruffleHog 3.88.32, Ruff, full pytest JUnit, exact skip waivers, catalog validation, plugin validation, and local MCP smoke.
2. PostgreSQL 17 migration/RPC verification against an ephemeral local
   Supabase stack, including numeric-qualified contamination regressions.
3. PEAK 64-action contract coverage with `http_attempts=0`.
4. A bounded untrusted relay of the sanitized release-control attestation.
   Mercury validates its schema, payload digest, pinned producer identity, and
   reviewed/staging bindings before building artifacts, but does not treat the
   relay as publication authority. The external publisher independently
   verifies the original release-control run, attempt, artifact ID/API digest,
   payload SHA-256, and producer head before any tag or release write. The
   attested production state includes FlowAccount exact 190-action coverage,
   PEAK 64-action coverage, and 254 reviewed endpoint-validation RAG
   documents/chunks with connector-bound citations.
5. Reproducible wheel, sdist, plugin, and source artifacts plus SHA-256
   manifest, built through the pinned Linux/amd64 release platform in a
   secretless, networkless, read-only candidate container.
6. History-free local staging construction and exact tree binding to the
   trusted hosted staging attestation.
7. Attempt-bound `release-ready` handoff containing exact artifact IDs and
   upload digests. The handoff is untrusted until the external publisher
   revalidates every original artifact identity. Mercury does not create the
   tag or publish assets.
8. Render exact commit verification: the hosted `/healthz` response and MCP
   deployment metadata must identify the same reviewed `main` SHA before the
   release can proceed.

The external `publish-v0.2.1.yml` workflow reruns the remote protection
preflight, consumes the exact Mercury run/attempt/handoff ID/API digest/payload
digest, independently verifies the original release-control run and artifact,
verifies every relayed/rebuilt artifact, and creates or verifies the annotated
tag before publishing. See `release-control/README.md` for the required
public-repository bootstrap sequence.

The post-public workflow then performs anonymous clone, release asset digest,
marketplace installation, MCP startup, and judge quickstart verification.

## Security And Data Boundary

ERP credentials, request payloads, previews, confirmations, execution state,
and the redacted local audit ledger remain repository-local. Cloud stores
catalog, RAG, and audit metadata only. Mercury release jobs contain no
production/provider credentials. Candidate containers receive no credentials,
have no network, mount the candidate workspace read-only, use a read-only root
filesystem with all capabilities dropped, and receive only explicit writable
tmpfs/output mounts.

Provider inspection and publication run only from the independently reviewed,
immutable release-control implementation behind the verified
`production-release` environment. Mercury consumes only the strict sanitized
attestation; the external workflow never checks out or executes Mercury
candidate code with production credentials.

Every release output is created under an owner-controlled mode-`0700` parent.
The reproducible release platform is the immutable Linux/amd64 image recorded
in `release-toolchain/platform.json`; unsupported local platforms fail closed.

The reviewed scanner false positives are exact, expiring fingerprints documented
in `docs/release/v0.2.1-secret-scan-review.md`; no whole path, commit, detector,
or repository is excluded.

## Operator Constraints

The checked-in release-control pin and policy sentinels intentionally block the
release until the remote public repository, protected branch, protected
environment, exact Supabase policy, and separately reviewed inspector are
configured. Do not recreate or move an existing tag. Do not publish assets
until every workflow dependency has passed for the reviewed SHA. Do not
substitute `/api/status` for `/healthz`. A post-public discrepancy is handled
as a security incident and corrected in a new version; the published tag
remains immutable.
