from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.config import Settings
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderOperationClass,
    ProviderQualificationState,
    ProviderResponseInvalid,
    ProviderStatusClass,
    QualifiedCapabilityBinding,
    VerifiedRuntimeBinding,
)
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.peak import (
    PeakCredentialError,
    PeakCredentialHeaderFactory,
    PeakCredentialMaterial,
    PeakMCPDriver,
    PeakProfile,
    QualifiedPeakProviderContract,
    open_peak_credentials,
    seal_peak_credentials,
)
from mercury_tools.providers.registry import build_provider_registry
from mercury_tools.qualification.artifacts import (
    build_qualification_artifact,
    write_qualification_artifact,
)
from mercury_tools.qualification.provider_mcp import (
    CatalogQualificationResolver,
    OwnerAuthorizedCanary,
    transition_qualification,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_provider_manifest(ROOT / "catalog/global/peak/driver.json")
NOW = datetime(2026, 7, 29, 3, 0, tzinfo=UTC)
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
CONNECTION_ID = UUID("44444444-4444-4444-8444-444444444444")
USER_TOKEN = "PEAK_USER_TOKEN_SENTINEL"
CONNECT_ID = "PEAK_CONNECT_ID_SENTINEL"
CONNECT_KEY = "PEAK_CONNECT_KEY_SENTINEL"
APPLICATION_CODE = "PEAK_APPLICATION_CODE_SENTINEL"


class ReviewedProfileRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class ReviewedProfileResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    reviewed_merchant_identifier: str
    reviewed_display_name: str


def _settings() -> Settings:
    return Settings(
        supabase_url="",
        supabase_service_role_key="",
        openai_api_key="",
        flowaccount_mcp_sandbox_url="https://flowaccount-sandbox.example/mcp",
        flowaccount_mcp_production_url="https://flowaccount.example/mcp",
        peak_mcp_uat_url="https://peak-uat.example/mcp",
        peak_mcp_production_url="https://peak.example/mcp",
        peak_application_code=APPLICATION_CODE,
    )


def _vault() -> CredentialVault:
    nonces = iter(bytes([index]) * 12 for index in range(1, 32))
    return CredentialVault(
        active_key_version="v1",
        keys={"v1": b"k" * 32},
        clock=lambda: NOW,
        nonce_factory=lambda _size: next(nonces),
    )


def _connection(
    *,
    readiness: ConnectionReadiness = ConnectionReadiness.REQUIRES_VALIDATION,
) -> ProviderConnection:
    return ProviderConnection(
        id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
        provider=ProviderId.PEAK,
        environment="production",
        provider_account_id="merchant-123",
        account_display_name="PEAK Test Merchant",
        authorization_method=AuthorizationMethod.PROVIDER_CREDENTIALS,
        granted_permissions=("profile.read",),
        readiness=readiness,
        revision=1,
        last_validated_at=NOW if readiness is ConnectionReadiness.READY else None,
        credential_envelope_ids=(
            UUID("55555555-5555-4555-8555-555555555555"),
            UUID("66666666-6666-4666-8666-666666666666"),
            UUID("77777777-7777-4777-8777-777777777777"),
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _material() -> PeakCredentialMaterial:
    return PeakCredentialMaterial.from_values(
        user_token=USER_TOKEN,
        connect_id=CONNECT_ID,
        connect_key=CONNECT_KEY,
    )


def _assert_no_internal_secret_references(
    error: BaseException,
    *sentinels: str,
) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    pending: list[object] = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, str):
            rendered.append(value)
        elif isinstance(value, (bytes, bytearray)):
            rendered.append(bytes(value).decode("utf-8", errors="ignore"))
        elif isinstance(value, TracebackType):
            if "/src/mercury_tools/" in value.tb_frame.f_code.co_filename:
                pending.extend(value.tb_frame.f_locals.values())
            if value.tb_next is not None:
                pending.append(value.tb_next)
        elif isinstance(value, BaseException):
            pending.extend(value.args)
            if value.__cause__ is not None:
                pending.append(value.__cause__)
            if value.__context__ is not None:
                pending.append(value.__context__)
            if value.__traceback__ is not None:
                pending.append(value.__traceback__)
        elif isinstance(value, Mapping):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, Sequence) and not isinstance(value, str):
            pending.extend(value)
        elif (
            not isinstance(value, (ModuleType, type))
            and not callable(value)
            and hasattr(value, "__dict__")
        ):
            pending.extend(vars(value).values())
    combined = "\n".join(rendered)
    assert all(sentinel not in combined for sentinel in sentinels)


class FailingSealVault(CredentialVault):
    def __init__(self) -> None:
        super().__init__(
            active_key_version="v1",
            keys={"v1": b"k" * 32},
            clock=lambda: NOW,
            nonce_factory=lambda _size: b"z" * 12,
        )
        self.observed_plaintexts: list[bytes | bytearray] = []

    def seal(self, binding, plaintext):
        self.observed_plaintexts.append(plaintext)
        raise RuntimeError(USER_TOKEN)


class FailingOpenVault(CredentialVault):
    def __init__(self) -> None:
        super().__init__(
            active_key_version="v1",
            keys={"v1": b"k" * 32},
            clock=lambda: NOW,
            nonce_factory=lambda _size: b"y" * 12,
        )
        self.opened: list[bytearray] = []
        self.calls = 0

    def open(self, binding, envelope):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError(CONNECT_KEY)
        plaintext = super().open(binding, envelope)
        self.opened.append(plaintext)
        return plaintext


def _contract() -> QualifiedPeakProviderContract:
    resource_hash = hashlib.sha256(_settings().peak_mcp_production_url.encode("utf-8")).hexdigest()
    return QualifiedPeakProviderContract(
        fixture_id="reviewed-peak-contract-2026-07-29",
        qualification_hash="a" * 64,
        resource_uri_sha256_by_environment={"production": resource_hash},
        credential_header_names={
            "user_token": "X-Reviewed-User",
            "connect_id": "X-Reviewed-Connect",
            "connect_key": "X-Reviewed-Key",
        },
        application_code_header_name="X-Reviewed-Application",
        profile_tool="reviewed_provider_profile",
        profile_request_model=ReviewedProfileRequest,
        profile_response_model=ReviewedProfileResponse,
        profile_normalizer=lambda response: PeakProfile(
            merchant_id=response.reviewed_merchant_identifier,
            merchant_display_name=response.reviewed_display_name,
        ),
    )


class _StaticQualificationCatalog:
    def __init__(self, qualifications: tuple[ProviderMCPQualification, ...]) -> None:
        self._qualifications = qualifications

    def list_provider_mcp_qualifications(self) -> list[ProviderMCPQualification]:
        return list(self._qualifications)


def _qualified_resolver(
    tmp_path: Path,
    *,
    connection: ProviderConnection,
    contract: QualifiedPeakProviderContract,
) -> CatalogQualificationResolver:
    def definition(environment: str) -> ProviderMCPQualification:
        return ProviderMCPQualification.discovered(
            provider="peak",
            environment=environment,
            provider_tool_name=contract.profile_tool,
            normalized_capability="provider_profile.get",
            input_schema=contract.profile_request_model.model_json_schema(
                by_alias=True,
                mode="serialization",
            ),
            output_schema=contract.profile_response_model.model_json_schema(
                by_alias=True,
                mode="serialization",
            ),
            response_shape_hash="b" * 64,
            required_permissions=("profile.read",),
        )

    def advance_nonproduction(
        item: ProviderMCPQualification,
        company_sha256: str,
    ) -> tuple[ProviderMCPQualification, object]:
        artifact = build_qualification_artifact(
            definition=item,
            company_sha256=company_sha256,
            runner_version="test-runner-v1",
            evaluated_at=NOW,
            input_sha256="c" * 64,
            sanitized_result_identifier="test-result",
            checks={"schema": True},
            reviewer="reviewer",
            evidence_expires_at=datetime(2026, 8, 5, tzinfo=UTC),
            passed=True,
        )
        schema = transition_qualification(
            item,
            QualificationState.SCHEMA_VALIDATED,
            now=NOW,
        )
        qualified = transition_qualification(
            schema,
            QualificationState.NONPRODUCTION_QUALIFIED,
            evidence=artifact,
            now=NOW,
        )
        return (
            transition_qualification(
                qualified,
                QualificationState.ENABLED,
                evidence=artifact,
                now=NOW,
            ),
            artifact,
        )

    sandbox_enabled, sandbox_artifact = advance_nonproduction(definition("sandbox"), "d" * 64)
    production_definition = definition("production")
    production_artifact = build_qualification_artifact(
        definition=production_definition,
        company_sha256=hashlib.sha256(connection.provider_account_id.encode("utf-8")).hexdigest(),
        runner_version="test-runner-v1",
        evaluated_at=NOW,
        input_sha256="e" * 64,
        sanitized_result_identifier="test-result-production",
        checks={"schema": True},
        reviewer="reviewer",
        evidence_expires_at=datetime(2026, 8, 5, tzinfo=UTC),
        passed=True,
    )
    production_schema = transition_qualification(
        production_definition,
        QualificationState.SCHEMA_VALIDATED,
        now=NOW,
    )
    production_qualified = transition_qualification(
        production_schema,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=production_artifact,
        nonproduction_evidence=(sandbox_enabled,),
        nonproduction_artifacts=(sandbox_artifact,),
        now=NOW,
    )
    production_enabled = transition_qualification(
        production_qualified,
        QualificationState.ENABLED,
        evidence=production_artifact,
        nonproduction_evidence=(sandbox_enabled,),
        nonproduction_artifacts=(sandbox_artifact,),
        canary=OwnerAuthorizedCanary(
            provider="peak",
            environment="production",
            normalized_capability="provider_profile.get",
            provider_tool_name=contract.profile_tool,
            capability_version_sha256=production_definition.capability_version_sha256,
            owner_authorized_by="owner",
            authorized_at=NOW,
        ),
        now=NOW,
    )
    for artifact in (sandbox_artifact, production_artifact):
        write_qualification_artifact(tmp_path, artifact)
    return CatalogQualificationResolver(
        catalog=_StaticQualificationCatalog((sandbox_enabled, production_enabled)),
        catalog_root=str(tmp_path),
        now=lambda: NOW,
    )


def _profile_binding() -> QualifiedCapabilityBinding:
    return QualifiedCapabilityBinding(
        provider=ProviderId.PEAK,
        environment="production",
        normalized_capability="provider_profile.get",
        provider_tool="reviewed_provider_profile",
        operation_class=ProviderOperationClass.READ,
        qualification_hash="a" * 64,
    )


class RecordingRuntime:
    provider = ProviderId.PEAK

    def __init__(self) -> None:
        self.events: list[str] = []

    async def discover(self, _connection: ProviderConnection) -> ProviderDiscovery:
        self.events.append("discover")
        return ProviderDiscovery(
            provider=ProviderId.PEAK,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={"capabilities": ["provider_profile.get"]},
            dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
        )

    async def call(
        self,
        _connection: ProviderConnection,
        _binding: Any,
        _arguments: BaseModel,
        _operation_id: UUID,
    ) -> ProviderCallResult:
        self.events.append("call")
        return ProviderCallResult(
            provider=ProviderId.PEAK,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={
                "merchant_id": "merchant-123",
                "merchant_display_name": "PEAK Test Merchant",
            },
            dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
        )


class EnvelopeAwareRuntime(RecordingRuntime):
    def __init__(self, header_factory: PeakCredentialHeaderFactory) -> None:
        super().__init__()
        self._header_factory = header_factory
        self.observed_headers: tuple[tuple[str, str], ...] = ()

    async def call(
        self,
        connection: ProviderConnection,
        binding: Any,
        arguments: BaseModel,
        operation_id: UUID,
    ) -> ProviderCallResult:
        headers = await self._header_factory(connection)
        self.observed_headers = tuple((header.name, header.value) for header in headers.headers)
        return await super().call(connection, binding, arguments, operation_id)


def test_peak_credentials_are_sealed_under_three_canonical_names_and_cleared() -> None:
    vault = _vault()
    material = _material()

    envelopes = seal_peak_credentials(
        vault=vault,
        connection=_connection(),
        credentials=material,
    )
    rendered = " ".join(
        [
            repr(material),
            repr(envelopes),
            *(repr(envelope.storage_record()) for envelope in envelopes),
        ]
    )

    assert tuple(envelope.credential_type for envelope in envelopes) == (
        "connect_id",
        "connect_key",
        "user_token",
    )
    assert all(sentinel not in rendered for sentinel in (USER_TOKEN, CONNECT_ID, CONNECT_KEY))

    with open_peak_credentials(
        vault=vault,
        connection=_connection(),
        envelopes=envelopes,
    ) as opened:
        buffers = (opened.user_token, opened.connect_id, opened.connect_key)
        assert bytes(opened.user_token).decode() == USER_TOKEN
        assert bytes(opened.connect_id).decode() == CONNECT_ID
        assert bytes(opened.connect_key).decode() == CONNECT_KEY

    assert all(buffer == bytearray(len(buffer)) for buffer in buffers)
    material.clear()
    assert material.cleared


def test_sealing_failure_clears_mutable_inputs_and_drops_secret_exception_graph() -> None:
    vault = FailingSealVault()
    material = _material()

    with pytest.raises(PeakCredentialError, match="^peak_credentials_invalid$") as caught:
        seal_peak_credentials(
            vault=vault,
            connection=_connection(),
            credentials=material,
        )

    assert material.cleared
    assert vault.observed_plaintexts
    assert all(isinstance(value, bytearray) for value in vault.observed_plaintexts)
    assert all(value == bytearray(len(value)) for value in vault.observed_plaintexts)
    _assert_no_internal_secret_references(
        caught.value,
        USER_TOKEN,
        CONNECT_ID,
        CONNECT_KEY,
    )


def test_credential_material_failure_clears_partial_buffers_and_drops_inputs() -> None:
    with pytest.raises(PeakCredentialError, match="^peak_credentials_invalid$") as caught:
        PeakCredentialMaterial.from_values(
            user_token=USER_TOKEN,
            connect_id="\ud800",
            connect_key=CONNECT_KEY,
        )

    _assert_no_internal_secret_references(
        caught.value,
        USER_TOKEN,
        CONNECT_KEY,
    )


def test_opening_failure_clears_partial_plaintext_and_drops_secret_exception_graph() -> None:
    material = _material()
    envelopes = seal_peak_credentials(
        vault=_vault(),
        connection=_connection(),
        credentials=material,
    )
    material.clear()
    vault = FailingOpenVault()

    with (
        pytest.raises(PeakCredentialError, match="^peak_credentials_invalid$") as caught,
        open_peak_credentials(
            vault=vault,
            connection=_connection(),
            envelopes=envelopes,
        ),
    ):
        pytest.fail("opening must fail before yielding credentials")

    assert vault.opened
    assert all(value == bytearray(len(value)) for value in vault.opened)
    _assert_no_internal_secret_references(
        caught.value,
        USER_TOKEN,
        CONNECT_ID,
        CONNECT_KEY,
    )


def test_profile_normalization_drops_raw_payload_exception_graph() -> None:
    contract = _contract()
    verified = VerifiedRuntimeBinding(
        qualification_state=ProviderQualificationState.ENABLED,
        provider=ProviderId.PEAK,
        environment="production",
        resource_uri_sha256=hashlib.sha256(
            _settings().peak_mcp_production_url.encode("utf-8")
        ).hexdigest(),
        normalized_capability="provider_profile.get",
        capability_version="test-capability-version",
        provider_tool=contract.profile_tool,
        operation_class=ProviderOperationClass.READ,
        request_schema_sha256=contract.profile_request_schema_sha256,
        response_schema_sha256=contract.profile_response_schema_sha256,
        qualification_hash="a" * 64,
    )
    payload = {
        "reviewed_merchant_identifier": "merchant-123",
        "reviewed_display_name": "PEAK Test Merchant",
        "unexpected_secret": USER_TOKEN,
    }

    with pytest.raises(ValueError, match="^peak_profile_invalid$") as caught:
        contract.normalize_profile(verified, payload)

    _assert_no_internal_secret_references(caught.value, USER_TOKEN)


def test_auth_header_failure_clears_material_and_drops_decoded_secret_graph() -> None:
    material = PeakCredentialMaterial(
        user_token=bytearray(USER_TOKEN.encode("utf-8")),
        connect_id=bytearray(CONNECT_ID.encode("utf-8")),
        connect_key=bytearray(b"\xff"),
    )

    with pytest.raises(PeakCredentialError, match="^peak_credentials_invalid$") as caught:
        _contract().authorization_headers(
            material,
            application_code=APPLICATION_CODE,
        )

    assert material.cleared
    _assert_no_internal_secret_references(
        caught.value,
        USER_TOKEN,
        CONNECT_ID,
        APPLICATION_CODE,
    )


@pytest.mark.asyncio
async def test_qualified_fixture_alone_supplies_auth_mapping_and_application_code() -> None:
    vault = _vault()
    connection = _connection()
    material = _material()
    envelopes = seal_peak_credentials(
        vault=vault,
        connection=connection,
        credentials=material,
    )
    material.clear()
    factory = PeakCredentialHeaderFactory(
        vault=vault,
        load_envelopes=lambda _connection: envelopes,
        contract=_contract(),
        application_code=APPLICATION_CODE,
    )

    headers = await factory(connection)
    pairs = tuple((header.name, header.value) for header in headers.headers)

    assert pairs == (
        ("X-Reviewed-Application", APPLICATION_CODE),
        ("X-Reviewed-Connect", CONNECT_ID),
        ("X-Reviewed-Key", CONNECT_KEY),
        ("X-Reviewed-User", USER_TOKEN),
    )
    rendered = f"{factory!r} {headers!r} {headers.model_dump_json()}"
    assert all(
        sentinel not in rendered
        for sentinel in (USER_TOKEN, CONNECT_ID, CONNECT_KEY, APPLICATION_CODE)
    )


@pytest.mark.asyncio
async def test_peak_driver_fails_before_transport_without_qualified_contract(
    tmp_path: Path,
) -> None:
    runtime = RecordingRuntime()
    driver = PeakMCPDriver(
        runtime=runtime,
        manifest=MANIFEST,
        contract=None,
        qualification_resolver=CatalogQualificationResolver(
            catalog=_StaticQualificationCatalog(()),
            catalog_root=str(tmp_path),
            now=lambda: NOW,
        ),
    )

    with pytest.raises(ProviderResponseInvalid):
        await driver.discover(_connection())
    with pytest.raises(ProviderResponseInvalid):
        await driver.validate_connection(_connection())
    with pytest.raises(ProviderResponseInvalid):
        await driver.call(
            _connection(readiness=ConnectionReadiness.READY),
            _profile_binding(),
            ReviewedProfileRequest(),
            uuid4(),
        )

    assert runtime.events == []


@pytest.mark.asyncio
async def test_peak_driver_validates_only_qualified_provider_profile_contract(
    tmp_path: Path,
) -> None:
    runtime = RecordingRuntime()
    contract = _contract()
    resolver = _qualified_resolver(tmp_path, connection=_connection(), contract=contract)
    driver = PeakMCPDriver(
        runtime=runtime,
        manifest=MANIFEST,
        contract=contract,
        qualification_resolver=resolver,
    )

    validation = await driver.validate_connection(_connection())

    assert validation.normalized_data == {
        "merchant_id": "merchant-123",
        "merchant_display_name": "PEAK Test Merchant",
    }
    assert runtime.events == ["call"]
    with pytest.raises(ProviderResponseInvalid):
        await driver.call(
            _connection(readiness=ConnectionReadiness.READY),
            resolver.bind_for_connection(
                _connection(readiness=ConnectionReadiness.READY),
                normalized_capability="provider_profile.get",
                provider_tool_name=contract.profile_tool,
            ),
            ReviewedProfileRequest(),
            uuid4(),
        )


@pytest.mark.asyncio
async def test_provider_validation_drops_raw_runtime_exception_graph(tmp_path: Path) -> None:
    class FailingRuntime(RecordingRuntime):
        async def call(self, *_args: object) -> ProviderCallResult:
            raw_provider_payload = {"credential": USER_TOKEN}
            raise RuntimeError(raw_provider_payload)

    driver = PeakMCPDriver(
        runtime=FailingRuntime(),
        manifest=MANIFEST,
        contract=_contract(),
        qualification_resolver=_qualified_resolver(
            tmp_path,
            connection=_connection(),
            contract=_contract(),
        ),
    )

    with pytest.raises(ProviderResponseInvalid) as caught:
        await driver.validate_connection(_connection())

    _assert_no_internal_secret_references(caught.value, USER_TOKEN)


@pytest.mark.asyncio
async def test_peak_setup_validation_uses_request_scoped_envelopes_then_clears_override(
    tmp_path: Path,
) -> None:
    vault = _vault()
    connection = _connection()
    material = _material()
    envelopes = seal_peak_credentials(
        vault=vault,
        connection=connection,
        credentials=material,
    )
    material.clear()
    persistent_loads = 0

    def reject_persistent_load(_connection: ProviderConnection):
        nonlocal persistent_loads
        persistent_loads += 1
        raise AssertionError("provisional setup must not load a persisted connection")

    factory = PeakCredentialHeaderFactory(
        vault=vault,
        load_envelopes=reject_persistent_load,
        contract=(contract := _contract()),
        application_code=APPLICATION_CODE,
    )
    runtime = EnvelopeAwareRuntime(factory)
    driver = PeakMCPDriver(
        runtime=runtime,
        manifest=MANIFEST,
        contract=contract,
        qualification_resolver=_qualified_resolver(
            tmp_path,
            connection=connection,
            contract=contract,
        ),
    )

    profile = await driver.validate_setup(connection, envelopes)

    assert profile == PeakProfile(
        merchant_id="merchant-123",
        merchant_display_name="PEAK Test Merchant",
    )
    assert runtime.observed_headers == (
        ("X-Reviewed-Application", APPLICATION_CODE),
        ("X-Reviewed-Connect", CONNECT_ID),
        ("X-Reviewed-Key", CONNECT_KEY),
        ("X-Reviewed-User", USER_TOKEN),
    )
    assert persistent_loads == 0
    with pytest.raises(PeakCredentialError, match="^peak_credentials_invalid$"):
        await factory(connection)
    assert persistent_loads == 1


def test_registry_wraps_peak_in_fail_closed_provider_driver() -> None:
    dependencies = {
        "header_factories": {
            AuthorizationMethod.OAUTH2_PKCE: lambda _connection: None,
        },
        "response_normalizer": lambda _binding, _content: None,
        "request_model_resolver": lambda _binding: ReviewedProfileRequest,
        "response_model_resolver": lambda _binding: ReviewedProfileResponse,
    }

    registry = build_provider_registry(
        settings=_settings(),
        manifest_root=ROOT / "catalog/global",
        **dependencies,
    )

    assert isinstance(registry.get("peak"), PeakMCPDriver)
    assert registry.get("peak").contract_qualified is False


def test_hosted_peak_source_never_imports_legacy_rest_or_infers_rest_headers() -> None:
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "src/mercury_tools/providers/peak.py",
            "src/mercury_tools/providers/peak_setup.py",
            "src/mercury_tools/providers/registry.py",
        )
    )

    assert "mercury_tools.drivers.peak" not in sources
    assert "PeakDriver" not in sources
    assert "Client-Token" not in sources
    assert "User-Token" not in sources
    assert "Time-Stamp" not in sources
    assert "Time-Signature" not in sources
