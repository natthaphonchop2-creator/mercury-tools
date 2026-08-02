from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "infra/aws/wave0/github-oidc-role.yaml"
WORKFLOW_PATH = ROOT / ".github/workflows/aws-wave0-oidc-smoke.yml"
RUNBOOK_PATH = ROOT / "docs/runbooks/aws-wave0-bootstrap.md"

EXPECTED_ACTIONS = {
    "bedrock-agentcore:ListAgentRuntimes",
    "bedrock-agentcore:ListGateways",
    "bedrock-agentcore:ListWorkloadIdentities",
    "bedrock:ListKnowledgeBases",
    "ecr:DescribeRepositories",
    "kms:ListAliases",
    "logs:DescribeLogGroups",
    "rds:DescribeOrderableDBInstanceOptions",
    "s3:ListAllMyBuckets",
    "servicequotas:ListServiceQuotas",
    "sts:GetCallerIdentity",
}


def load_template() -> dict[str, Any]:
    return yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))


def load_workflow() -> dict[str, Any]:
    # BaseLoader preserves GitHub's `on` key instead of applying YAML 1.1 booleans.
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def oidc_role(template: dict[str, Any]) -> dict[str, Any]:
    return template["Resources"]["MercuryWave0GithubOidcRole"]["Properties"]


def oidc_role_trust(template: dict[str, Any]) -> dict[str, Any]:
    statements = oidc_role(template)["AssumeRolePolicyDocument"]["Statement"]
    assert len(statements) == 1
    return statements[0]


def oidc_role_actions(template: dict[str, Any]) -> set[str]:
    policies = oidc_role(template)["Policies"]
    assert len(policies) == 1
    statements = policies[0]["PolicyDocument"]["Statement"]
    assert len(statements) == 1
    return set(statements[0]["Action"])


def test_template_parameters_are_closed_to_github_provider_and_environments() -> None:
    parameters = load_template()["Parameters"]
    assert set(parameters) == {"GitHubOidcProviderArn", "GitHubEnvironment"}
    assert parameters["GitHubOidcProviderArn"] == {
        "Type": "String",
        "AllowedPattern": (
            r"^arn:aws:iam::[0-9]{12}:oidc-provider/"
            r"token\.actions\.githubusercontent\.com$"
        ),
    }
    assert parameters["GitHubEnvironment"] == {
        "Type": "String",
        "AllowedValues": ["nonprod", "production"],
    }


def test_trust_is_exact_repository_environment_and_audience() -> None:
    trust = oidc_role_trust(load_template())
    assert trust == {
        "Effect": "Allow",
        "Principal": {"Federated": {"Ref": "GitHubOidcProviderArn"}},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
            "StringEquals": {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                "token.actions.githubusercontent.com:sub": {
                    "Fn::Sub": (
                        "repo:natthaphonchop2-creator/mercury-tools:"
                        "environment:${GitHubEnvironment}"
                    )
                },
            }
        },
    }
    assert "*" not in yaml.safe_dump(trust)


def test_smoke_role_is_read_only_and_matches_probe_inventory() -> None:
    template = load_template()
    actions = oidc_role_actions(template)
    assert actions == EXPECTED_ACTIONS
    assert not any(action.endswith(":*") for action in actions)
    assert not any(
        verb in action.lower()
        for action in actions
        for verb in ("create", "update", "delete", "put", "invoke")
    )

    statement = oidc_role(template)["Policies"][0]["PolicyDocument"]["Statement"][0]
    assert statement["Effect"] == "Allow"
    assert statement["Resource"] == "*"
    assert set(load_template()["Resources"]) == {"MercuryWave0GithubOidcRole"}


def test_template_outputs_only_the_role_arn() -> None:
    assert load_template()["Outputs"] == {
        "MercuryWave0GithubOidcRoleArn": {
            "Value": {"Fn::GetAtt": ["MercuryWave0GithubOidcRole", "Arn"]}
        }
    }


def test_workflow_is_manual_and_uses_oidc_only() -> None:
    workflow = load_workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["on"]["workflow_dispatch"]["inputs"] == {
        "evidence_nonce": {
            "description": "Non-secret identifier used to select this exact workflow run",
            "required": "true",
            "type": "string",
        }
    }
    assert workflow["run-name"] == (
        "AWS Wave 0 OIDC smoke [${{ inputs.evidence_nonce }}]"
    )
    assert workflow["permissions"] == {"contents": "read", "id-token": "write"}

    serialized = WORKFLOW_PATH.read_text(encoding="utf-8")
    credentials_action_sha = "00943011d9042930efac3dcd3a170e4273319bc8"
    assert f"aws-actions/configure-aws-credentials@{credentials_action_sha}" in serialized
    assert "role-to-assume: ${{ vars.AWS_WAVE0_ROLE_ARN }}" in serialized
    assert "aws-region: ap-southeast-1" in serialized
    assert "mask-aws-account-id: true" in serialized
    assert "AWS_ACCESS_KEY_ID" not in serialized
    assert "AWS_SECRET_ACCESS_KEY" not in serialized
    assert "AWS_SESSION_TOKEN" not in serialized


def test_workflow_targets_both_protected_github_environments() -> None:
    workflow = load_workflow()
    jobs = workflow["jobs"]
    assert set(jobs) == {"oidc-smoke"}
    job = jobs["oidc-smoke"]
    assert job["environment"] == "${{ matrix.environment }}"
    assert job["strategy"]["matrix"]["environment"] == ["nonprod", "production"]
    assert job["runs-on"] == "ubuntu-latest"


def test_workflow_runs_every_probe_without_printing_probe_output() -> None:
    serialized = WORKFLOW_PATH.read_text(encoding="utf-8")
    required_commands = (
        "sts get-caller-identity",
        "bedrock-agentcore-control list-agent-runtimes",
        "bedrock-agentcore-control list-gateways",
        "bedrock-agentcore-control list-workload-identities",
        "bedrock-agent list-knowledge-bases",
        "rds describe-orderable-db-instance-options",
        "s3api list-buckets",
        "kms list-aliases",
        "ecr describe-repositories",
        "logs describe-log-groups",
        "service-quotas list-service-quotas",
    )
    assert all(command in serialized for command in required_commands)
    assert serialized.count("--region ap-southeast-1") == len(required_commands)
    assert serialized.count('>"${output_dir}/') == len(required_commands)
    assert serialized.count("2>&1") == len(required_commands)
    assert "cat " not in serialized
    assert 'printf \'environment=%s\\n\' "${{ matrix.environment }}"' in serialized
    assert "printf 'wave0_oidc_smoke=pass\\n'" in serialized


def test_bootstrap_runbook_preserves_secret_and_live_access_boundaries() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    for value in (
        "mercury-nonprod",
        "mercury-prod",
        "aws configure sso --profile mercury-nonprod",
        "aws configure sso --profile mercury-prod",
        "aws sso login --profile mercury-nonprod",
        "aws sso login --profile mercury-prod",
        "https://token.actions.githubusercontent.com",
        "--client-id-list sts.amazonaws.com",
        "gh variable set AWS_WAVE0_ROLE_ARN --env nonprod",
        "gh variable set AWS_WAVE0_ROLE_ARN --env production",
        "blocked_account_access",
    ):
        assert value in runbook
    assert "ap-southeast-1" in runbook
    assert "required reviewer" in runbook.lower()
    assert "reuse" in runbook.lower()
    assert "credential file" in runbook.lower()
    assert "long-lived" in runbook.lower()
    assert "live OIDC" in runbook
    assert "not proven" in runbook


def test_bootstrap_dispatches_two_exact_runs_and_binds_distinct_environment_urls() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    assert 'nonprod_nonce="wave0-nonprod-$(uv run python -c' in runbook
    assert 'production_nonce="wave0-production-$(uv run python -c' in runbook
    assert 'if [ "${nonprod_nonce}" = "${production_nonce}" ]; then' in runbook
    assert '-f evidence_nonce="${evidence_nonce}"' in runbook
    assert 'nonprod_run_id="$(dispatch_and_capture "${nonprod_nonce}")"' in runbook
    assert 'production_run_id="$(dispatch_and_capture "${production_nonce}")"' in runbook
    assert '--workflow "${workflow}"' in runbook
    assert "--event workflow_dispatch" in runbook
    assert '--branch "${workflow_ref}"' in runbook
    assert "--json databaseId,displayTitle" in runbook
    assert "select(.displayTitle == $title)" in runbook
    assert 'if [ "${match_count}" -gt 1 ]; then' in runbook
    assert 'if [ "${nonprod_run_id}" = "${production_run_id}" ]; then' in runbook
    assert 'gh run watch "${nonprod_run_id}" --exit-status' in runbook
    assert 'gh run watch "${production_run_id}" --exit-status' in runbook
    assert (
        '--oidc-run "nonprod=${nonprod_run_url}" \\\n'
        '  --oidc-run "production=${production_run_url}"'
    ) in runbook
    assert "gh run watch --exit-status" not in runbook


def test_bootstrap_describes_closed_probe_output_without_overclaiming_job_logs() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    normalized = " ".join(runbook.split())
    assert "Both matrix jobs must print only" not in normalized
    assert "Mercury probe step emits only" in normalized
    assert "pinned credentials action may emit masked status logs" in normalized
