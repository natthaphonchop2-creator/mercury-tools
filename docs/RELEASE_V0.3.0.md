# Mercury Finance v0.3.0

## Candidate identity

- Python package: `0.3.0`
- Codex plugin: `0.3.0+codex.20260719`
- Git tag: `v0.3.0`
- Required Supabase migration: `20260719120000`
- Public plugin transport: hosted HTTPS MCP at
  `https://mercury-tools-mcp.onrender.com/mcp`

This document describes the reviewed release candidate. It does not record a
deployment, migration, tag, publication, or marketplace submission.

## Connector-neutral contract

Mercury exposes FlowAccount, PEAK, Express, Custom ERP, and Generic MCP through
mode-specific connector manifests. The hosted core provides secretless
connector discovery, sanitized profile metadata, validation evidence, cited
accounting knowledge, portable Skills, and capability-based flow planning.

The public plugin installs exactly one hosted 24-tool Mercury MCP. Provider OAuth
stays with the host or provider. Reviewed API drivers and Local Bridge integrations
remain an explicit 20-tool advanced-local handoff and are not embedded in the
hosted plugin configuration.

## Release controls

`.github/workflows/release-v0.3.0.yml` retains the v0.2.2 control model:

- exact reviewed `main` SHA and immutable annotated-tag binding;
- full-history gitleaks and TruffleHog scans with pinned scanner artifacts;
- ephemeral local Supabase migration verification;
- secretless, networkless, read-only candidate builds;
- deterministic wheel, sdist, plugin, and source artifacts with SHA-256 digests;
- exact repository ID, run ID, run attempt, artifact ID, artifact digest, and
  public-tree bindings to trusted release-control; and
- anonymous post-public verification after publication.

The independent `mercury-release-control-v2` repository owns protected provider
inspection, sanitized attestation, final tag creation, and immutable
publication. Mercury candidate jobs receive no production ERP, Render,
Supabase, marketplace, or publication credential.

## Migration boundary

`20260719120000_connector_neutral_profiles.sql` adds mode-specific sanitized
profile evidence and portable Skill capability requirements. Applying that
migration and verifying live provider state belong to Task 12 and must occur
before serving code that reads the new fields.

## Security boundary

No ERP credential, OAuth token, API key, raw provider payload, LAN detail, or
usable secret belongs in the package, plugin, release artifacts, attestation,
or release documentation. Production writes continue to require explicit host
approval, immutable request binding, provider capability evidence, and audit
records.
