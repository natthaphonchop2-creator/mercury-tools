from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

FLOWACCOUNT_CAPABILITIES = [
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
    normalized = str(capability or "").strip().lower()
    if normalized in PUBLIC_ALLOWED_EXACT:
        return True
    if any(segment in PUBLIC_BLOCKED_SEGMENTS for segment in normalized.split(".")):
        return False
    return normalized.endswith(PUBLIC_ALLOWED_SUFFIXES)


def public_capability_gate(capability: str) -> dict[str, Any] | None:
    normalized = str(capability or "").strip().lower()
    if normalized and is_public_capability_allowed(normalized):
        return None
    return {
        "status": "blocked",
        "reason": "public_preview_read_only",
        "capability": normalized,
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
        """Backward-compatible alias; safe setup probes are not product write policy."""
        return self.safe_probe


@dataclass(frozen=True)
class ConnectorManifest:
    connector_id: str
    name: str
    status: str
    environments: list[str]
    required_secret_fields: list[str]
    preset: dict[str, str] = field(default_factory=dict)
    environment_presets: dict[str, dict[str, str]] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    validation: ConnectorValidation = field(
        default_factory=lambda: ConnectorValidation(method="manual")
    )

    def preset_for_environment(self, environment: str) -> dict[str, str]:
        selected = environment.strip().lower()
        return {
            **self.preset,
            **self.environment_presets.get(selected, {}),
        }

    @property
    def read_capabilities(self) -> list[str]:
        return [
            capability
            for capability in self.capabilities
            if is_public_capability_allowed(capability)
        ]

    @property
    def blocked_capabilities(self) -> list[str]:
        return [
            capability
            for capability in self.capabilities
            if not is_public_capability_allowed(capability)
        ]

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_secret_fields"] = list(self.required_secret_fields)
        data["capabilities"] = list(self.capabilities)
        return data

    def public_summary(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "name": self.name,
            "status": self.status,
            "environments": list(self.environments),
            "required_secret_fields": list(self.required_secret_fields),
            "preset": dict(self.preset),
            "environment_presets": {
                key: dict(value) for key, value in self.environment_presets.items()
            },
            "capabilities": list(self.capabilities),
            "read_capabilities": self.read_capabilities,
            "blocked_capabilities": self.blocked_capabilities,
            "validation": {
                "method": self.validation.method,
                "token_url": self.validation.token_url,
                "healthcheck_endpoint": self.validation.read_only_endpoint,
                "safe_probe": self.validation.read_only,
            },
        }


CONNECTOR_CATALOG: list[ConnectorManifest] = [
    ConnectorManifest(
        connector_id="flowaccount",
        name="FlowAccount",
        status="available",
        environments=["production", "sandbox"],
        required_secret_fields=["client_id", "client_secret"],
        preset={
            "grant_type": "client_credentials",
            "scope": "flowaccount-api",
            "api_base_url": "https://openapi.flowaccount.com/v1",
            "token_url": "https://openapi.flowaccount.com/token",
        },
        environment_presets={
            "production": {
                "api_base_url": "https://openapi.flowaccount.com/v1",
                "token_url": "https://openapi.flowaccount.com/token",
            },
            "sandbox": {
                "api_base_url": "https://openapi.flowaccount.com/test",
                "token_url": "https://openapi.flowaccount.com/test/token",
            },
        },
        capabilities=FLOWACCOUNT_CAPABILITIES,
        validation=ConnectorValidation(
            method="oauth_client_credentials",
            token_url="https://openapi.flowaccount.com/token",
            healthcheck_endpoint="/company/info",
            safe_probe=True,
        ),
    ),
    ConnectorManifest(
        connector_id="peak",
        name="PEAK Accounting",
        status="available",
        environments=["production", "uat", "sandbox"],
        required_secret_fields=[
            "connect_id",
            "connect_key",
            "application_code",
            "user_token",
        ],
        preset={
            "auth_method": "hmac_sha1_client_token",
            "timestamp_format": "utc_yyyyMMddHHmmss",
            "client_token_ttl_hours": "24",
            "token_path": "/clienttoken",
            "api_base_url": "https://api.peakaccount.com/api/v1",
            "docs_url": "https://developers.peakaccount.com/reference/peak-open-api",
        },
        environment_presets={
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
        capabilities=[
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
        ],
        validation=ConnectorValidation(
            method="peak_hmac_client_token",
            token_url="https://peakengineapidev.azurewebsites.net/api/v1/clienttoken",
            healthcheck_endpoint="/user",
            safe_probe=True,
        ),
    ),
    ConnectorManifest(
        connector_id="express",
        name="Express Account",
        status="setup_target",
        environments=["local", "gateway"],
        required_secret_fields=["gateway_url", "api_key"],
        capabilities=[],
    ),
    ConnectorManifest(
        connector_id="custom",
        name="Custom ERP",
        status="setup_target",
        environments=["production", "sandbox", "gateway"],
        required_secret_fields=["base_url", "api_key"],
        capabilities=[],
    ),
]


def connector_by_id(connector_id: str) -> ConnectorManifest | None:
    clean = connector_id.strip().lower()
    return next((item for item in CONNECTOR_CATALOG if item.connector_id == clean), None)


def list_connector_summaries() -> list[dict[str, Any]]:
    return [item.summary() for item in CONNECTOR_CATALOG]


def list_connector_public_summaries() -> list[dict[str, Any]]:
    return [item.public_summary() for item in CONNECTOR_CATALOG]
