# AWS Wave 0 bootstrap

This runbook prepares the two AWS accounts and their read-only GitHub OIDC
smoke roles. It does not create Wave 1 infrastructure, IAM users, access keys,
or other long-lived credentials. Run every AWS command with an explicit
short-lived profile and Region `ap-southeast-1`; do not change the default AWS
Region.

Current Task 3 status is `blocked_account_access`. The committed template and
workflow have been verified offline, but live OIDC is not proven. Do not run
the live sections until the management account and both member accounts are
available.

## 1. Restore account access

The AWS account owner restores the management account through the approved AWS
billing and support process, then verifies that billing and account status are
active. Do not put account emails, passwords, support correspondence, account
IDs, principal ARNs, cookies, or authorization headers in Git, chat, command
logs, or committed evidence.

In AWS Organizations, create or rename the two member accounts exactly:

- `mercury-nonprod`
- `mercury-prod`

Keep the account emails in the owner's secure system only. Confirm that the two
accounts are distinct without writing raw account IDs to committed evidence;
reports use only `sha256(account_id)[:12]` fingerprints.

## 2. Configure short-lived local access

Assign the owner an appropriate temporary permission set through IAM Identity
Center. Configure and authenticate the exact local profiles:

```bash
aws configure sso --profile mercury-nonprod
aws configure sso --profile mercury-prod
aws sso login --profile mercury-nonprod
aws sso login --profile mercury-prod
```

Do not create an IAM user. Do not copy any value from an AWS credential file
into GitHub, Git, chat, logs, or documentation.

Run the secret-safe readiness command from the repository root. It stores
machine evidence only under the ignored `.artifacts/aws/wave0/` directory:

```bash
uv run python scripts/check_aws_readiness.py \
  --config infra/aws/wave0/environment.yaml \
  --output .artifacts/aws/wave0/readiness.json
```

The gate remains `blocked_account_access` unless both profiles authenticate,
have different sanitized account fingerprints, and all Region probes succeed.

## 3. Reuse or create the GitHub OIDC provider

Each member account needs the GitHub Actions OIDC provider with audience
`sts.amazonaws.com`. Reuse the existing provider when present. Create it only
when it is absent, once per account:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --profile mercury-nonprod \
  --region ap-southeast-1 \
  --no-cli-pager >/dev/null

aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --profile mercury-prod \
  --region ap-southeast-1 \
  --no-cli-pager >/dev/null
```

Before either create command, check IAM in the target account for
`oidc-provider/token.actions.githubusercontent.com`. An `EntityAlreadyExists`
response means the provider must be reused; do not delete or replace it.

## 4. Deploy the read-only roles

For each account, resolve the existing provider ARN into a shell variable. Do
not print or persist it because it contains the raw account ID. Deploy the same
template with the matching GitHub environment:

```bash
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

Repeat with profile `mercury-prod` and parameter
`GitHubEnvironment=production`. Do not delete, update, or recover unrelated
account resources as part of this runbook.

## 5. Configure GitHub environments

Create the `nonprod` and `production` environments in repository settings.
Protect `production` with a required reviewer before setting its variable.
Read each stack's `MercuryWave0GithubOidcRoleArn` output directly into a shell
variable, pass it to GitHub, and unset it without echoing it:

```bash
role_arn="$(aws cloudformation describe-stacks \
  --stack-name mercury-wave0-github-oidc \
  --query "Stacks[0].Outputs[?OutputKey=='MercuryWave0GithubOidcRoleArn'].OutputValue | [0]" \
  --output text --profile mercury-nonprod --region ap-southeast-1)"
gh variable set AWS_WAVE0_ROLE_ARN --env nonprod --body "${role_arn}"
unset role_arn

role_arn="$(aws cloudformation describe-stacks \
  --stack-name mercury-wave0-github-oidc \
  --query "Stacks[0].Outputs[?OutputKey=='MercuryWave0GithubOidcRoleArn'].OutputValue | [0]" \
  --output text --profile mercury-prod --region ap-southeast-1)"
gh variable set AWS_WAVE0_ROLE_ARN --env production --body "${role_arn}"
unset role_arn
```

`AWS_WAVE0_ROLE_ARN` is a role identifier, not an AWS credential. Never put an
access key, secret key, session token, or AWS credential-file value in GitHub.
The final verifier reads this exact variable from each GitHub environment with
an allowlisted `gh api` GET, extracts the IAM role ARN account ID only in memory,
hashes it with `fingerprint_account_id`, and requires it to match that
environment's readiness account fingerprint. It never persists or prints the
raw account ID or role ARN.

## 6. Run and record the manual smoke proof

Dispatch the manual workflow only after both environment variables and the
production reviewer are configured:

```bash
workflow="aws-wave0-oidc-smoke.yml"
workflow_ref="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')"
nonprod_nonce="wave0-nonprod-$(uv run python -c 'import uuid; print(uuid.uuid4())')"
production_nonce="wave0-production-$(uv run python -c 'import uuid; print(uuid.uuid4())')"

if [ "${nonprod_nonce}" = "${production_nonce}" ]; then
  printf 'wave0_oidc_nonce_collision\n' >&2
  exit 1
fi

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
production_run_id="$(dispatch_and_capture "${production_nonce}")"

if [ "${nonprod_run_id}" = "${production_run_id}" ]; then
  printf 'wave0_oidc_run_selection=duplicate\n' >&2
  exit 1
fi

nonprod_run_url="https://github.com/natthaphonchop2-creator/mercury-tools/actions/runs/${nonprod_run_id}"
production_run_url="https://github.com/natthaphonchop2-creator/mercury-tools/actions/runs/${production_run_id}"

if [ "${nonprod_run_url}" = "${production_run_url}" ]; then
  printf 'wave0_oidc_run_url=duplicate\n' >&2
  exit 1
fi

gh run watch "${nonprod_run_id}" --exit-status
gh run watch "${production_run_id}" --exit-status

uv run python scripts/check_aws_readiness.py \
  --identity-decision infra/aws/wave0/identity-decision.yaml \
  --identity-proof "codex=${CODEX_PROBE_RECORD:?},${CODEX_RAW_EVIDENCE:?}" \
  --identity-proof "chatgpt=${CHATGPT_PROBE_RECORD:?},${CHATGPT_RAW_EVIDENCE:?}" \
  --identity-proof "claude=${CLAUDE_PROBE_RECORD:?},${CLAUDE_RAW_EVIDENCE:?}" \
  --oidc-run "nonprod=${nonprod_run_url}" \
  --oidc-run "production=${production_run_url}"
```

Two dispatches are required even though the approved workflow remains a matrix:
the final gate binds one distinct, exact run URL to nonprod and a second
distinct, exact run URL to production. This prevents both environment claims
from collapsing onto one run reference. Each UUID input is a non-secret
correlation value. Selection is restricted to the exact workflow, dispatch
event, default branch, and generated run title; zero or multiple matches fail
closed instead of selecting the latest run.

The Mercury probe step emits only its environment and
`wave0_oidc_smoke=pass`, while the pinned credentials action may emit masked
status logs. The workflow masks the AWS account ID and redirects all AWS probe
stdout and stderr to runner-temporary files. A local template test pass is not
live OIDC proof; record success only after both jobs in each selected run pass.
