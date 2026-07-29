"""Qualified, bounded execution of hosted provider read capabilities."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification
from mercury_tools.mcp.generated_tools import catalog_wire_model, sanitize_provider_read_data
from mercury_tools.mcp.v1_schemas import ProviderReadEnvelope
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderOperationClass,
    ProviderRuntimeError,
    ProviderStatusClass,
)
from mercury_tools.providers.models import ProviderConnection
from mercury_tools.providers.streamable_mcp import (
    ProviderOperationDeadline,
    provider_operation_deadline,
)
from mercury_tools.qualification.provider_mcp import QualificationGateError
from mercury_tools.safety.redaction import redact_json
from mercury_tools.workspaces.models import WorkspaceMembership

RuntimeFactory: TypeAlias = Callable[[], Any | Awaitable[Any]]
MembershipResolver: TypeAlias = Callable[
    [MercuryPrincipal, UUID], WorkspaceMembership | Awaitable[WorkspaceMembership]
]
AuditRecorder: TypeAlias = Callable[[dict[str, object]], object | Awaitable[object]]
Sleep: TypeAlias = Callable[[float], object | Awaitable[object]]

_READ_DEADLINE_SECONDS = 5
_MAX_SAFE_READ_ATTEMPTS = 2
_RETRY_DELAYS = (0.05,)


async def _await_value(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _load_connection(
    runtime: Any,
    *,
    membership: WorkspaceMembership,
    workspace_id: UUID,
    principal: MercuryPrincipal,
    connection_id: UUID,
) -> ProviderConnection:
    result = await asyncio.to_thread(
        runtime.connection_store.load_connection,
        tenant_id=membership.tenant_id,
        workspace_id=workspace_id,
        auth_user_id=principal.subject,
        connection_id=connection_id,
    )
    return ProviderConnection.model_validate(await _await_value(result))


async def _close_runtime(runtime: Any) -> None:
    closer = getattr(runtime, "aclose", None)
    if callable(closer):
        await _await_value(closer())


def _identifier_sha256(value: UUID) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _input_sha256(inputs: BaseModel) -> str:
    encoded = json.dumps(
        inputs.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _retryable_read_failure(error: ProviderRuntimeError) -> bool:
    return error.dispatch_certainty is DispatchCertainty.NOT_DISPATCHED and error.status_class in {
        ProviderStatusClass.UNAVAILABLE,
        ProviderStatusClass.TIMEOUT,
    }


def _audit_event(
    *,
    workspace_id: UUID,
    connection_id: UUID,
    qualification: ProviderMCPQualification,
    envelope: ProviderReadEnvelope,
    inputs: BaseModel,
) -> dict[str, object]:
    return redact_json(
        {
            "tool_name": "generated_provider_read",
            "input": {
                "workspace_id_sha256": _identifier_sha256(workspace_id),
                "connection_id_sha256": _identifier_sha256(connection_id),
                "capability_id": qualification.normalized_capability,
                "capability_version": qualification.capability_version_sha256,
                "input_sha256": _input_sha256(inputs),
            },
            "output_summary": {
                "provider": qualification.provider,
                "environment": qualification.environment,
                "capability_id": envelope.capability_id,
                "capability_version": envelope.capability_version,
                "data_field_count": len(envelope.data),
            },
            "status": "ok",
            "metadata": {"runtime": "mcp", "surface": "v1"},
        }
    )


class HostedReadService:
    """Execute exactly one enabled, connection-bound, non-mutating capability."""

    def __init__(
        self,
        *,
        runtime_factory: RuntimeFactory,
        membership_resolver: MembershipResolver,
        audit_recorder: AuditRecorder | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._membership_resolver = membership_resolver
        self._audit_recorder = audit_recorder
        self._sleep = sleep or asyncio.sleep

    async def execute(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        inputs: BaseModel,
    ) -> ProviderReadEnvelope:
        """Resolve and execute one exact read without accepting provider authority from callers."""

        if not isinstance(principal, MercuryPrincipal) or not isinstance(inputs, BaseModel):
            raise ValueError("validation_failed")
        membership = WorkspaceMembership.model_validate(
            await _await_value(self._membership_resolver(principal, workspace_id))
        )
        if membership.workspace_id != workspace_id:
            raise ValueError("workspace_access_denied")
        runtime = await _await_value(self._runtime_factory())
        try:
            connection = await _load_connection(
                runtime,
                membership=membership,
                workspace_id=workspace_id,
                principal=principal,
                connection_id=connection_id,
            )
            if (
                connection.id != connection_id
                or connection.tenant_id != membership.tenant_id
                or connection.workspace_id != workspace_id
                or connection.auth_user_id != principal.subject
            ):
                raise ValueError("provider_connection_required")

            deadline = ProviderOperationDeadline.start(_READ_DEADLINE_SECONDS)
            with provider_operation_deadline(deadline):
                (
                    qualification,
                    binding,
                ) = await runtime.qualification_resolver.bind_exact_for_connection(
                    connection,
                    capability_id=capability_id,
                    capability_version=capability_version,
                    deadline=deadline,
                )
                qualification = ProviderMCPQualification.model_validate(qualification)
                if (
                    qualification.provider != connection.provider.value
                    or qualification.environment != connection.environment
                    or qualification.capability_version_sha256 != capability_version
                    or qualification.normalized_capability != capability_id
                    or binding.operation_class is not ProviderOperationClass.READ
                ):
                    raise QualificationGateError("capability_unavailable")

                input_model = catalog_wire_model(qualification.input_schema, kind="input")
                checked_inputs = input_model.model_validate(inputs)
                result = await self._call_read(
                    runtime,
                    connection=connection,
                    binding=binding,
                    inputs=checked_inputs,
                    deadline=deadline,
                )

            output_model = catalog_wire_model(qualification.output_schema, kind="output")
            sanitized = sanitize_provider_read_data(result.normalized_data)
            checked_output = output_model.model_validate(sanitized)
            data = checked_output.model_dump(mode="json")
            envelope = ProviderReadEnvelope(
                workspace_id=workspace_id,
                connection_id=connection_id,
                provider=connection.provider,
                company_display_name=connection.account_display_name,
                environment=connection.environment,
                capability_id=qualification.normalized_capability,
                capability_version=qualification.capability_version_sha256,
                data=data,
                next_allowed_actions=["list_provider_capabilities"],
            )
            if self._audit_recorder is not None:
                with suppress(Exception):
                    await _await_value(
                        self._audit_recorder(
                            _audit_event(
                                workspace_id=workspace_id,
                                connection_id=connection_id,
                                qualification=qualification,
                                envelope=envelope,
                                inputs=checked_inputs,
                            )
                        )
                    )
            return envelope
        finally:
            await _close_runtime(runtime)

    async def _call_read(
        self,
        runtime: Any,
        *,
        connection: ProviderConnection,
        binding: Any,
        inputs: BaseModel,
        deadline: ProviderOperationDeadline,
    ) -> ProviderCallResult:
        driver = runtime.registry.get(connection.provider)
        hosted_read = getattr(driver, "call_hosted_read", None)
        call = hosted_read if callable(hosted_read) else driver.call
        for attempt in range(_MAX_SAFE_READ_ATTEMPTS):
            deadline.check()
            try:
                result = await call(
                    connection,
                    binding,
                    inputs,
                    uuid4(),
                    deadline=deadline,
                )
                if result.status_class is not ProviderStatusClass.SUCCESS:
                    raise ValueError("capability_unavailable")
                return ProviderCallResult.model_validate(result)
            except ProviderRuntimeError as error:
                if attempt + 1 >= _MAX_SAFE_READ_ATTEMPTS or not _retryable_read_failure(error):
                    raise
                delay = _RETRY_DELAYS[attempt]
                if deadline.remaining() <= delay:
                    raise
                await _await_value(self._sleep(delay))
        raise ValueError("capability_unavailable")


__all__ = ["HostedReadService", "ProviderReadEnvelope"]
