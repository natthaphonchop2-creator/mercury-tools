from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConnectorValidation:
    method: str
    token_url: str = ""
    read_only_endpoint: str = ""
    read_only: bool = True


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

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["required_secret_fields"] = list(self.required_secret_fields)
        data["capabilities"] = list(self.capabilities)
        return data


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
        capabilities=[
            "company.info.read",
            "contacts.list",
            "products.list",
            "documents.invoice.list",
            "documents.invoice.get",
            "tax.vat_summary.read",
        ],
        validation=ConnectorValidation(
            method="oauth_client_credentials",
            token_url="https://openapi.flowaccount.com/token",
            read_only_endpoint="/company/info",
            read_only=True,
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
            read_only_endpoint="/user",
            read_only=True,
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
