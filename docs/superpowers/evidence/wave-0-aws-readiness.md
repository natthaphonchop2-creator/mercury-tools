# Mercury V1 Wave 0 AWS Readiness Evidence

Date: 2026-08-02
Region: `ap-southeast-1`
Final gate: `blocked_identity_compatibility`
Owner approval: not requested; the three-host identity proof is incomplete

## Boundary

This is a sanitized live nonprod owner-review record. The readiness command
called only the closed, read-only AWS probe allowlist. One manually dispatched
GitHub Actions workflow assumed the dedicated read-only Wave 0 role through
OIDC and ran the same bounded service probes. The ignored machine report is
`.artifacts/aws/wave0/readiness.json`, mode `0600`, with SHA-256
`b151e90edd6ae45a00d638516a41caaba7077fcb0e6c9e589289bc0d7ab4d884`.

No Mercury runtime, customer data, provider credentials, or Wave 1
infrastructure was created. The only persistent Wave 0 AWS resource is the
`CREATE_COMPLETE` CloudFormation stack `mercury-wave0-github-oidc`, which
contains the read-only GitHub smoke role. No AWS account ID, principal ARN, access key,
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
| nonprod | `mercury-nonprod` | present in ignored machine report | pass |

The local profile is verified as an AWS-managed `login_session` with no static
profile keys, shared-credential entry, or credential environment override; STS
then succeeds. The account is stored only as the existing 12-character SHA-256
fingerprint, which is intentionally not repeated in this document. Wave 0
neither requires nor creates `mercury-prod`.

## Region And Service Probes

The configured Region is exactly `ap-southeast-1`. Every required probe below
ran live through the closed, bounded, read-only command allowlist.

| Probe | nonprod |
| --- | --- |
| `agentcore_runtime` | pass |
| `agentcore_gateway` | pass |
| `agentcore_identity` | pass |
| `bedrock_knowledge_bases` | pass |
| `aurora_postgresql` | pass |
| `s3` | pass |
| `kms` | pass |
| `ecr` | pass |
| `cloudwatch_logs` | pass |
| `agentcore_quotas` | pass; required Runtime/Gateway/Identity records are positive |

These results prove API availability for Wave 0 readiness only. They do not
prove deployed Mercury resources, runtime behavior, provider connectivity, or
production readiness.

## OIDC And Identity

- Nonprod GitHub Actions run:
  `https://github.com/natthaphonchop2-creator/mercury-tools/actions/runs/30758015246`.
- Run event/status: exact `workflow_dispatch`, completed successfully in the
  `nonprod` GitHub environment from merged `main` commit
  `3e2cf6e1b114e28d4cac4f229d4a3a60829eb010`.
- OIDC assumption, run-bound proof creation, pinned artifact upload, and all ten
  read-only AWS probes passed.
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
- The exact run exposed one expected, non-expired, 409-byte artifact named
  `mercury-wave0-oidc-account-proof-nonprod`. The independent verifier
  accepted canonical sanitized evidence SHA-256
  `20cf3faac63b207f55855e1555be4dc0ff25fd6308a96840707329a2f94bbb80`.
- The current GitHub environment value of `AWS_WAVE0_ROLE_ARN` is not queried or
  used as historical proof authority. Changing or forging it after a run cannot
  alter or repair that run's artifact evidence.
- Preflight confirmed that the `AWS_WAVE0_ROLE_ARN` variable name exists.
  Its value and the role ARN are not recorded. The GitHub OIDC provider was
  created only after confirming it was absent, and no AWS access key or local
  credential-file value was copied to GitHub.
- Caller-constructed `OidcRunEvidence`, including a matching deterministic
  digest, cannot be supplied to the public final gate to produce `ready`.
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
- Disposable Cognito stack: confirmed absent through the exact read-only
  `cloudformation describe-stacks` call against `mercury-nonprod`.

The evidence record does not invent identity mode, issuer kind, host results,
account fingerprints, or customer/provider readiness.

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
| Nonprod-first Wave 0 and CI remediation | PR `#45`, squash commit `3e2cf6e1b114e28d4cac4f229d4a3a60829eb010` |

Fix Round 2 closes the remaining OIDC TOCTOU finding by replacing post-run
environment-variable authority with the immutable run-bound artifact contract
described above. This inventory intentionally does not attempt to name the
commit that contains its own update.

## Verification

- `npm ci --ignore-scripts`: pass.
- Exact tool version commands: pass.
- `uv sync --extra dev`: pass.
- Approved four-file Wave 0 pytest matrix: 180 passed.
- Full repository pytest matrix: 6209 passed, 88 skipped, with one pre-existing
  Starlette/httpx deprecation warning.
- CI-equivalent local matrix without an AWS profile: 6201 passed with the same
  pre-existing warning.
- Both PR `#45` required checks passed the 6201-test matrix, package/plugin
  validation, build, full-history and release-artifact secret scans, and
  one-click/hosted-MCP smoke.
- Approved Ruff matrix: pass.
- The `mercury-wave0-github-oidc` stack is `CREATE_COMPLETE`; its
  template validation and deployment passed without creating a runtime or data
  service.
- OIDC smoke run `30758015246`: pass in 24 seconds.
- `uv run python scripts/check_aws_readiness.py --oidc-run nonprod=https://github.com/natthaphonchop2-creator/mercury-tools/actions/runs/30758015246`: expected exit 2, `blocked_identity_compatibility`; all 16 tool, account, and service checks plus immutable OIDC evidence pass before the missing host identity gate is applied.
- Exact command-shape tests reject mutating AWS, npm, npx, GitHub, and arbitrary
  artifact-download forms while preserving only the Wave 0 version probes,
  bounded reads, and run/name/destination-bound artifact retrieval.
- Whitespace checks: pass after the Fix Round 2 diff.
- Gitleaks 8.24.3 found no leaks in the complete Git history, wheel, sdist, or
  plugin tree. Seven false positives were manually classified as five exact
  synthetic AWS redaction fixtures, one pinned GitHub Action commit SHA, and one
  public Render feature-flag name. Narrow path-and-line allowlists cover only
  those exact fixtures; no secret rule or broad path is disabled.

## Independent Review

A read-only senior review found no Critical issue and identified four Important
items: unproven short-lived credential source, zero/missing required quota
acceptance, test/evidence drift, and stale Space interfaces. The implementation,
tests, runbooks, and source-of-truth plan were remediated. Final re-review passed
with no remaining Critical or Important finding.

## Blockers And Stop Decision

Wave 0 remains blocked only on one validated identity decision with three
current host-bound proof references covering Codex, ChatGPT, and Claude.
Short-lived nonprod STS, all required service/quota probes, GitHub OIDC, and
absence of the disposable Cognito stack already pass. Both Wave 0 checkboxes
remain unchecked. Wave 1 planning and execution must not begin.
