"""Frozen, credential-safe contracts for AWS Wave 0 readiness evidence."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from mercury_tools.catalog.identity import (
    FrozenDict,
    validate_credential_safe,
    validate_credential_safe_paths,
)

_STABLE_IDENTIFIER = r"^[a-z][a-z0-9_-]{0,63}$"
_REQUIRED_SERVICE_PROBES = (
    "agentcore_runtime",
    "agentcore_gateway",
    "agentcore_identity",
    "bedrock_knowledge_bases",
    "aurora_postgresql",
    "s3",
    "kms",
    "ecr",
    "cloudwatch_logs",
    "agentcore_quotas",
)
_STABLE_IDENTIFIER_RE = re.compile(_STABLE_IDENTIFIER)

ScalarDetail = StrictStr | StrictBool | StrictInt | StrictFloat
SafeDetails = Annotated[dict[str, ScalarDetail], Field(default_factory=dict)]


class EnvironmentName(StrEnum):
    NONPROD = "nonprod"
    PRODUCTION = "production"


class ServiceProbeId(StrEnum):
    AGENTCORE_RUNTIME = "agentcore_runtime"
    AGENTCORE_GATEWAY = "agentcore_gateway"
    AGENTCORE_IDENTITY = "agentcore_identity"
    BEDROCK_KNOWLEDGE_BASES = "bedrock_knowledge_bases"
    AURORA_POSTGRESQL = "aurora_postgresql"
    S3 = "s3"
    KMS = "kms"
    ECR = "ecr"
    CLOUDWATCH_LOGS = "cloudwatch_logs"
    AGENTCORE_QUOTAS = "agentcore_quotas"


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


class _FrozenSafeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_content(cls, value: Any) -> Any:
        validate_credential_safe(value)
        validate_credential_safe_paths(value)
        return value


class AccountTarget(_FrozenSafeModel):
    environment: EnvironmentName
    alias: str = Field(pattern=_STABLE_IDENTIFIER)
    profile: str = Field(pattern=_STABLE_IDENTIFIER)
    github_environment: EnvironmentName


class CheckResult(_FrozenSafeModel):
    name: str = Field(pattern=_STABLE_IDENTIFIER)
    state: CheckState
    code: str = Field(pattern=_STABLE_IDENTIFIER)
    summary: str = Field(max_length=240)
    details: SafeDetails

    @model_validator(mode="after")
    def validate_details(self) -> CheckResult:
        if any(_STABLE_IDENTIFIER_RE.fullmatch(key) is None for key in self.details):
            raise ValueError("wave0_check_details_invalid")
        object.__setattr__(self, "details", FrozenDict(self.details))
        return self


class Wave0Config(_FrozenSafeModel):
    schema_version: Literal["mercury.aws.wave0.config.v1"]
    primary_region: Literal["ap-southeast-1"]
    github_repository: Literal["natthaphonchop2-creator/mercury-tools"]
    accounts: tuple[AccountTarget, ...]
    required_service_probes: tuple[ServiceProbeId, ...]

    @model_validator(mode="after")
    def validate_contract(self) -> Wave0Config:
        expected_environments = frozenset(EnvironmentName)
        if (
            len(self.accounts) != 2
            or {item.environment for item in self.accounts} != expected_environments
        ):
            raise ValueError("wave0_accounts_invalid")
        if len({item.alias for item in self.accounts}) != len(self.accounts):
            raise ValueError("wave0_accounts_invalid")
        if len({item.profile for item in self.accounts}) != len(self.accounts):
            raise ValueError("wave0_accounts_invalid")
        for item in self.accounts:
            expected_alias = (
                "mercury-nonprod"
                if item.environment is EnvironmentName.NONPROD
                else "mercury-prod"
            )
            if (
                item.alias != expected_alias
                or item.profile != item.alias
                or item.github_environment != item.environment
            ):
                raise ValueError("wave0_accounts_invalid")
        if tuple(probe.value for probe in self.required_service_probes) != _REQUIRED_SERVICE_PROBES:
            raise ValueError("wave0_service_probes_invalid")
        return self


class ReadinessReport(_FrozenSafeModel):
    schema_version: Literal["mercury.aws.wave0.report.v1"]
    primary_region: Literal["ap-southeast-1"]
    github_repository: Literal["natthaphonchop2-creator/mercury-tools"]
    checked_at: datetime
    accounts: tuple[AccountTarget, ...]
    checks: tuple[CheckResult, ...]
    gate_status: GateStatus
