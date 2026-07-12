from __future__ import annotations

import hashlib
import json
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from mercury_tools.catalog.importers.service import import_spec
from mercury_tools.catalog.models import CatalogAction, RiskTier
from mercury_tools.drivers.base import ConnectorAuthError
from mercury_tools.drivers.models import AuthContext, ConnectorResult, CredentialField
from mercury_tools.drivers.registry import DriverRegistry
from mercury_tools.execution.executor import ERPExecutor, ExecutionPolicyError
from mercury_tools.execution.models import PreparedRequest
from mercury_tools.execution.policy import effective_risk
from mercury_tools.execution.request_builder import RequestBuildError, build_request
from mercury_tools.execution.store import LocalRequestStore, RequestStateError
from mercury_tools.local.audit import AuditLedger
from mercury_tools.local.repository import RepositoryConfig, RepositoryContext
from mercury_tools.safety.network import NetworkPolicy


class MutableCatalog:
    def __init__(self, actions: tuple[CatalogAction, ...]) -> None:
        self.active = {action.action_id: action for action in actions}
        self.versions = {
            (action.action_id, action.version_id): action for action in actions
        }

    def require(self, action_id: str) -> CatalogAction:
        try:
            return self.active[action_id]
        except KeyError:
            raise LookupError("catalog_action_not_found") from None

    def require_version(self, action_id: str, version_id: str) -> CatalogAction:
        try:
            return self.versions[(action_id, version_id)]
        except KeyError:
            raise LookupError("catalog_action_version_not_found") from None

    def activate(self, action: CatalogAction) -> None:
        self.active[action.action_id] = action
        self.versions[(action.action_id, action.version_id)] = action


class CredentialStoreSpy:
    fields = (CredentialField("token", secret=True, label="Token"),)

    def __init__(self) -> None:
        self.load_calls = 0
        self.values = {"token": "top-secret-token"}

    def load(
        self,
        connector_id: str,
        environment: str,
        fields: tuple[CredentialField, ...],
    ) -> dict[str, str]:
        self.load_calls += 1
        assert connector_id == "flowaccount"
        assert environment == "production"
        assert fields == self.fields
        return dict(self.values)


class FakeDriver:
    connector_id = "flowaccount"
    driver_id = "fake_bearer"

    def __init__(self) -> None:
        self.base_url = "https://erp.example.com/v1"
        self.auth_calls = 0
        self.raise_auth = False
        self.on_auth: Callable[[], None] | None = None

    def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
        assert environment == "production"
        return CredentialStoreSpy.fields

    def resolve_base_url(self, environment: str) -> str:
        assert environment == "production"
        return self.base_url

    def safe_probe_action(self, environment: str) -> str:
        return "GET /company"

    def prepare_files(
        self,
        *,
        action: CatalogAction,
        inputs: Mapping[str, Any],
        roots: tuple[Path, ...],
    ) -> tuple[object, ...]:
        return ()

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext:
        self.auth_calls += 1
        if self.raise_auth:
            raise ConnectorAuthError("authentication_failed")
        if self.on_auth is not None:
            self.on_auth()
        assert credentials == {"token": "top-secret-token"}
        return AuthContext(
            headers={"Authorization": "Bearer top-secret-token"},
            query={},
            expires_at=None,
        )

    async def validate_credentials(self, **_: object) -> object:
        raise AssertionError("not used by executor")

    def interpret_response(
        self,
        *,
        action: CatalogAction,
        response: httpx.Response,
        dispatched: bool,
    ) -> ConnectorResult:
        payload = response.json() if response.content else None
        return ConnectorResult(
            status="succeeded" if response.is_success else "failed",
            http_status=response.status_code,
            data=payload,
            summary="json_response",
            dispatched=dispatched,
        )

    def sanitize_response(self, action: CatalogAction, value: Any) -> Any:
        return value


class TokenDriver(FakeDriver):
    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext:
        self.auth_calls += 1
        token_response = await client.post(
            "https://auth.example.com/token",
            data={"grant_type": "client_credentials"},
        )
        token = token_response.json()["access_token"]
        return AuthContext(
            headers={"Authorization": f"Bearer {token}"},
            query={},
            expires_at=None,
        )


class PeerStream:
    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return ("93.184.216.34", 443) if name == "server_addr" else None


def response(
    request: httpx.Request,
    status: int = 200,
    payload: object = None,
) -> httpx.Response:
    return httpx.Response(
        status,
        request=request,
        json={} if payload is None else payload,
        extensions={"network_stream": PeerStream()},
    )


@pytest.fixture(autouse=True)
def global_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
        ],
    )


@pytest.fixture
def read_action(action_factory: Callable[..., CatalogAction]) -> CatalogAction:
    return action_factory(
        method="GET",
        path_template="/invoices/{id}",
        operation_id="getInvoice",
        capability="documents.invoice.get",
        input_schema={
            "path": {"id": {"type": "string"}},
            "query": {"include": {"type": "string"}},
            "headers": {},
            "body": {},
            "files": {},
        },
        risk_tier=RiskTier.SAFE_READ,
        required_confirmations=0,
        side_effects=(),
    )


@pytest.fixture
def executor_parts(
    repository_context: RepositoryContext,
    catalog_action: CatalogAction,
    read_action: CatalogAction,
) -> dict[str, Any]:
    driver = FakeDriver()
    registry = DriverRegistry()
    registry.register(driver)
    credentials = CredentialStoreSpy()
    catalog = MutableCatalog((catalog_action, read_action))
    audit = AuditLedger(repository_context.audit_dir / "audit.jsonl")
    return {
        "context": repository_context,
        "driver": driver,
        "registry": registry,
        "credentials": credentials,
        "catalog": catalog,
        "audit": audit,
    }


def make_executor(
    parts: dict[str, Any],
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    repository_config: RepositoryConfig | None = None,
) -> ERPExecutor:
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    context = parts["context"]
    return ERPExecutor(
        context=context,
        repository_config=repository_config or RepositoryConfig(),
        catalog=parts["catalog"],
        drivers=parts["registry"],
        credentials=parts["credentials"],
        request_store=LocalRequestStore(context),
        audit_ledger=parts["audit"],
        network=NetworkPolicy(),
        roots=(context.root,),
        client_factory=client_factory,
    )


def test_request_builder_rejects_traversal_and_unresolved_path(
    action_factory: Callable[..., CatalogAction],
    tmp_path: Path,
) -> None:
    action = action_factory(
        path_template="/invoices/{id}",
        input_schema={
            "path": {"id": {"type": "string"}},
            "query": {},
            "headers": {},
            "body": {},
            "files": {},
        },
    )
    with pytest.raises(RequestBuildError, match="^unresolved_path_parameter$"):
        build_request(action, "https://erp.example.com", {"path": {}}, (tmp_path,))
    with pytest.raises(RequestBuildError, match="^path_traversal$"):
        build_request(
            action,
            "https://erp.example.com",
            {"path": {"id": "../admin"}},
            (tmp_path,),
        )


def test_request_builder_rejects_undeclared_and_auth_overrides(
    catalog_action: CatalogAction,
    tmp_path: Path,
) -> None:
    with pytest.raises(RequestBuildError, match="^undeclared_request_input$"):
        build_request(
            catalog_action,
            "https://erp.example.com/v1",
            {"query": {"unexpected": "x"}},
            (tmp_path,),
        )
    with pytest.raises(RequestBuildError, match="^authentication_override_forbidden$"):
        build_request(
            catalog_action,
            "https://erp.example.com/v1",
            {"headers": {"Authorization": "Bearer attacker"}},
            (tmp_path,),
        )


@pytest.mark.parametrize("header_name", ["Content-Type", "content-type", "CONTENT-TYPE"])
def test_request_builder_rejects_user_content_type_overrides(
    action_factory: Callable[..., CatalogAction],
    repository_context: RepositoryContext,
    header_name: str,
) -> None:
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {header_name: {"type": "string"}},
            "body": {"type": "object", "properties": {}},
            "files": {},
        },
    )

    with pytest.raises(RequestBuildError, match="^content_type_override_forbidden$"):
        build_request(
            action,
            "https://erp.example.com",
            {"headers": {header_name: "multipart/form-data; boundary=user-boundary"}},
            (repository_context.root,),
        )


def test_request_builder_allows_declared_raw_content_type_and_writes_raw_body(
    action_factory: Callable[..., CatalogAction],
    repository_context: RepositoryContext,
) -> None:
    action = action_factory(
        content_type="text/plain",
        input_schema={
            "path": {},
            "query": {},
            "headers": {"Content-Type": {"type": "string"}},
            "body": {"type": "string"},
            "files": {},
        },
    )

    request = build_request(
        action,
        "https://erp.example.com",
        {"headers": {"Content-Type": "text/plain"}, "body": "plain journal memo"},
        (repository_context.root,),
    ).to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert request.headers["content-type"] == "text/plain"
    assert request.content == b"plain journal memo"


def test_request_builder_replaces_auth_content_type_for_multipart_rendering(
    action_factory: Callable[..., CatalogAction],
    repository_context: RepositoryContext,
) -> None:
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"note": {"type": "string"}},
            },
            "files": {},
        },
    )
    template = build_request(
        action,
        "https://erp.example.com",
        {"body": {"note": "transport-owned"}},
        (repository_context.root,),
    )

    request = template.to_httpx_request(
        AuthContext(
            headers={"Content-Type": "multipart/form-data; boundary=user-boundary"},
            query={},
            expires_at=None,
        )
    )

    content_type = request.headers["content-type"]
    boundary = content_type.partition("boundary=")[2]
    body = request.read()
    assert content_type.startswith("multipart/form-data; boundary=")
    assert boundary and boundary != "user-boundary"
    assert f"--{boundary}".encode() in body


def test_request_builder_binds_relative_file_hash_without_absolute_path(
    action_factory: Callable[..., CatalogAction],
    repository_context: RepositoryContext,
) -> None:
    document = repository_context.root / "invoice.pdf"
    document.write_bytes(b"invoice-evidence")
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {},
            "files": {"document": {"type": "string", "format": "binary"}},
        },
    )

    template = build_request(
        action,
        "https://erp.example.com/v1",
        {"files": {"document": str(document)}},
        (repository_context.root,),
        repository_id=repository_context.repository_id,
        environment="production",
    )
    serialized = json.dumps(template.binding_payload(), sort_keys=True)

    assert str(repository_context.root) not in serialized
    assert template.request_inputs["files"]["document"]["relative_path"] == "invoice.pdf"
    assert template.request_inputs["files"]["document"]["sha256"] == hashlib.sha256(
        b"invoice-evidence"
    ).hexdigest()


def test_request_builder_derives_cataloged_idempotency_header_from_bound_input(
    action_factory: Callable[..., CatalogAction],
    repository_context: RepositoryContext,
) -> None:
    action = action_factory(
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"reference": {"type": "string"}},
                "required": ["reference"],
            },
            "files": {},
        },
        idempotency={
            "header_name": "Idempotency-Key",
            "source": "body.reference",
        },
    )

    template = build_request(
        action,
        "https://erp.example.com/v1",
        {"body": {"reference": "INV-2026-001"}},
        (repository_context.root,),
        repository_id=repository_context.repository_id,
        environment="production",
    )

    assert template.request_inputs["headers"] == {
        "Idempotency-Key": "INV-2026-001"
    }
    assert template.binding_payload()["request_inputs"]["headers"] == {
        "Idempotency-Key": "INV-2026-001"
    }


def test_request_template_exposes_only_detached_input_copies(
    catalog_action: CatalogAction,
    repository_context: RepositoryContext,
) -> None:
    template = build_request(
        catalog_action,
        "https://erp.example.com/v1",
        {"body": {"amount": 100}},
        (repository_context.root,),
        repository_id=repository_context.repository_id,
        environment="production",
    )
    before = template.payload_hash()

    detached = template.request_inputs
    detached["body"]["amount"] = 999

    assert template.request_inputs["body"]["amount"] == 100
    assert template.payload_hash() == before


def test_request_template_public_summary_never_exposes_dynamic_business_keys(
    catalog_action: CatalogAction,
    repository_context: RepositoryContext,
) -> None:
    template = build_request(
        catalog_action,
        "https://erp.example.com/v1",
        {
            "body": {
                "person@example.com": "0105559999999",
                "Ada Lovelace": "INV-001",
            }
        },
        (repository_context.root,),
        repository_id=repository_context.repository_id,
        environment="production",
    )

    public = json.dumps(template.public_summary(), sort_keys=True)

    assert "person@example.com" not in public
    assert "0105559999999" not in public
    assert "Ada Lovelace" not in public
    assert "INV-001" not in public


def test_request_builder_rejects_encoded_traversal_in_base_path(
    catalog_action: CatalogAction,
    repository_context: RepositoryContext,
) -> None:
    with pytest.raises(RequestBuildError, match="^path_traversal$"):
        build_request(
            catalog_action,
            "https://erp.example.com/v1/%2e%2e/admin",
            {"body": {"amount": 100}},
            (repository_context.root,),
        )


@pytest.mark.asyncio
async def test_preview_does_not_load_credentials(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    executor = make_executor(executor_parts, lambda request: response(request))

    preview = await executor.preview_write(
        repository=executor_parts["context"],
        action=catalog_action,
        environment="production",
        inputs={"body": {"amount": 100}},
    )

    assert preview.state.value == "awaiting_confirmation"
    assert executor_parts["credentials"].load_calls == 0
    assert "top-secret-token" not in json.dumps(preview.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_private_network_override_is_rejected_for_production_environment(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    config = RepositoryConfig(
        connectors={
            "flowaccount": {
                "production": {
                    "network_policy": {"allow_private_network": True}
                }
            }
        }
    )
    executor = make_executor(
        executor_parts,
        lambda request: response(request),
        repository_config=config,
    )

    with pytest.raises(
        ExecutionPolicyError,
        match="^private_network_environment_invalid$",
    ):
        await executor.preview_write(
            repository=executor_parts["context"],
            action=catalog_action,
            environment="production",
            inputs={"body": {"amount": 100}},
        )

    assert executor_parts["credentials"].load_calls == 0


@pytest.mark.asyncio
async def test_run_read_validates_inputs_before_loading_credentials(
    executor_parts: dict[str, Any],
    read_action: CatalogAction,
) -> None:
    executor = make_executor(executor_parts, lambda request: response(request))

    with pytest.raises(RequestBuildError, match="^unresolved_path_parameter$"):
        await executor.run_read(
            repository=executor_parts["context"],
            action=read_action,
            environment="production",
            inputs={"path": {}},
        )

    assert executor_parts["credentials"].load_calls == 0


@pytest.mark.parametrize(
    ("missing_section", "expected_error"),
    [
        ("query", "required_query_parameter_missing"),
        ("headers", "required_header_parameter_missing"),
        ("files", "required_file_missing"),
        ("body", "required_body_missing"),
    ],
)
def test_request_builder_rejects_missing_required_inputs(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
    missing_section: str,
    expected_error: str,
) -> None:
    upload = repository_context.root / "document.txt"
    upload.write_text("document")
    action = action_factory(
        input_schema={
            "path": {},
            "query": {"mode": {"type": "string", "required": True}},
            "headers": {"X-Mode": {"type": "string", "required": True}},
            "body": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
                "x-mercury-required": True,
            },
            "files": {
                "document": {
                    "type": "string",
                    "format": "binary",
                    "required": True,
                }
            },
        },
        content_type="multipart/form-data",
    )
    inputs = {
        "query": {"mode": "create"},
        "headers": {"X-Mode": "strict"},
        "body": {},
        "files": {"document": str(upload)},
    }
    inputs.pop(missing_section)

    with pytest.raises(RequestBuildError, match=f"^{expected_error}$"):
        build_request(action, "https://erp.example.com", inputs, (repository_context.root,))


def test_request_builder_distinguishes_absent_from_present_empty_required_body(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = action_factory(
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
                "x-mercury-required": True,
            },
            "files": {},
        }
    )

    with pytest.raises(RequestBuildError, match="^required_body_missing$"):
        build_request(action, "https://erp.example.com", {}, (repository_context.root,))

    template = build_request(
        action,
        "https://erp.example.com",
        {"body": {}},
        (repository_context.root,),
    )
    request = template.to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert request.content == b"{}"


def test_request_builder_keeps_optional_body_independent_from_required_properties(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = action_factory(
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"reference": {"type": "string"}},
                "required": ["reference"],
                "additionalProperties": False,
            },
            "files": {},
        }
    )

    template = build_request(action, "https://erp.example.com", {}, (repository_context.root,))
    request = template.to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert request.content == b""
    with pytest.raises(RequestBuildError, match="^required_body_field_missing$"):
        build_request(
            action,
            "https://erp.example.com",
            {"body": {}},
            (repository_context.root,),
        )


def test_request_builder_accepts_valid_empty_object_required_list(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = action_factory(
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object", "required": []},
            "files": {},
        }
    )

    request = build_request(
        action,
        "https://erp.example.com",
        {},
        (repository_context.root,),
    ).to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert request.content == b""


@pytest.mark.parametrize(
    "body_schema",
    [
        {"type": "string", "required": []},
        {
            "type": "object",
            "properties": {" bad ": {"type": "string"}},
            "required": [" bad "],
        },
        {
            "type": "object",
            "properties": {"bad/name": {"type": "string"}},
            "required": ["bad/name"],
        },
    ],
)
def test_request_builder_rejects_noncanonical_body_required_contracts(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
    body_schema: dict[str, Any],
) -> None:
    action = action_factory(
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": body_schema,
            "files": {},
        }
    )

    with pytest.raises(RequestBuildError, match="^invalid_action_input_schema$"):
        build_request(action, "https://erp.example.com", {}, (repository_context.root,))


def test_request_builder_encodes_body_only_multipart_with_client_boundary(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"caption": {"type": "string"}},
                "required": ["caption"],
                "additionalProperties": False,
            },
            "files": {},
        },
    )

    template = build_request(
        action,
        "https://erp.example.com",
        {"body": {"caption": "Invoice"}, "files": {}},
        (repository_context.root,),
    )
    request = template.to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))
    content_type = request.headers["content-type"]
    boundary = content_type.partition("boundary=")[2]
    body = request.read()

    assert content_type.startswith("multipart/form-data; boundary=")
    assert boundary
    assert f"--{boundary}".encode() in body
    assert b'Content-Disposition: form-data; name="caption"' in body
    assert b"Invoice" in body
    assert b'application/json' not in body


def test_request_builder_encodes_files_only_and_body_plus_files_as_multipart(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    document = repository_context.root / "invoice.txt"
    document.write_bytes(b"invoice-document")
    action = action_factory(
        content_type="Multipart/Form-Data; boundary=ignored-upstream-boundary",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"caption": {"type": "string"}},
                "additionalProperties": False,
            },
            "files": {"document": {"type": "string", "format": "binary"}},
        },
    )

    for inputs, expected_parts in (
        ({"files": {"document": str(document)}}, (b"invoice-document",)),
        (
            {
                "body": {"caption": "Invoice"},
                "files": {"document": str(document)},
            },
            (b"Invoice", b"invoice-document"),
        ),
    ):
        request = build_request(
            action,
            "https://erp.example.com",
            inputs,
            (repository_context.root,),
        ).to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))
        content_type = request.headers["content-type"]
        body = request.read()

        assert content_type.startswith("multipart/form-data; boundary=")
        assert "ignored-upstream-boundary" not in content_type
        assert b'Content-Disposition: form-data; name="document"; filename="invoice.txt"' in body
        for expected in expected_parts:
            assert expected in body


def test_request_builder_leaves_empty_optional_multipart_unencoded(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object", "properties": {}},
            "files": {},
        },
    )

    request = build_request(
        action,
        "https://erp.example.com",
        {},
        (repository_context.root,),
    ).to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert request.content == b""
    assert "content-type" not in request.headers


def test_request_builder_treats_optional_multipart_none_body_as_absent(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object", "properties": {}},
            "files": {},
        },
    )

    template = build_request(
        action,
        "https://erp.example.com",
        {"body": None},
        (repository_context.root,),
    )
    request = template.to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert template.request_inputs["body"] == {}
    assert request.content == b""
    assert "content-type" not in request.headers


def test_request_builder_omits_none_body_from_multipart_file_wire(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    document = repository_context.root / "invoice.txt"
    document.write_bytes(b"invoice-document")
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object", "properties": {}},
            "files": {"document": {"type": "string", "format": "binary"}},
        },
    )

    request = build_request(
        action,
        "https://erp.example.com",
        {"body": None, "files": {"document": str(document)}},
        (repository_context.root,),
    ).to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))
    wire = request.read()

    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="document"; filename="invoice.txt"' in wire
    assert b"invoice-document" in wire
    assert b'name="body"' not in wire


def test_request_builder_rejects_none_for_required_multipart_body(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
) -> None:
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {},
                "x-mercury-required": True,
            },
            "files": {},
        },
    )

    with pytest.raises(RequestBuildError, match="^required_body_missing$"):
        build_request(
            action,
            "https://erp.example.com",
            {"body": None},
            (repository_context.root,),
        )


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/x-www-form-urlencoded",
        "text/plain",
        "multipart/form-data",
    ],
)
def test_request_builder_treats_optional_none_body_as_absent_for_every_transport(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
    content_type: str,
) -> None:
    action = action_factory(
        content_type=content_type,
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object", "properties": {}},
            "files": {},
        },
    )

    request = build_request(
        action,
        "https://erp.example.com",
        {"body": None},
        (repository_context.root,),
    ).to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert request.content == b""
    assert "content-type" not in request.headers


@pytest.mark.parametrize(
    ("content_type", "body_schema"),
    [
        ("application/json", {"type": "null", "x-mercury-required": True}),
        (
            "application/x-www-form-urlencoded",
            {"type": "object", "properties": {}, "x-mercury-required": True},
        ),
        ("text/plain", {"type": "string", "x-mercury-required": True}),
        (
            "multipart/form-data",
            {"type": "object", "properties": {}, "x-mercury-required": True},
        ),
    ],
)
def test_request_builder_rejects_none_for_required_body_for_every_transport(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
    content_type: str,
    body_schema: dict[str, Any],
) -> None:
    action = action_factory(
        content_type=content_type,
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": body_schema,
            "files": {},
        },
    )

    with pytest.raises(RequestBuildError, match="^required_body_missing$"):
        build_request(
            action,
            "https://erp.example.com",
            {"body": None},
            (repository_context.root,),
        )


@pytest.mark.parametrize(
    "body_schema",
    [
        {"type": "object", "properties": {"amount": "not-a-schema"}},
        {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": "not-a-schema"},
            },
        },
    ],
)
def test_request_builder_rejects_malformed_nested_body_schema(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
    body_schema: dict[str, Any],
) -> None:
    action = action_factory(
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": body_schema,
            "files": {},
        },
    )

    with pytest.raises(RequestBuildError, match="^invalid_action_input_schema$"):
        build_request(
            action,
            "https://erp.example.com",
            {"body": {}},
            (repository_context.root,),
        )


@pytest.mark.parametrize(
    ("content_type", "expected_header", "expected_body"),
    [
        ("application/json", "application/json", b'{"status":"draft"}'),
        (
            "application/x-www-form-urlencoded",
            "application/x-www-form-urlencoded",
            b"status=draft",
        ),
    ],
)
def test_request_builder_preserves_json_and_urlencoded_body_encodings(
    repository_context: RepositoryContext,
    action_factory: Callable[..., CatalogAction],
    content_type: str,
    expected_header: str,
    expected_body: bytes,
) -> None:
    action = action_factory(
        content_type=content_type,
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
            },
            "files": {},
        },
    )

    request = build_request(
        action,
        "https://erp.example.com",
        {"body": {"status": "draft"}},
        (repository_context.root,),
    ).to_httpx_request(AuthContext(headers={}, query={}, expires_at=None))

    assert request.headers["content-type"].startswith(expected_header)
    assert request.content == expected_body


@pytest.mark.asyncio
async def test_imported_write_preview_rejects_required_inputs_before_creation(
    executor_parts: dict[str, Any],
) -> None:
    class NetworkSpy:
        def __init__(self) -> None:
            self.validate_calls = 0

        def validate_base_url(self, *_: Any, **__: Any) -> None:
            self.validate_calls += 1

    context = executor_parts["context"]
    source_path = context.root / "required-preview-openapi.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {
                    "/documents": {
                        "post": {
                            "parameters": [
                                {
                                    "name": "mode",
                                    "in": "query",
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            ],
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {},
                                            "additionalProperties": False,
                                        }
                                    }
                                },
                            },
                            "responses": {"201": {"description": "Created"}},
                        }
                    }
                },
            }
        )
    )
    action = import_spec(
        context,
        connector_id="flowaccount",
        source_path=source_path,
    ).actions[0]
    executor_parts["catalog"] = MutableCatalog((action,))
    executor = make_executor(executor_parts, lambda request: response(request))
    network = NetworkSpy()
    executor.network = network  # type: ignore[assignment]

    with pytest.raises(RequestBuildError, match="^required_query_parameter_missing$"):
        await executor.preview_write(
            repository=context,
            action=action,
            environment="production",
            inputs={"body": {}},
        )

    assert executor_parts["credentials"].load_calls == 0
    assert network.validate_calls == 0
    preview = await executor.preview_write(
        repository=context,
        action=action,
        environment="production",
        inputs={"query": {"mode": "create"}, "body": {}},
    )
    assert preview.state.value == "awaiting_confirmation"
    assert network.validate_calls == 1


@pytest.mark.asyncio
async def test_required_multipart_fails_before_credentials_network_or_preview(
    executor_parts: dict[str, Any],
    action_factory: Callable[..., CatalogAction],
) -> None:
    class NetworkSpy:
        def __init__(self) -> None:
            self.validate_calls = 0

        def validate_base_url(self, *_: Any, **__: Any) -> None:
            self.validate_calls += 1

    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"caption": {"type": "string"}},
                "required": ["caption"],
                "x-mercury-required": True,
            },
            "files": {},
        },
    )
    executor_parts["catalog"] = MutableCatalog((action,))
    executor = make_executor(executor_parts, lambda request: response(request))
    network = NetworkSpy()
    executor.network = network  # type: ignore[assignment]

    for inputs, error in (
        ({}, "required_body_missing"),
        ({"body": {}}, "required_body_field_missing"),
    ):
        with pytest.raises(RequestBuildError, match=f"^{error}$"):
            await executor.preview_write(
                repository=executor_parts["context"],
                action=action,
                environment="production",
                inputs=inputs,
            )

    assert executor_parts["credentials"].load_calls == 0
    assert network.validate_calls == 0


@pytest.mark.asyncio
async def test_complete_body_only_multipart_dispatches_form_data(
    executor_parts: dict[str, Any],
    action_factory: Callable[..., CatalogAction],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(request, payload={"status": "created"})

    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {
                "type": "object",
                "properties": {"caption": {"type": "string"}},
                "required": ["caption"],
                "x-mercury-required": True,
            },
            "files": {},
        },
    )
    executor_parts["catalog"] = MutableCatalog((action,))
    executor = make_executor(executor_parts, handler)
    preview = await executor.preview_write(
        repository=executor_parts["context"],
        action=action,
        environment="production",
        inputs={"body": {"caption": "Invoice"}, "files": {}},
    )
    ready = executor.confirm_write(preview.request_id, preview.payload_hash)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "succeeded"
    assert len(requests) == 1
    assert requests[0].headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'Content-Disposition: form-data; name="caption"' in requests[0].read()
    assert b"Invoice" in requests[0].read()


@pytest.mark.asyncio
async def test_run_read_sends_one_cataloged_request_with_ephemeral_auth(
    executor_parts: dict[str, Any],
    read_action: CatalogAction,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(request, payload={"status": "ok"})

    executor = make_executor(executor_parts, handler)

    result = await executor.run_read(
        repository=executor_parts["context"],
        action=read_action,
        environment="production",
        inputs={"path": {"id": "INV-42"}, "query": {"include": "items"}},
    )

    assert result.status == "succeeded"
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://erp.example.com/v1/invoices/INV-42?include=items"
    )
    assert requests[0].headers["authorization"] == "Bearer top-secret-token"


async def confirmed_request(
    executor: ERPExecutor,
    context: RepositoryContext,
    action: CatalogAction,
) -> object:
    preview = await executor.preview_write(
        repository=context,
        action=action,
        environment="production",
        inputs={"body": {"amount": 100}},
    )
    return executor.confirm_write(preview.request_id, preview.payload_hash)


@pytest.mark.asyncio
async def test_timeout_after_send_becomes_outcome_unknown_and_is_not_retried(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    executor = make_executor(executor_parts, timeout)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "outcome_unknown"
    assert result.dispatched is True
    assert calls == 1
    with pytest.raises(RequestStateError, match="^replay_blocked_outcome_unknown$"):
        executor.request_store.assert_replay_allowed(ready.payload_hash)


@pytest.mark.asyncio
async def test_auth_failure_before_mutation_is_definitive(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    sends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return response(request)

    executor_parts["driver"].raise_auth = True
    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "failed"
    assert result.dispatched is False
    assert sends == 0
    assert executor.get_request_status(ready.request_id)["state"] == "failed"


@pytest.mark.asyncio
async def test_active_action_version_change_invalidates_confirmation(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
    action_factory: Callable[..., CatalogAction],
) -> None:
    executor = make_executor(executor_parts, lambda request: response(request))
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)
    changed = action_factory(description="Changed provider contract", source_hash="b" * 64)
    assert changed.action_id == catalog_action.action_id
    assert changed.version_id != catalog_action.version_id
    executor_parts["catalog"].activate(changed)

    with pytest.raises(
        RequestStateError,
        match="^preview_invalidated_action_version$",
    ):
        await executor.execute_write(ready.request_id)

    assert executor.get_request_status(ready.request_id)["failure_reason"] == (
        "preview_invalidated_action_version"
    )
    assert executor_parts["credentials"].load_calls == 0


@pytest.mark.asyncio
async def test_action_version_change_during_auth_is_rechecked_before_dispatch(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
    action_factory: Callable[..., CatalogAction],
) -> None:
    sends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return response(request)

    changed = action_factory(description="Changed during auth", source_hash="b" * 64)
    executor_parts["driver"].on_auth = lambda: executor_parts["catalog"].activate(changed)
    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    with pytest.raises(
        RequestStateError,
        match="^preview_invalidated_action_version$",
    ):
        await executor.execute_write(ready.request_id)

    assert sends == 0
    assert executor.get_request_status(ready.request_id)["state"] == "failed"


@pytest.mark.asyncio
async def test_target_change_during_auth_is_rechecked_before_dispatch(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    sends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return response(request)

    executor_parts["driver"].on_auth = lambda: setattr(
        executor_parts["driver"],
        "base_url",
        "https://other.example.com/v2",
    )
    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    with pytest.raises(RequestStateError, match="^preview_invalidated_target$"):
        await executor.execute_write(ready.request_id)

    assert sends == 0
    assert executor.get_request_status(ready.request_id)["state"] == "failed"


@pytest.mark.asyncio
async def test_base_url_change_after_confirmation_requires_a_new_preview(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    executor = make_executor(executor_parts, lambda request: response(request))
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)
    executor_parts["driver"].base_url = "https://other.example.com/v2"

    with pytest.raises(RequestStateError, match="^preview_invalidated_target$"):
        await executor.execute_write(ready.request_id)

    assert executor_parts["credentials"].load_calls == 0


@pytest.mark.asyncio
async def test_successful_write_sends_once_without_persisting_auth(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response(request, payload={"status": "ok", "documentId": 42})

    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "succeeded"
    assert result.dispatched is True
    assert len(requests) == 1
    assert str(requests[0].url) == "https://erp.example.com/v1/invoices"
    assert requests[0].headers["authorization"] == "Bearer top-secret-token"
    persisted = executor.request_store.get(ready.request_id).model_dump_json()
    audit_text = (executor_parts["context"].audit_dir / "audit.jsonl").read_text()
    assert "top-secret-token" not in persisted
    assert "top-secret-token" not in audit_text


@pytest.mark.asyncio
async def test_token_and_api_requests_are_each_revalidated_against_trusted_hosts(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = TokenDriver()
    registry = DriverRegistry()
    registry.register(driver)
    executor_parts["driver"] = driver
    executor_parts["registry"] = registry
    resolved_hosts: list[str] = []

    def resolve(host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        resolved_hosts.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.example.com":
            return response(request, payload={"access_token": "short-lived-token"})
        return response(request, payload={"status": "ok"})

    config = RepositoryConfig(
        trusted_hosts={
            "flowaccount": {
                "production": ("auth.example.com", "erp.example.com")
            }
        }
    )
    executor = make_executor(
        executor_parts,
        handler,
        repository_config=config,
    )
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "succeeded"
    assert resolved_hosts.count("auth.example.com") == 1
    assert resolved_hosts.count("erp.example.com") >= 3


@pytest.mark.asyncio
async def test_untrusted_token_host_fails_before_mutation_dispatch(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    driver = TokenDriver()
    registry = DriverRegistry()
    registry.register(driver)
    executor_parts["driver"] = driver
    executor_parts["registry"] = registry
    transport_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        return response(request, payload={"access_token": "must-not-be-used"})

    config = RepositoryConfig(
        trusted_hosts={"flowaccount": {"production": ("erp.example.com",)}}
    )
    executor = make_executor(
        executor_parts,
        handler,
        repository_config=config,
    )
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "failed"
    assert result.dispatched is False
    assert transport_calls == 0
    assert executor.get_request_status(ready.request_id)["state"] == "failed"


@pytest.mark.asyncio
async def test_server_error_after_dispatch_becomes_outcome_unknown(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    executor = make_executor(
        executor_parts,
        lambda request: response(request, status=503, payload={"error": "unavailable"}),
    )
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "outcome_unknown"
    assert executor.get_request_status(ready.request_id)["state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_fails_the_write_once(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://evil.example.com/collect"},
            json={"status": "redirect"},
            extensions={"network_stream": PeerStream()},
        )

    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "failed"
    assert calls == 1
    assert executor.get_request_status(ready.request_id)["state"] == "failed"


@pytest.mark.asyncio
async def test_peer_mismatch_after_write_response_is_outcome_unknown(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    class ReboundPeer:
        def get_extra_info(self, name: str) -> tuple[str, int] | None:
            return ("127.0.0.1", 443) if name == "server_addr" else None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"status": "ok"},
            extensions={"network_stream": ReboundPeer()},
        )

    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "outcome_unknown"
    assert result.dispatched is True
    assert executor.get_request_status(ready.request_id)["state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_cataloged_duplicate_preflight_blocks_mutation_before_post(
    executor_parts: dict[str, Any],
    action_factory: Callable[..., CatalogAction],
) -> None:
    preflight = action_factory(
        method="GET",
        path_template="/invoices",
        operation_id="findInvoiceByReference",
        capability="documents.invoice.search",
        input_schema={
            "path": {},
            "query": {"reference": {"type": "string"}},
            "headers": {},
            "body": {},
            "files": {},
        },
        risk_tier=RiskTier.SAFE_READ,
        required_confirmations=0,
        side_effects=(),
    )
    write_action = action_factory(
        preflight_action_ids=(preflight.action_id,),
        idempotency={
            "preflight_inputs": {
                preflight.action_id: {"query": {"reference": "INV-001"}}
            },
            "duplicate_action_id": preflight.action_id,
        },
    )
    executor_parts["catalog"] = MutableCatalog((write_action, preflight))
    post_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_calls
        if request.method == "POST":
            post_calls += 1
        return response(request, payload={"items": [{"id": 42}]})

    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], write_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "duplicate_blocked"
    assert result.dispatched is False
    assert post_calls == 0
    assert executor.get_request_status(ready.request_id)["state"] == "failed"
    audit_rows = [
        json.loads(line)
        for line in (executor_parts["context"].audit_dir / "audit.jsonl")
        .read_text()
        .splitlines()
    ]
    assert any(
        row.get("event") == "preflight_completed"
        and row.get("action_id") == preflight.action_id
        for row in audit_rows
    )


@pytest.mark.asyncio
async def test_preflight_transport_failure_is_audited_and_blocks_mutation(
    executor_parts: dict[str, Any],
    action_factory: Callable[..., CatalogAction],
) -> None:
    preflight = action_factory(
        method="GET",
        path_template="/invoices",
        operation_id="findInvoiceByReference",
        capability="documents.invoice.search",
        input_schema={
            "path": {},
            "query": {"reference": {"type": "string"}},
            "headers": {},
            "body": {},
            "files": {},
        },
        risk_tier=RiskTier.SAFE_READ,
        required_confirmations=0,
        side_effects=(),
    )
    write_action = action_factory(
        preflight_action_ids=(preflight.action_id,),
        idempotency={
            "preflight_inputs": {
                preflight.action_id: {"query": {"reference": "INV-001"}}
            }
        },
    )
    executor_parts["catalog"] = MutableCatalog((write_action, preflight))
    post_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_calls
        if request.method == "POST":
            post_calls += 1
            return response(request)
        raise httpx.ReadTimeout("preflight timed out", request=request)

    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], write_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "failed"
    assert result.dispatched is False
    assert post_calls == 0
    audit_rows = [
        json.loads(line)
        for line in (executor_parts["context"].audit_dir / "audit.jsonl")
        .read_text()
        .splitlines()
    ]
    assert any(
        row.get("event") == "preflight_completed"
        and row.get("action_id") == preflight.action_id
        and row.get("state") == "failed"
        for row in audit_rows
    )


@pytest.mark.asyncio
async def test_changed_bound_file_invalidates_preview_before_credentials(
    executor_parts: dict[str, Any],
    action_factory: Callable[..., CatalogAction],
) -> None:
    context = executor_parts["context"]
    attachment = context.root / "evidence.txt"
    attachment.write_text("before")
    action = action_factory(
        content_type="multipart/form-data",
        input_schema={
            "path": {},
            "query": {},
            "headers": {},
            "body": {"type": "object"},
            "files": {"document": {"type": "string", "format": "binary"}},
        },
    )
    executor_parts["catalog"] = MutableCatalog((action,))
    executor = make_executor(executor_parts, lambda request: response(request))
    preview = await executor.preview_write(
        repository=context,
        action=action,
        environment="production",
        inputs={"files": {"document": str(attachment)}},
    )
    ready = executor.confirm_write(preview.request_id, preview.payload_hash)
    attachment.write_text("after")

    with pytest.raises(RequestStateError, match="^preview_binding_changed$"):
        await executor.execute_write(ready.request_id)

    assert executor_parts["credentials"].load_calls == 0


@pytest.mark.asyncio
async def test_dispatch_audit_failure_fails_closed_before_network_send(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    class FailingDispatchAudit:
        def __init__(self, delegate: AuditLedger) -> None:
            self.delegate = delegate

        def record(self, event: Mapping[str, Any]) -> str:
            if event.get("event") == "dispatch_started":
                raise ValueError("audit_write_failed")
            return self.delegate.record(event)

    sends = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sends
        sends += 1
        return response(request)

    executor_parts["audit"] = FailingDispatchAudit(executor_parts["audit"])
    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)

    result = await executor.execute_write(ready.request_id)

    assert result.status == "failed"
    assert result.dispatched is False
    assert sends == 0
    assert executor.get_request_status(ready.request_id)["state"] == "failed"


def test_confirmation_audit_failure_invalidates_preview(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    class FailingConfirmationAudit:
        def __init__(self, delegate: AuditLedger) -> None:
            self.delegate = delegate

        def record(self, event: Mapping[str, Any]) -> str:
            if event.get("event") == "confirmation_recorded":
                raise ValueError("audit_write_failed")
            return self.delegate.record(event)

    executor_parts["audit"] = FailingConfirmationAudit(executor_parts["audit"])
    executor = make_executor(executor_parts, lambda request: response(request))
    template = build_request(
        catalog_action,
        "https://erp.example.com/v1",
        {"body": {"amount": 100}},
        (executor_parts["context"].root,),
        repository_id=executor_parts["context"].repository_id,
        environment="production",
    )
    prepared = PreparedRequest.from_template(
        repository=executor_parts["context"],
        action=catalog_action,
        environment="production",
        request=template,
        risk=effective_risk(catalog_action),
        payload_hash=template.payload_hash(),
    )
    preview = executor.request_store.create_preview(prepared, action=catalog_action)

    with pytest.raises(ValueError, match="^audit_write_failed$"):
        executor.confirm_write(preview.request_id, preview.payload_hash)

    status = executor.get_request_status(preview.request_id)
    assert status["state"] == "failed"
    assert status["failure_reason"] == "audit_failed"


@pytest.mark.asyncio
async def test_unknown_without_status_action_requires_manual_reconciliation(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    executor = make_executor(
        executor_parts,
        lambda request: (_ for _ in ()).throw(
            httpx.ReadTimeout("timed out", request=request)
        ),
    )
    ready = await confirmed_request(executor, executor_parts["context"], catalog_action)
    await executor.execute_write(ready.request_id)

    result = await executor.resolve_unknown_with_status(ready.request_id)

    assert result.status == "manual_reconciliation_required"
    assert executor.get_request_status(ready.request_id)["state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_unknown_status_action_can_conclusively_resolve_original_write(
    executor_parts: dict[str, Any],
    action_factory: Callable[..., CatalogAction],
) -> None:
    status_action = action_factory(
        method="GET",
        path_template="/operations/{id}",
        operation_id="getOperationStatus",
        capability="operations.status.get",
        input_schema={
            "path": {"id": {"type": "string"}},
            "query": {},
            "headers": {},
            "body": {},
            "files": {},
        },
        risk_tier=RiskTier.SAFE_READ,
        required_confirmations=0,
        side_effects=(),
    )
    write_action = action_factory(
        idempotency={
            "status_action_id": status_action.action_id,
            "status_inputs": {"path": {"id": "op-42"}},
            "status_result_path": "status",
            "success_values": ["completed"],
            "failure_values": ["failed"],
        }
    )
    executor_parts["catalog"] = MutableCatalog((write_action, status_action))
    write_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal write_calls
        if request.method == "POST":
            write_calls += 1
            raise httpx.ReadTimeout("timed out", request=request)
        return response(request, payload={"status": "completed"})

    executor = make_executor(executor_parts, handler)
    ready = await confirmed_request(executor, executor_parts["context"], write_action)
    await executor.execute_write(ready.request_id)

    result = await executor.resolve_unknown_with_status(ready.request_id)

    assert result.status == "succeeded"
    assert write_calls == 1
    assert executor.get_request_status(ready.request_id)["state"] == "succeeded"
    with pytest.raises(RequestStateError, match="^replay_blocked_active_request$"):
        executor.request_store.assert_replay_allowed(ready.payload_hash)


def test_get_request_status_never_returns_bound_business_payload(
    executor_parts: dict[str, Any],
    catalog_action: CatalogAction,
) -> None:
    template = build_request(
        catalog_action,
        "https://erp.example.com/v1",
        {"body": {"customer": "Ada Lovelace", "amount": 100}},
        (executor_parts["context"].root,),
        repository_id=executor_parts["context"].repository_id,
        environment="production",
    )
    assert "Ada Lovelace" not in json.dumps(template.public_summary())


@pytest.mark.asyncio
async def test_preview_rejects_read_action(
    executor_parts: dict[str, Any],
    read_action: CatalogAction,
) -> None:
    executor = make_executor(executor_parts, lambda request: response(request))

    with pytest.raises(ExecutionPolicyError, match="^read_action_cannot_be_previewed$"):
        await executor.preview_write(
            repository=executor_parts["context"],
            action=read_action,
            environment="production",
            inputs={"path": {"id": "1"}},
        )
