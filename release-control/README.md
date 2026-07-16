# Mercury v0.2.1 Release-Control Bootstrap

This directory is the copyable contract for a separate, public
`natthaphonchop2-creator/mercury-release-control` repository. Nothing under
this directory is invoked by the Mercury release workflow. It becomes trusted
only after an independent review, a commit in the separate repository, and an
immutable 40-character pin in `.github/workflows/release.yml`.

The checked-in policy is intentionally `unconfigured`, contains zero digest
sentinels, and fails closed. Do not replace the Mercury workflow sentinel until
the complete bootstrap below passes.

## Required Sequence

1. Create the release-control repository as **public** and copy the contents of
   `release-control/scaffold/` to its root plus `policy-v0.2.1.json` to its root.
   Add the independently reviewed inspector described by
   `inspector-contract-v1.md` at
   `bin/mercury-release-control-inspector`.
2. Protect release-control `main` before any attestation run: require pull
   requests, at least one approval, the strict commit status
   `Mercury release-control CI / required`, and enforce the rule for
   administrators. The status is posted on the candidate SHA by the
   base-owned `pull_request_target` verifier. Candidate workflow and inspector
   files are read as data and never executed. The dispatch-only
   `Mercury v0.2.1 trusted hosted attestation / remote-preflight` job is a
   separate release-time gate and must not be the pull-request required check.
   The base-owned verifier requires every workflow, executable, script,
   release-notes file, and inspector dependency manifest to remain
   byte-identical to the trusted base. It permits `policy-v0.2.1.json` to
   change only as data: the candidate policy must pass both trusted policy
   validators and its configured inspector digest must equal the candidate
   inspector binary. Python bytecode and `__pycache__` entries under critical
   roots are rejected rather than ignored.
3. Administratively create `production-release` before enabling either
   workflow. Configure the exact reviewer IDs in the policy, require those
   independent reviewers, enable prevent-self-review, disable administrator
   bypass, and allow deployments from protected branches only.
4. Put every name in `required_environment_secrets` and
   `required_environment_variables` on that environment. Delete all copies of
   those secrets and variables from release-control repository scope. Delete
   all production/provider and cross-repository tokens from the Mercury
   repository. The Mercury workflow receives only the bounded, sanitized
   attestation through `workflow_dispatch`; it does not read release-control
   secrets. Keep three target-repository credentials only in the protected
   release-control environment: `MERCURY_TARGET_REPOSITORY_READ_TOKEN` for
   Contents/Actions reads, `MERCURY_TARGET_WORKFLOW_DISPATCH_TOKEN` for
   Actions dispatch, and `MERCURY_TARGET_REPOSITORY_TOKEN` for the final
   tag/release publication step.
5. Fill the immutable numeric `repository_id` for release-control, the
   immutable numeric `reviewed_repository_id` for Mercury Tools, the exact
   staging repository, inspector SHA-256, reviewer IDs, and approved
   production Supabase state in `policy-v0.2.1.json`; set `bootstrap_state` to
   `configured`. Obtain each repository ID from GitHub's repository API and
   verify that its returned `full_name` matches the reviewed policy name.
   Commit through the protected branch. This initial policy transition is
   expected to pass the base-owned verifier without allowing candidate control
   code to execute.
6. Run `verify_remote_preflight.py` with the environment-scoped preflight
   token. It must report success. A workflow declaration is not accepted as
   proof that the environment exists.
7. Record the resulting release-control commit SHA. Replace the all-zero
   `RELEASE_CONTROL_PIN` in Mercury's release workflow through normal review.
   Make Mercury public and protect its `main` before selecting the final
   reviewed Mercury SHA. Do not tag or create a release yet.
8. Create the history-free staging ref, deploy Render from that reviewed SHA,
   and run `attest-v0.2.1.yml`. The workflow records the attestation identity
   and dispatches Mercury `release.yml` with a bounded base64 copy of the
   sanitized attestation; no credential is forwarded.
9. Mercury validates the bounded relay's payload digest, pinned producer
   identity, strict bundle, candidate manifest/allowlist digests, and staging
   identity. The relay artifact name is
   `mercury-v0.2.1-trusted-attestation-<run>-attempt-<attempt>`, exactly as
   required by the external publisher. It does not grant publication authority
   to the relay.
10. After Mercury emits its attempt-bound `release-ready` artifact, dispatch
    `publish-v0.2.1.yml` with the exact Mercury run, attempt, handoff artifact
    ID/digest, and handoff payload SHA-256. This trusted workflow independently
    verifies the original release-control run, attempt, artifact ID/digest,
    payload digest, and producer commit, then creates or verifies the annotated
    tag and publishes exact artifacts without checking out or executing
    Mercury candidate code.

Making either repository public after an unprotected run is too late. Branch
and environment protections must exist and pass the remote preflight first.

## Production Supabase Approval

The approved project ref is not a secret and must be the exact 20-character
production ref. The policy's migration history digest is SHA-256 over the
UTF-8 sequence of migration version strings returned from
`supabase_migrations.schema_migrations`, sorted ascending and joined with a
single `\n`, including a final `\n`. The list must include `20260716100000`.

Each function digest is SHA-256 over the exact UTF-8 bytes returned by
`pg_get_functiondef(to_regprocedure(<signature>))`; do not trim or normalize
the database result. The schema digest is computed by
`build_supabase_schema_digest()` in `verify_remote_preflight.py` from the exact
migration ID/history digest, 17-table inventory, empty storage bucket
inventory, ordered signatures including `match_knowledge_chunks` and
`resolve_erp_action_validation_batch`, and definition digests. The inspector must
compare observed production values to policy values before emitting evidence.
Validation and endpoint-validation RAG rows must also match the exact
action/version identities parsed from the reviewed FlowAccount and PEAK catalog
files, not merely the expected aggregate counts.

## Fail-Closed State

Until the remote repository exists, the environment passes preflight, the
inspector binary digest is configured, and Mercury's pin is nonzero, no v0.2.1
release run can pass. This is intentional. Never weaken the sentinel, strict
bundle schema, exact 19/20-tool counts, or artifact identity checks to work
around incomplete remote bootstrap.
