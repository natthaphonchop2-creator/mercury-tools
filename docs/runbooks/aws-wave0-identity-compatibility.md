# AWS Wave 0 identity compatibility

This procedure proves one inbound identity issuer for Codex, ChatGPT, Claude,
and the Mercury Web Console. Current status is `blocked_account_access`: these
offline contracts do not prove identity compatibility. Do not record
`identity-decision.yaml` until complete host evidence exists.

## 1. Prepare nonprod-only inputs

Use the short-lived `mercury-nonprod` profile and Region `ap-southeast-1`.
Obtain the callback URL directly from each host's current setup experience. Do
not put callback URLs, codes, tokens, cookies, credential values, or captured
browser pages in Git, chat, or committed evidence.

The exact required host contract is in
`infra/aws/wave0/identity-host-contract.yaml`: authorization code flow, PKCE
`S256`, refresh-token rotation, and audience/resource binding are required for
each host.

## 2. Deploy the disposable Cognito spike

Only after AWS access is restored, deploy the disposable stack in nonprod. Pass
each callback list directly at deploy time; never commit the supplied values.

```bash
aws cloudformation deploy \
  --template-file infra/aws/wave0/cognito-compatibility-spike.yaml \
  --stack-name mercury-wave0-identity-spike \
  --parameter-overrides \
    TargetEnvironment=nonprod \
    CodexCallbackUrls="${CODEX_CALLBACK_URLS}" \
    ChatGPTCallbackUrls="${CHATGPT_CALLBACK_URLS}" \
    ClaudeCallbackUrls="${CLAUDE_CALLBACK_URLS}" \
  --profile mercury-nonprod \
  --region ap-southeast-1 \
  --no-cli-pager
unset CODEX_CALLBACK_URLS CHATGPT_CALLBACK_URLS CLAUDE_CALLBACK_URLS
```

The stack creates only a nonprod disposable Cognito user pool, its domain, and
three public clients. It creates no runtime, customer, provider, or business
resource. Every client has no secret and accepts authorization code plus PKCE
`S256` only.

## 3. Capture host proof

For each host, configure its matching public client ID and callback URL, then
complete a real authorization code and PKCE `S256` flow. Keep the raw browser
or host result in a local evidence file outside Git. A documentation claim alone
is not passing evidence.

Record only a pass or fail, host, registration mode, validated issuer origin,
timestamp, and SHA-256 of that local evidence file:

```bash
uv run python scripts/record_identity_probe.py record \
  --host codex \
  --mode pre_registered \
  --result pass \
  --issuer-origin cognito \
  --evidence-file /secure/local/codex-proof.json
```

Repeat for `chatgpt` and `claude`. The recorder writes closed records below
`.artifacts/aws/wave0/identity/`; it does not copy evidence bytes, paths,
tokens, codes, cookies, or unsafe URLs. Do not edit these JSON records.

## 4. Fall back to one external issuer only when required

Select Cognito only when all three pre-registered host proofs pass. If any host
requires Dynamic Client Registration, test one DCR-capable external HTTPS issuer
with authorization code plus PKCE `S256` against all three hosts. Record the
same exact issuer origin for every DCR proof. Mixed issuers are forbidden, and
Mercury must never split identity between Cognito and an external issuer.

## 5. Generate the decision after complete proof

Only a complete compatible proof writes the committed decision:

```bash
uv run python scripts/record_identity_probe.py decide \
  --probe-dir .artifacts/aws/wave0/identity \
  --output infra/aws/wave0/identity-decision.yaml
```

An incomplete, failed, mixed, or unsafe proof exits `2`, prints a stable
`identity_*` code, and leaves `identity-decision.yaml` absent. This task is
offline while AWS remains blocked, so do not run this command to create a
decision now.

## 6. Delete the spike after capture

After recording complete proof and generating the decision, delete the
nonprod-only spike immediately:

```bash
aws cloudformation delete-stack \
  --stack-name mercury-wave0-identity-spike \
  --profile mercury-nonprod \
  --region ap-southeast-1 \
  --no-cli-pager
aws cloudformation wait stack-delete-complete \
  --stack-name mercury-wave0-identity-spike \
  --profile mercury-nonprod \
  --region ap-southeast-1 \
  --no-cli-pager
```

Do not remove, change, or create unrelated AWS resources while handling this
disposable compatibility proof.
