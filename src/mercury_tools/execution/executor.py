"""Local-only execution of immutable ERP catalog actions."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from mercury_tools.catalog.models import CatalogAction, HttpMethod, revalidate_catalog_action
from mercury_tools.drivers.base import ConnectorAuthError, ConnectorDriver
from mercury_tools.drivers.flowaccount import FlowAccountDriver
from mercury_tools.drivers.models import AuthContext, ConnectorResult
from mercury_tools.drivers.registry import DriverRegistry
from mercury_tools.execution.models import PreparedRequest, RequestState
from mercury_tools.execution.policy import MutationClass, effective_risk
from mercury_tools.execution.request_builder import (
    RequestBuildError,
    RequestTemplate,
    build_request,
    rebuild_bound_request,
)
from mercury_tools.execution.store import LocalRequestStore, RequestStateError
from mercury_tools.local.audit import AuditLedger
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import RepositoryConfig, RepositoryContext
from mercury_tools.qualification.manifest import (
    SandboxActionPolicy,
    SandboxExecutionManifest,
)
from mercury_tools.qualification.network import (
    SandboxOrigins,
    SandboxTenantBinding,
    execute_flowaccount_sandbox_action,
    sandbox_request_transport_eligible,
)
from mercury_tools.safety.network import NetworkPolicy, NetworkPolicyError, ResolvedTarget


class ExecutionPolicyError(ValueError):
    """A stable, payload-free execution policy failure."""


class CatalogResolver(Protocol):
    def require(self, action_id: str) -> CatalogAction: ...

    def require_version(self, action_id: str, version_id: str) -> CatalogAction: ...


class CredentialLoader(Protocol):
    def load(
        self,
        connector_id: str,
        environment: str,
        fields: Sequence[Any],
    ) -> dict[str, str]: ...


ClientFactory = Callable[..., httpx.AsyncClient]


class ERPExecutor:
    """Apply policy, confirmation, auth, dispatch, and audit around ERP calls."""

    def __init__(
        self,
        *,
        context: RepositoryContext,
        repository_config: RepositoryConfig,
        catalog: CatalogResolver,
        drivers: DriverRegistry,
        credentials: CredentialLoader | None = None,
        request_store: LocalRequestStore | None = None,
        audit_ledger: AuditLedger | None = None,
        network: NetworkPolicy | None = None,
        roots: Sequence[Path] = (),
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not isinstance(context, RepositoryContext):
            raise ValueError("invalid_repository_context")
        if not isinstance(repository_config, RepositoryConfig):
            raise ValueError("invalid_repository_config")
        self.context = context
        self.repository_config = repository_config
        self.catalog = catalog
        self.drivers = drivers
        self.credentials = credentials or CredentialStore(context)
        self.request_store = request_store or LocalRequestStore(context)
        self.audit_ledger = audit_ledger or AuditLedger(context.audit_dir / "audit.jsonl")
        self.network = network or NetworkPolicy()
        self.roots = tuple(roots) or (context.root,)
        self.client_factory = client_factory or httpx.AsyncClient
        self.local_session_id = "session_" + secrets.token_hex(12)

    async def run_read(
        self,
        *,
        repository: RepositoryContext,
        action: CatalogAction,
        environment: str,
        inputs: Mapping[str, Any],
    ) -> ConnectorResult:
        action = self._require_active_action(action)
        if action.method is not HttpMethod.GET:
            raise ExecutionPolicyError("write_action_requires_preview")
        driver, template, allow_private, allowed_hosts = self._prepare_template(
            repository,
            action,
            environment,
            inputs,
        )
        fields = driver.credential_fields(environment)
        credentials = self.credentials.load(action.connector_id, environment, fields)
        started = time.monotonic()
        try:
            async with self._client(allowed_hosts, allow_private) as client:
                try:
                    auth = await driver.prepare_auth(
                        environment=environment,
                        credentials=credentials,
                        client=client,
                    )
                    request = template.to_httpx_request(auth)
                    response = await client.send(request)
                    result = driver.interpret_response(
                        action=action,
                        response=response,
                        dispatched=True,
                    )
                except (ConnectorAuthError, NetworkPolicyError, httpx.TransportError):
                    result = _failed_result(dispatched=False)
                except Exception:
                    result = _failed_result(dispatched=True)
        finally:
            credentials.clear()
        self._audit_result(
            action=action,
            environment=environment,
            event="completed",
            state=result.status,
            result=result,
            latency_ms=_latency_ms(started),
        )
        return result

    async def run_flowaccount_sandbox_read(
        self,
        *,
        repository: RepositoryContext,
        action: CatalogAction,
        inputs: Mapping[str, Any],
        manifest: SandboxExecutionManifest,
        expected_tenant: SandboxTenantBinding,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> ConnectorResult:
        """Qualify one exact FlowAccount sandbox tenant before one reviewed read."""
        if not isinstance(expected_tenant, SandboxTenantBinding):
            raise ValueError("flowaccount_sandbox_expected_tenant_invalid")
        if action.connector_id != "flowaccount":
            raise ExecutionPolicyError("flowaccount_sandbox_connector_invalid")
        driver = self.drivers.get(action.connector_id)
        if not isinstance(driver, FlowAccountDriver):
            raise ExecutionPolicyError("flowaccount_sandbox_driver_invalid")

        def load_credentials() -> dict[str, str]:
            fields = driver.credential_fields("sandbox")
            return self.credentials.load("flowaccount", "sandbox", fields)

        async def request_hook(
            *,
            client: httpx.AsyncClient,
            auth: AuthContext,
            binding: SandboxTenantBinding,
            origins: SandboxOrigins,
            policy: SandboxActionPolicy,
        ) -> ConnectorResult:
            del binding, policy
            started = time.monotonic()
            request: httpx.Request | None = None
            try:
                active = self._require_active_action(action)
                if repository != self.context:
                    raise ExecutionPolicyError("repository_mismatch")
                if active.method is not HttpMethod.GET:
                    raise ExecutionPolicyError("sandbox_action_requires_safe_read")
                template = build_request(
                    active,
                    origins.api_url,
                    inputs,
                    self.roots,
                    repository_id=repository.repository_id,
                    environment="sandbox",
                )
                request = template.to_httpx_request(auth)
                response = await client.send(request)
                result = driver.interpret_response(
                    action=active,
                    response=response,
                    dispatched=True,
                )
            except (RequestBuildError, NetworkPolicyError, httpx.TransportError):
                result = _failed_result(
                    dispatched=(
                        request is not None and sandbox_request_transport_eligible(request)
                    )
                )
            except Exception:
                result = _failed_result(
                    dispatched=(
                        request is not None and sandbox_request_transport_eligible(request)
                    )
                )
            self._audit_result(
                action=action,
                environment="sandbox",
                event="completed",
                state=result.status,
                result=result,
                latency_ms=_latency_ms(started),
            )
            return result

        return await execute_flowaccount_sandbox_action(
            driver=driver,
            environment="sandbox",
            load_credentials=load_credentials,
            action=action,
            manifest=manifest,
            request_hook=request_hook,
            expected_tenant=expected_tenant,
            transport=transport,
            network=self.network,
        )

    async def preview_write(
        self,
        *,
        repository: RepositoryContext,
        action: CatalogAction,
        environment: str,
        inputs: Mapping[str, Any],
    ) -> PreparedRequest:
        action = self._require_active_action(action)
        if action.method is HttpMethod.GET:
            raise ExecutionPolicyError("read_action_cannot_be_previewed")
        _, template, _, _ = self._prepare_template(
            repository,
            action,
            environment,
            inputs,
        )
        risk = effective_risk(action)
        payload_hash = template.payload_hash()
        self.request_store.assert_replay_allowed(payload_hash)
        prepared = PreparedRequest.from_template(
            repository=repository,
            action=action,
            environment=environment,
            request=template,
            risk=risk,
            payload_hash=payload_hash,
        )
        created = self.request_store.create_preview(prepared, action=action)
        try:
            self._audit_request(created, event="preview_created")
        except Exception:
            self.request_store.invalidate(created.request_id, "audit_failed")
            raise
        return created

    def confirm_write(self, request_id: str, payload_hash: str) -> PreparedRequest:
        pending = self.request_store.get(request_id)
        return self._record_approval(
            request_id,
            payload_hash,
            pending.mutation_class,
            event="confirmation_recorded",
        )

    async def approve_and_execute(
        self,
        request_id: str,
        payload_hash: str,
        expected_class: MutationClass,
    ) -> ConnectorResult:
        approved = self._record_approval(
            request_id,
            payload_hash,
            expected_class,
            event="approval_recorded",
        )
        return await self.execute_write(approved.request_id)

    def _record_approval(
        self,
        request_id: str,
        payload_hash: str,
        expected_class: MutationClass,
        *,
        event: str,
    ) -> PreparedRequest:
        approved = self.request_store.approve(request_id, payload_hash, expected_class)
        try:
            self._audit_request(approved, event=event)
        except Exception:
            self.request_store.invalidate(approved.request_id, "audit_failed")
            raise
        return approved

    async def execute_write(self, request_id: str) -> ConnectorResult:
        prepared = self.request_store.require_ready(request_id)
        action = self._active_for_prepared(prepared)
        driver = self.drivers.get(prepared.connector_id)
        base_url = driver.resolve_base_url(prepared.environment)
        allow_private = self._allow_private(
            prepared.connector_id,
            prepared.environment,
        )
        try:
            self.network.validate_base_url(
                base_url,
                allow_private_network=allow_private,
            )
            template = rebuild_bound_request(
                action,
                base_url,
                prepared.request_inputs,
                self.roots,
                repository_id=prepared.repository_id,
                environment=prepared.environment,
            )
        except RequestBuildError as exc:
            reason = (
                "preview_invalidated_target"
                if str(exc) == "bound_target_changed"
                else "preview_binding_changed"
            )
            self.request_store.invalidate(prepared.request_id, reason)
            raise RequestStateError(reason) from None
        except NetworkPolicyError:
            self.request_store.invalidate(prepared.request_id, "preview_invalidated_target")
            raise RequestStateError("preview_invalidated_target") from None
        if not secrets.compare_digest(template.payload_hash(), prepared.payload_hash):
            self.request_store.invalidate(prepared.request_id, "preview_binding_changed")
            raise RequestStateError("preview_binding_changed")

        fields = driver.credential_fields(prepared.environment)
        credentials = self.credentials.load(
            prepared.connector_id,
            prepared.environment,
            fields,
        )
        allowed_hosts = self._allowed_hosts(prepared.connector_id, prepared.environment, base_url)
        started = time.monotonic()
        try:
            async with self._client(allowed_hosts, allow_private) as client:
                try:
                    auth = await driver.prepare_auth(
                        environment=prepared.environment,
                        credentials=credentials,
                        client=client,
                    )
                except (ConnectorAuthError, NetworkPolicyError, httpx.TransportError):
                    return self._fail_before_dispatch(
                        prepared,
                        action,
                        started,
                        error_code="authentication_failed",
                    )

                preflight = await self._run_preflights(
                    action=action,
                    prepared=prepared,
                    driver=driver,
                    auth=auth,
                    client=client,
                    allow_private=allow_private,
                )
                if preflight is not None:
                    return self._fail_before_dispatch(
                        prepared,
                        action,
                        started,
                        error_code=preflight,
                    )

                try:
                    request = template.to_httpx_request(auth)
                except RequestBuildError:
                    return self._fail_before_dispatch(
                        prepared,
                        action,
                        started,
                        error_code="validation_failed",
                    )
                self._active_for_prepared(prepared)
                if driver.resolve_base_url(prepared.environment) != base_url:
                    self.request_store.invalidate(
                        prepared.request_id,
                        "preview_invalidated_target",
                    )
                    raise RequestStateError("preview_invalidated_target")
                executing = self.request_store.start_execution(prepared.request_id)
                try:
                    self._audit_request(executing, event="dispatch_started")
                except Exception:
                    self.request_store.complete(
                        executing.request_id,
                        "failed",
                        {"status": "failed", "error_code": "validation_failed"},
                    )
                    return _failed_result(dispatched=False)
                try:
                    response = await client.send(request)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                    return self._complete_failed(
                        executing,
                        action,
                        started,
                        dispatched=False,
                        error_code="network_error",
                    )
                except NetworkPolicyError:
                    if request.extensions.get("mercury_response_received") is True:
                        return self._complete_unknown(executing, action, started)
                    return self._complete_failed(
                        executing,
                        action,
                        started,
                        dispatched=False,
                        error_code="network_error",
                    )
                except httpx.TransportError:
                    return self._complete_unknown(executing, action, started)

                if response.status_code >= 500:
                    return self._complete_unknown(
                        executing,
                        action,
                        started,
                        http_status=response.status_code,
                    )
                try:
                    result = driver.interpret_response(
                        action=action,
                        response=response,
                        dispatched=True,
                    )
                except Exception:
                    return self._complete_unknown(
                        executing,
                        action,
                        started,
                        http_status=response.status_code,
                    )
                return self._complete_result(executing, action, started, result)
        finally:
            credentials.clear()

    def get_request_status(self, request_id: str) -> dict[str, Any]:
        return self.request_store.get(request_id).public_dict()

    async def resolve_unknown_with_status(self, request_id: str) -> ConnectorResult:
        prepared = self.request_store.get(request_id)
        if prepared.state is not RequestState.OUTCOME_UNKNOWN:
            raise RequestStateError("request_not_outcome_unknown")
        action = self._active_for_prepared(prepared, invalidate=False)
        status_action_id = action.idempotency.get("status_action_id")
        if not isinstance(status_action_id, str) or not status_action_id:
            return _manual_reconciliation_result()
        try:
            status_action = self.catalog.require(status_action_id)
        except (LookupError, ValueError):
            return _manual_reconciliation_result()
        if status_action.method is not HttpMethod.GET:
            return _manual_reconciliation_result()
        status_inputs = action.idempotency.get("status_inputs", {})
        if not isinstance(status_inputs, Mapping):
            return _manual_reconciliation_result()
        result = await self.run_read(
            repository=self.context,
            action=status_action,
            environment=prepared.environment,
            inputs=status_inputs,
        )
        if result.status != "succeeded":
            return _manual_reconciliation_result()
        outcome = _status_outcome(action.idempotency, result.data)
        if outcome is None:
            return _manual_reconciliation_result()
        resolved = self.request_store.resolve_outcome_unknown(
            request_id,
            outcome,
            _response_summary(result),
        )
        final = ConnectorResult(
            status=outcome,
            http_status=result.http_status,
            data=result.data,
            summary="status_action_resolved",
            dispatched=False,
        )
        self._audit_request(
            resolved,
            event="execution_completed",
            response_summary=_response_summary(final),
        )
        return final

    def _prepare_template(
        self,
        repository: RepositoryContext,
        action: CatalogAction,
        environment: str,
        inputs: Mapping[str, Any],
    ) -> tuple[ConnectorDriver, RequestTemplate, bool, set[str]]:
        if repository != self.context:
            raise ExecutionPolicyError("repository_mismatch")
        driver = self.drivers.get(action.connector_id)
        base_url = driver.resolve_base_url(environment)
        allow_private = self._allow_private(action.connector_id, environment)
        template = build_request(
            action,
            base_url,
            inputs,
            self.roots,
            repository_id=repository.repository_id,
            environment=environment,
        )
        self.network.validate_base_url(
            base_url,
            allow_private_network=allow_private,
        )
        return (
            driver,
            template,
            allow_private,
            self._allowed_hosts(action.connector_id, environment, base_url),
        )

    def _require_active_action(self, action: CatalogAction) -> CatalogAction:
        try:
            action = revalidate_catalog_action(action)
            active = revalidate_catalog_action(self.catalog.require(action.action_id))
        except (AttributeError, LookupError, TypeError, ValueError):
            raise ExecutionPolicyError("catalog_action_not_active") from None
        if active.version_id != action.version_id:
            raise ExecutionPolicyError("catalog_action_not_active")
        return active

    def _active_for_prepared(
        self,
        prepared: PreparedRequest,
        *,
        invalidate: bool = True,
    ) -> CatalogAction:
        try:
            active = revalidate_catalog_action(self.catalog.require(prepared.action_id))
        except (AttributeError, LookupError, TypeError, ValueError):
            if invalidate:
                self.request_store.invalidate(
                    prepared.request_id,
                    "preview_invalidated_action_version",
                )
            raise RequestStateError("preview_invalidated_action_version") from None
        if active.version_id != prepared.version_id:
            if invalidate:
                self.request_store.invalidate(
                    prepared.request_id,
                    "preview_invalidated_action_version",
                )
            raise RequestStateError("preview_invalidated_action_version")
        try:
            version = revalidate_catalog_action(
                self.catalog.require_version(prepared.action_id, prepared.version_id)
            )
        except (AttributeError, LookupError, TypeError, ValueError):
            if invalidate:
                self.request_store.invalidate(
                    prepared.request_id,
                    "preview_invalidated_action_version",
                )
            raise RequestStateError("preview_invalidated_action_version") from None
        return version

    def _allowed_hosts(
        self,
        connector_id: str,
        environment: str,
        base_url: str,
    ) -> set[str]:
        hostname = urlsplit(base_url).hostname
        if not hostname:
            raise ExecutionPolicyError("invalid_connector_target")
        hosts = {hostname.rstrip(".").casefold()}
        hosts.update(
            host.rstrip(".").casefold()
            for host in self.repository_config.trusted_hosts.get(connector_id, {}).get(
                environment,
                (),
            )
        )
        return hosts

    def _allow_private(self, connector_id: str, environment: str) -> bool:
        allowed = self.repository_config.allow_private_network(connector_id, environment)
        if allowed and environment not in {"local", "gateway"}:
            raise ExecutionPolicyError("private_network_environment_invalid")
        return allowed

    def _client(
        self,
        allowed_hosts: set[str],
        allow_private: bool,
    ) -> httpx.AsyncClient:
        async def validate_request(request: httpx.Request) -> None:
            target = self.network.validate_request_url(
                str(request.url),
                allowed_hosts=allowed_hosts,
                allow_private_network=allow_private,
            )
            request.extensions["mercury_resolved_target"] = target

        async def validate_response(response: httpx.Response) -> None:
            target = response.request.extensions.get("mercury_resolved_target")
            if not isinstance(target, ResolvedTarget):
                raise NetworkPolicyError("remote_peer_unverified")
            response.request.extensions["mercury_response_received"] = True
            stream = response.extensions.get("network_stream")
            if stream is None or not hasattr(stream, "get_extra_info"):
                raise NetworkPolicyError("remote_peer_unverified")
            try:
                peer = stream.get_extra_info("server_addr")
            except Exception:
                raise NetworkPolicyError("remote_peer_unverified") from None
            if not (isinstance(peer, tuple) and peer and isinstance(peer[0], str)):
                raise NetworkPolicyError("remote_peer_unverified")
            target.verify_peer(peer[0])

        return self.client_factory(
            follow_redirects=False,
            timeout=httpx.Timeout(20.0),
            trust_env=False,
            event_hooks={
                "request": [validate_request],
                "response": [validate_response],
            },
        )

    async def _run_preflights(
        self,
        *,
        action: CatalogAction,
        prepared: PreparedRequest,
        driver: ConnectorDriver,
        auth: Any,
        client: httpx.AsyncClient,
        allow_private: bool,
    ) -> str | None:
        if not action.preflight_action_ids:
            return None
        configured = action.idempotency.get("preflight_inputs", {})
        if not isinstance(configured, Mapping):
            return "validation_failed"
        duplicate_action = action.idempotency.get("duplicate_action_id")
        for action_id in action.preflight_action_ids:
            try:
                preflight = revalidate_catalog_action(self.catalog.require(action_id))
            except (AttributeError, LookupError, TypeError, ValueError):
                return "validation_failed"
            if preflight.method is not HttpMethod.GET:
                return "validation_failed"
            values = configured.get(action_id, {})
            if not isinstance(values, Mapping):
                return "validation_failed"
            preflight_driver = self.drivers.get(preflight.connector_id)
            if preflight_driver.connector_id != driver.connector_id:
                return "validation_failed"
            base_url = preflight_driver.resolve_base_url(prepared.environment)
            try:
                self.network.validate_base_url(
                    base_url,
                    allow_private_network=allow_private,
                )
                template = build_request(
                    preflight,
                    base_url,
                    values,
                    self.roots,
                    repository_id=prepared.repository_id,
                    environment=prepared.environment,
                )
                response = await client.send(template.to_httpx_request(auth))
                result = preflight_driver.interpret_response(
                    action=preflight,
                    response=response,
                    dispatched=True,
                )
            except (RequestBuildError, NetworkPolicyError, httpx.TransportError) as exc:
                self._audit_result(
                    action=preflight,
                    environment=prepared.environment,
                    event="preflight_completed",
                    state="failed",
                    result=_failed_result(dispatched=isinstance(exc, httpx.TransportError)),
                    latency_ms=0,
                )
                return "request_failed"
            if result.status != "succeeded":
                self._audit_result(
                    action=preflight,
                    environment=prepared.environment,
                    event="preflight_completed",
                    state="failed",
                    result=result,
                    latency_ms=0,
                )
                return "request_failed"
            self._audit_result(
                action=preflight,
                environment=prepared.environment,
                event="preflight_completed",
                state="succeeded",
                result=result,
                latency_ms=0,
            )
            if duplicate_action == action_id and _truthy_provider_data(result.data):
                return "duplicate_blocked"
        return None

    def _fail_before_dispatch(
        self,
        prepared: PreparedRequest,
        action: CatalogAction,
        started: float,
        *,
        error_code: str,
    ) -> ConnectorResult:
        summary = {"status": "failed", "error_code": error_code}
        failed = self.request_store.fail_before_dispatch(
            prepared.request_id,
            reason="pre_dispatch_failed",
            response_summary=summary,
        )
        result = (
            ConnectorResult(
                status="duplicate_blocked",
                http_status=0,
                data=None,
                summary="duplicate_blocked",
                dispatched=False,
            )
            if error_code == "duplicate_blocked"
            else _failed_result(dispatched=False)
        )
        self._audit_request(failed, event="pre_dispatch_failed", response_summary=summary)
        self._audit_result(
            action=action,
            environment=prepared.environment,
            event="completed",
            state="failed",
            result=result,
            latency_ms=_latency_ms(started),
        )
        return result

    def _complete_failed(
        self,
        executing: PreparedRequest,
        action: CatalogAction,
        started: float,
        *,
        dispatched: bool,
        error_code: str,
    ) -> ConnectorResult:
        result = _failed_result(dispatched=dispatched)
        completed = self.request_store.complete(
            executing.request_id,
            "failed",
            {"status": "failed", "error_code": error_code},
        )
        self._audit_request(
            completed,
            event="execution_completed",
            response_summary={"status": "failed", "error_code": error_code},
        )
        return result

    def _complete_unknown(
        self,
        executing: PreparedRequest,
        action: CatalogAction,
        started: float,
        *,
        http_status: int = 0,
    ) -> ConnectorResult:
        result = ConnectorResult(
            status="outcome_unknown",
            http_status=http_status,
            data=None,
            summary="manual_reconciliation_required",
            dispatched=True,
        )
        summary: dict[str, Any] = {
            "status": "unknown",
            "outcome": "outcome_unknown",
            "status_class": "5xx" if http_status >= 500 else "timeout",
        }
        if http_status:
            summary["http_status"] = http_status
        completed = self.request_store.complete(
            executing.request_id,
            "outcome_unknown",
            summary,
        )
        self._audit_request(
            completed,
            event="execution_completed",
            response_summary=summary,
        )
        return result

    def _complete_result(
        self,
        executing: PreparedRequest,
        action: CatalogAction,
        started: float,
        result: ConnectorResult,
    ) -> ConnectorResult:
        if result.status == "outcome_unknown":
            summary = _response_summary(result)
            completed = self.request_store.complete(
                executing.request_id,
                "outcome_unknown",
                summary,
            )
            self._audit_request(
                completed,
                event="execution_completed",
                response_summary=summary,
            )
            return result
        outcome = "succeeded" if result.status == "succeeded" else "failed"
        summary = _response_summary(result)
        completed = self.request_store.complete(executing.request_id, outcome, summary)
        self._audit_request(
            completed,
            event="execution_completed",
            response_summary=summary,
        )
        return result

    def _audit_request(
        self,
        request: PreparedRequest,
        *,
        event: str,
        response_summary: Mapping[str, Any] | None = None,
    ) -> str:
        row: dict[str, Any] = {
            "event": event,
            "local_session_id": self.local_session_id,
            "repository_id": request.repository_id,
            "connector_id": request.connector_id,
            "environment": request.environment,
            "action_id": request.action_id,
            "version_id": request.version_id,
            "request_id": request.request_id,
            "method": request.method,
            "payload_hash": request.payload_hash,
            "risk_tier": int(request.risk_tier),
            "approval_level": request.approval_level.value,
            "mutation_class": request.mutation_class.value,
            "approval_count": request.approval_count,
            "state": request.state.value,
        }
        if response_summary:
            row["response_summary"] = dict(response_summary)
        return self.audit_ledger.record(row)

    def _audit_result(
        self,
        *,
        action: CatalogAction,
        environment: str,
        event: str,
        state: str,
        result: ConnectorResult,
        latency_ms: int,
    ) -> str:
        summary = _response_summary(result)
        summary["latency_ms"] = latency_ms
        row: dict[str, Any] = {
            "event": event,
            "local_session_id": self.local_session_id,
            "repository_id": self.context.repository_id,
            "connector_id": action.connector_id,
            "environment": environment,
            "action_id": action.action_id,
            "version_id": action.version_id,
            "method": action.method.value,
            "risk_tier": int(action.risk_tier),
            "state": state if state in {"succeeded", "failed"} else "failed",
            "latency_ms": latency_ms,
            "response_summary": summary,
        }
        if action.method is not HttpMethod.GET:
            risk = effective_risk(action)
            row.update(
                {
                    "risk_tier": int(risk.tier),
                    "approval_level": risk.approval_level.value,
                    "mutation_class": risk.mutation_class.value,
                }
            )
        return self.audit_ledger.record(row)


def _failed_result(*, dispatched: bool) -> ConnectorResult:
    return ConnectorResult(
        status="failed",
        http_status=0,
        data=None,
        summary="request_failed",
        dispatched=dispatched,
    )


def _manual_reconciliation_result() -> ConnectorResult:
    return ConnectorResult(
        status="manual_reconciliation_required",
        http_status=0,
        data=None,
        summary="status_action_required",
        dispatched=False,
    )


def _response_summary(result: ConnectorResult) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": "success" if result.status == "succeeded" else "failed",
        "success": result.status == "succeeded",
        "outcome": result.status
        if result.status in {"succeeded", "failed", "outcome_unknown"}
        else "unknown",
    }
    if result.http_status:
        summary["http_status"] = result.http_status
        summary["status_class"] = f"{result.http_status // 100}xx"
    return summary


def _latency_ms(started: float) -> int:
    return max(0, min(int((time.monotonic() - started) * 1000), 86_400_000))


def _status_outcome(metadata: Mapping[str, Any], data: Any) -> str | None:
    path = metadata.get("status_result_path", "status")
    if not isinstance(path, str) or not path:
        return None
    current = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    success = metadata.get("success_values", ("completed", "posted", "succeeded", "success"))
    failure = metadata.get("failure_values", ("failed", "rejected", "voided"))
    if isinstance(success, (list, tuple)) and current in success:
        return "succeeded"
    if isinstance(failure, (list, tuple)) and current in failure:
        return "failed"
    return None


def _truthy_provider_data(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key in ("items", "data", "records", "results"):
            if key in value:
                return bool(value[key])
    return bool(value)


__all__ = ["CatalogResolver", "ERPExecutor", "ExecutionPolicyError"]
