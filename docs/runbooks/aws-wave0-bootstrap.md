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

## 6. Run and record the manual smoke proof

Dispatch the manual workflow only after both environment variables and the
production reviewer are configured:

```bash
gh workflow run aws-wave0-oidc-smoke.yml
gh run watch --exit-status
```

Both matrix jobs must print only their environment and
`wave0_oidc_smoke=pass`. The workflow masks the AWS account ID and redirects
all probe output to runner-temporary files. A local template test pass is not
live OIDC proof; record success only after both GitHub environment jobs pass.
