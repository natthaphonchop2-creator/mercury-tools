# AWS Wave 0 bootstrap

This runbook prepares the current authenticated AWS account as
`mercury-nonprod` and its read-only GitHub OIDC smoke role. It does not create
`mercury-prod`, Wave 1 infrastructure, IAM users, access keys, or other
long-lived credentials. Run every AWS command with the explicit short-lived
`mercury-nonprod` profile and Region `ap-southeast-1`; do not change the default
AWS Region.

No production customer or provider data may enter nonprod. Wave 7 creates the
separate production account, deploys its production foundation, and proves its
GitHub OIDC deployment path before any production canary or release.

Current Task 3 status is `blocked_account_access`. The committed template and
workflow have been verified offline, but live nonprod OIDC is not proven. Do
not run the live sections until the current AWS account is available.

## 1. Restore account access

The AWS account owner restores the management account through the approved AWS
billing and support process, then verifies that billing and account status are
active. Do not put account emails, passwords, support correspondence, account
IDs, principal ARNs, cookies, or authorization headers in Git, chat, command
logs, or committed evidence.

Bind the current authenticated account as `mercury-nonprod`. Do not create or
rename a production account in Wave 0. Keep the account email in the owner's
secure system only; reports use only the `sha256(account_id)[:12]` fingerprint.

## 2. Configure short-lived local access

Assign the owner an appropriate temporary permission set through IAM Identity
Center. Configure and authenticate the exact local profiles:

```bash
set -euo pipefail

aws configure sso --profile mercury-nonprod
aws sso login --profile mercury-nonprod
```

Do not create an IAM user. Do not copy any value from an AWS credential file
into GitHub, Git, chat, logs, or documentation.

Run the secret-safe readiness command from the repository root. It stores
machine evidence only under the ignored `.artifacts/aws/wave0/` directory:

```bash
set -euo pipefail

uv run python scripts/check_aws_readiness.py \
  --config infra/aws/wave0/environment.yaml \
  --output .artifacts/aws/wave0/readiness.json
```

The gate remains `blocked_account_access` unless the nonprod profile
authenticates, has a sanitized account fingerprint, and all Region probes
succeed.

## 3. Reuse or create the GitHub OIDC provider

The nonprod account needs the GitHub Actions OIDC provider with audience
`sts.amazonaws.com`. Reuse the existing provider when present. Create it only
when it is absent:

```bash
set -euo pipefail

aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --profile mercury-nonprod \
  --region ap-southeast-1 \
  --no-cli-pager >/dev/null
```

Before the create command, check IAM in the target account for
`oidc-provider/token.actions.githubusercontent.com`. An `EntityAlreadyExists`
response means the provider must be reused; do not delete or replace it.

## 4. Deploy the read-only roles

Resolve the existing provider ARN into a shell variable. Do not print or
persist it because it contains the raw account ID. Deploy the template bound to
the nonprod GitHub environment:

```bash
set -euo pipefail

provider_arn="$(aws iam list-open-id-connect-providers \
  --query "OpenIDConnectProviderList[?ends_with(Arn, 'oidc-provider/token.actions.githubusercontent.com')].Arn | [0]" \
  --output text --profile mercury-nonprod --region ap-southeast-1)"

aws cloudformation deploy \
  --template-file infra/aws/wave0/github-oidc-role.yaml \
  --stack-name mercury-wave0-github-oidc \
  --parameter-overrides GitHubOidcProviderArn="${provider_arn}" GitHubEnvironment=nonprod \
  --capabilities CAPABILITY_IAM \
  --profile mercury-nonprod \
  --region ap-southeast-1 \
  --no-cli-pager >/dev/null
unset provider_arn
```

Do not delete, update, or recover unrelated account resources as part of this
runbook.

## 5. Configure GitHub environments

Create the `nonprod` environment in repository settings. Read the stack's
`MercuryWave0GithubOidcRoleArn` output directly into a shell variable, pass it
to GitHub, and unset it without echoing it:

```bash
set -euo pipefail

role_arn="$(aws cloudformation describe-stacks \
  --stack-name mercury-wave0-github-oidc \
  --query "Stacks[0].Outputs[?OutputKey=='MercuryWave0GithubOidcRoleArn'].OutputValue | [0]" \
  --output text --profile mercury-nonprod --region ap-southeast-1)"
gh variable set AWS_WAVE0_ROLE_ARN --env nonprod --body "${role_arn}"
unset role_arn
```

`AWS_WAVE0_ROLE_ARN` is a role identifier, not an AWS credential. Never put an
access key, secret key, session token, or AWS credential-file value in GitHub.
The workflow consumes exactly this variable in the pinned
`configure-aws-credentials` step. Immediately afterward, the nonprod job calls
STS, validates the account ID as 12 digits without printing it, computes
`sha256(account_id)[:12]`, and unsets the raw value. It writes only a closed JSON
proof containing schema, repository, workflow, run ID, run attempt, head SHA,
environment, and account fingerprint.

The job uploads one environment-named artifact through the pinned official
`actions/upload-artifact` v4.6.2 commit, with one-day retention and
missing-file failure: `mercury-wave0-oidc-account-proof-nonprod`.

The current value of `AWS_WAVE0_ROLE_ARN` is not verification authority for a
historical run. The final verifier queries the exact run's artifact inventory,
requires one non-expired bounded artifact with the expected name, and retrieves
only that artifact through an allowlisted `gh run download`. Retrieval uses a
symlink-safe temporary directory under the controlled `.artifacts/aws/wave0/`
tree, accepts one expected regular size-limited JSON file with a closed schema,
and removes the download after hashing the canonical proof. It binds the proof's
run ID, run attempt, head SHA, and environment to verified run/job metadata and
binds its account fingerprint to readiness evidence. No raw account ID, role
ARN, download path, or artifact content enters the report or command output.

## 6. Run and record the manual smoke proof

Dispatch the manual workflow only after the nonprod environment variable is
configured:

```bash
set -euo pipefail

workflow="aws-wave0-oidc-smoke.yml"
workflow_ref="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')"
nonprod_nonce="wave0-nonprod-$(uv run python -c 'import uuid; print(uuid.uuid4())')"

dispatch_and_capture() {
  evidence_nonce="$1"
  run_title="AWS Wave 0 OIDC smoke [${evidence_nonce}]"

  gh workflow run "${workflow}" \
    --ref "${workflow_ref}" \
    -f evidence_nonce="${evidence_nonce}" >/dev/null

  run_id=""
  for attempt in $(seq 1 30); do
    matching_run_ids="$(
      gh run list \
        --workflow "${workflow}" \
        --event workflow_dispatch \
        --branch "${workflow_ref}" \
        --limit 100 \
        --json databaseId,displayTitle |
        jq -r --arg title "${run_title}" \
          '.[] | select(.displayTitle == $title) | .databaseId'
    )"
    match_count="$(
      printf '%s\n' "${matching_run_ids}" | sed '/^$/d' | wc -l | tr -d ' '
    )"

    if [ "${match_count}" -gt 1 ]; then
      printf 'wave0_oidc_run_selection=ambiguous\n' >&2
      return 1
    fi
    if [ "${match_count}" -eq 1 ]; then
      run_id="${matching_run_ids}"
      break
    fi
    sleep 2
  done

  if [ -z "${run_id}" ]; then
    printf 'wave0_oidc_run_selection=not_found\n' >&2
    return 1
  fi
  printf '%s\n' "${run_id}"
}

nonprod_run_id="$(dispatch_and_capture "${nonprod_nonce}")"

nonprod_run_url="https://github.com/natthaphonchop2-creator/mercury-tools/actions/runs/${nonprod_run_id}"

gh run watch "${nonprod_run_id}" --exit-status

uv run python scripts/check_aws_readiness.py \
  --identity-decision infra/aws/wave0/identity-decision.yaml \
  --identity-proof "codex=${CODEX_PROBE_RECORD:?},${CODEX_RAW_EVIDENCE:?}" \
  --identity-proof "chatgpt=${CHATGPT_PROBE_RECORD:?},${CHATGPT_RAW_EVIDENCE:?}" \
  --identity-proof "claude=${CLAUDE_PROBE_RECORD:?},${CLAUDE_RAW_EVIDENCE:?}" \
  --oidc-run "nonprod=${nonprod_run_url}"
```

One dispatch is required for the nonprod GitHub environment. The UUID input is
a non-secret correlation value. Selection is restricted to the exact workflow,
dispatch event, default branch, and generated run title; zero or multiple
matches fail closed instead of selecting the latest run.

The Mercury probe step emits only its environment and
`wave0_oidc_smoke=pass`, while the pinned credentials action may emit masked
status logs. The workflow masks the AWS account ID and redirects all AWS probe
stdout and stderr to runner-temporary files. A local template test pass is not
live OIDC proof; record success only after the selected nonprod job passes.
Because proof artifacts expire after one day, an expired selected artifact fails
closed and requires a new dispatch; the verifier never repairs historical
evidence from current GitHub environment configuration.
