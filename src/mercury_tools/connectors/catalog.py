from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ConnectionMode(StrEnum):
    NATIVE_MCP = "native_mcp"
    API_DRIVER = "api_driver"
    LOCAL_BRIDGE = "local_bridge"


class CapabilityState(StrEnum):
    DECLARED = "declared"
    OBSERVED = "observed"
    ENABLED = "enabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NOT_AUTHORIZED = "not_authorized"
    NOT_VALIDATED = "not_validated"
    VALIDATION_FAILED = "validation_failed"
    POLICY_CONFIRMATION_REQUIRED = "policy_confirmation_required"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    LOCAL_BRIDGE_REQUIRED = "local_bridge_required"


class CapabilityClass(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class ConnectorModeManifest:
    mode: ConnectionMode
    status: str
    auth_modes: tuple[str, ...]
    supported_environments: tuple[str, ...]
    capability_source: str
    provider_capability_status: Mapping[str, CapabilityState]
    capability_aliases: Mapping[str, tuple[str, ...]]
    setup_defaults: Mapping[str, str] = field(default_factory=dict)
    official_mcp_url: str | None = None
    provider_setup_url: str | None = None
    local_bridge_requirement: str | None = None

    def __post_init__(self) -> None:
        provider_capability_status = dict(self.provider_capability_status)
        capability_aliases = {
            normalized_capability: tuple(provider_actions)
            for normalized_capability, provider_actions in self.capability_aliases.items()
        }
        setup_defaults = dict(self.setup_defaults)

        object.__setattr__(
            self,
            "provider_capability_status",
            MappingProxyType(provider_capability_status),
        )
        object.__setattr__(
            self,
            "capability_aliases",
            MappingProxyType(capability_aliases),
        )
        object.__setattr__(self, "setup_defaults", MappingProxyType(setup_defaults))

        declared_actions = set(provider_capability_status)
        for normalized_capability, provider_actions in capability_aliases.items():
            if not normalized_capability or not provider_actions:
                raise ValueError("capability_alias_invalid")
            if any(action not in declared_actions for action in provider_actions):
                raise ValueError("capability_alias_action_undeclared")

    def public_summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "status": self.status,
            "auth_modes": list(self.auth_modes),
            "supported_environments": list(self.supported_environments),
            "capability_source": self.capability_source,
            "provider_capability_status": {
                action: state.value
                for action, state in self.provider_capability_status.items()
            },
            "capability_aliases": {
                capability: list(actions)
                for capability, actions in self.capability_aliases.items()
            },
            "setup_defaults": dict(self.setup_defaults),
            "official_mcp_url": self.official_mcp_url,
            "provider_setup_url": self.provider_setup_url,
            "local_bridge_requirement": self.local_bridge_requirement,
        }


@dataclass(frozen=True)
class ConnectorValidation:
    method: str
    token_url: str = ""
    healthcheck_endpoint: str = ""
    safe_probe: bool = True

    @property
    def read_only_endpoint(self) -> str:
        """Backward-compatible alias for older callers."""
        return self.healthcheck_endpoint

    @property
    def read_only(self) -> bool:
        """Backward-compatible alias; probes are not write policy."""
        return self.safe_probe


@dataclass(frozen=True)
class ConnectorManifest:
    connector_id: str
    display_name: str
    status: str
    connection_modes: tuple[ConnectorModeManifest, ...]
    last_reviewed_at: str
    validation: ConnectorValidation = field(
        default_factory=lambda: ConnectorValidation(method="manual")
    )
    legacy_required_secret_fields: tuple[str, ...] = ()
    legacy_environment_presets: Mapping[str, Mapping[str, str]] = field(
        default_factory=dict
    )

    def connection_mode(self, mode: ConnectionMode | str) -> ConnectorModeManifest | None:
        normalized_mode = str(mode).strip().lower()
        return next(
            (
                connection_mode
                for connection_mode in self.connection_modes
                if connection_mode.mode.value == normalized_mode
            ),
            None,
        )

    @property
    def connection_mode_ids(self) -> list[str]:
        return [connection_mode.mode.value for connection_mode in self.connection_modes]

    def provider_capabilities(
        self,
        mode: ConnectionMode | str,
        normalized_capability: str,
    ) -> tuple[str, ...]:
        connection_mode = self.connection_mode(mode)
        if connection_mode is None:
            return ()
        normalized = str(normalized_capability).strip().lower()
        if normalized in connection_mode.capability_aliases:
            return connection_mode.capability_aliases[normalized]
        if normalized in connection_mode.provider_capability_status:
            return (normalized,)
        return ()

    def capability_state(
        self,
        mode: ConnectionMode | str,
        capability: str,
    ) -> CapabilityState | None:
        connection_mode = self.connection_mode(mode)
        if connection_mode is None:
            return None
        provider_actions = self.provider_capabilities(mode, capability)
        states = {
            connection_mode.provider_capability_status[action]
            for action in provider_actions
        }
        if len(states) == 1:
            return next(iter(states))
        return None

    def preset_for_environment(self, environment: str) -> dict[str, str]:
        normalized_environment = environment.strip().lower()
        return {
            **self.preset,
            **self.legacy_environment_presets.get(normalized_environment, {}),
        }

    @property
    def name(self) -> str:
        """Deprecated v0.3.x compatibility alias for display_name."""
        return self.display_name

    @property
    def environments(self) -> list[str]:
        """Deprecated v0.3.x aggregate of mode-specific environments."""
        return list(
            dict.fromkeys(
                environment
                for connection_mode in self.connection_modes
                for environment in connection_mode.supported_environments
            )
        )

    @property
    def preset(self) -> dict[str, str]:
        """Deprecated v0.3.x compatibility alias for reviewed setup defaults."""
        return dict(self.connection_modes[0].setup_defaults) if self.connection_modes else {}

    @property
    def required_secret_fields(self) -> list[str]:
        """Deprecated v0.3.x field-name compatibility metadata."""
        return list(self.legacy_required_secret_fields)

    @property
    def environment_presets(self) -> dict[str, dict[str, str]]:
        """Deprecated v0.3.x compatibility view for setup callers."""
        return {
            environment: dict(values)
            for environment, values in self.legacy_environment_presets.items()
        }

    @property
    def capabilities(self) -> list[str]:
        """Deprecated v0.3.x aggregate; callers must select a connection mode."""
        return list(
            dict.fromkeys(
                action
                for connection_mode in self.connection_modes
                for action in connection_mode.provider_capability_status
            )
        )

    @property
    def read_capabilities(self) -> list[str]:
        """Deprecated v0.3.x compatibility view for legacy callers."""
        return [
            capability
            for capability in self.capabilities
            if is_public_capability_allowed(capability)
        ]

    @property
    def blocked_capabilities(self) -> list[str]:
        """Deprecated v0.3.x compatibility view for legacy callers."""
        return [
            capability
            for capability in self.capabilities
            if not is_public_capability_allowed(capability)
        ]

    def summary(self) -> dict[str, Any]:
        return {
            **self.public_summary(),
            "name": self.name,
            "environments": self.environments,
            "required_secret_fields": self.required_secret_fields,
            "preset": self.preset,
            "validation": {
                "method": self.validation.method,
                "token_url": self.validation.token_url,
                "healthcheck_endpoint": self.validation.read_only_endpoint,
                "safe_probe": self.validation.read_only,
            },
        }

    def public_summary(self) -> dict[str, Any]:
        mode_summaries = [
            connection_mode.public_summary() for connection_mode in self.connection_modes
        ]
        return {
            "connector_id": self.connector_id,
            "display_name": self.display_name,
            "connection_mode_ids": self.connection_mode_ids,
            "connection_modes": mode_summaries,
            "auth_modes": {
                item["mode"]: item["auth_modes"] for item in mode_summaries
            },
            "supported_environments": {
                item["mode"]: item["supported_environments"] for item in mode_summaries
            },
            "capability_source": {
                item["mode"]: item["capability_source"] for item in mode_summaries
            },
            "provider_capability_status": {
                item["mode"]: item["provider_capability_status"]
                for item in mode_summaries
            },
            "setup_defaults": {
                item["mode"]: item["setup_defaults"] for item in mode_summaries
            },
            "local_bridge_requirement": {
                item["mode"]: item["local_bridge_requirement"]
                for item in mode_summaries
            },
            "last_reviewed_at": self.last_reviewed_at,
        }


FLOWACCOUNT_CAPABILITIES = (
    "auth.token.create",
    "company.info.read",
    "company.payment_channels.read",
    "company.settings.read",
    "contacts.list",
    "contacts.get",
    "contacts.create",
    "contacts.update",
    "products.list",
    "products.get",
    "products.create",
    "products.update",
    "documents.quotation.list",
    "documents.quotation.get",
    "documents.quotation.create",
    "documents.quotation.update",
    "documents.quotation.delete",
    "documents.billing_note.list",
    "documents.billing_note.get",
    "documents.billing_note.create",
    "documents.billing_note.update",
    "documents.billing_note.delete",
    "documents.invoice.list",
    "documents.invoice.get",
    "documents.invoice.create",
    "documents.invoice.update",
    "documents.invoice.delete",
    "documents.invoice.payment.create",
    "documents.receipt.list",
    "documents.receipt.get",
    "documents.receipt.create",
    "documents.receipt.update",
    "documents.receipt.delete",
    "documents.purchase_order.list",
    "documents.purchase_order.get",
    "documents.purchase_order.create",
    "documents.purchase_order.update",
    "documents.purchase_order.delete",
    "documents.receiving_inventory.list",
    "documents.receiving_inventory.get",
    "documents.receiving_inventory.create",
    "documents.receiving_inventory.update",
    "documents.receiving_inventory.delete",
    "documents.expense.list",
    "documents.expense.get",
    "documents.expense.create",
    "documents.expense.update",
    "documents.expense.delete",
    "documents.withholding_tax.list",
    "documents.withholding_tax.get",
    "documents.withholding_tax.create",
    "documents.withholding_tax.update",
    "documents.withholding_tax.delete",
    "documents.attachment.upload",
    "documents.email.send",
    "documents.share_link.create",
    "documents.status.update",
    "journal.draft.create",
    "journal.approve.create",
    "tax.vat_summary.read",
)

PEAK_CAPABILITIES = (
    "auth.client_token.create",
    "user.info.read",
    "contacts.get",
    "contacts.list",
    "contacts.create",
    "contacts.update",
    "products.get",
    "products.list",
    "products.create",
    "products.update",
    "services.get",
    "services.list",
    "services.create",
    "services.update",
    "payment_methods.list",
    "payment_methods.create",
    "documents.quotation.get",
    "documents.quotation.list",
    "documents.quotation.create",
    "documents.quotation.update",
    "documents.quotation.void",
    "documents.invoice.get",
    "documents.invoice.list",
    "documents.invoice.create",
    "documents.invoice.update",
    "documents.invoice.approve",
    "documents.invoice.payment.create",
    "documents.invoice.payment.void",
    "documents.receipt.get",
    "documents.receipt.list",
    "documents.receipt.create",
    "documents.receipt.update",
    "documents.receipt.void",
    "documents.receipt.create_from_invoice",
    "documents.expense.get",
    "documents.expense.list",
    "documents.expense.create",
    "documents.expense.update",
    "documents.expense.payment.create",
    "documents.purchase_order.get",
    "documents.purchase_order.list",
    "documents.purchase_order.create",
    "documents.billing_note.get",
    "documents.billing_note.create",
    "documents.credit_note.get",
    "documents.credit_note.create",
    "documents.credit_note_expense.get",
    "documents.credit_note_expense.create",
    "daily_journal.get",
    "daily_journal.create",
    "journal.account_code.read",
    "tags.create",
    "tags.remove",
    "files.attach",
    "invitation.create",
)


def _states(
    capabilities: tuple[str, ...],
    state: CapabilityState,
) -> dict[str, CapabilityState]:
    return {capability: state for capability in capabilities}


def _aliases(*, company_action: str, invoice_action: str) -> dict[str, tuple[str, ...]]:
    return {
        "company.read": (company_action,),
        "documents.invoice.read": (invoice_action,),
    }


CONNECTOR_CATALOG: list[ConnectorManifest] = [
    ConnectorManifest(
        connector_id="flowaccount",
        display_name="FlowAccount",
        status="available",
        connection_modes=(
            ConnectorModeManifest(
                mode=ConnectionMode.API_DRIVER,
                status="reviewed",
                auth_modes=("oauth_client_credentials",),
                supported_environments=("production", "sandbox"),
                capability_source="reviewed_api_catalog",
                provider_capability_status=_states(
                    FLOWACCOUNT_CAPABILITIES,
                    CapabilityState.NOT_VALIDATED,
                ),
                capability_aliases=_aliases(
                    company_action="company.info.read",
                    invoice_action="documents.invoice.get",
                ),
                setup_defaults={
                    "grant_type": "client_credentials",
                    "scope": "flowaccount-api",
                    "api_base_url": "https://openapi.flowaccount.com/v1",
                    "token_url": "https://openapi.flowaccount.com/v1/token",
                },
            ),
            ConnectorModeManifest(
                mode=ConnectionMode.NATIVE_MCP,
                status="available",
                auth_modes=("provider_managed",),
                supported_environments=("production",),
                capability_source="provider_documentation",
                provider_capability_status={
                    "company.info.read": CapabilityState.DECLARED,
                    "documents.invoice.list": CapabilityState.DECLARED,
                    "documents.invoice.get": CapabilityState.DECLARED,
                    "documents.invoice.create": CapabilityState.PROVIDER_UNAVAILABLE,
                },
                capability_aliases=_aliases(
                    company_action="company.info.read",
                    invoice_action="documents.invoice.get",
                ),
                official_mcp_url="https://mcp.flowaccount.com/mcp",
                provider_setup_url=(
                    "https://flowaccount.com/en/help-center/category/ai-connector-mcp"
                ),
            ),
        ),
        last_reviewed_at="2026-07-19",
        validation=ConnectorValidation(
            method="oauth_client_credentials",
            token_url="https://openapi.flowaccount.com/v1/token",
            healthcheck_endpoint="/company/info",
            safe_probe=True,
        ),
        legacy_required_secret_fields=("client_id", "client_secret"),
        legacy_environment_presets={
            "production": {
                "api_base_url": "https://openapi.flowaccount.com/v1",
                "token_url": "https://openapi.flowaccount.com/v1/token",
            },
            "sandbox": {
                "api_base_url": "https://openapi.flowaccount.com/test",
                "token_url": "https://openapi.flowaccount.com/test/token",
            },
        },
    ),
    ConnectorManifest(
        connector_id="peak",
        display_name="PEAK Accounting",
        status="available",
        connection_modes=(
            ConnectorModeManifest(
                mode=ConnectionMode.API_DRIVER,
                status="reviewed",
                auth_modes=("hmac_sha1_client_token",),
                supported_environments=("production", "uat", "sandbox"),
                capability_source="reviewed_api_catalog",
                provider_capability_status=_states(
                    PEAK_CAPABILITIES,
                    CapabilityState.NOT_VALIDATED,
                ),
                capability_aliases=_aliases(
                    company_action="user.info.read",
                    invoice_action="documents.invoice.get",
                ),
                setup_defaults={
                    "auth_method": "hmac_sha1_client_token",
                    "timestamp_format": "utc_yyyyMMddHHmmss",
                    "client_token_ttl_hours": "24",
                    "token_path": "/clienttoken",
                    "api_base_url": "https://api.peakaccount.com/api/v1",
                    "docs_url": "https://developers.peakaccount.com/reference/peak-open-api",
                },
            ),
        ),
        last_reviewed_at="2026-07-19",
        validation=ConnectorValidation(
            method="peak_hmac_client_token",
            token_url="https://peakengineapidev.azurewebsites.net/api/v1/clienttoken",
            healthcheck_endpoint="/user",
            safe_probe=True,
        ),
        legacy_required_secret_fields=(
            "connect_id",
            "connect_key",
            "application_code",
            "user_token",
        ),
        legacy_environment_presets={
            "production": {
                "api_base_url": "https://api.peakaccount.com/api/v1",
            },
            "uat": {
                "api_base_url": "https://peakengineapidev.azurewebsites.net/api/v1",
            },
            "sandbox": {
                "api_base_url": "https://peakengineapidev.azurewebsites.net/api/v1",
            },
        },
    ),
    ConnectorManifest(
        connector_id="express",
        display_name="Express Account",
        status="setup_target",
        connection_modes=(
            ConnectorModeManifest(
                mode=ConnectionMode.LOCAL_BRIDGE,
                status="needs_validation",
                auth_modes=("local_bridge",),
                supported_environments=("local", "gateway"),
                capability_source="local_bridge_discovery",
                provider_capability_status={},
                capability_aliases={},
                local_bridge_requirement="A separately installed local bridge is required.",
            ),
        ),
        last_reviewed_at="2026-07-19",
        legacy_required_secret_fields=("gateway_url", "api_key"),
    ),
    ConnectorManifest(
        connector_id="custom",
        display_name="Custom ERP",
        status="setup_target",
        connection_modes=(
            ConnectorModeManifest(
                mode=ConnectionMode.API_DRIVER,
                status="draft",
                auth_modes=("user_supplied",),
                supported_environments=("production", "sandbox", "gateway"),
                capability_source="imported_catalog",
                provider_capability_status={},
                capability_aliases={},
            ),
        ),
        last_reviewed_at="2026-07-19",
        legacy_required_secret_fields=("base_url", "api_key"),
    ),
    ConnectorManifest(
        connector_id="generic_mcp",
        display_name="Generic MCP",
        status="user_supplied",
        connection_modes=(
            ConnectorModeManifest(
                mode=ConnectionMode.NATIVE_MCP,
                status="user_supplied",
                auth_modes=("user_supplied",),
                supported_environments=("user_supplied",),
                capability_source="discovered_tools",
                provider_capability_status={},
                capability_aliases={},
            ),
        ),
        last_reviewed_at="2026-07-19",
    ),
]


PUBLIC_ALLOWED_EXACT = {"auth.token.create", "auth.client_token.create"}
PUBLIC_ALLOWED_SUFFIXES = (".read", ".list", ".get")
PUBLIC_BLOCKED_SEGMENTS = {
    "approve",
    "attach",
    "create",
    "delete",
    "invite",
    "payment",
    "post",
    "send",
    "share",
    "update",
    "upload",
    "void",
}


def is_public_capability_allowed(capability: str) -> bool:
    """Deprecated v0.3.x legacy policy helper; not part of public manifests."""
    normalized = str(capability or "").strip().lower()
    if normalized in PUBLIC_ALLOWED_EXACT:
        return True
    if any(segment in PUBLIC_BLOCKED_SEGMENTS for segment in normalized.split(".")):
        return False
    return normalized.endswith(PUBLIC_ALLOWED_SUFFIXES)


def public_capability_gate(capability: str) -> dict[str, Any] | None:
    """Deprecated v0.3.x legacy policy helper; not part of public manifests."""
    normalized = str(capability or "").strip().lower()
    if normalized and is_public_capability_allowed(normalized):
        return None
    return {
        "status": "blocked",
        "reason": "public_preview_read_only",
        "capability": normalized,
    }


def connector_by_id(connector_id: str) -> ConnectorManifest | None:
    clean = connector_id.strip().lower()
    return next((item for item in CONNECTOR_CATALOG if item.connector_id == clean), None)


def list_connector_summaries() -> list[dict[str, Any]]:
    return [item.summary() for item in CONNECTOR_CATALOG]


def list_connector_public_summaries() -> list[dict[str, Any]]:
    return [item.public_summary() for item in CONNECTOR_CATALOG]
