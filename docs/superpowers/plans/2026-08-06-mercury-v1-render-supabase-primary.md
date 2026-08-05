# Mercury V1 Render + Supabase Primary Implementation Plan

**Status:** Active
**Approved:** 2026-08-06
**Canonical design:** `docs/superpowers/specs/2026-08-06-mercury-v1-render-supabase-primary-design.md`

## Goal

Ship one usable Mercury V1 path: a hosted FastMCP service on Render backed by
Supabase Auth, Postgres, RAG, connector state, preview/approval, and audit data.
AWS work is paused and is not a release dependency.

## Task 1: Lock the production contract

- Add a release-contract test for `render.yaml` and public product copy.
- Require V1 mode and HTTP authentication in the deployment blueprint.
- Keep the legacy HTTP API disabled.
- Remove claims that the hosted MCP is unauthenticated or cannot manage ERP
  connections.
- Verify the focused contract tests and commit.

## Task 2: Make health and smoke checks auth-aware

- Expose `v1_enabled` in `/healthz` without exposing secret material.
- Make the hosted smoke test verify the public health and OAuth discovery
  surfaces first.
- Require an explicit test access token before exercising protected MCP tools.
- Verify focused HTTP and smoke tests and commit.

## Task 3: Qualify Supabase for V1

- Verify required migrations and V1 tables against the selected Supabase
  project.
- Verify RLS and security advisors for exposed schemas.
- Verify OAuth 2.1 discovery, PKCE, and Dynamic Client Registration settings.
- Record any account-permission blocker explicitly; do not bypass tenant or RLS
  boundaries.

## Task 4: Qualify connector execution

- Validate the Render environment has every required V1 variable by key name
  without printing values.
- Run FlowAccount sandbox read and create qualification through the existing
  provider lifecycle.
- Preserve the mandatory sequence: prepare, validate, immutable Thai preview,
  explicit confirmation, dispatch once, verify/reconcile, sanitized audit.
- Leave unavailable provider capabilities clearly marked instead of pretending
  they are ready.

## Task 5: Release the hosted V1

- Run the full automated test suite and secret scan.
- Push the reviewed branch, merge through GitHub, and let Render deploy `main`.
- Verify `/healthz`, OAuth metadata, unauthenticated rejection, authenticated
  MCP initialization, and the supported V1 demo flow.
- Publish the exact install URL and known qualification boundaries.

## Completion Gate

Mercury V1 is ready only when a fresh user can authenticate, connect a supported
ERP account, read accounting data, prepare and preview a supported document,
explicitly confirm its creation, and inspect a sanitized audit trail through the
single hosted MCP endpoint.
