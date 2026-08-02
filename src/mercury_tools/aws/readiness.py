"""Secret-safe local and live readiness probes for AWS Wave 0."""

from __future__ import annotations

import base64
import binascii
import configparser
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)

from mercury_tools.aws.commands import CommandResult, CommandRunner, run_command
from mercury_tools.aws.identity import (
    IdentityDecision,
    IdentityProofReference,
    verify_identity_proof,
)
from mercury_tools.aws.models import (
    WAVE0_ENVIRONMENTS,
    CheckResult,
    CheckState,
    EnvironmentName,
    GateStatus,
    ReadinessReport,
    Wave0Config,
)

TOOL_COMMANDS = {
    "aws_cli": ("aws", "--version"),
    "node": ("node", "--version"),
    "python": ("uv", "run", "python", "--version"),
    "agentcore_cli": ("npx", "--no-install", "agentcore", "--version"),
    "aws_cdk": ("npx", "--no-install", "cdk", "--version"),
}

SERVICE_COMMANDS = {
    "agentcore_runtime": (
        "bedrock-agentcore-control",
        "list-agent-runtimes",
        "--max-results",
        "1",
    ),
    "agentcore_gateway": (
        "bedrock-agentcore-control",
        "list-gateways",
        "--max-results",
        "1",
    ),
    "agentcore_identity": (
        "bedrock-agentcore-control",
        "list-workload-identities",
        "--max-results",
        "1",
    ),
    "bedrock_knowledge_bases": (
        "bedrock-agent",
        "list-knowledge-bases",
        "--max-results",
        "1",
    ),
    "aurora_postgresql": (
        "rds",
        "describe-orderable-db-instance-options",
        "--engine",
        "aurora-postgresql",
        "--db-instance-class",
        "db.serverless",
        "--max-records",
        "20",
        "--query",
        (
            "{OrderableDBInstanceOptions: "
            "OrderableDBInstanceOptions[0:1]."
            "{Engine:Engine,DBInstanceClass:DBInstanceClass}}"
        ),
    ),
    "s3": ("s3api", "list-buckets"),
    "kms": ("kms", "list-aliases", "--limit", "1"),
    "ecr": ("ecr", "describe-repositories", "--max-results", "1"),
    "cloudwatch_logs": ("logs", "describe-log-groups", "--limit", "1"),
    "agentcore_quotas": (
        "service-quotas",
        "list-service-quotas",
        "--service-code",
        "bedrock-agentcore",
        "--max-results",
        "20",
        "--query",
        "{Quotas: Quotas[].{QuotaCode:QuotaCode,Value:Value}}",
    ),
}

_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
_ACCOUNT_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{12}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_GITHUB_REPOSITORY = "natthaphonchop2-creator/mercury-tools"
_GITHUB_RUN_PATH_RE = re.compile(
    r"^/natthaphonchop2-creator/mercury-tools/actions/runs/([1-9]\d*)/?$"
)
_GITHUB_WORKFLOW_PATH = ".github/workflows/aws-wave0-oidc-smoke.yml"
_GITHUB_WORKFLOW_SHA256 = "83fb7b55e395250cc87c6e66fe92faa8453e345651de6dbd0bc17a966e1b560c"
_GITHUB_CREDENTIALS_ACTION = (
    "aws-actions/configure-aws-credentials@00943011d9042930efac3dcd3a170e4273319bc8"
)
_GITHUB_UPLOAD_ACTION = (
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
)
_SHORT_LIVED_PROFILE_SOURCES = frozenset({"login_session", "sso_session"})
_STATIC_CREDENTIAL_KEYS = frozenset(
    {"aws_access_key_id", "aws_secret_access_key", "aws_session_token"}
)
_CREDENTIAL_ENVIRONMENT_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)
_REQUIRED_AGENTCORE_QUOTA_CODES = frozenset(
    {
        "L-AB3B12EE",  # ListAgentRuntimeEndpoints
        "L-DEAB43C2",  # ListWorkloadIdentities
        "L-55F87EC2",  # Gateway inline schema size
    }
)
_OIDC_ACCOUNT_PROOF_SCHEMA = "mercury.aws.wave0.oidc_account_proof.v1"
_OIDC_ARTIFACT_MAX_BYTES = 65_536
_OIDC_PROOF_MAX_BYTES = 8_192
_COGNITO_STACK_NAME = "mercury-wave0-identity-spike"
_COGNITO_STACK_COMMAND = (
    "aws",
    "cloudformation",
    "describe-stacks",
    "--stack-name",
    _COGNITO_STACK_NAME,
    "--profile",
    "mercury-nonprod",
    "--region",
    "ap-southeast-1",
    "--output",
    "json",
    "--no-cli-pager",
)
_GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_THROTTLING_MARKERS = (
    "throttl",
    "rate exceeded",
    "too many requests",
    "requestlimitexceeded",
)
_TOOL_NAMES = frozenset(TOOL_COMMANDS)
_ACCOUNT_CHECK_NAMES = frozenset({"nonprod_account"})
_SERVICE_CHECK_NAMES = frozenset(
    f"{environment.value}_{service}"
    for environment in WAVE0_ENVIRONMENTS
    for service in SERVICE_COMMANDS
)
_EXPECTED_CHECK_NAMES = _TOOL_NAMES | _ACCOUNT_CHECK_NAMES | _SERVICE_CHECK_NAMES
_ACCOUNT_ACCESS_CODES = frozenset(
    {
        "aws_account_access_blocked",
        "aws_account_response_invalid",
        "aws_profile_not_short_lived",
        "aws_live_checks_skipped",
    }
)
_IDENTITY_CODES = frozenset({"wave0_evidence_inventory_invalid"})
_INVENTORY_INVALID_CODE = "wave0_evidence_inventory_invalid"
_REGION_CODES = frozenset(
    {
        "aws_service_probe_failed",
        "aws_service_response_invalid",
        "aws_service_throttled",
        "aurora_serverless_unavailable",
        "agentcore_quotas_unavailable",
    }
)


class _OidcModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class OidcRunReference(_OidcModel):
    """One explicitly environment-bound GitHub Actions run URL."""

    environment: EnvironmentName
    run_url: AnyHttpUrl

    @model_validator(mode="after")
    def validate_run_reference(self) -> OidcRunReference:
        if _run_id_from_url(self.run_url) is None:
            raise ValueError("wave0_oidc_run_url_invalid")
        return self


class OidcRunEvidence(OidcRunReference):
    """Closed evidence produced only after independent GitHub verification."""

    run_id: StrictInt = Field(gt=0)
    run_attempt: StrictInt = Field(gt=0)
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    workflow_id: StrictInt = Field(gt=0)
    workflow_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    job_id: StrictInt = Field(gt=0)
    account_fingerprint: str = Field(pattern=r"^[a-f0-9]{12}$")
    account_proof_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_run_evidence(self) -> OidcRunEvidence:
        if _run_id_from_url(self.run_url) != self.run_id:
            raise ValueError("wave0_oidc_run_url_invalid")
        if self.workflow_sha256 != _GITHUB_WORKFLOW_SHA256:
            raise ValueError("wave0_oidc_workflow_source_unverified")
        expected_hash = _canonical_oidc_evidence_sha256(
            environment=self.environment,
            run_url=self.run_url,
            run_id=self.run_id,
            run_attempt=self.run_attempt,
            head_sha=self.head_sha,
            workflow_id=self.workflow_id,
            workflow_sha256=self.workflow_sha256,
            job_id=self.job_id,
            account_fingerprint=self.account_fingerprint,
            account_proof_sha256=self.account_proof_sha256,
        )
        if not secrets.compare_digest(self.evidence_sha256, expected_hash):
            raise ValueError("wave0_oidc_evidence_hash_invalid")
        return self


class Wave0GateFinalization(_OidcModel):
    """Final gate status plus evidence produced by its internal verifier."""

    gate_status: GateStatus
    oidc_evidence: tuple[OidcRunEvidence, ...]


def _run_id_from_url(run_url: AnyHttpUrl) -> int | None:
    if (
        run_url.scheme != "https"
        or run_url.host != "github.com"
        or run_url.username is not None
        or run_url.password is not None
        or run_url.query is not None
        or run_url.fragment is not None
    ):
        return None
    match = _GITHUB_RUN_PATH_RE.fullmatch(run_url.path)
    return int(match.group(1)) if match is not None else None


def _canonical_oidc_evidence_sha256(
    *,
    environment: EnvironmentName,
    run_url: AnyHttpUrl,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    workflow_id: int,
    workflow_sha256: str,
    job_id: int,
    account_fingerprint: str,
    account_proof_sha256: str,
) -> str:
    payload = {
        "account_fingerprint": account_fingerprint,
        "account_proof_sha256": account_proof_sha256,
        "environment": environment.value,
        "head_sha": head_sha,
        "job_id": job_id,
        "repository": _GITHUB_REPOSITORY,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url_sha256": hashlib.sha256(str(run_url).encode("utf-8")).hexdigest(),
        "schema_version": "mercury.aws.wave0.oidc_run_evidence.v4",
        "workflow_id": workflow_id,
        "workflow_path": _GITHUB_WORKFLOW_PATH,
        "workflow_sha256": workflow_sha256,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def verify_oidc_runs(
    references: tuple[OidcRunReference, ...],
    expected_account_fingerprints: Mapping[EnvironmentName, str],
    runner: CommandRunner = run_command,
    *,
    repository_root: Path | None = None,
) -> tuple[OidcRunEvidence, ...]:
    """Verify exact GitHub runs and return only closed canonical evidence."""

    try:
        checked = tuple(
            OidcRunReference.model_validate(item.model_dump(mode="python"))
            for item in references
        )
    except (AttributeError, TypeError, ValidationError):
        raise ValueError("wave0_oidc_bindings_invalid") from None
    if len({item.environment for item in checked}) != len(checked) or len(
        {str(item.run_url) for item in checked}
    ) != len(checked):
        raise ValueError("wave0_oidc_bindings_invalid")
    if checked and tuple(item.environment for item in checked) != WAVE0_ENVIRONMENTS:
        raise ValueError("wave0_oidc_bindings_invalid")
    if set(expected_account_fingerprints) != {item.environment for item in checked} or any(
        not isinstance(fingerprint, str)
        or _ACCOUNT_FINGERPRINT_RE.fullmatch(fingerprint) is None
        for fingerprint in expected_account_fingerprints.values()
    ):
        raise ValueError("wave0_oidc_account_unverified")
    root = (repository_root or Path.cwd()).resolve()
    return tuple(
        _verify_oidc_run(
            item,
            expected_account_fingerprints[item.environment],
            runner,
            root,
        )
        for item in checked
    )


def _verify_oidc_run(
    reference: OidcRunReference,
    expected_account_fingerprint: str,
    runner: CommandRunner,
    repository_root: Path,
) -> OidcRunEvidence:
    run_id = _run_id_from_url(reference.run_url)
    if run_id is None:
        raise ValueError("wave0_oidc_run_unverified")
    run_endpoint = f"repos/{_GITHUB_REPOSITORY}/actions/runs/{run_id}"
    run = _gh_json(
        run_endpoint,
        (
            "{id,html_url,event,status,conclusion,head_sha,run_attempt,workflow_id,path,"
            "repository_full_name:.repository.full_name}"
        ),
        runner,
        "wave0_oidc_run_unverified",
    )
    expected_run_keys = {
        "id",
        "html_url",
        "event",
        "status",
        "conclusion",
        "head_sha",
        "run_attempt",
        "workflow_id",
        "path",
        "repository_full_name",
    }
    if set(run) != expected_run_keys:
        raise ValueError("wave0_oidc_run_unverified")
    head_sha = run["head_sha"]
    run_attempt = run["run_attempt"]
    workflow_id = run["workflow_id"]
    if (
        type(run["id"]) is not int
        or run["id"] != run_id
        or run["html_url"] != str(reference.run_url)
        or run["event"] != "workflow_dispatch"
        or run["status"] != "completed"
        or run["conclusion"] != "success"
        or type(head_sha) is not str
        or _GIT_SHA_RE.fullmatch(head_sha) is None
        or type(run_attempt) is not int
        or run_attempt <= 0
        or type(workflow_id) is not int
        or workflow_id <= 0
        or run["path"] != _GITHUB_WORKFLOW_PATH
        or run["repository_full_name"] != _GITHUB_REPOSITORY
    ):
        raise ValueError("wave0_oidc_run_unverified")

    workflow = _gh_json(
        f"repos/{_GITHUB_REPOSITORY}/actions/workflows/{workflow_id}",
        "{id,path}",
        runner,
        "wave0_oidc_workflow_unverified",
    )
    if workflow != {"id": workflow_id, "path": _GITHUB_WORKFLOW_PATH}:
        raise ValueError("wave0_oidc_workflow_unverified")

    jobs = _gh_json(
        f"{run_endpoint}/jobs?per_page=100",
        (
            "{total_count,jobs:[.jobs[]|"
            "{id,name,status,conclusion,run_id,run_attempt,head_sha}]}"
        ),
        runner,
        "wave0_oidc_job_unverified",
    )
    job_id = _verified_environment_job(
        jobs,
        reference.environment,
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=head_sha,
    )

    source_result = runner(
        (
            "gh",
            "api",
            f"repos/{_GITHUB_REPOSITORY}/contents/{_GITHUB_WORKFLOW_PATH}?ref={head_sha}",
            "--jq",
            ".content",
        ),
        20,
    )
    try:
        workflow_source = base64.b64decode(
            "".join(source_result.stdout.split()).encode("ascii"), validate=True
        ).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error):
        raise ValueError("wave0_oidc_workflow_source_unverified") from None
    if source_result.returncode != 0 or not _workflow_source_valid(workflow_source):
        raise ValueError("wave0_oidc_workflow_source_unverified")
    workflow_sha256 = hashlib.sha256(workflow_source.encode("utf-8")).hexdigest()
    account_fingerprint, account_proof_sha256 = _verified_oidc_account_proof(
        environment=reference.environment,
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=head_sha,
        expected_account_fingerprint=expected_account_fingerprint,
        runner=runner,
        repository_root=repository_root,
    )
    evidence_sha256 = _canonical_oidc_evidence_sha256(
        environment=reference.environment,
        run_url=reference.run_url,
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=head_sha,
        workflow_id=workflow_id,
        workflow_sha256=workflow_sha256,
        job_id=job_id,
        account_fingerprint=account_fingerprint,
        account_proof_sha256=account_proof_sha256,
    )
    return OidcRunEvidence(
        environment=reference.environment,
        run_url=reference.run_url,
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=head_sha,
        workflow_id=workflow_id,
        workflow_sha256=workflow_sha256,
        job_id=job_id,
        account_fingerprint=account_fingerprint,
        account_proof_sha256=account_proof_sha256,
        evidence_sha256=evidence_sha256,
    )


def _verified_oidc_account_proof(
    *,
    environment: EnvironmentName,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    expected_account_fingerprint: str,
    runner: CommandRunner,
    repository_root: Path,
) -> tuple[str, str]:
    artifact_name = f"mercury-wave0-oidc-account-proof-{environment.value}"
    artifacts = _gh_json(
        (
            f"repos/{_GITHUB_REPOSITORY}/actions/runs/{run_id}/"
            "artifacts?per_page=100"
        ),
        (
            "{total_count,artifacts:[.artifacts[]|"
            "{id,name,size_in_bytes,expired,expires_at,"
            "workflow_run:{id:.workflow_run.id,head_sha:.workflow_run.head_sha}}]}"
        ),
        runner,
        "wave0_oidc_artifact_unverified",
    )
    _verified_artifact_record(artifacts, artifact_name, run_id, head_sha)
    proof = _download_account_proof(
        runner=runner,
        repository_root=repository_root,
        environment=environment,
        run_id=run_id,
        artifact_name=artifact_name,
    )
    expected_keys = {
        "schema",
        "repository",
        "workflow",
        "run_id",
        "run_attempt",
        "head_sha",
        "environment",
        "account_fingerprint",
    }
    if (
        set(proof) != expected_keys
        or proof["schema"] != _OIDC_ACCOUNT_PROOF_SCHEMA
        or proof["repository"] != _GITHUB_REPOSITORY
        or proof["workflow"] != _GITHUB_WORKFLOW_PATH
        or type(proof["run_id"]) is not int
        or proof["run_id"] != run_id
        or type(proof["run_attempt"]) is not int
        or proof["run_attempt"] != run_attempt
        or proof["head_sha"] != head_sha
        or proof["environment"] != environment.value
    ):
        raise ValueError("wave0_oidc_artifact_unverified")
    account_fingerprint = proof["account_fingerprint"]
    if (
        not isinstance(account_fingerprint, str)
        or _ACCOUNT_FINGERPRINT_RE.fullmatch(account_fingerprint) is None
        or not secrets.compare_digest(
            account_fingerprint, expected_account_fingerprint
        )
    ):
        raise ValueError("wave0_oidc_account_unverified")
    canonical = json.dumps(
        proof,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return account_fingerprint, hashlib.sha256(canonical).hexdigest()


def _verified_artifact_record(
    payload: dict[str, Any],
    artifact_name: str,
    run_id: int,
    head_sha: str,
) -> None:
    if set(payload) != {"total_count", "artifacts"} or type(
        payload["total_count"]
    ) is not int:
        raise ValueError("wave0_oidc_artifact_unverified")
    records = payload["artifacts"]
    if (
        not isinstance(records, list)
        or payload["total_count"] != len(records)
        or len(records) > 100
    ):
        raise ValueError("wave0_oidc_artifact_unverified")
    matches = [
        item
        for item in records
        if isinstance(item, dict) and item.get("name") == artifact_name
    ]
    if len(matches) != 1:
        raise ValueError("wave0_oidc_artifact_unverified")
    artifact = matches[0]
    expected_keys = {
        "id",
        "name",
        "size_in_bytes",
        "expired",
        "expires_at",
        "workflow_run",
    }
    expires_at = artifact.get("expires_at")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("wave0_oidc_artifact_unverified") from None
    if (
        set(artifact) != expected_keys
        or type(artifact["id"]) is not int
        or artifact["id"] <= 0
        or type(artifact["size_in_bytes"]) is not int
        or not 0 < artifact["size_in_bytes"] <= _OIDC_ARTIFACT_MAX_BYTES
        or artifact["expired"] is not False
        or expiry.tzinfo is None
        or expiry <= datetime.now(UTC)
        or artifact["workflow_run"] != {"id": run_id, "head_sha": head_sha}
    ):
        raise ValueError("wave0_oidc_artifact_unverified")


def _download_account_proof(
    *,
    runner: CommandRunner,
    repository_root: Path,
    environment: EnvironmentName,
    run_id: int,
    artifact_name: str,
) -> dict[str, Any]:
    artifact_directory_fd = _open_artifact_directory(repository_root)
    temporary_name: str | None = None
    temporary_fd: int | None = None
    environment_fd: int | None = None
    try:
        temporary_name, temporary_fd = _create_download_directory(
            artifact_directory_fd, run_id
        )
        os.mkdir(environment.value, mode=0o700, dir_fd=temporary_fd)
        environment_fd = os.open(
            environment.value,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=temporary_fd,
        )
        destination = (
            repository_root
            / ".artifacts/aws/wave0"
            / temporary_name
            / environment.value
        )
        result = runner(
            (
                "gh",
                "run",
                "download",
                str(run_id),
                "--repo",
                _GITHUB_REPOSITORY,
                "--name",
                artifact_name,
                "--dir",
                str(destination),
            ),
            30,
        )
        if result.returncode != 0:
            raise ValueError("wave0_oidc_artifact_unverified")
        return _read_closed_account_proof(environment_fd, environment)
    except OSError:
        raise ValueError("wave0_oidc_artifact_unverified") from None
    finally:
        if environment_fd is not None:
            os.close(environment_fd)
        if temporary_fd is not None:
            os.close(temporary_fd)
        os.close(artifact_directory_fd)
        if temporary_name is not None:
            shutil.rmtree(
                repository_root / ".artifacts/aws/wave0" / temporary_name,
                ignore_errors=True,
            )


def _create_download_directory(parent_fd: int, run_id: int) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    for _ in range(10):
        name = f"oidc-download-{run_id}-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            return name, os.open(name, flags, dir_fd=parent_fd)
        except FileExistsError:
            continue
    raise OSError("wave0_temporary_directory_unavailable")


def _read_closed_account_proof(
    directory_fd: int,
    environment: EnvironmentName,
) -> dict[str, Any]:
    expected_name = f"oidc-account-proof-{environment.value}.json"
    if os.listdir(directory_fd) != [expected_name]:
        raise ValueError("wave0_oidc_artifact_unverified")
    metadata = os.stat(expected_name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= _OIDC_PROOF_MAX_BYTES:
        raise ValueError("wave0_oidc_artifact_unverified")
    descriptor = os.open(expected_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(4_096, _OIDC_PROOF_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _OIDC_PROOF_MAX_BYTES:
                raise ValueError("wave0_oidc_artifact_unverified")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) != metadata.st_size:
        raise ValueError("wave0_oidc_artifact_unverified")
    try:
        proof: Any = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise ValueError("wave0_oidc_artifact_unverified") from None
    if not isinstance(proof, dict):
        raise ValueError("wave0_oidc_artifact_unverified")
    return proof


def _gh_json(
    endpoint: str,
    jq_filter: str,
    runner: CommandRunner,
    error_code: str,
) -> dict[str, Any]:
    result = runner(("gh", "api", endpoint, "--jq", jq_filter), 20)
    if result.returncode != 0:
        raise ValueError(error_code)
    try:
        payload: Any = json.loads(result.stdout)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise ValueError(error_code) from None
    if not isinstance(payload, dict):
        raise ValueError(error_code)
    return payload


def _verified_environment_job(
    jobs: dict[str, Any],
    environment: EnvironmentName,
    *,
    run_id: int,
    run_attempt: int,
    head_sha: str,
) -> int:
    if set(jobs) != {"total_count", "jobs"} or type(jobs["total_count"]) is not int:
        raise ValueError("wave0_oidc_job_unverified")
    records = jobs["jobs"]
    if (
        not isinstance(records, list)
        or jobs["total_count"] != len(records)
        or len(records) > 100
    ):
        raise ValueError("wave0_oidc_job_unverified")
    expected_name = f"OIDC smoke ({environment.value})"
    matches = [
        item
        for item in records
        if isinstance(item, dict) and item.get("name") == expected_name
    ]
    if len(matches) != 1:
        raise ValueError("wave0_oidc_job_unverified")
    job = matches[0]
    if (
        set(job)
        != {"id", "name", "status", "conclusion", "run_id", "run_attempt", "head_sha"}
        or type(job["id"]) is not int
        or job["id"] <= 0
        or job["status"] != "completed"
        or job["conclusion"] != "success"
        or job["run_id"] != run_id
        or job["run_attempt"] != run_attempt
        or job["head_sha"] != head_sha
    ):
        raise ValueError("wave0_oidc_job_unverified")
    return job["id"]


def _workflow_source_valid(source: str) -> bool:
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != _GITHUB_WORKFLOW_SHA256:
        return False
    try:
        workflow: Any = yaml.load(source, Loader=yaml.BaseLoader)
        dispatch = workflow["on"]
        permissions = workflow["permissions"]
        jobs = workflow["jobs"]
        job = jobs["oidc-smoke"]
        steps = job["steps"]
        assume_role = steps[0]
        proof = steps[1]
        proof_source = proof["run"]
        upload = steps[2]
    except (KeyError, TypeError, yaml.YAMLError):
        return False
    try:
        return bool(
            set(dispatch) == {"workflow_dispatch"}
            and permissions == {"contents": "read", "id-token": "write"}
            and set(jobs) == {"oidc-smoke"}
            and len(steps) == 4
            and job.get("environment") == "${{ matrix.environment }}"
            and job.get("strategy", {}).get("fail-fast") == "false"
            and job.get("strategy", {}).get("matrix", {}).get("environment")
            == ["nonprod"]
            and assume_role.get("uses") == _GITHUB_CREDENTIALS_ACTION
            and assume_role.get("with")
            == {
                "role-to-assume": "${{ vars.AWS_WAVE0_ROLE_ARN }}",
                "aws-region": "ap-southeast-1",
                "mask-aws-account-id": "true",
            }
            and proof.get("name") == "Create run-bound account proof"
            and proof.get("shell") == "bash"
            and all(
                required in proof_source
                for required in (
                    "set -euo pipefail",
                    "^[0-9]{12}$",
                    "sha256sum",
                    "cut -c1-12",
                    '"schema": "mercury.aws.wave0.oidc_account_proof.v1"',
                    '"repository": $repository',
                    '"workflow": $workflow',
                    '"run_id": $run_id',
                    '"run_attempt": $run_attempt',
                    '"head_sha": $head_sha',
                    '"environment": $environment',
                    '"account_fingerprint": $account_fingerprint',
                )
            )
            and '"account_id":' not in proof_source
            and upload.get("uses") == _GITHUB_UPLOAD_ACTION
            and upload.get("with")
            == {
                "name": "mercury-wave0-oidc-account-proof-${{ matrix.environment }}",
                "path": (
                    "${{ runner.temp }}/mercury-wave0-oidc-account-proof/"
                    "${{ matrix.environment }}/oidc-account-proof-"
                    "${{ matrix.environment }}.json"
                ),
                "if-no-files-found": "error",
                "retention-days": "1",
            }
        )
    except (AttributeError, TypeError):
        return False


def _check(
    name: str,
    state: CheckState,
    code: str,
    summary: str,
    **details: str | bool | int | float,
) -> CheckResult:
    return CheckResult(name=name, state=state, code=code, summary=summary, details=details)


def _version_tuple(output: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(output)
    if match is None:
        return None
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _tool_version_allowed(name: str, version: tuple[int, int, int]) -> bool:
    if name == "aws_cli":
        return version >= (2, 36, 14)
    if name == "node":
        return version[0] >= 20
    if name == "python":
        return (3, 11, 0) <= version < (3, 14, 0)
    if name == "agentcore_cli":
        return version == (0, 25, 0)
    if name == "aws_cdk":
        return version == (2, 1134, 0)
    return False


def check_local_toolchain(runner: CommandRunner = run_command) -> tuple[CheckResult, ...]:
    """Verify the exact Wave 0 local command set and version constraints."""

    checks: list[CheckResult] = []
    for name, command in TOOL_COMMANDS.items():
        result = runner(command, 20)
        output = f"{result.stdout}\n{result.stderr}"
        version = _version_tuple(output)
        if result.returncode == 0 and version is not None and _tool_version_allowed(name, version):
            checks.append(
                _check(
                    name,
                    CheckState.PASS,
                    "tool_version_verified",
                    "Required local tool version verified.",
                    version=".".join(str(item) for item in version),
                )
            )
        else:
            checks.append(
                _check(
                    name,
                    CheckState.BLOCKED,
                    "tool_version_unsupported",
                    "Required local tool is missing or outside the supported version range.",
                    returncode=result.returncode,
                )
            )
    return tuple(checks)


def fingerprint_account_id(account_id: str) -> str:
    """Return the publishable fingerprint for one validated AWS account ID."""

    if _ACCOUNT_ID_RE.fullmatch(account_id) is None:
        raise ValueError("wave0_account_id_invalid")
    return hashlib.sha256(account_id.encode("ascii")).hexdigest()[:12]


ProfileSourceInspector = Callable[[str], str | None]


def inspect_short_lived_profile(profile: str) -> str | None:
    """Return an approved temporary source without exposing credential values."""

    if any(os.environ.get(name) for name in _CREDENTIAL_ENVIRONMENT_KEYS):
        return None

    config_path = Path(
        os.environ.get("AWS_CONFIG_FILE", str(Path.home() / ".aws" / "config"))
    ).expanduser()
    credentials_path = Path(
        os.environ.get(
            "AWS_SHARED_CREDENTIALS_FILE",
            str(Path.home() / ".aws" / "credentials"),
        )
    ).expanduser()
    config = _read_aws_ini(config_path)
    if config is None:
        return None
    section = "default" if profile == "default" else f"profile {profile}"
    if not config.has_section(section):
        return None
    configured_keys = {
        key for key, value in config.items(section) if isinstance(value, str) and value.strip()
    }
    if configured_keys & _STATIC_CREDENTIAL_KEYS:
        return None
    sources = tuple(
        source for source in _SHORT_LIVED_PROFILE_SOURCES if source in configured_keys
    )
    if len(sources) != 1:
        return None

    credentials = _read_aws_ini(credentials_path, missing_ok=True)
    if credentials is None:
        return None
    if credentials.has_section(profile):
        credential_keys = {
            key
            for key, value in credentials.items(profile)
            if isinstance(value, str) and value.strip()
        }
        if credential_keys & _STATIC_CREDENTIAL_KEYS:
            return None
    return sources[0]


def _read_aws_ini(
    path: Path,
    *,
    missing_ok: bool = False,
) -> configparser.RawConfigParser | None:
    parser = configparser.RawConfigParser()
    try:
        if not path.exists():
            return parser if missing_ok else None
        if not path.is_file() or path.stat().st_size > 1_048_576:
            return None
        with path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError, configparser.Error):
        return None
    return parser


def check_aws_accounts(
    config: Wave0Config,
    runner: CommandRunner = run_command,
    profile_source_inspector: ProfileSourceInspector = inspect_short_lived_profile,
) -> tuple[CheckResult, ...]:
    """Verify the single nonprod AWS profile required by Wave 0."""

    checks: list[CheckResult] = []
    for account in config.accounts:
        credential_source = profile_source_inspector(account.profile)
        if credential_source not in _SHORT_LIVED_PROFILE_SOURCES:
            checks.append(
                _check(
                    f"{account.environment.value}_account",
                    CheckState.BLOCKED,
                    "aws_profile_not_short_lived",
                    "AWS profile is not bound to an approved short-lived source.",
                )
            )
            continue
        command = (
            "aws",
            "sts",
            "get-caller-identity",
            "--profile",
            account.profile,
            "--region",
            config.primary_region,
            "--output",
            "json",
            "--no-cli-pager",
        )
        result = runner(command, 20)
        if result.returncode != 0:
            checks.append(
                _check(
                    f"{account.environment.value}_account",
                    CheckState.BLOCKED,
                    "aws_account_access_blocked",
                    "AWS account identity could not be verified.",
                    returncode=result.returncode,
                )
            )
            continue

        account_id = _parse_account_id(result)
        if account_id is None:
            checks.append(
                _check(
                    f"{account.environment.value}_account",
                    CheckState.BLOCKED,
                    "aws_account_response_invalid",
                    "AWS account identity response did not match the required shape.",
                )
            )
            continue

        fingerprint = fingerprint_account_id(account_id)
        checks.append(
            _check(
                f"{account.environment.value}_account",
                CheckState.PASS,
                "aws_account_verified",
                "AWS account identity verified.",
                account_fingerprint=fingerprint,
                credential_source=credential_source,
            )
        )

    return tuple(checks)


def _parse_account_id(result: CommandResult) -> str | None:
    try:
        payload: Any = json.loads(result.stdout)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    account_id = payload.get("Account")
    if not isinstance(account_id, str) or _ACCOUNT_ID_RE.fullmatch(account_id) is None:
        return None
    return account_id


def check_region_services(
    config: Wave0Config,
    runner: CommandRunner = run_command,
) -> tuple[CheckResult, ...]:
    """Probe every required service for the Wave 0 nonprod profile."""

    checks: list[CheckResult] = []
    for account in config.accounts:
        for probe in config.required_service_probes:
            name = probe.value
            command = (
                "aws",
                *SERVICE_COMMANDS[name],
                "--profile",
                account.profile,
                "--region",
                config.primary_region,
                "--output",
                "json",
                "--no-cli-pager",
            )
            result, attempts = _run_service_probe(command, runner)
            check_name = f"{account.environment.value}_{name}"
            if result.returncode != 0:
                throttled = _is_throttled(result)
                checks.append(
                    _check(
                        check_name,
                        CheckState.BLOCKED,
                        "aws_service_throttled" if throttled else "aws_service_probe_failed",
                        "Required AWS service probe did not complete successfully.",
                        attempts=attempts,
                        returncode=result.returncode,
                    )
                )
                continue

            payload = _parse_service_payload(result.stdout)
            if payload is None:
                checks.append(
                    _check(
                        check_name,
                        CheckState.BLOCKED,
                        "aws_service_response_invalid",
                        "Required AWS service returned an invalid response shape.",
                    )
                )
                continue
            if name == "aurora_postgresql" and not _aurora_serverless_available(payload):
                checks.append(
                    _check(
                        check_name,
                        CheckState.BLOCKED,
                        "aurora_serverless_unavailable",
                        "Aurora PostgreSQL Serverless options were not returned.",
                    )
                )
                continue
            if name == "agentcore_quotas" and not _agentcore_quotas_available(payload):
                checks.append(
                    _check(
                        check_name,
                        CheckState.BLOCKED,
                        "agentcore_quotas_unavailable",
                        "AgentCore quota evidence did not match the required shape.",
                    )
                )
                continue
            checks.append(
                _check(
                    check_name,
                    CheckState.PASS,
                    "aws_service_available",
                    "Required AWS service probe passed.",
                )
            )
    return tuple(checks)


def _run_service_probe(
    command: tuple[str, ...],
    runner: CommandRunner,
) -> tuple[CommandResult, int]:
    result = runner(command, 20)
    attempts = 1
    while attempts < 3 and _is_throttled(result):
        result = runner(command, 20)
        attempts += 1
    return result, attempts


def _is_throttled(result: CommandResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    return any(marker in output for marker in _THROTTLING_MARKERS)


def _parse_service_payload(output: str) -> dict[str, Any] | None:
    try:
        payload: Any = json.loads(output)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _aurora_serverless_available(payload: dict[str, Any]) -> bool:
    options = payload.get("OrderableDBInstanceOptions")
    return (
        set(payload) == {"OrderableDBInstanceOptions"}
        and isinstance(options, list)
        and len(options) == 1
        and options[0]
        == {
            "Engine": "aurora-postgresql",
            "DBInstanceClass": "db.serverless",
        }
    )


def _agentcore_quotas_available(payload: dict[str, Any]) -> bool:
    quotas = payload.get("Quotas")
    shape_is_valid = (
        set(payload) == {"Quotas"}
        and isinstance(quotas, list)
        and bool(quotas)
        and all(
            isinstance(item, dict)
            and set(item) == {"QuotaCode", "Value"}
            and isinstance(item["QuotaCode"], str)
            and re.fullmatch(r"L-[A-Z0-9]{8}", item["QuotaCode"]) is not None
            and type(item["Value"]) in {int, float}
            and math.isfinite(item["Value"])
            and item["Value"] > 0
            for item in quotas
        )
    )
    if not shape_is_valid:
        return False
    quota_values = {item["QuotaCode"]: item["Value"] for item in quotas}
    return (
        len(quota_values) == len(quotas)
        and set(quota_values) >= _REQUIRED_AGENTCORE_QUOTA_CODES
    )


def aggregate_gate(checks: tuple[CheckResult, ...] | list[CheckResult]) -> GateStatus:
    """Aggregate checks using the frozen fail-closed gate precedence."""

    counts = Counter(item.name for item in checks)
    blocked = [item for item in checks if item.state is not CheckState.PASS]
    substantive_blocked = [item for item in blocked if item.code != _INVENTORY_INVALID_CODE]
    if any(item.name in _TOOL_NAMES for item in substantive_blocked):
        return GateStatus.BLOCKED_TOOLING
    if any(item.code in _ACCOUNT_ACCESS_CODES for item in substantive_blocked):
        return GateStatus.BLOCKED_ACCOUNT_ACCESS
    if any(item.code in _REGION_CODES for item in substantive_blocked):
        return GateStatus.BLOCKED_REGION_SERVICE
    if any(item.code in _IDENTITY_CODES for item in substantive_blocked) or substantive_blocked:
        return GateStatus.BLOCKED_IDENTITY_COMPATIBILITY

    if _inventory_group_invalid(counts, _TOOL_NAMES):
        return GateStatus.BLOCKED_TOOLING
    if _inventory_group_invalid(counts, _ACCOUNT_CHECK_NAMES):
        return GateStatus.BLOCKED_ACCOUNT_ACCESS
    if _inventory_group_invalid(counts, _SERVICE_CHECK_NAMES):
        return GateStatus.BLOCKED_REGION_SERVICE
    if any(name not in _EXPECTED_CHECK_NAMES for name in counts):
        return GateStatus.BLOCKED_IDENTITY_COMPATIBILITY
    return GateStatus.READY


def finalize_wave0_gate(
    report: ReadinessReport,
    identity_decision: IdentityDecision | None,
    oidc_references: tuple[OidcRunReference, ...],
    runner: CommandRunner = run_command,
    *,
    identity_proof_references: tuple[IdentityProofReference, ...] = (),
) -> Wave0GateFinalization:
    """Independently verify every Wave 0 proof in fail-closed precedence order."""

    checks = tuple(report.checks)
    counts = Counter(item.name for item in checks)
    by_name = {item.name: item for item in checks}

    if (
        not _final_tooling_valid(counts, by_name)
        or report.gate_status is GateStatus.BLOCKED_TOOLING
    ):
        return Wave0GateFinalization(gate_status=GateStatus.BLOCKED_TOOLING, oidc_evidence=())
    if (
        not _final_accounts_valid(report, counts, by_name)
        or report.gate_status is GateStatus.BLOCKED_ACCOUNT_ACCESS
    ):
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_ACCOUNT_ACCESS,
            oidc_evidence=(),
        )

    try:
        oidc_evidence = verify_oidc_runs(
            oidc_references,
            {
                environment: by_name[f"{environment.value}_account"].details[
                    "account_fingerprint"
                ]
                for environment in WAVE0_ENVIRONMENTS
            },
            runner,
        )
    except ValueError:
        oidc_evidence = ()
    if not _final_oidc_valid(oidc_evidence):
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_ACCOUNT_ACCESS,
            oidc_evidence=(),
        )
    if report.gate_status is GateStatus.BLOCKED_ACCOUNT_ACCESS:
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_ACCOUNT_ACCESS,
            oidc_evidence=oidc_evidence,
        )

    if not _final_region_services_valid(report, counts, by_name):
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_REGION_SERVICE,
            oidc_evidence=oidc_evidence,
        )
    if report.gate_status is GateStatus.BLOCKED_REGION_SERVICE:
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_REGION_SERVICE,
            oidc_evidence=oidc_evidence,
        )

    if not _final_report_contract_valid(report, counts):
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_IDENTITY_COMPATIBILITY,
            oidc_evidence=oidc_evidence,
        )
    if not _final_identity_valid(
        identity_decision,
        identity_proof_references,
        verified_at=report.checked_at,
    ) or not _cognito_stack_absent(runner):
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_IDENTITY_COMPATIBILITY,
            oidc_evidence=oidc_evidence,
        )
    if report.gate_status is not GateStatus.READY:
        return Wave0GateFinalization(
            gate_status=GateStatus.BLOCKED_IDENTITY_COMPATIBILITY,
            oidc_evidence=oidc_evidence,
        )
    return Wave0GateFinalization(gate_status=GateStatus.READY, oidc_evidence=oidc_evidence)


def _final_tooling_valid(
    counts: Counter[str],
    by_name: dict[str, CheckResult],
) -> bool:
    if _inventory_group_invalid(counts, _TOOL_NAMES):
        return False
    for name in TOOL_COMMANDS:
        check = by_name[name]
        if check.state is not CheckState.PASS or check.code != "tool_version_verified":
            return False
        if set(check.details) != {"version"}:
            return False
        version_text = check.details["version"]
        if not isinstance(version_text, str) or _VERSION_RE.fullmatch(version_text) is None:
            return False
        version = _version_tuple(version_text)
        if version is None or not _tool_version_allowed(name, version):
            return False
    return True


def _final_accounts_valid(
    report: ReadinessReport,
    counts: Counter[str],
    by_name: dict[str, CheckResult],
) -> bool:
    expected_accounts = (
        (EnvironmentName.NONPROD, "mercury-nonprod", EnvironmentName.NONPROD),
    )
    actual_accounts = tuple(
        (account.environment, account.alias, account.github_environment)
        for account in report.accounts
    )
    if actual_accounts != expected_accounts or any(
        account.profile != account.alias for account in report.accounts
    ):
        return False
    if _inventory_group_invalid(counts, _ACCOUNT_CHECK_NAMES):
        return False

    for name in sorted(_ACCOUNT_CHECK_NAMES):
        check = by_name[name]
        if check.state is not CheckState.PASS or check.code != "aws_account_verified":
            return False
        allowed_keys = {
            "account_fingerprint",
            "credential_source",
            "identity_evidence_sha256",
            "oidc_evidence_sha256",
        }
        if "account_fingerprint" not in check.details or not set(check.details) <= allowed_keys:
            return False
        fingerprint = check.details["account_fingerprint"]
        if (
            not isinstance(fingerprint, str)
            or _ACCOUNT_FINGERPRINT_RE.fullmatch(fingerprint) is None
        ):
            return False
        if check.details.get("credential_source") not in _SHORT_LIVED_PROFILE_SOURCES:
            return False
        oidc_hash = check.details.get("oidc_evidence_sha256")
        if oidc_hash is not None and (
            not isinstance(oidc_hash, str) or _SHA256_RE.fullmatch(oidc_hash) is None
        ):
            return False
        identity_hash = check.details.get("identity_evidence_sha256")
        if identity_hash is not None and (
            not isinstance(identity_hash, str) or _SHA256_RE.fullmatch(identity_hash) is None
        ):
            return False
    return True


def _final_oidc_valid(oidc_evidence: tuple[OidcRunEvidence, ...]) -> bool:
    if len(oidc_evidence) != len(WAVE0_ENVIRONMENTS):
        return False
    try:
        checked = tuple(
            OidcRunEvidence.model_validate(item.model_dump(mode="python"))
            for item in oidc_evidence
        )
    except (AttributeError, TypeError, ValidationError):
        return False
    return (
        tuple(item.environment for item in checked) == WAVE0_ENVIRONMENTS
        and len({str(item.run_url) for item in checked}) == len(checked)
        and len({item.evidence_sha256 for item in checked}) == len(checked)
    )


def _final_region_services_valid(
    report: ReadinessReport,
    counts: Counter[str],
    by_name: dict[str, CheckResult],
) -> bool:
    if report.primary_region != "ap-southeast-1":
        return False
    if _inventory_group_invalid(counts, _SERVICE_CHECK_NAMES):
        return False
    return all(
        by_name[name].state is CheckState.PASS
        and by_name[name].code == "aws_service_available"
        and not by_name[name].details
        for name in _SERVICE_CHECK_NAMES
    )


def _final_report_contract_valid(
    report: ReadinessReport,
    counts: Counter[str],
) -> bool:
    try:
        ReadinessReport.model_validate(report.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError):
        return False
    if (
        report.schema_version != "mercury.aws.wave0.report.v1"
        or report.github_repository != "natthaphonchop2-creator/mercury-tools"
        or report.checked_at.tzinfo is None
        or report.checked_at.utcoffset() is None
    ):
        return False
    return set(counts) == set(_EXPECTED_CHECK_NAMES) and all(
        counts[name] == 1 for name in _EXPECTED_CHECK_NAMES
    )


def _final_identity_valid(
    identity_decision: IdentityDecision | None,
    references: tuple[IdentityProofReference, ...],
    *,
    verified_at: datetime,
) -> bool:
    if identity_decision is None:
        return False
    try:
        checked_decision = IdentityDecision.model_validate(
            identity_decision.model_dump(mode="python")
        )
        verify_identity_proof(checked_decision, references, verified_at=verified_at)
    except (AttributeError, TypeError, ValidationError, ValueError):
        return False
    return True


def _cognito_stack_absent(runner: CommandRunner) -> bool:
    result = runner(_COGNITO_STACK_COMMAND, 20)
    expected_errors = {
        f"Stack with id {_COGNITO_STACK_NAME} does not exist",
        (
            "An error occurred (ValidationError) when calling the DescribeStacks operation: "
            f"Stack with id {_COGNITO_STACK_NAME} does not exist"
        ),
    }
    return (
        result.returncode == 255
        and not result.stdout.strip()
        and result.stderr.strip() in expected_errors
    )


def _inventory_group_invalid(counts: Counter[str], expected: frozenset[str]) -> bool:
    return any(counts[name] != 1 for name in expected)


def _expected_check_names(config: Wave0Config) -> frozenset[str]:
    account_names = {f"{account.environment.value}_account" for account in config.accounts}
    service_names = {
        f"{account.environment.value}_{probe.value}"
        for account in config.accounts
        for probe in config.required_service_probes
    }
    return frozenset((*TOOL_COMMANDS, *account_names, *service_names))


def _inventory_is_exact(config: Wave0Config, checks: tuple[CheckResult, ...]) -> bool:
    expected = _expected_check_names(config)
    counts = Counter(item.name for item in checks)
    return set(counts) == set(expected) and all(counts[name] == 1 for name in expected)


def build_readiness_report(
    config: Wave0Config,
    checks: tuple[CheckResult, ...] | list[CheckResult],
    *,
    checked_at: datetime | None = None,
) -> ReadinessReport:
    """Build the frozen report model from already sanitized checks."""

    check_tuple = tuple(checks)
    if not _inventory_is_exact(config, check_tuple):
        check_tuple += (
            _check(
                "wave0_evidence_inventory",
                CheckState.BLOCKED,
                _INVENTORY_INVALID_CODE,
                "AWS readiness evidence inventory is incomplete or invalid.",
            ),
        )
    return ReadinessReport(
        schema_version="mercury.aws.wave0.report.v1",
        primary_region=config.primary_region,
        github_repository=config.github_repository,
        checked_at=checked_at or datetime.now(UTC),
        accounts=config.accounts,
        checks=check_tuple,
        gate_status=aggregate_gate(check_tuple),
    )


def write_readiness_report(
    report: ReadinessReport,
    output: Path,
    *,
    repository_root: Path | None = None,
) -> None:
    """Atomically write private evidence below the fixed Wave 0 artifact directory."""

    root = (repository_root or Path.cwd()).resolve()
    allowed_root = root / ".artifacts/aws/wave0"
    candidate = output if output.is_absolute() else root / output
    normalized_output = Path(os.path.abspath(candidate))
    if normalized_output.parent != allowed_root or normalized_output.name in {"", ".", ".."}:
        raise ValueError("wave0_output_path_invalid")

    payload = report.model_dump_json(indent=2).encode("utf-8") + b"\n"
    directory_fd = _open_artifact_directory(root)
    temporary_name: str | None = None
    try:
        _reject_symlinked_output(directory_fd, normalized_output.name)
        descriptor, temporary_name = _create_temporary_file(directory_fd)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            normalized_output.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        os.fsync(directory_fd)
    except BaseException:
        if temporary_name is not None:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=directory_fd)
        raise
    finally:
        os.close(directory_fd)


def _open_artifact_directory(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(root, flags)
    except OSError:
        raise ValueError("wave0_output_path_invalid") from None
    try:
        for component in (".artifacts", "aws", "wave0"):
            with suppress(FileExistsError):
                os.mkdir(component, mode=0o700, dir_fd=directory_fd)
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except OSError:
        os.close(directory_fd)
        raise ValueError("wave0_output_path_invalid") from None


def _reject_symlinked_output(directory_fd: int, output_name: str) -> None:
    try:
        mode = os.stat(output_name, dir_fd=directory_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    except OSError:
        raise ValueError("wave0_output_path_invalid") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError("wave0_output_path_invalid")


def _create_temporary_file(directory_fd: int) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(10):
        name = f".readiness-{secrets.token_hex(8)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_fd), name
        except FileExistsError:
            continue
    raise OSError("wave0_temporary_file_unavailable")
