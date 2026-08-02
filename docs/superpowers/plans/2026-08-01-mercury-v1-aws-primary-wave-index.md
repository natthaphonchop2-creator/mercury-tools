# Mercury V1 AWS-Primary Wave Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Mercury V1 as one AWS-primary, one-click, OAuth-protected MCP product through nine separately reviewed migration Waves.

**Architecture:** This document is the program control index, not an all-at-once coding plan. Each Wave has one bounded implementation plan, consumes only reviewed outputs from the preceding Wave, records verification evidence, and stops for owner approval before the next Wave is planned or executed.

**Tech Stack:** Python 3.11-3.13, MCP Python SDK/FastMCP 1.26, Amazon Bedrock AgentCore Runtime/Gateway/Identity/Policy, AWS CDK in Python, Aurora PostgreSQL Serverless v2, S3, Bedrock Knowledge Bases, Cognito or one DCR-capable OIDC issuer, KMS, ECR, CloudWatch, GitHub Actions OIDC, pytest, Ruff

## Global Constraints

- The authoritative design is `docs/superpowers/specs/2026-08-01-mercury-v1-aws-primary-agentcore-design.md`.
- Mercury remains MCP-first. Do not add Mercury Chat, AgentCore Harness, or a Mercury-owned general LLM loop.
- FastMCP remains the MCP protocol layer inside AgentCore Runtime.
- The first AWS runtime uses `mcp==1.26.0` and `from mcp.server.fastmcp import FastMCP`; a standalone FastMCP upgrade requires a later gated compatibility decision.
- The primary AWS Region is `ap-southeast-1`.
- AWS accounts are isolated from the beginning as `mercury-nonprod` and `mercury-prod`.
- There is no hybrid runtime, dual-write period, or migration of current test tenants, credentials, operations, or audit records.
- The Capability Catalog is the only execution authority. Skills and RAG may explain or route but cannot enable an endpoint or operation.
- ERP credentials never enter chat, model context, MCP arguments, widgets, RAG, logs, audit output, or Git.
- Full ERP operations are enabled only per exact provider, API version, environment, operation, and qualification evidence.
- Mutations follow `prepare -> immutable preview -> explicit confirmation -> dispatch once -> verify or reconcile -> audit`.
- No arbitrary URL or raw HTTP execution tool may be published.
- One identity issuer serves both the one-click plugin and the minimal Web Console.
- The Web Console is limited to sign-in, workspace selection, connectors, approvals, audit, members, and plans. It has no chat interface.
- Every Wave must follow `read Space -> execute current Wave only -> test/security -> evidence/progress -> review -> owner approval`.
- Package/plugin version stays below `1.0.0` until Wave 8 release gates pass.
- Do not stage, modify, or delete the pre-existing untracked RED tests `tests/test_document_batch.py`, `tests/test_document_operations.py`, and `tests/test_hosted_outcome_reconciliation.py` unless a later approved Wave names them explicitly.

---

## Plan Authority

| Document | Status | Rule |
| --- | --- | --- |
| `docs/superpowers/specs/2026-08-01-mercury-v1-aws-primary-agentcore-design.md` | Approved | Product and architecture authority |
| `docs/superpowers/plans/2026-08-01-mercury-v1-aws-primary-wave-index.md` | Active | Wave order, dependencies, and stop gates |
| `docs/superpowers/plans/2026-08-01-mercury-v1-wave-0-aws-readiness.md` | Active after owner selects execution mode | The only currently executable implementation plan |
| `docs/superpowers/plans/2026-07-26-mercury-v1-authorization-gateway.md` | Superseded | Historical Supabase/Render and create-only plan; do not continue its unchecked tasks |

Existing domain code produced by the superseded plan remains reusable where it
matches the approved AWS-primary design. Supersession applies to infrastructure,
identity, deployment, cutover, and create-only scope rather than deleting tested
domain contracts.

## Cross-Wave Interface Map

| Wave | Produces | Consumed by |
| --- | --- | --- |
| 0 | Secret-safe AWS readiness report, two-account bindings, Singapore service/quota evidence, GitHub OIDC proof, identity compatibility decision | Wave 1 environment and identity inputs |
| 1 | CDK application, isolated VPC/KMS/ECR/S3/Aurora foundations, budgets, baseline CloudWatch | Waves 2-8 |
| 2 | AgentCore Runtime-hosted FastMCP `/mcp`, validated inbound identity, token-derived tenant/workspace context | Waves 3-8 |
| 3 | Aurora product stores, Git-published Skills, S3 sources, Bedrock Knowledge Base retrieval | Waves 4-8 |
| 4 | AgentCore Gateway targets, provider credential bindings, qualified reads, connector-learning quarantine, capability coverage | Waves 5-8 |
| 5 | Exact mutation tools, preview/confirmation state machine, dispatch, idempotency, reconciliation, batch behavior | Waves 6-8 |
| 6 | One-click plugin, install authentication, minimal Web Console, Thai preview widget and text fallback | Waves 7-8 |
| 7 | Nonprod qualification, security/load/backup evidence, owner-authorized production canaries, release candidate | Wave 8 |
| 8 | Canonical AWS MCP URL, clean-install verification, Render/Supabase decommission evidence, `v1.0.0` release | Product operation |

## Wave Gates

### Wave 0: AWS Access and Architecture Readiness

**Detailed plan:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-0-aws-readiness.md`

**Entry:** Approved AWS-primary Written Spec and a repository branch based on commit `9330e67`.

**Exit:** Both AWS accounts are reachable through short-lived identity, Singapore probes pass, GitHub OIDC assumes a read-only smoke role in each account, and the one-issuer compatibility decision is recorded without credentials.

- [ ] Execute the bounded Wave 0 plan.
- [ ] Review Wave 0 evidence and stop for owner approval.

Current evidence (2026-08-02): `blocked_account_access`. Offline checks are
recorded in `docs/superpowers/evidence/wave-0-aws-readiness.md`; live account,
service/quota, OIDC, and identity proof remains absent. Wave 1 is not authorized.

### Wave 1: AWS Foundation

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-1-aws-foundation.md`

**Entry:** Owner-approved Wave 0 evidence.

**Exit:** Isolated nonprod/prod foundations exist through reviewed CDK diffs; no customer or provider data is loaded.

- [ ] Write the bounded Wave 1 plan only after Wave 0 approval.
- [ ] Execute, review, and stop for owner approval.

### Wave 2: FastMCP Runtime and Inbound Identity

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-2-runtime-identity.md`

**Entry:** Owner-approved Wave 1 foundation and Wave 0 identity decision.

**Exit:** AgentCore Runtime serves authenticated Streamable HTTP at `/mcp`, derives tenant/workspace from validated tokens, and passes host and isolation smoke tests.

- [ ] Write the bounded Wave 2 plan only after Wave 1 approval.
- [ ] Execute, review, and stop for owner approval.

### Wave 3: Product Data, Skills, and Knowledge

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-3-data-skills-knowledge.md`

**Entry:** Owner-approved authenticated Runtime.

**Exit:** Clean product state runs on Aurora and approved Git/S3 knowledge is retrievable with citations through Bedrock Knowledge Bases; no legacy test state is imported.

- [ ] Write the bounded Wave 3 plan only after Wave 2 approval.
- [ ] Execute, review, and stop for owner approval.

### Wave 4: Connector Reads and Qualification

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-4-connector-reads.md`

**Entry:** Owner-approved product data and knowledge stores.

**Exit:** FlowAccount and PEAK use secure provider identity, expose qualified reads only, report exact coverage, and quarantine failed endpoint versions.

- [ ] Write the bounded Wave 4 plan only after Wave 3 approval.
- [ ] Execute, review, and stop for owner approval.

### Wave 5: Full ERP Operations

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-5-full-erp-operations.md`

**Entry:** Owner-approved provider reads and qualification authority.

**Exit:** Qualified POST, PUT, PATCH, DELETE, and semantic actions use exact schemas, immutable preview, one confirmation, single dispatch, idempotency, reconciliation, and audit.

- [ ] Write the bounded Wave 5 plan only after Wave 4 approval.
- [ ] Execute, review, and stop for owner approval.

### Wave 6: One-Click Product Surfaces

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-6-one-click-surfaces.md`

**Entry:** Owner-approved read and mutation runtime.

**Exit:** A user installs one plugin, authenticates, selects a workspace, uses Mercury without a local server, and can approve through the minimal Web Console or MCP Apps preview.

- [ ] Write the bounded Wave 6 plan only after Wave 5 approval.
- [ ] Execute, review, and stop for owner approval.

### Wave 7: Certification and Release Candidate

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-7-certification.md`

**Entry:** Owner-approved one-click product surfaces.

**Exit:** Provider qualification, failure/security/load tests, backup/restore proof, production canaries, and public coverage report all pass.

- [ ] Write the bounded Wave 7 plan only after Wave 6 approval.
- [ ] Execute, review, and stop for owner approval.

### Wave 8: Cutover and `v1.0.0`

**Reserved plan path:** `docs/superpowers/plans/2026-08-01-mercury-v1-wave-8-cutover-release.md`

**Entry:** Owner-approved release candidate and explicit cutover authorization.

**Exit:** The plugin points to AWS once, clean installs pass, live Render/Supabase services and secrets are removed, and GitHub publishes `v1.0.0`.

- [ ] Write the bounded Wave 8 plan only after Wave 7 approval.
- [ ] Execute the one-time cutover, verify rollback criteria, review, and request release approval.

## Program Stop Rules

Stop the current execution and report evidence when any of these conditions is
true:

1. The active Wave exit gate cannot be proven.
2. AWS account suspension, access denial, missing quota, or unavailable regional feature blocks a live check.
3. A required host cannot complete the selected one-issuer OAuth flow.
4. A provider capability lacks exact catalog authority or qualification evidence.
5. A mutation may have reached a provider but its outcome is uncertain.
6. A task would require provider credentials in chat, Git, logs, RAG, or a model-visible payload.
7. Work would cross into the next Wave before owner approval.

The correct result of a blocked gate is a sanitized blocked evidence record, not
an inferred pass and not an unreviewed architecture change.
