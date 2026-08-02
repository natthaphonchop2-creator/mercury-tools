# Mercury V1 Wave 0 AWS Readiness Evidence

Date: 2026-08-02
Region: `ap-southeast-1`
Final gate: `blocked_account_access`
Owner approval: not requested; Wave 0 exit proof is incomplete

## Boundary

This is a sanitized offline owner-review record. The gate was run with
`--skip-live`; no live AWS API was called and no GitHub Actions workflow was
dispatched. The ignored machine report is
`.artifacts/aws/wave0/readiness.json`, mode `0600`, with SHA-256
`0a276f890d166ccbdeb6d696e91d7b14209ce3bcfbbfd126c3843a6baf600654`.

No Mercury runtime, customer data, provider credentials, or Wave 1
infrastructure was created. No AWS account ID, principal ARN, access key,
secret key, session token, raw JWT, cookie, authorization header, or provider
credential is recorded here.

## Toolchain

| Tool | Required | Observed | Status |
| --- | --- | --- | --- |
| AWS CLI | `>=2.36.14` | `2.36.14` | pass |
| Node.js | `>=20` | `22.22.2` | pass |
| Python | `>=3.11,<3.14` | `3.11.15` | pass |
| AgentCore CLI | `0.25.0` | `0.25.0` | pass |
| AWS CDK | `2.1134.0` | `2.1134.0` | pass |

`npm ci --ignore-scripts` passed with the approved exact pins. npm reported
13 transitive advisories: 1 low, 11 moderate, and 1 high. No dependency was
changed because advisory remediation is outside this gate's approved scope.

## Account Evidence

| Environment | Alias/profile | Account fingerprint | STS status |
| --- | --- | --- | --- |
| nonprod | `mercury-nonprod` | unavailable | not run |
| production | `mercury-prod` | unavailable | not run |

Distinct account fingerprints are not proven. Account access remains the first
blocked category after the passing tool checks.

## Region And Service Probes

The configured Region is exactly `ap-southeast-1`. Every required probe below
is present in the closed configuration, but neither account was probed live.

| Probe | nonprod | production |
| --- | --- | --- |
| `agentcore_runtime` | not run | not run |
| `agentcore_gateway` | not run | not run |
| `agentcore_identity` | not run | not run |
| `bedrock_knowledge_bases` | not run | not run |
| `aurora_postgresql` | not run | not run |
| `s3` | not run | not run |
| `kms` | not run | not run |
| `ecr` | not run | not run |
| `cloudwatch_logs` | not run | not run |
| `agentcore_quotas` | not run | not run |

No service or quota availability claim is made.

## OIDC And Identity

- Nonprod GitHub Actions run URL: absent; no URL or hash recorded.
- Production GitHub Actions run URL: absent; no URL or hash recorded.
- OIDC workflow dispatch: not performed.
- OIDC CLI inputs require explicit `nonprod=URL` and `production=URL` bindings
  from two separate workflow dispatches with distinct nonces and run URLs.
- URL shape or order does not create pass evidence. When bindings exist, the
  public final gate accepts only explicit run references and independently
  verifies closed run metadata, workflow identity, the expected successful
  matrix job, run attempt, and pinned workflow source at the run head SHA through
  exact, shell-free allowlisted `gh api` GET calls.
- After the pinned credentials action, each matrix job calls STS without
  printing the 12-digit account ID, computes the existing 12-character account
  fingerprint, and uploads one closed environment-named JSON proof through
  pinned `actions/upload-artifact` v4.6.2 with one-day retention.
- The verifier queries the exact selected run's artifact inventory and requires
  one non-expired bounded artifact with the expected name. It downloads only
  that artifact through the exact allowlisted `gh run download` shape into a
  symlink-safe temporary directory below `.artifacts/aws/wave0/`, then accepts
  only one expected regular size-limited JSON file with the closed proof schema.
- The proof is bound to repository, workflow, run ID, run attempt, head SHA,
  environment, verified job, and the corresponding readiness account
  fingerprint. Canonical OIDC evidence v4 contains only the account fingerprint
  and canonical proof hash; it contains no account ID, role ARN, download path,
  or raw artifact content.
- The current GitHub environment value of `AWS_WAVE0_ROLE_ARN` is not queried or
  used as historical proof authority. Changing or forging it after a run cannot
  alter or repair that run's artifact evidence.
- Caller-constructed `OidcRunEvidence`, including a matching deterministic
  digest, cannot be supplied to the public final gate to produce `ready`.
- No `gh` or CloudFormation call ran during this blocked offline gate execution:
  the account gate is evaluated first and both OIDC bindings are absent.
- Identity decision file: absent.
- Identity decision reads reject symlinked parent components and final
  symlinks before parsing or hashing evidence.
- A supplied identity decision is insufficient by itself. The CLI also requires
  one explicit host-bound proof reference for each of Codex, ChatGPT, and
  Claude. The final gate reloads all three closed probe records, re-hashes the
  corresponding local raw files without copying their bytes, rejects duplicate,
  missing, mismatched, unsafe, future, or older-than-24-hour proof, recomputes
  `decide_identity`, and compares the decision exactly.
- Identity mode and issuer kind: not selected.
- Codex, ChatGPT, and Claude host results: not available.
- Disposable Cognito stack: not deployed, so no deletion command was run. A
  future otherwise-ready gate must independently confirm absence through the
  exact read-only `cloudformation describe-stacks` call against
  `mercury-nonprod` before returning `ready`.

The evidence record does not invent OIDC URLs, OIDC hashes, identity mode,
issuer kind, host results, account fingerprints, or stack-deletion proof.

## Commits Reviewed

| Task | Commits |
| --- | --- |
| Task 1 | `f5b902e953dc21a42cc338fe459dae463e9aa740`, `89d6503c8d4b8f3518f4f793c10e8c09ebe34f22` |
| Task 2 | `25e90006c6b8a8703b5bee4efb730ceb776c640a`, `b30c948db34500cc1adfc8b5ce7f6e9cb798d7ad`, `3fd54420120239f5bd658d420ba68f607bd34f07` |
| Task 3 | `96931300c5e9a7e5a029112cf129f175e348db06`, `d054321da038099f82bea6e76ce42699864d3298` |
| Task 4 | `f356e59d94d4e3c6bdfc31913557d61b386653aa`, `8ae5661eeb41c3eb4e9ec3a98cb8d32c5fb9f614` |
| Task 5 | `36d8cc57cacd63347c7a34bf93a38fb237a3fb37`, `5314f35c2ac52efe27473998bb41fd2b1a9ec91b`, `2ac92269ecda45bef92bf70c51530c30ad32f967` |
| Broad review base | `2ac92269ecda45bef92bf70c51530c30ad32f967` |
| Broad remediation Fix Round 1 | `64c3be83c81f699677239b7e7141c3e7ef947d1c` |

Fix Round 2 closes the remaining OIDC TOCTOU finding by replacing post-run
environment-variable authority with the immutable run-bound artifact contract
described above. This inventory intentionally does not attempt to name the
commit that contains its own update.

## Offline Verification

- `npm ci --ignore-scripts`: pass.
- Exact tool version commands: pass.
- `uv sync --extra dev`: pass.
- Approved four-file Wave 0 pytest matrix: 164 passed.
- Approved Ruff matrix: pass.
- `uv run python scripts/check_aws_readiness.py --skip-live --output .artifacts/aws/wave0/readiness.json`: expected exit 2, `blocked_account_access`.
- Exact command-shape tests reject mutating AWS, npm, npx, GitHub, and arbitrary
  artifact-download forms while preserving only the Wave 0 version probes,
  bounded reads, and run/name/destination-bound artifact retrieval.
- Whitespace checks: pass after the Fix Round 2 diff.
- The approved repository secret scan returned only its own plan command and
  intentional pre-existing redaction fixtures; the Fix Round 2 added-lines scan
  returned no matches.
- The three pre-existing untracked RED tests remain unchanged and unstaged.

## Blockers And Stop Decision

Wave 0 remains blocked until all of the following owner-controlled live evidence
exists: two reachable short-lived AWS profiles with distinct account
fingerprints, all service and quota probes in both accounts, two distinct
successful GitHub Actions OIDC run URLs, and one validated identity decision
with three current host-bound proof references covering Codex, ChatGPT, and
Claude, plus verified deletion/absence of the disposable Cognito stack. Both
Wave 0 checkboxes remain unchecked. Wave 1 planning and execution must not
begin.
