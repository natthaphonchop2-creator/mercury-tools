# Mercury V1 Wave 0 AWS Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a repeatable, secret-safe proof that Mercury can begin AWS-primary implementation in the current authenticated account as `mercury-nonprod` in Singapore, with one nonprod GitHub OIDC proof and one compatible customer identity strategy.

**Architecture:** Wave 0 adds an offline-testable Python readiness package and deterministic Node toolchain, then uses short-lived AWS profiles to collect live account, Region, quota, and OIDC evidence. It creates no Mercury runtime, database, customer tenant, provider connector, or persistent business data; one disposable Cognito stack is allowed only for identity compatibility testing and is deleted before the Wave closes.

**Tech Stack:** Python 3.11-3.13, Pydantic 2.13.4, PyYAML 6.0.3, AWS CLI 2.36.14 or newer, Node.js 20 or newer, `@aws/agentcore==0.25.0`, `aws-cdk==2.1134.0`, AWS Organizations/IAM/STS/CloudFormation/Cognito, GitHub Actions OIDC, pytest 9, Ruff 0.15

## Global Constraints

- The authoritative design is `docs/superpowers/specs/2026-08-01-mercury-v1-aws-primary-agentcore-design.md`.
- The program controller is `docs/superpowers/plans/2026-08-01-mercury-v1-aws-primary-wave-index.md`.
- Execute Wave 0 only. Do not create Wave 1 VPC, KMS, ECR, S3, Aurora, Runtime, Gateway, Knowledge Base, or customer identity resources.
- The primary Region is exactly `ap-southeast-1`; every live AWS command passes it explicitly and does not alter the user's default Region.
- Use the current authenticated AWS account as `mercury-nonprod` for development, sandbox, UAT, and qualification. Do not require, create, or bind `mercury-prod` in Wave 0.
- No production customer or provider data may enter nonprod. Wave 7 creates the separate production account, deploys its production foundation, and proves production OIDC before any production canary.
- Local access uses short-lived AWS profiles. Do not create IAM users or long-lived access keys.
- GitHub assumes read-only Wave 0 roles through OIDC. No AWS access key is stored in GitHub.
- Do not accept, print, store, or commit passwords, access keys, secret keys, session tokens, provider credentials, raw JWTs, cookies, or authorization headers.
- Machine evidence is written only under `.artifacts/aws/wave0/`, which is gitignored. Committed evidence contains statuses, hashes, Region, aliases, tool versions, and public documentation links only.
- AWS account IDs and principal ARNs may be read transiently but account IDs appear in reports only as `sha256(account_id)[:12]` fingerprints.
- Live nonprod STS, the required Singapore service/quota probes, and the nonprod GitHub OIDC proof passed on 2026-08-02. Wave 0 remains blocked until the three-host identity compatibility proof passes.
- One inbound issuer serves the plugin and Web Console. Select `cognito_pre_registered` only if Codex, ChatGPT, and Claude all pass public-client authorization code plus PKCE; otherwise select one tested `external_oidc_dcr` issuer for all hosts and the console.
- The disposable Cognito stack contains no customer, provider, or production data and is deleted after evidence capture.
- Keep package version `0.3.1`; do not tag or publish a release in Wave 0.
- Do not change Render, Supabase, the hosted MCP URL, provider drivers, Capability Catalog, Skills, RAG content, or ERP behavior.
- Do not stage, modify, or delete `tests/test_document_batch.py`, `tests/test_document_operations.py`, or `tests/test_hosted_outcome_reconciliation.py`.
- Every task ends with focused tests, `git diff --check`, and one commit.
- At the Wave exit gate, update evidence, review the complete diff, and stop for owner approval.

---

## File and Interface Map

| File | Responsibility |
| --- | --- |
| `package.json`, `package-lock.json` | Pin AgentCore CLI and AWS CDK CLI |
| `infra/aws/wave0/environment.yaml` | Non-secret Region, account aliases, GitHub environments, and required probe IDs |
| `src/mercury_tools/aws/models.py` | Closed readiness and identity evidence contracts |
| `src/mercury_tools/aws/config.py` | Load the non-secret Wave 0 configuration |
| `src/mercury_tools/aws/commands.py` | Allowlisted subprocess runner with redaction and timeouts |
| `src/mercury_tools/aws/readiness.py` | Tool, account, Region, service, quota, and final gate checks |
| `src/mercury_tools/aws/identity.py` | Host OAuth probe validation and one-issuer decision |
| `scripts/check_aws_readiness.py` | Human/JSON readiness entry point |
| `scripts/record_identity_probe.py` | Secret-free identity evidence recorder |
| `infra/aws/wave0/github-oidc-role.yaml` | Read-only GitHub OIDC smoke role |
| `.github/workflows/aws-wave0-oidc-smoke.yml` | Manual nonprod OIDC proof |
| `infra/aws/wave0/cognito-compatibility-spike.yaml` | Disposable public-client Cognito test stack |
| `infra/aws/wave0/identity-host-contract.yaml` | Codex, ChatGPT, and Claude test requirements |
| `infra/aws/wave0/identity-decision.yaml` | Committed one-issuer result after live proof |
| `docs/runbooks/aws-wave0-bootstrap.md` | Account, profile, OIDC, service, and cleanup procedure |
| `docs/runbooks/aws-wave0-identity-compatibility.md` | Host-by-host OAuth proof procedure |
| `docs/superpowers/evidence/wave-0-aws-readiness.md` | Sanitized owner-review evidence |
| `tests/test_aws_wave0_config.py` | Configuration and npm pin tests |
| `tests/test_aws_readiness.py` | Tool, redaction, account, service, and gate tests |
| `tests/test_aws_wave0_templates.py` | CloudFormation and workflow security tests |
| `tests/test_aws_identity_compatibility.py` | One-issuer decision tests |

Stable Wave 0 interfaces:

```python
def load_wave0_config(path: Path) -> Wave0Config:
    """Return one validated non-secret Wave 0 configuration."""

def build_readiness_report(
    config: Wave0Config,
    checks: tuple[CheckResult, ...] | list[CheckResult],
    *,
    checked_at: datetime | None = None,
) -> ReadinessReport:
    """Build the frozen report model from already sanitized checks."""

def decide_identity(probes: tuple[HostIdentityProbe, ...]) -> IdentityDecision:
    """Return the only issuer strategy allowed by complete host evidence."""

def record_host_probe(
    contract: IdentityHostContract,
    probe: HostIdentityProbe,
    evidence_path: Path,
    output_dir: Path,
) -> Path:
    """Hash local evidence and persist only its closed sanitized record."""

def finalize_wave0_gate(
    report: ReadinessReport,
    identity_decision: IdentityDecision | None,
    oidc_references: tuple[OidcRunReference, ...],
    runner: CommandRunner = run_command,
    *,
    identity_proof_references: tuple[IdentityProofReference, ...] = (),
) -> Wave0GateFinalization:
    """Independently verify every Wave 0 proof in fail-closed order."""
```

### Task 1: Lock the Toolchain and Configuration Contract

**Files:**

- Create: `package.json`
- Create: `package-lock.json`
- Create: `infra/aws/wave0/environment.yaml`
- Create: `src/mercury_tools/aws/__init__.py`
- Create: `src/mercury_tools/aws/models.py`
- Create: `src/mercury_tools/aws/config.py`
- Create: `tests/test_aws_wave0_config.py`

**Interfaces:**

- Consumes: Existing Python floor `>=3.11,<3.14`, Pydantic 2.13.4, PyYAML 6.0.3, and credential-safe validators in `mercury_tools.catalog.identity`.
- Produces: `EnvironmentName`, `ServiceProbeId`, `GateStatus`, `CheckState`, `CheckResult`, `Wave0Config`, `ReadinessReport`, and `load_wave0_config(path: Path) -> Wave0Config`.

- [x] **Step 1: Write failing configuration tests**

Create `tests/test_aws_wave0_config.py` with these assertions:

```python
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.models import GateStatus

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "infra/aws/wave0/environment.yaml"


def test_config_locks_region_accounts_and_repository() -> None:
    config = load_wave0_config(CONFIG_PATH)
    assert config.primary_region == "ap-southeast-1"
    assert tuple(item.alias for item in config.accounts) == ("mercury-nonprod",)
    assert tuple(item.github_environment for item in config.accounts) == ("nonprod",)
    assert config.github_repository == "natthaphonchop2-creator/mercury-tools"


def test_config_rejects_non_singapore_region(tmp_path: Path) -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["primary_region"] = "us-east-1"
    path = tmp_path / "environment.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_wave0_config(path)


def test_config_rejects_multiple_wave_zero_accounts(tmp_path: Path) -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["accounts"].append(raw["accounts"][0])
    path = tmp_path / "environment.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_wave0_config(path)


def test_gate_status_values_are_stable() -> None:
    assert {item.value for item in GateStatus} == {
        "ready", "blocked_tooling", "blocked_account_access",
        "blocked_region_service", "blocked_identity_compatibility",
    }


def test_node_tools_are_exactly_pinned() -> None:
    package = __import__("json").loads((ROOT / "package.json").read_text())
    assert package["private"] is True
    assert package["devDependencies"] == {
        "@aws/agentcore": "0.25.0",
        "aws-cdk": "2.1134.0",
    }
```

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/test_aws_wave0_config.py -q`

Expected: collection fails because `mercury_tools.aws` and Wave 0 files do not
exist.

- [x] **Step 3: Add the deterministic Node toolchain**

Create `package.json`:

```json
{
  "name": "mercury-tools-aws-tooling",
  "private": true,
  "engines": {"node": ">=20"},
  "devDependencies": {
    "@aws/agentcore": "0.25.0",
    "aws-cdk": "2.1134.0"
  }
}
```

Run:

```bash
npm install --package-lock-only --ignore-scripts
npm ci --ignore-scripts
npx --no-install agentcore --version
npx --no-install cdk --version
```

Expected: versions `0.25.0` and `2.1134.0`.

- [x] **Step 4: Implement closed models and configuration**

Use frozen `extra="forbid"` Pydantic models. Define exact enum values:

```python
class EnvironmentName(StrEnum):
    NONPROD = "nonprod"


class GateStatus(StrEnum):
    READY = "ready"
    BLOCKED_TOOLING = "blocked_tooling"
    BLOCKED_ACCOUNT_ACCESS = "blocked_account_access"
    BLOCKED_REGION_SERVICE = "blocked_region_service"
    BLOCKED_IDENTITY_COMPATIBILITY = "blocked_identity_compatibility"


class CheckState(StrEnum):
    PASS = "pass"
    BLOCKED = "blocked"
    FAIL = "fail"
```

`ServiceProbeId` has exact values `agentcore_runtime`, `agentcore_gateway`,
`agentcore_identity`, `bedrock_knowledge_bases`, `aurora_postgresql`, `s3`,
`kms`, `ecr`, `cloudwatch_logs`, and `agentcore_quotas`.

`Wave0Config` locks schema `mercury.aws.wave0.config.v1`, Region
`ap-southeast-1`, repository `natthaphonchop2-creator/mercury-tools`, exactly
one nonprod account target, and all ten probe IDs. `CheckResult` permits only a stable
name, state, code, summary up to 240 characters, and scalar details with safe
keys. Validate summaries/details with `validate_credential_safe` and
`validate_credential_safe_paths`.

Create `environment.yaml`:

```yaml
schema_version: mercury.aws.wave0.config.v1
primary_region: ap-southeast-1
github_repository: natthaphonchop2-creator/mercury-tools
accounts:
  - environment: nonprod
    alias: mercury-nonprod
    profile: mercury-nonprod
    github_environment: nonprod
required_service_probes:
  - agentcore_runtime
  - agentcore_gateway
  - agentcore_identity
  - bedrock_knowledge_bases
  - aurora_postgresql
  - s3
  - kms
  - ecr
  - cloudwatch_logs
  - agentcore_quotas
```

`load_wave0_config` uses `yaml.safe_load`, rejects a non-mapping document, and
returns `Wave0Config.model_validate(raw)`.

- [x] **Step 5: Verify GREEN and commit**

```bash
uv run pytest tests/test_aws_wave0_config.py -q
uv run ruff check src/mercury_tools/aws tests/test_aws_wave0_config.py
git diff --check
git add package.json package-lock.json infra/aws/wave0/environment.yaml \
  src/mercury_tools/aws tests/test_aws_wave0_config.py
git commit -m "feat: define AWS wave zero contracts"
```

Expected: tests pass, lint and whitespace checks are clean, and only named
files are committed.

### Task 2: Build Secret-Safe Local and Live Readiness Checks

**Files:**

- Create: `src/mercury_tools/aws/commands.py`
- Create: `src/mercury_tools/aws/readiness.py`
- Create: `scripts/check_aws_readiness.py`
- Create: `tests/test_aws_readiness.py`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: Wave 0 models/config from Task 1 and `redact_text(value: str) -> str` from `mercury_tools.safety.redaction`.
- Produces: `CommandResult`, `CommandRunner`, `run_command`, `check_local_toolchain`, `check_aws_accounts`, `check_region_services`, `aggregate_gate`, `build_readiness_report`, and `write_readiness_report`.

- [x] **Step 1: Write failing command, account, and gate tests**

Create `tests/test_aws_readiness.py` with a callable fake runner and these
cases:

```python
def test_runner_rejects_shell_and_unknown_programs() -> None:
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(("sh", "-c", "env"))
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(("curl", "https://example.com"))


def test_pinned_local_toolchain_passes() -> None:
    runner = FakeRunner.for_tool_versions(
        aws="aws-cli/2.36.14",
        node="v22.22.2",
        python="Python 3.11.15",
        agentcore="0.25.0",
        cdk="2.1134.0",
    )
    assert all(item.state == "pass" for item in check_local_toolchain(runner))


def test_missing_agentcore_blocks_tooling() -> None:
    runner = FakeRunner.with_failed_command(
        ("npx", "--no-install", "agentcore", "--version")
    )
    assert aggregate_gate(check_local_toolchain(runner)) == "blocked_tooling"


def test_account_ids_are_fingerprinted() -> None:
    result = fingerprint_account_id("123456789012")
    assert len(result) == 12
    assert result != "123456789012"


def test_unavailable_nonprod_profile_blocks_account_access() -> None:
    config = load_wave0_config(CONFIG_PATH)
    runner = FakeRunner.with_failed_command(
        ("aws", "sts", "get-caller-identity")
    )
    checks = check_aws_accounts(config, runner)
    assert any(item.code == "aws_account_access_blocked" for item in checks)


def test_failed_required_service_blocks_region() -> None:
    config = load_wave0_config(CONFIG_PATH)
    checks = check_region_services(
        config,
        FakeRunner.for_services(failed="agentcore_gateway"),
    )
    assert aggregate_gate(checks) == "blocked_region_service"


def test_report_contains_no_raw_account_or_secret() -> None:
    report = build_report_fixture()
    payload = report.model_dump_json()
    assert "123456789012" not in payload
    assert "AKIA" not in payload
    assert "secret_access_key" not in payload.lower()
```

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/test_aws_readiness.py -q`

Expected: imports fail because command and readiness modules do not exist.

- [x] **Step 3: Implement the bounded command runner**

Use this exact public contract:

```python
@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[tuple[str, ...], int], CommandResult]
```

`run_command(argv, timeout_seconds=20)` allows executable names `aws`, `node`,
`npm`, `npx`, `uv`, and `gh` only. It calls `subprocess.run` with `shell=False`,
`check=False`, `capture_output=True`, `text=True`, and a bounded environment
containing only `PATH`, `HOME`, `AWS_PROFILE`, `AWS_REGION`,
`AWS_DEFAULT_REGION`, `AWS_CONFIG_FILE`, and `AWS_SHARED_CREDENTIALS_FILE` when
present. Truncate each output stream to 4,096 characters and apply
`redact_text`. Represent timeout as return code 124 and missing executable as
127 without including raw exception data.

- [x] **Step 4: Implement tool and AWS probes**

Local commands and versions are exact:

```python
TOOL_COMMANDS = {
    "aws_cli": ("aws", "--version"),
    "node": ("node", "--version"),
    "python": ("uv", "run", "python", "--version"),
    "agentcore_cli": ("npx", "--no-install", "agentcore", "--version"),
    "aws_cdk": ("npx", "--no-install", "cdk", "--version"),
}
```

Require AWS CLI `>=2.36.14`, Node `>=20`, Python `>=3.11,<3.14`, AgentCore
`0.25.0`, and CDK `2.1134.0`.

For the nonprod account profile run:

```text
aws sts get-caller-identity --profile PROFILE --region ap-southeast-1 --output json --no-cli-pager
```

Before STS, inspect only AWS profile key names. Reject environment credential
overrides and static credential keys in either AWS config file, and require
exactly one approved temporary source: `login_session` or `sso_session`. Parse
only `Account` from STS, require 12 digits, and report only
`sha256(account_id)[:12]` plus the non-secret credential-source kind.

Define exact service command suffixes:

```python
SERVICE_COMMANDS = {
    "agentcore_runtime": ("bedrock-agentcore-control", "list-agent-runtimes", "--max-results", "1"),
    "agentcore_gateway": ("bedrock-agentcore-control", "list-gateways", "--max-results", "1"),
    "agentcore_identity": ("bedrock-agentcore-control", "list-workload-identities", "--max-results", "1"),
    "bedrock_knowledge_bases": ("bedrock-agent", "list-knowledge-bases", "--max-results", "1"),
    "aurora_postgresql": (
        "rds", "describe-orderable-db-instance-options", "--engine", "aurora-postgresql",
        "--db-instance-class", "db.serverless", "--max-records", "20", "--query",
        "{OrderableDBInstanceOptions: OrderableDBInstanceOptions[0:1].{Engine:Engine,DBInstanceClass:DBInstanceClass}}"
    ),
    "s3": ("s3api", "list-buckets"),
    "kms": ("kms", "list-aliases", "--limit", "1"),
    "ecr": ("ecr", "describe-repositories", "--max-results", "1"),
    "cloudwatch_logs": ("logs", "describe-log-groups", "--limit", "1"),
    "agentcore_quotas": (
        "service-quotas", "list-service-quotas", "--service-code",
        "bedrock-agentcore", "--max-results", "20", "--query",
        "{Quotas: Quotas[].{QuotaCode:QuotaCode,Value:Value}}"
    ),
}
```

Prefix with `aws`; append profile, Region, JSON output, and no pager. A probe
passes only on exit 0 with valid JSON. Access denial, unavailable endpoint,
invalid quota service, throttling after three bounded attempts, or a missing
exact Aurora Serverless option blocks the Region gate. AgentCore quota evidence
must include positive values for Runtime endpoint listing (`L-AB3B12EE`),
Identity workload listing (`L-DEAB43C2`), and Gateway inline schema size
(`L-55F87EC2`); zero, duplicate, malformed, or missing required quota records
block the gate. Do not reinterpret a blocked response as a pass.

Gate precedence is tooling, account access, Region/service, identity, then
ready. `ReadinessReport` uses schema `mercury.aws.wave0.report.v1`.

- [x] **Step 5: Add the CLI and ignored machine evidence**

Append to `.gitignore`:

```gitignore
.artifacts/aws/
agentcore/.env.local
agentcore/.cli/
```

`scripts/check_aws_readiness.py` accepts:

```text
--config infra/aws/wave0/environment.yaml
--output .artifacts/aws/wave0/readiness.json
--skip-live
```

Default behavior runs local and live checks for the nonprod profile. `--skip-live`
emits `blocked_account_access` and exists only for offline serialization tests.
Exit codes are `0=ready`, `2=blocked`, `3=invalid or unsafe input`. Write JSON
atomically with file mode `0o600`; reject output outside
`.artifacts/aws/wave0/`.

- [x] **Step 6: Verify GREEN, exercise the known blocker, and commit**

```bash
uv run pytest tests/test_aws_wave0_config.py tests/test_aws_readiness.py -q
uv run ruff check src/mercury_tools/aws scripts/check_aws_readiness.py \
  tests/test_aws_wave0_config.py tests/test_aws_readiness.py
uv run python scripts/check_aws_readiness.py --skip-live
uv run python scripts/check_aws_readiness.py
git diff --check
git add .gitignore src/mercury_tools/aws/commands.py \
  src/mercury_tools/aws/readiness.py scripts/check_aws_readiness.py \
  tests/test_aws_readiness.py
git commit -m "feat: add secret-safe AWS readiness probes"
```

Expected before AWS restoration: tests/lint pass and live CLI exits 2 with
`blocked_account_access`, without account IDs or credentials. Expected after
restoration: live CLI exits 0 only when the nonprod profile and every service
probe pass.

### Task 3: Prove Nonprod Access and GitHub OIDC

**Files:**

- Create: `infra/aws/wave0/github-oidc-role.yaml`
- Create: `.github/workflows/aws-wave0-oidc-smoke.yml`
- Create: `tests/test_aws_wave0_templates.py`
- Create: `docs/runbooks/aws-wave0-bootstrap.md`

**Interfaces:**

- Consumes: The nonprod account profile, Region, probe list, and GitHub repository from Tasks 1-2.
- Produces: one `MercuryWave0GithubOidcRoleArn` output in nonprod and one successful nonprod OIDC smoke job.

- [x] **Step 1: Write failing template and workflow tests**

Tests parse YAML and assert:

```python
def test_trust_is_exact_repository_environment_and_audience() -> None:
    trust = oidc_role_trust(load_template())
    assert trust["Condition"]["StringEquals"][
        "token.actions.githubusercontent.com:aud"
    ] == "sts.amazonaws.com"
    subject = dump(trust["Condition"]["StringEquals"][
        "token.actions.githubusercontent.com:sub"
    ])
    assert "natthaphonchop2-creator/mercury-tools" in subject
    assert "GitHubEnvironment" in subject
    assert "*" not in subject


def test_smoke_role_is_read_only() -> None:
    actions = oidc_role_actions(load_template())
    assert "bedrock-agentcore:ListAgentRuntimes" in actions
    assert "servicequotas:ListServiceQuotas" in actions
    assert not any(action.endswith(":*") for action in actions)
    assert not any(
        verb in action.lower()
        for action in actions
        for verb in ("create", "update", "delete", "put", "invoke")
    )


def test_workflow_is_manual_and_uses_oidc_only() -> None:
    workflow = load_workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}
    serialized = dump(workflow)
    assert "00943011d9042930efac3dcd3a170e4273319bc8" in serialized
    assert "AWS_ACCESS_KEY_ID" not in serialized
    assert "AWS_SECRET_ACCESS_KEY" not in serialized
    assert "mask-aws-account-id: true" in serialized
```

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/test_aws_wave0_templates.py -q`

Expected: failures because template/workflow files are absent.

- [x] **Step 3: Implement the read-only OIDC role and workflow**

The CloudFormation template accepts `GitHubOidcProviderArn` with an exact IAM
OIDC provider ARN pattern and `GitHubEnvironment` fixed to `nonprod`. The role
trust conditions are:

```yaml
StringEquals:
  token.actions.githubusercontent.com:aud: sts.amazonaws.com
  token.actions.githubusercontent.com:sub:
    Fn::Sub: repo:natthaphonchop2-creator/mercury-tools:environment:${GitHubEnvironment}
```

The inline policy permits only STS identity plus the list/describe calls from
Task 2. Output the role ARN as `MercuryWave0GithubOidcRoleArn`.

The manual workflow uses the `nonprod` GitHub environment variable
`AWS_WAVE0_ROLE_ARN`, Region `ap-southeast-1`, and this
immutable action:

```yaml
- uses: aws-actions/configure-aws-credentials@00943011d9042930efac3dcd3a170e4273319bc8
  with:
    role-to-assume: ${{ vars.AWS_WAVE0_ROLE_ARN }}
    aws-region: ap-southeast-1
    mask-aws-account-id: true
```

Run all required probes with output redirected to temporary files; print only
the environment and `wave0_oidc_smoke=pass`.

- [x] **Step 4: Write the bootstrap runbook**

The runbook gives exact owner actions:

1. restore the AWS management account and verify billing/account status;
2. bind the current authenticated account as `mercury-nonprod`, keeping its
   account email out of Git/chat; do not create or bind `mercury-prod`;
3. assign short-lived IAM Identity Center access and run:

```bash
aws configure sso --profile mercury-nonprod
aws sso login --profile mercury-nonprod
```

4. create the GitHub OIDC provider in nonprod when absent:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --profile mercury-nonprod
```

5. deploy `github-oidc-role.yaml`, set its output ARN with
   `gh variable set AWS_WAVE0_ROLE_ARN --env nonprod`, and run the nonprod
   smoke workflow.

State that an existing provider is reused and no AWS credential-file value is
copied into GitHub.

- [x] **Step 5: Verify GREEN, run OIDC proof, and commit**

```bash
uv run pytest tests/test_aws_wave0_templates.py -q
uv run ruff check tests/test_aws_wave0_templates.py
git diff --check
gh workflow run aws-wave0-oidc-smoke.yml
gh run watch --exit-status
git add infra/aws/wave0/github-oidc-role.yaml \
  .github/workflows/aws-wave0-oidc-smoke.yml \
  tests/test_aws_wave0_templates.py docs/runbooks/aws-wave0-bootstrap.md
git commit -m "feat: prove AWS access through GitHub OIDC"
```

Expected: local checks pass and the nonprod job succeeds. If account access,
OIDC assumption, or a required read-only probe fails, commit sanitized blocked
evidence and leave the live step unchecked.

### Task 4: Decide One Identity Strategy for All Required Hosts

**Files:**

- Create: `infra/aws/wave0/cognito-compatibility-spike.yaml`
- Create: `infra/aws/wave0/identity-host-contract.yaml`
- Create: `infra/aws/wave0/identity-decision.yaml` after live proof
- Create: `src/mercury_tools/aws/identity.py`
- Create: `scripts/record_identity_probe.py`
- Create: `tests/test_aws_identity_compatibility.py`
- Create: `docs/runbooks/aws-wave0-identity-compatibility.md`

**Interfaces:**

- Consumes: Nonprod short-lived AWS access and host set `codex`, `chatgpt`, `claude`.
- Produces: `HostName`, `RegistrationMode`, `ProbeResult`, `IdentityMode`, `IdentityHostContract`, `HostIdentityProbe`, `IdentityDecision`, `record_host_probe(contract: IdentityHostContract, probe: HostIdentityProbe, evidence_path: Path, output_dir: Path) -> Path`, and `decide_identity(probes: tuple[HostIdentityProbe, ...]) -> IdentityDecision`.

- [x] **Step 1: Write failing one-issuer tests**

Create tests for these exact rules:

```python
def test_all_pre_registered_hosts_select_cognito() -> None:
    probes = tuple(
        passing_probe(host, mode="pre_registered", issuer_origin="cognito")
        for host in ("codex", "chatgpt", "claude")
    )
    decision = decide_identity(probes)
    assert decision.mode == "cognito_pre_registered"
    assert decision.issuer_kind == "cognito"


def test_pre_registered_failure_requires_one_external_dcr_issuer() -> None:
    probes = (
        failing_probe("codex", mode="pre_registered", issuer_origin="cognito"),
        passing_probe("codex", mode="dcr", issuer_origin="https://identity.mercury.example"),
        passing_probe("chatgpt", mode="dcr", issuer_origin="https://identity.mercury.example"),
        passing_probe("claude", mode="dcr", issuer_origin="https://identity.mercury.example"),
    )
    decision = decide_identity(probes)
    assert decision.mode == "external_oidc_dcr"
    assert decision.issuer_origin == "https://identity.mercury.example"


def test_mixed_external_issuers_are_rejected() -> None:
    probes = (
        passing_probe("codex", mode="dcr", issuer_origin="https://issuer-a.example"),
        passing_probe("chatgpt", mode="dcr", issuer_origin="https://issuer-b.example"),
        passing_probe("claude", mode="dcr", issuer_origin="https://issuer-a.example"),
    )
    with pytest.raises(ValueError, match="identity_issuer_not_shared"):
        decide_identity(probes)


def test_missing_host_blocks_decision() -> None:
    probes = (
        passing_probe("codex", mode="pre_registered", issuer_origin="cognito"),
        passing_probe("chatgpt", mode="pre_registered", issuer_origin="cognito"),
    )
    with pytest.raises(ValueError, match="identity_required_host_missing"):
        decide_identity(probes)


def test_recorder_hashes_evidence_without_copying_bytes(tmp_path: Path) -> None:
    evidence = tmp_path / "codex.json"
    evidence.write_text('{"result":"authorized"}', encoding="utf-8")
    output = record_host_probe(contract(), passing_probe("codex"), evidence)
    serialized = output.read_text(encoding="utf-8")
    assert "authorized" not in serialized
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() in serialized
```

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/test_aws_identity_compatibility.py -q`

Expected: imports fail because identity contracts are absent.

- [x] **Step 3: Implement host evidence and decision contracts**

Exact enum values:

```python
class HostName(StrEnum):
    CODEX = "codex"
    CHATGPT = "chatgpt"
    CLAUDE = "claude"


class RegistrationMode(StrEnum):
    PRE_REGISTERED = "pre_registered"
    DCR = "dcr"


class ProbeResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class IdentityMode(StrEnum):
    COGNITO_PRE_REGISTERED = "cognito_pre_registered"
    EXTERNAL_OIDC_DCR = "external_oidc_dcr"
```

`HostIdentityProbe` requires host, registration mode, result, issuer origin,
PKCE method exactly `S256`, checked timestamp, and SHA-256 of local evidence.
Reject raw tokens, authorization codes, cookies, query strings, fragments,
userinfo, localhost origins, and non-HTTPS external origins.

`decide_identity` first selects Cognito only when all three pre-registered
probes pass. Otherwise it requires all three DCR probes to pass against one
identical external HTTPS origin. Missing evidence, mixed issuers, mixed modes,
or a failed required probe raises a stable `identity_*` error.

- [x] **Step 4: Add the host contract and disposable Cognito stack**

`identity-host-contract.yaml` requires all three hosts, authorization-code
flow, PKCE `S256`, refresh-token rotation expectation, and audience/resource
binding. It contains no client IDs, callback URLs, users, or secrets.

`cognito-compatibility-spike.yaml` creates in nonprod only:

- user pool `mercury-wave0-identity-spike`, self-sign-up disabled and deletion
  protection inactive;
- domain prefix `Fn::Sub: mercury-wave0-${AWS::AccountId}`;
- public clients `mercury-wave0-codex`, `mercury-wave0-chatgpt`, and
  `mercury-wave0-claude`;
- `GenerateSecret: false`, authorization-code only, scopes
  `openid email profile`, provider `COGNITO`, token revocation enabled, and
  callback URL list parameters provided at deploy time;
- outputs for issuer origin, public endpoints, and client IDs.

The stack must not create AgentCore, customer, provider, or business resources.

- [x] **Step 5: Implement the evidence CLI and runbook**

CLI surface:

```text
record --host {codex,chatgpt,claude} --mode {pre_registered,dcr}
       --result {pass,fail} --issuer-origin cognito-or-validated-https-origin
       --evidence-file local-evidence-path
decide --probe-dir .artifacts/aws/wave0/identity
       --output infra/aws/wave0/identity-decision.yaml
```

`record` hashes the local evidence and writes only closed fields under the
ignored probe directory. `decide` writes the committed decision only when the
complete probe set satisfies one identity mode.

The runbook instructs the owner to obtain each callback URL from the host,
deploy the Cognito spike, configure each public client, complete code plus PKCE,
capture local evidence, record each result, test one DCR-capable issuer across
all hosts if any pre-registered flow fails, generate the decision, and delete
the spike. A documentation claim alone is not passing evidence; mixed issuers
are forbidden.

- [ ] **Step 6: Verify GREEN, generate live decision, clean up, and commit**

```bash
uv run pytest tests/test_aws_identity_compatibility.py \
  tests/test_aws_wave0_templates.py -q
uv run ruff check src/mercury_tools/aws/identity.py \
  scripts/record_identity_probe.py tests/test_aws_identity_compatibility.py
uv run python scripts/record_identity_probe.py decide \
  --probe-dir .artifacts/aws/wave0/identity \
  --output infra/aws/wave0/identity-decision.yaml
aws cloudformation delete-stack --stack-name mercury-wave0-identity-spike \
  --profile mercury-nonprod --region ap-southeast-1
aws cloudformation wait stack-delete-complete \
  --stack-name mercury-wave0-identity-spike \
  --profile mercury-nonprod --region ap-southeast-1
git diff --check
git add infra/aws/wave0/cognito-compatibility-spike.yaml \
  infra/aws/wave0/identity-host-contract.yaml \
  infra/aws/wave0/identity-decision.yaml src/mercury_tools/aws/identity.py \
  scripts/record_identity_probe.py tests/test_aws_identity_compatibility.py \
  docs/runbooks/aws-wave0-identity-compatibility.md
git commit -m "feat: decide Mercury host identity compatibility"
```

Expected: decision names one strategy covering all three hosts and the spike
is deleted. Incomplete proof exits 2 with a stable `identity_*` code and leaves
Wave 0 blocked.

### Task 5: Assemble the Wave 0 Gate and Stop

**Files:**

- Modify: `src/mercury_tools/aws/readiness.py`
- Modify: `scripts/check_aws_readiness.py`
- Modify: `tests/test_aws_readiness.py`
- Create: `docs/superpowers/evidence/wave-0-aws-readiness.md`
- Modify: `docs/superpowers/plans/2026-08-01-mercury-v1-aws-primary-wave-index.md`

**Interfaces:**

- Consumes: Readiness report, one successful nonprod OIDC run URL, and the validated identity decision.
- Produces: closed run/job/workflow/account-bound `OidcRunEvidence`,
  `Wave0GateFinalization(gate_status, oidc_evidence)`, and one sanitized
  owner-review record. The public finalizer accepts untrusted
  `OidcRunReference` values plus a bounded runner and independently constructs
  evidence; callers cannot submit `OidcRunEvidence` directly.

- [x] **Step 1: Write failing final-gate tests**

```python
def test_gate_requires_ready_nonprod_account() -> None:
    report = replace_check(
        build_report_fixture(),
        "nonprod_account",
        details={"account_fingerprint": "a" * 11},
    )
    assert finalize_status(report, valid_identity()) == "blocked_account_access"


def test_gate_requires_every_service_and_quota_probe() -> None:
    report = build_report_fixture()
    report = report.model_copy(
        update={
            "checks": tuple(
                item
                for item in report.checks
                if item.name != "nonprod_agentcore_quotas"
            )
        }
    )
    assert finalize_status(report, valid_identity()) == "blocked_region_service"


def test_gate_requires_identity_and_nonprod_oidc_job() -> None:
    report = build_report_fixture()
    assert finalize_status(report, None) == "blocked_identity_compatibility"
    assert finalize_status(report, valid_identity(), ()) == "blocked_account_access"


def test_gate_is_ready_only_with_complete_proof(tmp_path: Path) -> None:
    references = valid_oidc_references()
    runner = VerifiedGhRunner.for_references(references)
    result = finalize_wave0_gate(
        build_report_fixture(),
        valid_identity(),
        references,
        runner,
        identity_proof_references=valid_identity_proof(tmp_path),
    )
    assert result.gate_status == "ready"
```

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/test_aws_readiness.py -q`

Expected: failures for undefined final-gate behavior.

- [x] **Step 3: Implement the independent final gate**

The gate revalidates schema versions, Region, the `mercury-nonprod` alias and
account fingerprint, all ten probes in nonprod, pinned tools, the nonprod OIDC
GitHub environment, all three host identity results, and credential-safe fields.

Define `OidcRunReference` as the caller input containing only
`environment: EnvironmentName` and `run_url: AnyHttpUrl`. Define the internally
produced `OidcRunEvidence` as a frozen, extra-forbidden extension containing
positive `run_id`, `run_attempt`, `workflow_id`, and `job_id`; exact
`head_sha`, pinned `workflow_sha256`, sanitized `account_fingerprint`,
`account_proof_sha256`, and canonical `evidence_sha256`. Validate that the
environment is `nonprod`, the URL is the exact HTTPS GitHub Actions run path,
and every evidence field is independently derived from the selected run,
verified job, pinned workflow source, and closed downloaded proof artifact.

Add CLI arguments:

```text
--identity-decision infra/aws/wave0/identity-decision.yaml
--identity-proof HOST=PROBE_PATH,RAW_EVIDENCE_PATH
--oidc-run nonprod=URL
```

Store only SHA-256 hashes of identity and OIDC evidence in machine output. A
ready result requires exactly one HTTPS nonprod GitHub Actions run URL.

- [x] **Step 4: Run the complete Wave 0 verification matrix**

```bash
npm ci --ignore-scripts
npx --no-install agentcore --version
npx --no-install cdk --version
uv sync --extra dev
uv run pytest tests/test_aws_wave0_config.py tests/test_aws_readiness.py \
  tests/test_aws_wave0_templates.py tests/test_aws_identity_compatibility.py -q
uv run ruff check src/mercury_tools/aws scripts/check_aws_readiness.py \
  scripts/record_identity_probe.py tests/test_aws_wave0_config.py \
  tests/test_aws_readiness.py tests/test_aws_wave0_templates.py \
  tests/test_aws_identity_compatibility.py
uv run python scripts/check_aws_readiness.py \
  --identity-decision infra/aws/wave0/identity-decision.yaml \
  --identity-proof "codex=${CODEX_PROBE_RECORD:?},${CODEX_RAW_EVIDENCE:?}" \
  --identity-proof "chatgpt=${CHATGPT_PROBE_RECORD:?},${CHATGPT_RAW_EVIDENCE:?}" \
  --identity-proof "claude=${CLAUDE_PROBE_RECORD:?},${CLAUDE_RAW_EVIDENCE:?}" \
  --oidc-run "nonprod=${MERCURY_NONPROD_OIDC_RUN_URL:?}" \
  --output .artifacts/aws/wave0/readiness.json
git diff --check
```

Expected: tests/lint pass; readiness exits 0 only for `ready`, otherwise exit 2
with one exact blocked category and no sensitive output.

- [x] **Step 5: Run scope and secret checks**

```bash
git status --short
git diff --cached --check
git grep -nE '(AKIA|ASIA)[A-Z0-9]{16}|aws_secret_access_key|aws_session_token|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' -- . ':!uv.lock' ':!package-lock.json'
git diff --name-only 9330e67...HEAD
```

Expected: no secret matches; changes are limited to Wave 0 paths; the three
pre-existing untracked RED tests remain unstaged and unchanged.

- [x] **Step 6: Write sanitized owner-review evidence**

`docs/superpowers/evidence/wave-0-aws-readiness.md` records final status, Task
commit SHAs, tool versions, Region, account aliases/fingerprints, per-probe
statuses, GitHub run URLs, identity mode/issuer kind/host results, evidence
hashes, Cognito stack deletion, verification commands, and residual risks. It
states explicitly that no Mercury runtime, customer data, provider credentials,
or Wave 1 infrastructure was created.

Mark the Wave Index Wave 0 checklist complete only for a ready gate. For a
blocked gate, commit the blocked evidence and keep both checkboxes unchecked.

- [x] **Step 7: Commit evidence and stop**

```bash
git add src/mercury_tools/aws/readiness.py scripts/check_aws_readiness.py \
  tests/test_aws_readiness.py docs/superpowers/evidence/wave-0-aws-readiness.md \
  docs/superpowers/plans/2026-08-01-mercury-v1-aws-primary-wave-index.md
git commit -m "docs: record AWS wave zero readiness evidence"
```

Report the final gate, evidence path, commits, tests, and blocker if present.
Do not draft or execute Wave 1 until the owner explicitly approves Wave 0.

## Wave 0 Acceptance Checklist

- [x] AWS account suspension is resolved and the `mercury-nonprod` short-lived profile passes STS.
- [x] The authenticated account is recorded only as the sanitized `mercury-nonprod` fingerprint.
- [x] All required AgentCore, Bedrock, Aurora, S3, KMS, ECR, CloudWatch, and quota probes pass in `ap-southeast-1` for nonprod.
- [x] GitHub OIDC assumes the read-only role in `nonprod` without long-lived AWS keys.
- [ ] Codex, ChatGPT, and Claude are covered by one identity mode and one issuer strategy.
- [x] The disposable Cognito compatibility stack is deleted or confirmed absent.
- [x] Machine evidence remains ignored and sanitized evidence is committed.
- [x] No customer/provider data, credentials, Mercury runtime, or Wave 1 infrastructure exists.
- [x] No production customer or provider data entered nonprod, and no production account was created or bound in Wave 0.
- [x] Focused tests, Ruff, whitespace checks, and secret scans pass.
- [ ] Owner reviews the evidence before Wave 1 planning begins.

## Primary References

- [AWS AgentCore CLI quickstart](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html)
- [AgentCore supported Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)
- [AgentCore endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock_agentcore.html)
- [AgentCore Runtime IAM permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html)
- [AWS IAM OIDC federation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-idp_oidc.html)
