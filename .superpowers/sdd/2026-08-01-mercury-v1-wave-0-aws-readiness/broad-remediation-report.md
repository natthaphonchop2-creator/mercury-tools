# Mercury V1 AWS Wave 0 Broad Remediation Report

Date: 2026-08-02
Base head: `2ac92269ecda45bef92bf70c51530c30ad32f967`
Scope: Wave 0 readiness only; no Wave 1 or runtime changes
Current gate: `blocked_account_access`

## Findings Remediated

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
3. Each OIDC run now reads only the exact GitHub environment variable
   `AWS_WAVE0_ROLE_ARN`, accepts only an exact IAM role ARN, fingerprints its
   account ID, and compares it with the corresponding readiness account
   fingerprint. Canonical OIDC evidence v3 stores the fingerprint only. Same-
   account and wrong-account role mutations fail closed.
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

## Verification

- Four-file Wave 0 matrix: `136 passed`.
- Ruff for changed Python and Wave 0 tests: pass.
- `git diff --check`: pass.
- Added-line and changed non-test secret scans: no matches.
- Offline readiness command: expected exit `2`,
  `gate_status=blocked_account_access`.
- Ignored report: mode `0600`, SHA-256
  `734d5fadde805d22453f8dc7dd72eb70bf33af26ffcf4d94d320d9ef8091c603`.
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
