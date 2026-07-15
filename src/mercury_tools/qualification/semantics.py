"""Version-bound accounting semantics for immutable catalog actions."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias

from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.qualification.models import SemanticContract

SemanticMap: TypeAlias = Mapping[tuple[str, str], SemanticContract]

ACCOUNTING_USES: dict[str, tuple[str, ...]] = {
    "documents.invoice": (
        "accounts_receivable_reconciliation",
        "revenue_review",
        "vat_output_review",
    ),
    "documents.expense": (
        "accounts_payable_reconciliation",
        "expense_review",
        "vat_input_review",
    ),
    "documents.receipt": (
        "accounts_receivable_reconciliation",
        "cash_receipt_review",
    ),
    "journal_entry": ("general_ledger_review", "month_end_close"),
    "contacts": ("counterparty_matching",),
    "product_masters": ("item_master_review", "cost_classification"),
    "bank_channels": ("settlement_reconciliation",),
}

JOIN_KEYS: dict[str, tuple[str, ...]] = {
    "documents.invoice": (
        "document_id",
        "document_number",
        "contact_id",
        "issue_date",
        "due_date",
        "total_amount",
        "tax_amount",
    ),
    "documents.expense": (
        "document_id",
        "document_number",
        "contact_id",
        "expense_date",
        "total_amount",
        "tax_amount",
    ),
    "documents.receipt": (
        "document_id",
        "document_number",
        "contact_id",
        "receipt_date",
        "total_amount",
    ),
    "journal_entry": (
        "journal_entry_id",
        "entry_date",
        "account_code",
        "debit_amount",
        "credit_amount",
    ),
    "contacts": ("contact_id", "counterparty_tax_id"),
    "product_masters": ("product_id", "product_code"),
    "bank_channels": ("bank_channel_id", "account_number"),
}

OUTPUT_SEMANTICS: dict[str, dict[str, str]] = {
    "documents.invoice": {
        "contact_id": "counterparty identifier",
        "document_id": "invoice identifier",
        "document_number": "invoice number",
        "due_date": "invoice due date",
        "issue_date": "invoice issue date",
        "tax_amount": "output tax amount",
        "total_amount": "gross invoice amount",
    },
    "documents.expense": {
        "contact_id": "counterparty identifier",
        "document_id": "expense document identifier",
        "document_number": "expense document number",
        "expense_date": "expense document date",
        "tax_amount": "input tax amount",
        "total_amount": "gross expense amount",
    },
    "documents.receipt": {
        "contact_id": "counterparty identifier",
        "document_id": "receipt identifier",
        "document_number": "receipt number",
        "receipt_date": "receipt date",
        "total_amount": "gross receipt amount",
    },
    "journal_entry": {
        "account_code": "ledger account code",
        "credit_amount": "credit amount",
        "debit_amount": "debit amount",
        "entry_date": "journal entry date",
        "journal_entry_id": "journal entry identifier",
    },
    "contacts": {
        "contact_id": "counterparty identifier",
        "counterparty_tax_id": "counterparty tax identifier",
    },
    "product_masters": {
        "product_code": "item code",
        "product_id": "item identifier",
    },
    "bank_channels": {
        "account_number": "settlement account number",
        "bank_channel_id": "settlement channel identifier",
    },
}

_CONNECTORS = ("flowaccount", "peak")


def load_actions(path: Path) -> list[CatalogAction]:
    """Load catalog actions while proving every immutable identity again."""
    payload = _load_json(path, "semantic_actions_invalid")
    if not isinstance(payload, list):
        raise ValueError("semantic_actions_invalid")

    actions: list[CatalogAction] = []
    identities: set[tuple[str, str]] = set()
    for row in payload:
        if not isinstance(row, Mapping):
            raise ValueError("semantic_actions_invalid")
        action = _catalog_action_from_row(row)
        identity = _action_identity(action)
        if identity in identities:
            raise ValueError("semantic_catalog_identity_duplicate")
        identities.add(identity)
        actions.append(action)
    return actions


def load_semantic_contracts(
    path: Path,
    actions: Sequence[CatalogAction],
) -> dict[tuple[str, str], SemanticContract]:
    """Load complete semantics keyed by exact immutable action version."""
    expected, versions_by_action = _expected_action_identities(actions)
    payload = _load_json(path, "semantic_contracts_invalid")
    if not isinstance(payload, Mapping) or set(payload) != {"contracts"}:
        raise ValueError("semantic_contracts_invalid")
    rows = payload.get("contracts")
    if not isinstance(rows, list):
        raise ValueError("semantic_contracts_invalid")

    result: dict[tuple[str, str], SemanticContract] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("semantic_contract_row_invalid")
        copied_row = copy.deepcopy(dict(row))
        action_id = copied_row.pop("action_id", None)
        version_id = copied_row.pop("version_id", None)
        if (
            not isinstance(action_id, str)
            or not action_id
            or not isinstance(version_id, str)
            or not version_id
        ):
            raise ValueError("semantic_contract_identity_invalid")
        key = (action_id, version_id)
        if key in result:
            raise ValueError("semantic_contract_identity_duplicate")
        if action_id not in versions_by_action:
            raise ValueError("semantic_contract_identity_unknown")
        if version_id not in versions_by_action[action_id]:
            raise ValueError("semantic_contract_version_drift")
        try:
            result[key] = SemanticContract.model_validate(copied_row)
        except (TypeError, ValueError):
            raise ValueError("semantic_contract_invalid") from None

    if set(result) != expected:
        raise ValueError("semantic_contract_coverage_incomplete")
    return result


def require_semantic_contract(
    action: CatalogAction,
    contracts: SemanticMap,
) -> SemanticContract:
    """Return semantics only when the current action version is mapped."""
    key = _action_identity(action)
    contract = contracts.get(key)
    if not isinstance(contract, SemanticContract):
        raise ValueError("semantic_contract_missing")
    return contract


def contract_for(action: CatalogAction) -> SemanticContract:
    """Build the reviewed, conservative contract for one action version."""
    validated_action = _validated_action(action)
    root = _accounting_root(validated_action.capability)
    operation = validated_action.capability.rsplit(".", 1)[-1]
    if root == "general":
        return SemanticContract(
            business_object="general",
            operation=operation,
        )
    return SemanticContract(
        business_object=root.removeprefix("documents."),
        operation=operation,
        accounting_uses=ACCOUNTING_USES[root],
        output_semantics=OUTPUT_SEMANTICS[root],
        join_keys=JOIN_KEYS[root],
    )


def write_semantic_contracts(path: Path, actions: Sequence[CatalogAction]) -> int:
    """Write one deterministic, exact-identity semantic sidecar."""
    validated_actions = _validated_actions(actions)
    rows = [
        {
            "action_id": action.action_id,
            "version_id": action.version_id,
            **contract_for(action).model_dump(mode="json"),
        }
        for action in sorted(
            validated_actions,
            key=lambda action: (action.action_id, action.version_id),
        )
    ]
    payload = {"contracts": rows}
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    )
    path.write_text(f"{serialized}\n", encoding="utf-8")
    return len(rows)


def build_semantic_sidecars(catalog_root: Path) -> dict[str, int]:
    """Generate and reload the built-in connector sidecars."""
    counts: dict[str, int] = {}
    for connector in _CONNECTORS:
        connector_root = catalog_root / connector
        actions = load_actions(connector_root / "actions.json")
        sidecar_path = connector_root / "semantic-contracts.json"
        write_semantic_contracts(sidecar_path, actions)
        contracts = load_semantic_contracts(sidecar_path, actions)
        counts[connector] = len(contracts)
    return counts


def _load_json(path: Path, error: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(error) from None


def _catalog_action_from_row(row: Mapping[object, object]) -> CatalogAction:
    try:
        copied_row = copy.deepcopy(dict(row))
        action = CatalogAction.model_validate(copied_row)
    except (TypeError, ValueError):
        raise ValueError("semantic_catalog_action_invalid") from None
    return _validated_action(action)


def _validated_actions(actions: Sequence[CatalogAction]) -> list[CatalogAction]:
    validated: list[CatalogAction] = []
    identities: set[tuple[str, str]] = set()
    for action in actions:
        checked = _validated_action(action)
        identity = (checked.action_id, checked.version_id)
        if identity in identities:
            raise ValueError("semantic_catalog_identity_duplicate")
        identities.add(identity)
        validated.append(checked)
    return validated


def _expected_action_identities(
    actions: Sequence[CatalogAction],
) -> tuple[set[tuple[str, str]], dict[str, set[str]]]:
    validated = _validated_actions(actions)
    expected = {(action.action_id, action.version_id) for action in validated}
    versions_by_action: dict[str, set[str]] = {}
    for action_id, version_id in expected:
        versions_by_action.setdefault(action_id, set()).add(version_id)
    return expected, versions_by_action


def _validated_action(action: object) -> CatalogAction:
    if not isinstance(action, CatalogAction):
        raise ValueError("semantic_catalog_action_invalid")
    try:
        validated = revalidate_catalog_action(action)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("semantic_catalog_action_invalid") from None
    if not isinstance(validated.action_id, str) or not isinstance(validated.version_id, str):
        raise ValueError("semantic_catalog_action_invalid")
    return validated


def _action_identity(action: CatalogAction) -> tuple[str, str]:
    validated = _validated_action(action)
    return validated.action_id, validated.version_id


def _accounting_root(capability: str) -> str:
    for root in ACCOUNTING_USES:
        if capability == root or capability.startswith(f"{root}."):
            return root
    return "general"
