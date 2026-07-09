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
    capabilities: list[str] = field(default_factory=list)
    validation: ConnectorValidation = field(
        default_factory=lambda: ConnectorValidation(method="manual")
    )

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
        status="setup_target",
        environments=["production", "sandbox"],
        required_secret_fields=["client_id", "client_secret"],
        capabilities=[],
    ),
    ConnectorManifest(
        connector_id="express",
        name="Express Account",
        status="setup_target",
        environments=["local", "gateway"],
        required_secret_fields=["gateway_url", "api_key"],
        capabilities=[],
    ),
]


def connector_by_id(connector_id: str) -> ConnectorManifest | None:
    clean = connector_id.strip().lower()
    return next((item for item in CONNECTOR_CATALOG if item.connector_id == clean), None)


def list_connector_summaries() -> list[dict[str, Any]]:
    return [item.summary() for item in CONNECTOR_CATALOG]
