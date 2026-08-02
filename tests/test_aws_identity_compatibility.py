import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mercury_tools.aws.identity import (
    HostIdentityProbe,
    HostName,
    IdentityHostContract,
    ProbeResult,
    decide_identity,
    record_host_probe,
)

ROOT = Path(__file__).resolve().parents[1]
SPIKE_PATH = ROOT / "infra/aws/wave0/cognito-compatibility-spike.yaml"
CONTRACT_PATH = ROOT / "infra/aws/wave0/identity-host-contract.yaml"
RUNBOOK_PATH = ROOT / "docs/runbooks/aws-wave0-identity-compatibility.md"
CLI_PATH = ROOT / "scripts/record_identity_probe.py"


def contract() -> IdentityHostContract:
    return IdentityHostContract(
        schema_version="mercury.aws.wave0.identity_host_contract.v1",
        required_hosts=(HostName.CODEX, HostName.CHATGPT, HostName.CLAUDE),
        authorization_flow="authorization_code",
        pkce_method="S256",
        refresh_token_rotation="required",
        audience_resource_binding="required",
    )


def passing_probe(
    host: str,
    *,
    mode: str = "pre_registered",
    issuer_origin: str = "cognito",
) -> HostIdentityProbe:
    return HostIdentityProbe(
        host=host,
        registration_mode=mode,
        result=ProbeResult.PASS,
        issuer_origin=issuer_origin,
        pkce_method="S256",
        checked_at=datetime(2026, 8, 2, tzinfo=UTC),
        evidence_sha256="a" * 64,
    )


def failing_probe(
    host: str,
    *,
    mode: str = "pre_registered",
    issuer_origin: str = "cognito",
) -> HostIdentityProbe:
    return passing_probe(
        host,
        mode=mode,
        issuer_origin=issuer_origin,
    ).model_copy(update={"result": ProbeResult.FAIL})


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_all_pre_registered_hosts_select_cognito() -> None:
    probes = tuple(
        passing_probe(host, mode="pre_registered", issuer_origin="cognito")
        for host in ("codex", "chatgpt", "claude")
    )

    decision = decide_identity(probes)

    assert decision.mode == "cognito_pre_registered"
    assert decision.issuer_kind == "cognito"
    assert decision.issuer_origin == "cognito"


def test_pre_registered_failure_requires_one_external_dcr_issuer() -> None:
    probes = (
        failing_probe("codex", mode="pre_registered", issuer_origin="cognito"),
        passing_probe(
            "codex", mode="dcr", issuer_origin="https://identity.mercury.example"
        ),
        passing_probe(
            "chatgpt", mode="dcr", issuer_origin="https://identity.mercury.example"
        ),
        passing_probe(
            "claude", mode="dcr", issuer_origin="https://identity.mercury.example"
        ),
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


def test_failed_dcr_probe_blocks_decision() -> None:
    probes = (
        passing_probe("codex", mode="dcr", issuer_origin="https://identity.mercury.example"),
        failing_probe("chatgpt", mode="dcr", issuer_origin="https://identity.mercury.example"),
        passing_probe("claude", mode="dcr", issuer_origin="https://identity.mercury.example"),
    )

    with pytest.raises(ValueError, match="identity_required_probe_failed"):
        decide_identity(probes)


@pytest.mark.parametrize(
    "issuer_origin",
    (
        "http://identity.mercury.example",
        "https://localhost",
        "https://identity.mercury.example?code=unsafe",
        "https://identity.mercury.example#access_token=unsafe",
        "https://user:password@identity.mercury.example",
    ),
)
def test_probe_rejects_unsafe_issuer_origins(issuer_origin: str) -> None:
    with pytest.raises(
        ValidationError, match="identity_(issuer_origin_invalid|probe_unsafe)"
    ):
        passing_probe("codex", mode="dcr", issuer_origin=issuer_origin)


def test_probe_rejects_wrong_pkce_or_raw_sensitive_fields() -> None:
    with pytest.raises(ValidationError, match="identity_pkce_method_invalid"):
        HostIdentityProbe(
            host="codex",
            registration_mode="pre_registered",
            result="pass",
            issuer_origin="cognito",
            pkce_method="plain",
            checked_at=datetime(2026, 8, 2, tzinfo=UTC),
            evidence_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="identity_probe_unsafe"):
        HostIdentityProbe(
            host="codex",
            registration_mode="pre_registered",
            result="pass",
            issuer_origin="cognito",
            pkce_method="S256",
            checked_at=datetime(2026, 8, 2, tzinfo=UTC),
            evidence_sha256="a" * 64,
            authorization_code="unsafe",
        )


def test_recorder_hashes_evidence_without_copying_bytes(tmp_path: Path) -> None:
    evidence = tmp_path / "codex.json"
    evidence.write_text('{"result":"authorized"}', encoding="utf-8")

    output = record_host_probe(
        contract(),
        passing_probe("codex"),
        evidence,
        tmp_path / "identity",
    )

    serialized = output.read_text(encoding="utf-8")
    assert "authorized" not in serialized
    assert str(evidence) not in serialized
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() in serialized
    assert json.loads(serialized) == {
        "checked_at": "2026-08-02T00:00:00Z",
        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "host": "codex",
        "issuer_origin": "cognito",
        "pkce_method": "S256",
        "registration_mode": "pre_registered",
        "result": "pass",
        "schema_version": "mercury.aws.wave0.identity_probe.v1",
    }


def test_host_contract_is_closed_and_requires_all_hosts() -> None:
    contract_yaml = load_yaml(CONTRACT_PATH)
    assert contract_yaml == {
        "schema_version": "mercury.aws.wave0.identity_host_contract.v1",
        "required_hosts": ["codex", "chatgpt", "claude"],
        "authorization_flow": "authorization_code",
        "pkce_method": "S256",
        "refresh_token_rotation": "required",
        "audience_resource_binding": "required",
    }
    assert "client" not in CONTRACT_PATH.read_text(encoding="utf-8").lower()
    assert "callback" not in CONTRACT_PATH.read_text(encoding="utf-8").lower()
    assert "secret" not in CONTRACT_PATH.read_text(encoding="utf-8").lower()


def test_cognito_spike_is_nonprod_public_client_only_and_disposable() -> None:
    template = load_yaml(SPIKE_PATH)
    assert template["Parameters"]["TargetEnvironment"] == {
        "Type": "String",
        "AllowedValues": ["nonprod"],
    }
    resources = template["Resources"]
    assert set(resources) == {
        "MercuryWave0IdentitySpikeUserPool",
        "MercuryWave0IdentitySpikeDomain",
        "MercuryWave0CodexClient",
        "MercuryWave0ChatGPTClient",
        "MercuryWave0ClaudeClient",
    }
    user_pool = resources["MercuryWave0IdentitySpikeUserPool"]["Properties"]
    assert user_pool["UserPoolName"] == "mercury-wave0-identity-spike"
    assert user_pool["AdminCreateUserConfig"] == {"AllowAdminCreateUserOnly": True}
    assert user_pool["DeletionProtection"] == "INACTIVE"
    assert resources["MercuryWave0IdentitySpikeDomain"]["Properties"]["Domain"] == {
        "Fn::Sub": "mercury-wave0-${AWS::AccountId}"
    }

    for host, resource_name in (
        ("Codex", "MercuryWave0CodexClient"),
        ("ChatGPT", "MercuryWave0ChatGPTClient"),
        ("Claude", "MercuryWave0ClaudeClient"),
    ):
        properties = resources[resource_name]["Properties"]
        assert properties["ClientName"] == f"mercury-wave0-{host.lower()}"
        assert properties["GenerateSecret"] is False
        assert properties["AllowedOAuthFlows"] == ["code"]
        assert properties["AllowedOAuthScopes"] == ["openid", "email", "profile"]
        assert properties["SupportedIdentityProviders"] == ["COGNITO"]
        assert properties["EnableTokenRevocation"] is True
        assert properties["CallbackURLs"] == {"Ref": f"{host}CallbackUrls"}

    resource_types = {item["Type"] for item in resources.values()}
    assert resource_types == {
        "AWS::Cognito::UserPool",
        "AWS::Cognito::UserPoolDomain",
        "AWS::Cognito::UserPoolClient",
    }
    assert set(template["Outputs"]) == {
        "IssuerOrigin",
        "AuthorizationEndpoint",
        "TokenEndpoint",
        "JwksUri",
        "CodexClientId",
        "ChatGPTClientId",
        "ClaudeClientId",
    }


def test_cli_incomplete_proof_exits_two_and_never_writes_decision(tmp_path: Path) -> None:
    probe_dir = tmp_path / "identity"
    decision_path = tmp_path / "identity-decision.yaml"
    probe_dir.mkdir()
    probe = passing_probe("codex")
    (probe_dir / "codex-pre_registered.json").write_text(
        probe.model_dump_json(), encoding="utf-8"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(CLI_PATH),
            "decide",
            "--probe-dir",
            str(probe_dir),
            "--output",
            str(decision_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout.strip() == "identity_required_host_missing"
    assert result.stderr == ""
    assert not decision_path.exists()


def test_runbook_requires_live_host_proof_and_cleanup_without_overclaiming() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    for required in (
        "codex",
        "chatgpt",
        "claude",
        "authorization code",
        "PKCE",
        "S256",
        "Dynamic Client Registration",
        "identity-decision.yaml",
        "delete-stack",
        "ap-southeast-1",
        "mercury-nonprod",
    ):
        assert required in runbook
    assert "not prove identity compatibility" in runbook
    assert "documentation claim alone is not" in " ".join(runbook.lower().split())
