from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from mercury_tools.catalog.models import (
    CatalogAction,
    CatalogSource,
    revalidate_catalog_action,
    revalidate_catalog_source,
)
from mercury_tools.cloud.models import validate_public_catalog_action
from mercury_tools.execution.request_builder import build_request
from mercury_tools.rag.chunking import chunk_document, document_from_markdown

ROOT = Path(__file__).resolve().parents[1]


def load_actions(connector: str) -> list[dict[str, object]]:
    path = ROOT / "catalog" / "global" / connector / "actions.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return value


def load_source(connector: str) -> dict[str, object]:
    path = ROOT / "catalog" / "global" / connector / "source.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_flowaccount_catalog_preserves_all_documented_variants() -> None:
    actions = load_actions("flowaccount")

    assert len(actions) == 190
    assert Counter(item["method"] for item in actions) == {
        "GET": 36,
        "POST": 119,
        "PUT": 22,
        "DELETE": 13,
    }
    identities = {(item["action_id"], item["version_id"]) for item in actions}
    assert len(identities) == 190


def test_peak_catalog_preserves_all_documented_actions() -> None:
    actions = load_actions("peak")

    assert len(actions) == 64
    assert Counter(item["method"] for item in actions) == {"GET": 20, "POST": 44}
    identities = {(item["action_id"], item["version_id"]) for item in actions}
    assert len(identities) == 64


def test_every_action_has_valid_identity_routing_and_safety_metadata() -> None:
    for connector in ("flowaccount", "peak"):
        actions = load_actions(connector)
        assert actions == sorted(actions, key=lambda item: str(item["action_id"]))
        for value in actions:
            action = revalidate_catalog_action(CatalogAction.model_validate(value))
            assert action.connector_id == connector
            assert action.capability
            assert action.aliases_en or action.aliases_th
            assert action.input_schema.keys() == {
                "path",
                "query",
                "headers",
                "body",
                "files",
            }
            assert int(action.risk_tier) in (0, 1, 2)
            assert action.required_confirmations == int(action.risk_tier)
            assert action.source_hash
            assert action.confidence in ("exact", "example_derived", "inferred")
            assert action.examples == ()


def test_every_builtin_action_is_cloud_valid_and_request_buildable(tmp_path: Path) -> None:
    upload = tmp_path / "synthetic-upload.txt"
    upload.write_text("synthetic catalog validation input", encoding="utf-8")
    validated = Counter()

    for connector in ("flowaccount", "peak"):
        for value in load_actions(connector):
            action = revalidate_catalog_action(CatalogAction.model_validate(value))
            validate_public_catalog_action(action)
            build_request(
                action,
                "https://erp.example.test",
                _synthetic_required_inputs(action, upload),
                (tmp_path,),
                environment=action.environments[0],
            )
            validated[connector] += 1

    assert validated == {"flowaccount": 190, "peak": 64}


def _synthetic_required_inputs(
    action: CatalogAction,
    upload: Path,
) -> dict[str, object]:
    schema = action.input_schema
    inputs: dict[str, object] = {}
    path_names = set(re.findall(r"\{([^{}]+)\}", action.path_template))
    if path_names:
        inputs["path"] = {
            name: _synthetic_schema_value(schema["path"].get(name, {"type": "string"}))
            for name in sorted(path_names)
        }
    for section in ("query", "headers"):
        required = {
            name: _synthetic_schema_value(declaration)
            for name, declaration in schema[section].items()
            if declaration.get("required") is True
        }
        if required:
            inputs[section] = required
    required_files = {
        name: str(upload)
        for name, declaration in schema["files"].items()
        if declaration.get("required") is True
    }
    if required_files:
        inputs["files"] = required_files
    if schema["body"].get("x-mercury-required") is True:
        inputs["body"] = _synthetic_schema_value(schema["body"])
    return inputs


def _synthetic_schema_value(schema: object) -> object:
    if not isinstance(schema, dict):
        return "value"
    enum = schema.get("enum")
    if isinstance(enum, tuple) and enum:
        return enum[0]
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        if not isinstance(properties, dict) or not isinstance(required, tuple):
            return {}
        return {
            name: _synthetic_schema_value(properties[name])
            for name in required
            if name in properties
        }
    if schema_type == "array":
        return []
    if schema_type == "boolean":
        return True
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "null":
        return None
    return "value"


def test_repeated_flowaccount_routes_have_explicit_unique_variants() -> None:
    grouped: dict[tuple[object, object], list[dict[str, object]]] = defaultdict(list)
    for action in load_actions("flowaccount"):
        grouped[(action["method"], action["path_template"])].append(action)

    repeated = [items for items in grouped.values() if len(items) > 1]
    assert repeated
    for items in repeated:
        variants = {item["variant_id"] for item in items}
        assert "default" not in variants
        assert len(variants) == len(items)


def test_every_builtin_source_has_a_valid_identity() -> None:
    for connector in ("flowaccount", "peak"):
        source = revalidate_catalog_source(CatalogSource.model_validate(load_source(connector)))
        assert source.connector_id == connector
        assert source.source_hash
        assert source.imported_at.isoformat() == "1970-01-01T00:00:00+00:00"


def test_sensitive_mutations_are_tier_two() -> None:
    high_risk_effects = {"payment", "approve", "void", "email", "share", "invite"}
    for connector in ("flowaccount", "peak"):
        for action in load_actions(connector):
            if action["method"] == "DELETE" or (
                action["method"] != "GET"
                and high_risk_effects.intersection(action["side_effects"])
            ):
                assert action["risk_tier"] == 2
                assert action["required_confirmations"] == 2


def test_documented_high_risk_routes_have_semantic_side_effects() -> None:
    markers = (
        (r"/approve(?:/|$)", "approve"),
        (r"/email-document(?:/|$)", "email"),
        (r"/invitation(?:/|$)", "invite"),
        (r"/paidpayment(?:allinone)?(?:/|$)", "payment"),
        (r"/payment(?:/|$)", "payment"),
        (r"/sharedocument(?:/|$)", "share"),
        (r"/void(?:payment)?(?:/|$)", "void"),
        (r"/with-payment(?:/|$)", "payment"),
    )
    for connector in ("flowaccount", "peak"):
        for action in load_actions(connector):
            if action["method"] == "GET":
                continue
            path = str(action["path_template"]).casefold()
            for marker, effect in markers:
                if re.search(marker, path):
                    assert effect in action["side_effects"]
                    assert action["risk_tier"] == 2


def test_catalog_paths_do_not_retain_example_record_ids() -> None:
    for connector in ("flowaccount", "peak"):
        for action in load_actions(connector):
            assert not re.search(r"/(?:[0-9]{4,})(?:/|$)", str(action["path_template"]))


def test_flowaccount_journal_drafts_are_not_misclassified_as_payments() -> None:
    drafts = [
        action
        for action in load_actions("flowaccount")
        if action["path_template"] == "/journal-entries/draft"
    ]

    assert len(drafts) == 5
    assert {action["capability"] for action in drafts} == {"journal_entry.draft.create"}
    assert {action["risk_tier"] for action in drafts} == {1}
    assert all("payment" not in action["side_effects"] for action in drafts)


def test_catalog_contains_no_source_credentials_or_personal_examples() -> None:
    serialized = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "catalog/global/flowaccount/source.json",
            "catalog/global/flowaccount/actions.json",
            "catalog/global/flowaccount/semantic-contracts.json",
            "catalog/global/peak/source.json",
            "catalog/global/peak/actions.json",
            "catalog/global/peak/semantic-contracts.json",
        )
    )

    assert not re.search(
        r'"client_secret"\s*:\s*"(?!\[REDACTED\]|)\S+',
        serialized,
        re.IGNORECASE,
    )
    assert "authorization: bearer" not in serialized.casefold()
    assert not re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", serialized)
    assert not re.search(r"\b\d{13}\b", serialized)
    assert not re.search(r"\b0\d{8,9}\b", serialized)
    assert '"sample"' not in serialized


def test_endpoint_dictionaries_link_every_generated_action() -> None:
    for connector in ("flowaccount", "peak"):
        wiki = (
            ROOT / "wiki" / "connectors" / f"{connector}-endpoint-dictionary.md"
        ).read_text(encoding="utf-8")
        action_ids = {str(action["action_id"]) for action in load_actions(connector)}
        assert action_ids
        assert all(f"action_id: {action_id}" in wiki for action_id in action_ids)
        assert "public_preview_read_only" not in wiki
        assert "Production-changing calls remain blocked" not in wiki
        assert "local MCP" in wiki
        assert not re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", wiki)
        assert not re.search(r"\b\d{13}\b", wiki)
        assert not re.search(r"\b0\d{8,9}\b", wiki)


def test_endpoint_dictionary_chunks_route_to_generated_actions() -> None:
    wiki_root = ROOT / "wiki"
    for connector in ("flowaccount", "peak"):
        path = wiki_root / "connectors" / f"{connector}-endpoint-dictionary.md"
        document = document_from_markdown(path, root=wiki_root)
        indexed_ids = {
            str(chunk.metadata["action_id"])
            for chunk in chunk_document(document)
            if "action_id" in chunk.metadata
        }
        expected_ids = {str(action["action_id"]) for action in load_actions(connector)}
        assert indexed_ids == expected_ids
