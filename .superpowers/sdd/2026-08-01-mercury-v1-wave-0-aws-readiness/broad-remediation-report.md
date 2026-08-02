# Mercury V1 AWS Wave 0 Broad Remediation Report

Date: 2026-08-02
Base head: `2ac92269ecda45bef92bf70c51530c30ad32f967`
Scope: Wave 0 readiness only; no Wave 1 or runtime changes
Current gate: `blocked_account_access`

## Fix Round 1

1. Identity proof is now host-bound and independently reverified. The public
   finalizer requires exactly one Codex, ChatGPT, and Claude proof reference,
   reloads each sanitized `HostIdentityProbe`, re-hashes the corresponding raw
   local file, enforces a 24-hour freshness window, recomputes
   `decide_identity`, and compares the decision exactly. Missing, duplicate,
   mismatched, stale, unsafe, oversized, non-regular, or symlinked proof fails
   closed. Raw evidence bytes and paths are not returned or persisted.
2. The finalizer independently checks that
   `mercury-wave0-identity-spike` is absent in `mercury-nonprod` through one
   exact shell-free `cloudformation describe-stacks` read. Existing,
   inaccessible, or ambiguous stack state blocks identity compatibility.
   Account failure is evaluated first, so an earlier account block makes no
   GitHub or CloudFormation call.
3. Each OIDC run originally read only the exact GitHub environment variable
   `AWS_WAVE0_ROLE_ARN`, accepted only an exact IAM role ARN, fingerprinted its
   account ID, and compared it with the corresponding readiness account
   fingerprint. Canonical OIDC evidence v3 stored the fingerprint only. Same-
   account and wrong-account role mutations fail closed. Broad review then
   identified the remaining historical-run TOCTOU gap in this authority; Fix
   Round 2 below supersedes it.
4. The approved matrix workflow remains unchanged. The runbook dispatches it
   twice with distinct nonces, rejects zero/multiple/duplicate run selection,
   derives two distinct run URLs, and passes explicit `nonprod=...` and
   `production=...` bindings to the final CLI.
5. The subprocess allowlist now contains only the exact local version probes,
   Wave 0 AWS reads, Cognito absence read, and closed GitHub GET routes used by
   the verifier. Mutating AWS, npm, npx, and GitHub forms are rejected before
   subprocess execution.

## TDD Evidence

- RED: a hand-written `IdentityDecision` produced `ready`; the new assertion
  failed with `ready != blocked_identity_compatibility`.
- GREEN: minimal decision-only finalization is blocked.
- RED: identity proof reload/re-hash/redecision APIs were absent; seven focused
  proof tests failed.
- GREEN: the seven focused proof tests passed, including missing, duplicate,
  mismatched, stale, unsafe, and exact-decision cases.
- RED: Cognito cleanup and account-gate ordering tests showed no stack read and
  an early GitHub call.
- GREEN: all four finalizer identity/account ordering tests passed.
- RED: OIDC account-binding tests failed because verified evidence had no
  readiness fingerprint binding.
- GREEN: correct bindings pass; same-account and wrong-account mutations fail.
- RED: command-shape tests exposed the old broad program allowlist.
- GREEN: seven exact allowlist tests pass, including mutating-form rejection.
- RED: the runbook lacked two nonce/run bindings.
- GREEN: the two-dispatch/distinct-URL/final-CLI contract tests pass.

## Fix Round 2

The remaining Important finding is closed with run-bound account evidence:

1. Immediately after pinned `configure-aws-credentials`, each matrix job calls
   STS, validates the account ID as exactly 12 digits without printing it,
   computes `sha256(account_id)[:12]`, unsets the raw value, and writes a closed
   JSON proof containing only schema, repository, workflow, run ID, run attempt,
   head SHA, environment, and account fingerprint.
2. Each environment proof is uploaded under its exact environment name through
   official `actions/upload-artifact` v4.6.2 pinned to
   `ea165f8d65b6e75b540449e92b4886f43607fa02`, with one-day retention and
   missing-file failure. The previous raw `sts.json` runner file was removed.
3. The verifier binds run attempt and head SHA across the selected run and job,
   queries the exact run artifact inventory, requires one non-expired artifact
   with the expected name and bounded size, and downloads only that artifact.
   The shell-free command allowlist ties repository, run ID, artifact name,
   environment, and randomized destination below `.artifacts/aws/wave0/`.
4. Download directories are created through no-follow directory descriptors.
   Parsing accepts exactly one expected regular JSON file, rejects symlinks,
   extra/missing/oversized files and non-closed or mismatched proof fields, and
   removes the download in every result path. Canonical evidence v4 stores only
   the account fingerprint and canonical closed-proof hash.
5. The verifier no longer queries current `AWS_WAVE0_ROLE_ARN` values. Regression
   tests prove that changing a current role ARN cannot alter historical evidence
   and that a forged current value cannot repair a wrong historical artifact.
6. The two-dispatch runbook remains intact because one distinct selected run is
   bound to nonprod and another to production. It now documents artifact expiry,
   retrieval, and fail-closed behavior and adds `set -euo pipefail` to applicable
   command blocks.

### Fix Round 2 TDD Evidence

- RED: workflow tests failed because no closed proof or pinned upload step
  existed and the probe step still stored raw STS JSON.
- GREEN: exact configure/proof/upload/probe ordering and closed output pass.
- RED: artifact verifier tests failed because `verify_oidc_runs` had no
  repository-controlled download contract and still queried the current role
  variable.
- GREEN: run/job/artifact/proof binding passes; duplicate, missing, expired,
  oversized, additional-file, symlink, schema, run, attempt, SHA, environment,
  and account mutations fail closed.
- RED: with a matching monkeypatched source digest, an account proof field
  mutation passed workflow validation.
- GREEN: structural workflow validation independently requires every closed
  proof field plus both exact pinned action SHAs.
- RED: runbook tests showed no strict-shell preamble or historical artifact
  authority documentation.
- GREEN: two distinct dispatches and final CLI bindings remain unchanged while
  artifact generation, retrieval, expiry, and current-variable non-authority
  are explicit.

## Verification

- Four-file Wave 0 matrix: `164 passed`.
- Ruff for changed Python and Wave 0 tests: pass.
- `git diff --check`: pass.
- Added-line and changed non-test secret scans: no matches.
- Offline readiness command: expected exit `2`,
  `gate_status=blocked_account_access`.
- Ignored report: mode `0600`, SHA-256
  `0a276f890d166ccbdeb6d696e91d7b14209ce3bcfbbfd126c3843a6baf600654`.
- Wave Index remains unchecked. The three pre-existing untracked RED tests were
  not modified or staged.

## Execution Incident

During the initial RED test for command rejection, the test called the old
broad `run_command` boundary before subprocess was monkeypatched. Dummy
mutating command shapes were therefore attempted instead of being rejected at
validation. No repository, package metadata, or generated-file change was
observed, and no process remained running. Live external side effects were not
verified because this remediation is prohibited from making follow-up live
AWS/GitHub calls. The test was corrected to fail if any rejected command reaches
`subprocess.run`, and the production allowlist now rejects all covered forms.

## Stop Decision

Wave 0 remains blocked on owner-controlled account access and all dependent
live account, service/quota, OIDC, identity, and stack-absence evidence. No
readiness claim is made, owner approval is not requested, and Wave 1/runtime
work remains unauthorized.
