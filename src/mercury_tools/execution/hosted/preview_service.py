"""Prepare encrypted immutable document previews without provider access."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.providers.models import ConnectionReadiness, ProviderConnection
from mercury_tools.v1.constants import PREVIEW_TTL_SECONDS
from mercury_tools.workspaces.models import WorkspaceMembership

from .models import (
    UNCONFIRMED_PAYLOAD_RETENTION,
    BatchDocumentCreate,
    DocumentCreateDraft,
    DocumentPreview,
    PreparedDocumentPreview,
    PrepareDocumentCreate,
    PreviewPayloadBinding,
    PreviewState,
    SingleDocumentCreate,
    StoredPreviewItem,
    authoritative_payload_bytes,
    preview_integrity_hash_for_hashes,
    preview_item_integrity_hash,
    preview_provider_call_hash_for_hashes,
)
from .projectors import (
    DocumentProjectorRegistry,
    ProjectedDocument,
    ProjectorError,
    ReviewedInvoiceProjector,
    provider_call_hash,
)
from .sanitization import sanitize_public_text
from .store import HostedPayloadVault, HostedPreviewError, HostedPreviewStore

_DOCUMENT_CREATE = re.compile(r"^documents\.([a-z][a-z0-9_]*)\.create$")
_MAX_SCHEMA_CONSTRAINED_INTEGER = 2_147_483_647
_CONSTRAINED_INTEGER_KEYWORDS = (
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minContains",
    "maxContains",
    "minProperties",
    "maxProperties",
)

MembershipResolver = Callable[
    [MercuryPrincipal, UUID],
    WorkspaceMembership | Awaitable[WorkspaceMembership],
]
ConnectionResolver = Callable[
    [WorkspaceMembership, MercuryPrincipal, UUID],
    ProviderConnection | Awaitable[ProviderConnection],
]
QualificationResolver = Callable[
    [ProviderConnection, str, str],
    ProviderMCPQualification | Awaitable[ProviderMCPQualification],
]


class UUIDFactory(Protocol):
    def __call__(self) -> UUID: ...


async def _await_value(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _offload(callback: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))

    def consume(task: asyncio.Task[Any]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            return

    task.add_done_callback(consume)
    return await asyncio.shield(task)


def _now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise HostedPreviewError("preview_state_invalid") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HostedPreviewError("preview_state_invalid")
    return value.astimezone(UTC)


def _next_uuid(factory: UUIDFactory) -> UUID:
    try:
        value = factory()
    except Exception:
        raise HostedPreviewError("preview_store_unavailable") from None
    if not isinstance(value, UUID) or value.int == 0:
        raise HostedPreviewError("preview_store_unavailable")
    return value


def _company_sha256(connection: ProviderConnection) -> str:
    return hashlib.sha256(connection.provider_account_id.encode("utf-8")).hexdigest()


def _schema_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        raise HostedPreviewError("capability_unavailable") from None
    if not isinstance(copied, dict):
        raise HostedPreviewError("capability_unavailable")
    return copied


def _require_closed_object_schemas(value: Any, *, root: bool = True) -> None:
    if isinstance(value, bool) or not isinstance(value, Mapping) or not value:
        raise HostedPreviewError("capability_unavailable")
    if any(
        key in value for key in ("$ref", "allOf", "anyOf", "oneOf", "not", "if", "then", "else")
    ):
        raise HostedPreviewError("capability_unavailable")
    for keyword in _CONSTRAINED_INTEGER_KEYWORDS:
        if keyword not in value:
            continue
        constrained = value[keyword]
        if (
            isinstance(constrained, bool)
            or not isinstance(constrained, int)
            or not 0 <= constrained <= _MAX_SCHEMA_CONSTRAINED_INTEGER
        ):
            raise HostedPreviewError("capability_unavailable")
    schema_type = value.get("type")
    if not isinstance(schema_type, str):
        raise HostedPreviewError("capability_unavailable")
    if root and schema_type != "object":
        raise HostedPreviewError("capability_unavailable")
    if schema_type == "object":
        properties = value.get("properties")
        required = value.get("required")
        if (
            not isinstance(properties, Mapping)
            or not properties
            or not isinstance(required, list)
            or not required
            or any(not isinstance(name, str) or name not in properties for name in required)
            or value.get("additionalProperties") is not False
            or value.get("unevaluatedProperties", False) is not False
            or "patternProperties" in value
        ):
            raise HostedPreviewError("capability_unavailable")
        for child in properties.values():
            _require_closed_object_schemas(child, root=False)
        return
    if schema_type == "array":
        items = value.get("items")
        if (
            not isinstance(items, Mapping)
            or not items
            or not isinstance(value.get("maxItems"), int)
            or isinstance(value["maxItems"], bool)
            or value["maxItems"] < 1
        ):
            raise HostedPreviewError("capability_unavailable")
        _require_closed_object_schemas(items, root=False)
        return
    if schema_type not in {"string", "integer", "boolean", "null"}:
        raise HostedPreviewError("capability_unavailable")


def _assert_no_floats(value: Any) -> None:
    if isinstance(value, float):
        raise HostedPreviewError("document_schema_invalid")
    if isinstance(value, Mapping):
        for child in value.values():
            _assert_no_floats(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_floats(child)


def _unique_review_codes(codes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(codes))


def _validate_exact_schema(
    qualification: ProviderMCPQualification,
    documents: tuple[DocumentCreateDraft, ...],
) -> None:
    schema = _schema_copy(qualification.input_schema)
    try:
        Draft202012Validator.check_schema(schema)
        _require_closed_object_schemas(schema)
    except HostedPreviewError:
        raise
    except Exception:
        raise HostedPreviewError("capability_unavailable") from None
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    try:
        for document in documents:
            provider_arguments = document.provider_arguments_copy()
            _assert_no_floats(provider_arguments)
            validator.validate(provider_arguments)
    except HostedPreviewError:
        raise
    except Exception:
        raise HostedPreviewError("document_schema_invalid") from None


def _validate_connection(
    membership: WorkspaceMembership,
    principal: MercuryPrincipal,
    workspace_id: UUID,
    connection_id: UUID,
    connection: ProviderConnection,
) -> ProviderConnection:
    try:
        checked = ProviderConnection.model_validate(connection)
    except (TypeError, ValueError, ValidationError):
        raise HostedPreviewError("preview_binding_changed") from None
    if (
        membership.workspace_id != workspace_id
        or checked.id != connection_id
        or checked.tenant_id != membership.tenant_id
        or checked.workspace_id != workspace_id
        or checked.auth_user_id != principal.subject
        or checked.readiness is not ConnectionReadiness.READY
    ):
        raise HostedPreviewError("preview_binding_changed")
    return checked


def _validate_qualification(
    connection: ProviderConnection,
    qualification: ProviderMCPQualification,
    *,
    capability_id: str,
    capability_version: str,
    now: datetime,
) -> ProviderMCPQualification:
    try:
        checked = ProviderMCPQualification.model_validate(qualification)
        _require_closed_object_schemas(_schema_copy(checked.input_schema))
    except HostedPreviewError:
        raise
    except (TypeError, ValueError, ValidationError):
        raise HostedPreviewError("capability_unavailable") from None
    if (
        _DOCUMENT_CREATE.fullmatch(capability_id) is None
        or checked.id is None
        or checked.qualification_state is not QualificationState.ENABLED
        or checked.normalized_capability != capability_id
        or checked.capability_version_sha256 != capability_version
        or checked.provider != connection.provider.value
        or checked.environment != connection.environment
        or checked.company_sha256 != _company_sha256(connection)
        or checked.evidence_revision_sha256 is None
        or checked.evidence_evaluated_at is None
        or checked.evidence_evaluated_at > now
        or checked.evidence_expires_at is None
        or checked.evidence_expires_at <= now
        or not set(checked.required_permissions).issubset(connection.granted_permissions)
    ):
        raise HostedPreviewError("capability_unavailable")
    return checked


def _request_documents(request: PrepareDocumentCreate) -> tuple[DocumentCreateDraft, ...]:
    try:
        if isinstance(request, SingleDocumentCreate):
            checked: PrepareDocumentCreate = SingleDocumentCreate.model_validate(request)
        elif isinstance(request, BatchDocumentCreate):
            checked = BatchDocumentCreate.model_validate(request)
        else:
            raise TypeError
    except (TypeError, ValueError, ValidationError):
        raise HostedPreviewError("document_payload_invalid") from None
    return checked.documents


class HostedPreviewService:
    """Validate, encrypt, persist, and return one immutable preview."""

    def __init__(
        self,
        *,
        store: HostedPreviewStore,
        payload_vault: HostedPayloadVault,
        membership_resolver: MembershipResolver,
        connection_resolver: ConnectionResolver,
        qualification_resolver: QualificationResolver,
        projector_registry: DocumentProjectorRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: UUIDFactory | None = None,
    ) -> None:
        if not isinstance(payload_vault, HostedPayloadVault):
            raise TypeError("hosted_preview_service_invalid")
        self._store = store
        self._payload_vault = payload_vault
        self._membership_resolver = membership_resolver
        self._connection_resolver = connection_resolver
        self._qualification_resolver = qualification_resolver
        self._projector_registry = projector_registry or DocumentProjectorRegistry(())
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    async def prepare_document_create(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        request: PrepareDocumentCreate,
        *,
        supersedes_preview_id: UUID | None = None,
    ) -> PreparedDocumentPreview:
        if not isinstance(principal, MercuryPrincipal):
            raise HostedPreviewError("workspace_access_denied")
        now = _now(self._clock)
        try:
            membership = WorkspaceMembership.model_validate(
                await _await_value(self._membership_resolver(principal, workspace_id))
            )
        except Exception:
            raise HostedPreviewError("workspace_access_denied") from None
        if membership.workspace_id != workspace_id:
            raise HostedPreviewError("workspace_access_denied")
        try:
            connection = _validate_connection(
                membership,
                principal,
                workspace_id,
                connection_id,
                await _await_value(self._connection_resolver(membership, principal, connection_id)),
            )
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("preview_binding_changed") from None
        try:
            qualification = _validate_qualification(
                connection,
                await _await_value(
                    self._qualification_resolver(connection, capability_id, capability_version)
                ),
                capability_id=capability_id,
                capability_version=capability_version,
                now=now,
            )
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("capability_unavailable") from None
        projector = self._projector_registry.resolve(qualification)
        if projector is None:
            raise HostedPreviewError("capability_unreviewed")
        documents = _request_documents(request)
        if len({document.client_item_id for document in documents}) != len(documents):
            raise HostedPreviewError("document_payload_invalid")
        _validate_exact_schema(qualification, documents)
        projections = self._project_documents(projector, documents)
        currencies = {projection.financials.currency for projection in projections}
        if len(currencies) != 1:
            raise HostedPreviewError("document_schema_invalid")

        provider_hashes = tuple(
            provider_call_hash(
                provider=connection.provider.value,
                environment=connection.environment,
                provider_tool_name=qualification.provider_tool_name,
                capability_id=qualification.normalized_capability,
                capability_version=qualification.capability_version_sha256,
                schema_hash=qualification.schema_hash,
                provider_arguments=document.provider_arguments_copy(),
            )
            for document in documents
        )
        if len(provider_hashes) != len(set(provider_hashes)):
            raise HostedPreviewError("duplicate_provider_call")
        item_integrity_hashes = tuple(
            preview_item_integrity_hash(
                client_item_id=document.client_item_id,
                provider_call_hash=provider_hash,
                projector_id=projector.projector_id,
                projector_version=projector.projector_version,
                document_type=projection.document_type,
                counterparty_display=projection.counterparty_display,
                issue_date=projection.issue_date,
                due_date=projection.due_date,
                financials=projection.financials,
                warnings=document.warnings,
                accountant_review_points=document.accountant_review_points,
            )
            for document, projection, provider_hash in zip(
                documents, projections, provider_hashes, strict=True
            )
        )
        provider_hash = preview_provider_call_hash_for_hashes(
            provider=connection.provider,
            environment=connection.environment,
            provider_tool_name=qualification.provider_tool_name,
            capability_id=qualification.normalized_capability,
            capability_version=qualification.capability_version_sha256,
            schema_hash=qualification.schema_hash,
            item_provider_call_hashes=provider_hashes,
        )
        account_display_name = sanitize_public_text(
            connection.account_display_name,
            code="preview_binding_changed",
        )
        warnings = _unique_review_codes(
            tuple(code for document in documents for code in document.warnings)
        )
        review_points = _unique_review_codes(
            tuple(code for document in documents for code in document.accountant_review_points)
        )
        integrity_hash = preview_integrity_hash_for_hashes(
            provider_call_hash=provider_hash,
            account_display_name=account_display_name,
            projector_id=projector.projector_id,
            projector_version=projector.projector_version,
            item_preview_integrity_hashes=item_integrity_hashes,
            warnings=warnings,
            accountant_review_points=review_points,
        )
        existing = await self._find_existing(
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            connection_id=connection.id,
            provider_call_hash=provider_hash,
        )
        if existing is not None:
            return self._recover_existing(existing, integrity_hash)

        preview = self._build_preview(
            now=now,
            membership=membership,
            principal=principal,
            connection=connection,
            qualification=qualification,
            projector=projector,
            documents=documents,
            projections=projections,
            provider_hashes=provider_hashes,
            item_integrity_hashes=item_integrity_hashes,
            provider_hash=provider_hash,
            integrity_hash=integrity_hash,
            account_display_name=account_display_name,
            warnings=warnings,
            review_points=review_points,
            supersedes_preview_id=supersedes_preview_id,
        )
        try:
            persisted = await _offload(self._store.create_preview, preview)
        except HostedPreviewError as error:
            if error.code not in {
                "preview_store_unavailable",
                "preview_conflict",
                "duplicate_provider_call",
            }:
                raise
            existing = await self._find_existing(
                tenant_id=membership.tenant_id,
                auth_user_id=principal.subject,
                workspace_id=workspace_id,
                connection_id=connection.id,
                provider_call_hash=provider_hash,
            )
            if existing is not None:
                return self._recover_existing(existing, integrity_hash)
            raise
        except Exception:
            raise HostedPreviewError("preview_store_unavailable") from None
        return PreparedDocumentPreview.from_preview(persisted)

    def _project_documents(
        self,
        projector: ReviewedInvoiceProjector,
        documents: tuple[DocumentCreateDraft, ...],
    ) -> tuple[ProjectedDocument, ...]:
        try:
            return tuple(
                projector.project(document.provider_arguments_copy()) for document in documents
            )
        except (ProjectorError, TypeError, ValueError):
            raise HostedPreviewError("document_schema_invalid") from None

    def _build_preview(
        self,
        *,
        now: datetime,
        membership: WorkspaceMembership,
        principal: MercuryPrincipal,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        projector: ReviewedInvoiceProjector,
        documents: tuple[DocumentCreateDraft, ...],
        projections: tuple[ProjectedDocument, ...],
        provider_hashes: tuple[str, ...],
        item_integrity_hashes: tuple[str, ...],
        provider_hash: str,
        integrity_hash: str,
        account_display_name: str,
        warnings: tuple[str, ...],
        review_points: tuple[str, ...],
        supersedes_preview_id: UUID | None,
    ) -> DocumentPreview:
        preview_id = _next_uuid(self._uuid_factory)
        provider_account_sha256 = _company_sha256(connection)
        expires_at = now + timedelta(seconds=PREVIEW_TTL_SECONDS)
        payload_purge_after = expires_at + UNCONFIRMED_PAYLOAD_RETENTION
        stored_items: list[StoredPreviewItem] = []
        for index, (document, projection, item_provider_hash, item_integrity_hash) in enumerate(
            zip(documents, projections, provider_hashes, item_integrity_hashes, strict=True)
        ):
            preview_item_id = _next_uuid(self._uuid_factory)
            binding = PreviewPayloadBinding(
                preview_id=preview_id,
                preview_item_id=preview_item_id,
                tenant_id=membership.tenant_id,
                auth_user_id=principal.subject,
                workspace_id=membership.workspace_id,
                connection_id=connection.id,
                provider=connection.provider,
                provider_account_sha256=provider_account_sha256,
                environment=connection.environment,
                qualification_id=qualification.id,
                provider_tool_name=qualification.provider_tool_name,
                capability_id=qualification.normalized_capability,
                capability_version=qualification.capability_version_sha256,
                schema_hash=qualification.schema_hash,
                evidence_revision_sha256=qualification.evidence_revision_sha256,
                projector_id=projector.projector_id,
                projector_version=projector.projector_version,
                connection_revision=connection.revision,
                connection_readiness=connection.readiness,
                preview_state_version=1,
                client_item_id=document.client_item_id,
                provider_call_hash=item_provider_hash,
                preview_integrity_hash=item_integrity_hash,
                created_at=now,
                expires_at=expires_at,
            )
            plaintext = bytearray(authoritative_payload_bytes(document))
            try:
                envelope = self._payload_vault.seal(binding, plaintext)
            finally:
                plaintext[:] = b"\x00" * len(plaintext)
            stored_items.append(
                StoredPreviewItem(
                    preview_item_id=preview_item_id,
                    preview_id=preview_id,
                    tenant_id=membership.tenant_id,
                    auth_user_id=principal.subject,
                    workspace_id=membership.workspace_id,
                    connection_id=connection.id,
                    item_index=index,
                    client_item_id=document.client_item_id,
                    provider_call_hash=item_provider_hash,
                    preview_integrity_hash=item_integrity_hash,
                    document_type=projection.document_type,
                    counterparty_display=projection.counterparty_display,
                    issue_date=projection.issue_date,
                    due_date=projection.due_date,
                    financials=projection.financials,
                    warnings=document.warnings,
                    accountant_review_points=document.accountant_review_points,
                    payload_envelope=envelope,
                    created_at=now,
                    payload_purge_after=payload_purge_after,
                )
            )
        items = tuple(stored_items)
        return DocumentPreview(
            preview_id=preview_id,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=membership.workspace_id,
            connection_id=connection.id,
            provider=connection.provider,
            provider_account_sha256=provider_account_sha256,
            account_display_name=account_display_name,
            environment=connection.environment,
            qualification_id=qualification.id,
            provider_tool_name=qualification.provider_tool_name,
            capability_id=qualification.normalized_capability,
            capability_version=qualification.capability_version_sha256,
            schema_hash=qualification.schema_hash,
            response_shape_hash=qualification.response_shape_hash,
            evidence_revision_sha256=qualification.evidence_revision_sha256,
            projector_id=projector.projector_id,
            projector_version=projector.projector_version,
            connection_revision=connection.revision,
            connection_readiness=connection.readiness,
            provider_call_hash=provider_hash,
            preview_integrity_hash=integrity_hash,
            state=PreviewState.AWAITING_CONFIRMATION,
            state_version=1,
            currency=items[0].financials.currency,
            subtotal=sum(item.financials.subtotal for item in items),
            discount_total=sum(item.financials.discount_total for item in items),
            vat_total=sum(item.financials.vat_total for item in items),
            withholding_tax_total=sum(item.financials.withholding_tax_total for item in items),
            grand_total=sum(item.financials.grand_total for item in items),
            warnings=warnings,
            accountant_review_points=review_points,
            items=items,
            supersedes_preview_id=supersedes_preview_id,
            created_at=now,
            expires_at=expires_at,
            payload_purge_after=payload_purge_after,
        )

    async def _find_existing(self, **identity: Any) -> DocumentPreview | None:
        finder = getattr(self._store, "find_preview_by_provider_call", None)
        if not callable(finder):
            return None
        try:
            value = await _offload(finder, **identity)
        except HostedPreviewError as error:
            if error.code == "preview_not_found":
                return None
            raise
        except Exception:
            return None
        if value is None:
            return None
        try:
            return DocumentPreview.model_validate(value)
        except (TypeError, ValueError, ValidationError):
            raise HostedPreviewError("preview_store_unavailable") from None

    def _recover_existing(
        self,
        preview: DocumentPreview,
        expected_integrity_hash: str,
    ) -> PreparedDocumentPreview:
        if preview.preview_integrity_hash != expected_integrity_hash:
            raise HostedPreviewError("duplicate_provider_call")
        return PreparedDocumentPreview.from_preview(preview)

    async def edit_document_preview(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        *,
        preview_id: UUID,
        request: PrepareDocumentCreate,
    ) -> PreparedDocumentPreview:
        if not isinstance(principal, MercuryPrincipal):
            raise HostedPreviewError("workspace_access_denied")
        try:
            membership = WorkspaceMembership.model_validate(
                await _await_value(self._membership_resolver(principal, workspace_id))
            )
        except Exception:
            raise HostedPreviewError("workspace_access_denied") from None
        try:
            original = await _offload(
                self._store.get_preview,
                tenant_id=membership.tenant_id,
                auth_user_id=principal.subject,
                workspace_id=workspace_id,
                preview_id=preview_id,
            )
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("preview_store_unavailable") from None
        if (
            original.connection_id != connection_id
            or original.capability_id != capability_id
            or original.capability_version != capability_version
        ):
            raise HostedPreviewError("preview_binding_changed")
        return await self.prepare_document_create(
            principal,
            workspace_id,
            connection_id,
            capability_id,
            capability_version,
            request,
            supersedes_preview_id=original.preview_id,
        )


__all__ = ["HostedPreviewService"]
