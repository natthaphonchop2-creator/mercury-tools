from __future__ import annotations

import copy
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from mercury_tools.catalog.identity import build_version_id
from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action

ROOT = Path(__file__).resolve().parents[1]


def semantics_module() -> ModuleType:
    try:
        return importlib.import_module("mercury_tools.qualification.semantics")
    except ModuleNotFoundError:
        pytest.fail("semantic loader module is missing")


def action_rows(connector: str) -> list[dict[str, Any]]:
    value = json.loads(
        (ROOT / "catalog" / "global" / connector / "actions.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value


def semantic_row(action: CatalogAction, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "action_id": action.action_id,
        "version_id": action.version_id,
        "business_object": "invoice",
        "operation": "list",
        "accounting_uses": ["accounts_receivable_reconciliation"],
        "output_semantics": {"total_amount": "gross invoice amount"},
        "join_keys": ["total_amount"],
    }
    row.update(overrides)
    return row


def write_sidecar(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_actions(connector: str) -> list[CatalogAction]:
    module = semantics_module()
    return module.load_actions(ROOT / "catalog" / "global" / connector / "actions.json")


def action_with_capability(action: CatalogAction, capability: str) -> CatalogAction:
    changed = action.model_copy(update={"capability": capability})
    versioned = changed.model_copy(update={"version_id": build_version_id(changed)})
    return revalidate_catalog_action(versioned)


@pytest.mark.parametrize(
    ("connector", "expected"),
    [("flowaccount", 190), ("peak", 64)],
)
def test_semantic_sidecar_covers_every_exact_action_version(
    connector: str, expected: int
) -> None:
    module = semantics_module()
    actions = load_actions(connector)
    contracts = module.load_semantic_contracts(
        ROOT / "catalog" / "global" / connector / "semantic-contracts.json",
        actions,
    )

    assert len(contracts) == expected
    assert set(contracts) == {(action.action_id, action.version_id) for action in actions}


def test_invoice_list_has_reviewed_accounting_uses_and_join_keys() -> None:
    module = semantics_module()
    actions = load_actions("flowaccount")
    action = next(
        item for item in actions if item.capability == "documents.invoice.list"
    )
    contracts = module.load_semantic_contracts(
        ROOT / "catalog/global/flowaccount/semantic-contracts.json", actions
    )
    contract = module.require_semantic_contract(action, contracts)

    assert contract.business_object == "invoice"
    assert "accounts_receivable_reconciliation" in contract.accounting_uses
    assert "revenue_review" in contract.accounting_uses
    assert "vat_output_review" in contract.accounting_uses
    assert "total_amount" in contract.join_keys
    assert contract.output_semantics["total_amount"] == "gross invoice amount"


def test_load_actions_revalidates_immutable_catalog_identities(tmp_path: Path) -> None:
    module = semantics_module()
    rows = action_rows("flowaccount")
    rows[0]["version_id"] = "av_" + "0" * 64
    path = tmp_path / "actions.json"
    write_sidecar(path, rows)

    with pytest.raises(ValueError, match="^semantic_catalog_action_invalid$"):
        module.load_actions(path)


@pytest.mark.parametrize("payload", [{}, ["not an action"], {"rows": []}])
def test_load_actions_rejects_malformed_payloads(
    tmp_path: Path, payload: object
) -> None:
    module = semantics_module()
    path = tmp_path / "actions.json"
    write_sidecar(path, payload)

    with pytest.raises(ValueError, match="^semantic_actions_invalid$"):
        module.load_actions(path)


def test_load_actions_does_not_mutate_parsed_json_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = semantics_module()
    payload = action_rows("flowaccount")[:1]
    original = copy.deepcopy(payload)
    path = tmp_path / "actions.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(module.json, "loads", lambda _text: payload)

    module.load_actions(path)

    assert payload == original


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({}, "semantic_contracts_invalid"),
        ({"contracts": {}}, "semantic_contracts_invalid"),
        ({"contracts": ["not a row"]}, "semantic_contract_row_invalid"),
    ],
)
def test_load_semantic_contracts_rejects_malformed_payloads(
    tmp_path: Path, payload: object, error: str
) -> None:
    module = semantics_module()
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(path, payload)

    with pytest.raises(ValueError, match=rf"^{error}$"):
        module.load_semantic_contracts(path, load_actions("flowaccount")[:1])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("metadata", {"note": "envelope-value-that-must-not-appear"}),
        ("client_secret", "envelope-value-that-must-not-appear"),
        ("source_path", "/access_token-envelope-value-that-must-not-appear"),
    ],
)
def test_load_semantic_contracts_rejects_all_envelope_metadata_without_leaking_it(
    tmp_path: Path, key: str, value: object
) -> None:
    module = semantics_module()
    action = load_actions("flowaccount")[0]
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(
        path,
        {
            "contracts": [semantic_row(action)],
            key: value,
        },
    )

    with pytest.raises(ValueError, match="^semantic_contracts_invalid$") as error:
        module.load_semantic_contracts(path, [action])

    assert "envelope-value-that-must-not-appear" not in str(error.value)


@pytest.mark.parametrize(
    ("identity", "error"),
    [
        ({"action_id": 1}, "semantic_contract_identity_invalid"),
        ({"version_id": 1}, "semantic_contract_identity_invalid"),
        ({"action_id": ""}, "semantic_contract_identity_invalid"),
        ({"version_id": ""}, "semantic_contract_identity_invalid"),
    ],
)
def test_load_semantic_contracts_rejects_invalid_identity_types(
    tmp_path: Path, identity: dict[str, object], error: str
) -> None:
    module = semantics_module()
    action = load_actions("flowaccount")[0]
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(path, {"contracts": [semantic_row(action, **identity)]})

    with pytest.raises(ValueError, match=rf"^{error}$"):
        module.load_semantic_contracts(path, [action])


def test_load_semantic_contracts_rejects_duplicate_exact_identity(tmp_path: Path) -> None:
    module = semantics_module()
    action = load_actions("flowaccount")[0]
    row = semantic_row(action)
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(path, {"contracts": [row, copy.deepcopy(row)]})

    with pytest.raises(ValueError, match="^semantic_contract_identity_duplicate$"):
        module.load_semantic_contracts(path, [action])


def test_load_semantic_contracts_rejects_unknown_identity(tmp_path: Path) -> None:
    module = semantics_module()
    action = load_actions("flowaccount")[0]
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(
        path,
        {
            "contracts": [
                semantic_row(action, action_id="act_" + "0" * 24),
            ]
        },
    )

    with pytest.raises(ValueError, match="^semantic_contract_identity_unknown$"):
        module.load_semantic_contracts(path, [action])


def test_load_semantic_contracts_rejects_version_drift(tmp_path: Path) -> None:
    module = semantics_module()
    action = load_actions("flowaccount")[0]
    replacement = "0" if action.version_id[-1] != "0" else "1"
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(
        path,
        {
            "contracts": [
                semantic_row(action, version_id=action.version_id[:-1] + replacement),
            ]
        },
    )

    with pytest.raises(ValueError, match="^semantic_contract_version_drift$"):
        module.load_semantic_contracts(path, [action])


def test_load_semantic_contracts_rejects_missing_identity(tmp_path: Path) -> None:
    module = semantics_module()
    actions = load_actions("flowaccount")[:2]
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(path, {"contracts": [semantic_row(actions[0])]})

    with pytest.raises(ValueError, match="^semantic_contract_coverage_incomplete$"):
        module.load_semantic_contracts(path, actions)


def test_load_semantic_contracts_does_not_mutate_parsed_json_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = semantics_module()
    action = load_actions("flowaccount")[0]
    payload = {"contracts": [semantic_row(action)]}
    original = copy.deepcopy(payload)
    path = tmp_path / "semantic-contracts.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module.json, "loads", lambda _text: payload)

    module.load_semantic_contracts(path, [action])

    assert payload == original


@pytest.mark.parametrize(
    "row",
    [
        {"client_secret": "synthetic-secret-value-that-must-not-appear"},
        {
            "output_semantics": {
                "path": "/access_token-secret-value-that-must-not-appear"
            }
        },
    ],
)
def test_loader_rejects_credential_and_path_content_without_leaking_it(
    tmp_path: Path, row: dict[str, object]
) -> None:
    module = semantics_module()
    action = load_actions("flowaccount")[0]
    secret = "secret-value-that-must-not-appear"
    path = tmp_path / "semantic-contracts.json"
    write_sidecar(path, {"contracts": [semantic_row(action, **row)]})

    with pytest.raises(ValueError, match="^semantic_contract_invalid$") as error:
        module.load_semantic_contracts(path, [action])

    assert secret not in str(error.value)


def test_require_semantic_contract_uses_the_exact_action_version() -> None:
    module = semantics_module()
    actions = load_actions("flowaccount")
    contracts = module.load_semantic_contracts(
        ROOT / "catalog/global/flowaccount/semantic-contracts.json", actions
    )
    action = actions[0]
    drifted = action.model_copy(
        update={"version_id": action.version_id[:-1] + "0"}
    )

    with pytest.raises(ValueError, match="^semantic_catalog_action_invalid$"):
        module.require_semantic_contract(drifted, contracts)


def test_generator_is_byte_deterministic_and_preserves_action_catalogs(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog"
    action_bytes: dict[str, bytes] = {}
    for connector in ("flowaccount", "peak"):
        source = ROOT / "catalog" / "global" / connector / "actions.json"
        destination = catalog / connector / "actions.json"
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)
        action_bytes[connector] = destination.read_bytes()

    command = [
        sys.executable,
        str(ROOT / "scripts/build_semantic_sidecars.py"),
        "--catalog",
        str(catalog),
    ]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    generated_once = {
        connector: (catalog / connector / "semantic-contracts.json").read_bytes()
        for connector in ("flowaccount", "peak")
    }
    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    generated_twice = {
        connector: (catalog / connector / "semantic-contracts.json").read_bytes()
        for connector in ("flowaccount", "peak")
    }
    committed = {
        connector: (
            ROOT / "catalog" / "global" / connector / "semantic-contracts.json"
        ).read_bytes()
        for connector in ("flowaccount", "peak")
    }

    assert first.stdout == "flowaccount=190 peak=64 missing=0\n"
    assert second.stdout == first.stdout
    assert generated_once == committed
    assert generated_twice == generated_once
    assert {
        connector: (catalog / connector / "actions.json").read_bytes()
        for connector in ("flowaccount", "peak")
    } == action_bytes


def test_contract_for_uses_true_capability_segment_boundaries() -> None:
    module = semantics_module()
    actions = load_actions("flowaccount")
    invoice = next(
        item for item in actions if item.capability == "documents.invoice.list"
    )
    adjustment = action_with_capability(invoice, "documents.invoice_adjustment.list")

    contract = module.contract_for(adjustment)

    assert contract.business_object == "general"
    assert contract.operation == "list"
    assert contract.accounting_uses == ()
    assert contract.join_keys == ()
    assert contract.output_semantics == {}


@pytest.mark.parametrize(
    ("connector", "capability"),
    [
        ("flowaccount", "auth.token.create"),
        ("peak", "auth.client_token.create"),
        ("peak", "invitation.create"),
    ],
)
def test_unmatched_auth_and_invitation_actions_have_neutral_general_semantics(
    connector: str, capability: str
) -> None:
    module = semantics_module()
    action = next(item for item in load_actions(connector) if item.capability == capability)

    contract = module.contract_for(action)

    assert contract.business_object == "general"
    assert contract.operation == capability.rsplit(".", 1)[-1]
    assert contract.accounting_uses == ()
    assert contract.output_semantics == {}
    assert contract.join_keys == ()
