#!/usr/bin/env python3
"""Build deterministic, credential-free FlowAccount and PEAK action catalogs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mercury_tools.catalog.identity import (
    build_action_id,
    build_version_id,
    canonical_json,
)
from mercury_tools.catalog.importers._common import build_action, normalize_path
from mercury_tools.catalog.importers.postman import parse_postman
from mercury_tools.catalog.importers.sanitize import SanitizationReport, sanitize_spec
from mercury_tools.catalog.models import (
    CatalogAction,
    CatalogSource,
    revalidate_catalog_action,
    revalidate_catalog_source,
)
from mercury_tools.execution.policy import effective_risk

ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 10 * 1024 * 1024
BUILTIN_IMPORTED_AT = datetime(1970, 1, 1, tzinfo=UTC)
POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
GENERATED_START = "<!-- MERCURY GENERATED ACTION CATALOG START -->"
GENERATED_END = "<!-- MERCURY GENERATED ACTION CATALOG END -->"
AUTH_HEADERS = {
    "authorization",
    "client-token",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "time-signature",
    "time-stamp",
    "user-token",
}
RESOURCE_MAP: dict[str, tuple[str, str]] = {
    "bank-accounts": ("bank_accounts", "บัญชีธนาคาร"),
    "bank-channel": ("bank_channels", "ช่องทางการเงิน"),
    "billing-notes": ("documents.billing_note", "ใบวางบิล"),
    "billingnotes": ("documents.billing_note", "ใบวางบิล"),
    "billingnotesexpenses": ("documents.billing_note_expense", "ใบวางบิลรายจ่าย"),
    "cash-invoices": ("documents.cash_invoice", "ใบกำกับภาษีเงินสด"),
    "chart-of-accounts": ("journal.account_code", "ผังบัญชี"),
    "clienttoken": ("auth.client_token", "โทเคนเชื่อมต่อ"),
    "company": ("company", "ข้อมูลบริษัท"),
    "contacts": ("contacts", "ผู้ติดต่อ"),
    "creditnotes": ("documents.credit_note", "ใบลดหนี้"),
    "creditnotesexpenses": ("documents.credit_note_expense", "ใบลดหนี้รายจ่าย"),
    "dailyjournals": ("daily_journal", "สมุดรายวัน"),
    "expenses": ("documents.expense", "ค่าใช้จ่าย"),
    "invitation": ("invitation", "คำเชิญผู้ใช้"),
    "invoices": ("documents.invoice", "ใบแจ้งหนี้"),
    "journal-entries": ("journal_entry", "รายการสมุดรายวัน"),
    "paymentmethods": ("payment_methods", "ช่องทางรับชำระ"),
    "product-masters": ("product_masters", "ข้อมูลหลักสินค้า"),
    "products": ("products", "สินค้า"),
    "purchaseorders": ("documents.purchase_order", "ใบสั่งซื้อ"),
    "purchases-orders": ("documents.purchase_order", "ใบสั่งซื้อ"),
    "purchases": ("documents.purchase", "เอกสารซื้อ"),
    "quotations": ("documents.quotation", "ใบเสนอราคา"),
    "receipts": ("documents.receipt", "ใบเสร็จรับเงิน"),
    "services": ("services", "บริการ"),
    "settings": ("settings", "การตั้งค่า"),
    "tags": ("tags", "แท็ก"),
    "tax-invoices": ("documents.invoice", "ใบกำกับภาษี"),
    "token": ("auth.token", "โทเคนเชื่อมต่อ"),
    "withholding-taxes": ("documents.withholding_tax", "หนังสือรับรองหัก ณ ที่จ่าย"),
}


def build_catalog(connector_id: str, source_path: Path, output_dir: Path) -> None:
    connector = connector_id.casefold().strip()
    if connector not in {"flowaccount", "peak"}:
        raise ValueError("unsupported_builtin_connector")
    raw = _load_json(source_path)
    sanitized_raw, raw_report = sanitize_spec(raw)

    if connector == "flowaccount":
        drafts = _flowaccount_drafts(sanitized_raw)
    else:
        drafts = _peak_drafts(sanitized_raw, raw_report)

    source = _catalog_source(connector, drafts, raw_report)
    actions = _build_actions(connector, drafts, source)
    expected = {"flowaccount": 190, "peak": 64}[connector]
    if len(actions) != expected:
        raise ValueError("builtin_catalog_action_count_mismatch")

    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(output_dir / "source.json", source.model_dump(mode="json"))
    _atomic_json_write(
        output_dir / "actions.json",
        [action.model_dump(mode="json") for action in actions],
    )
    wiki = ROOT / "wiki" / "connectors" / f"{connector}-endpoint-dictionary.md"
    if wiki.exists():
        _update_wiki(wiki, actions)


def _load_json(path: Path) -> Any:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise ValueError("builtin_source_symlink_forbidden")
    try:
        data = candidate.read_bytes()
    except OSError:
        raise ValueError("builtin_source_unreadable") from None
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("builtin_source_too_large")
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("builtin_source_invalid") from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _flowaccount_drafts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("flowaccount_endpoint_dictionary_invalid")
    drafts: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError("flowaccount_endpoint_dictionary_invalid")
        method = _method(row.get("method"))
        name = _text(row.get("name"))
        path = _flow_path(row.get("path"))
        name = name or f"{method} {path}"
        description = _text(row.get("purposeTh")) or _text(row.get("descriptionTh"))
        input_schema, _ = sanitize_spec(_flow_input_schema(row, path))
        drafts.append(
            {
                "method": method,
                "path_template": path,
                "operation_id": _operation_id(name, method, path),
                "content_type": _flow_content_type(row.get("body")),
                "input_schema": input_schema,
                "confidence": "example_derived",
                "description": description or name,
                "aliases_en": _aliases(name, _text(row.get("module"))),
                "aliases_th": _aliases(
                    _text(row.get("purposeTh")),
                    _text(row.get("moduleMeaning")),
                ),
            }
        )
    return drafts


def _peak_drafts(value: Any, raw_report: SanitizationReport) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError("peak_postman_collection_invalid")
    ephemeral = CatalogSource.from_document(
        uri="mercury://catalog/global/peak/raw-postman",
        connector_id="peak",
        document=value,
        report=raw_report.model_dump(mode="json"),
        source_type="postman2.1",
    )
    actions = parse_postman(value, ephemeral, "peak")
    drafts: list[dict[str, Any]] = []
    for action in actions:
        schema = action.model_dump(mode="json")["input_schema"]
        schema["headers"] = {
            name: declaration
            for name, declaration in schema["headers"].items()
            if name.casefold() not in AUTH_HEADERS and name.casefold() != "content-type"
        }
        files = schema["files"]
        if isinstance(files, dict) and isinstance(files.get("properties"), dict):
            schema["files"] = files["properties"]
        drafts.append(
            {
                "method": action.method.value,
                "path_template": action.path_template,
                "operation_id": action.operation_id,
                "content_type": action.content_type,
                "input_schema": schema,
                "confidence": action.confidence.value,
                "description": action.description or action.operation_id,
                "aliases_en": _aliases(action.description, action.operation_id),
                "aliases_th": (),
            }
        )
    return drafts


def _catalog_source(
    connector: str,
    drafts: Sequence[Mapping[str, Any]],
    raw_report: SanitizationReport,
) -> CatalogSource:
    endpoints = [
        {
            "method": draft["method"],
            "path_template": draft["path_template"],
            "operation_id": draft["operation_id"],
            "content_type": draft["content_type"],
            "input_schema": draft["input_schema"],
            "confidence": draft["confidence"],
            "description": draft["description"],
        }
        for draft in sorted(
            drafts,
            key=lambda item: (
                str(item["path_template"]),
                str(item["method"]),
                str(item["operation_id"]),
            ),
        )
    ]
    normalized, normalized_report = sanitize_spec(
        {
            "version": "builtin-v1",
            "connector_id": connector,
            "endpoints": endpoints,
        }
    )
    report = {
        "redacted_values": raw_report.redacted_values
        + normalized_report.redacted_values,
        "safe": True,
    }
    source = CatalogSource.from_document(
        uri=f"mercury://catalog/global/{connector}/source",
        connector_id=connector,
        document=normalized,
        report=report,
        source_type="documentation" if connector == "flowaccount" else "postman2.1",
    )
    values = source.model_dump(mode="python")
    values["imported_at"] = BUILTIN_IMPORTED_AT
    values["driver_suggestion"] = {
        "driver_id": "flowaccount_oauth" if connector == "flowaccount" else "peak_hmac_sha1"
    }
    return revalidate_catalog_source(CatalogSource.model_validate(values))


def _build_actions(
    connector: str,
    drafts: Sequence[Mapping[str, Any]],
    source: CatalogSource,
) -> list[CatalogAction]:
    duplicates = Counter(
        (str(draft["method"]), str(draft["path_template"])) for draft in drafts
    )
    actions: list[CatalogAction] = []
    for draft in drafts:
        base = build_action(
            source=source,
            connector_id=connector,
            method=str(draft["method"]),
            path_template=str(draft["path_template"]),
            operation_id=str(draft["operation_id"]),
            confidence=str(draft["confidence"]),
            description=str(draft["description"]),
            content_type=str(draft["content_type"]),
            input_schema=dict(draft["input_schema"]),
            examples=(),
        )
        key = (base.method.value, base.path_template)
        variant = (
            _variant_id(base.operation_id, base.input_schema)
            if duplicates[key] > 1
            else "default"
        )
        capability, thai_alias, effects = _routing(base)
        values = base.model_dump(mode="python")
        values.update(
            {
                "action_id": "",
                "version_id": "",
                "environments": (
                    ("production", "sandbox")
                    if connector == "flowaccount"
                    else ("production", "uat", "sandbox")
                ),
                "variant_id": variant,
                "aliases_en": _aliases(*draft.get("aliases_en", ()), base.operation_id),
                "aliases_th": _aliases(*draft.get("aliases_th", ()), thai_alias),
                "capability": capability,
                "examples": (),
                "side_effects": effects,
                "source_uri": source.source_uri,
                "source_hash": source.source_hash,
            }
        )
        provisional = CatalogAction.model_validate(values)
        decision = effective_risk(provisional)
        values["risk_tier"] = decision.tier
        values["required_confirmations"] = decision.required_confirmations
        action = CatalogAction.model_validate(values)
        action = action.model_copy(update={"action_id": build_action_id(action)})
        action = action.model_copy(update={"version_id": build_version_id(action)})
        actions.append(revalidate_catalog_action(action))
    actions.sort(key=lambda action: action.action_id)
    if len({action.action_id for action in actions}) != len(actions):
        raise ValueError("builtin_catalog_action_identity_collision")
    return actions


def _flow_input_schema(row: Mapping[str, Any], path: str) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "path": {},
        "query": {},
        "headers": {},
        "body": {},
        "files": {},
    }
    path_parameters = row.get("pathParams")
    if isinstance(path_parameters, list):
        for item in path_parameters:
            if isinstance(item, Mapping) and _text(item.get("key")):
                schema["path"][_text(item.get("key"))] = {
                    "type": "string",
                    "description": _text(item.get("meaningTh")),
                }
    for name in re.findall(r"\{([^{}]+)\}", path):
        schema["path"].setdefault(name, {"type": "string"})

    query = row.get("queryParams")
    if isinstance(query, list):
        for item in query:
            if isinstance(item, Mapping) and _text(item.get("key")):
                schema["query"][_text(item.get("key"))] = {
                    "type": "string",
                    "description": _text(item.get("meaningTh"))
                    or _text(item.get("description")),
                }

    headers = row.get("headers")
    if isinstance(headers, list):
        for item in headers:
            if not isinstance(item, Mapping):
                continue
            name = _text(item.get("key"))
            if not name or name.casefold() in AUTH_HEADERS or name.casefold() == "content-type":
                continue
            schema["headers"][name] = {
                "type": "string",
                "description": _text(item.get("description")),
            }

    body = row.get("body")
    if not isinstance(body, Mapping):
        return schema
    mode = _text(body.get("mode")).casefold()
    fields = body.get("fields")
    if not isinstance(fields, list):
        return schema
    body_properties: dict[str, Any] = {}
    for item in fields:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("field")) or _text(item.get("key"))
        if not name:
            continue
        declaration = _field_declaration(item)
        if mode == "formdata" and _text(item.get("type")).casefold() == "file":
            schema["files"][name] = {"type": "string", "format": "binary"}
        else:
            _insert_property(body_properties, name, declaration)
    if body_properties:
        schema["body"] = {
            "type": "object",
            "properties": body_properties,
            "additionalProperties": False,
        }
    return schema


def _insert_property(properties: dict[str, Any], name: str, declaration: dict[str, Any]) -> None:
    parts = re.findall(r"([^.\[\]]+)(\[\])?", name)
    if not parts:
        return
    current = properties
    for index, (part, array_marker) in enumerate(parts):
        last = index == len(parts) - 1
        if last:
            if array_marker:
                current[part] = {"type": "array", "items": declaration}
            elif part not in current or current[part].get("type") not in {"array", "object"}:
                current[part] = declaration
            continue
        if array_marker:
            node = current.setdefault(
                part,
                {"type": "array", "items": {"type": "object", "properties": {}}},
            )
            items = node.setdefault("items", {"type": "object", "properties": {}})
            current = items.setdefault("properties", {})
        else:
            node = current.setdefault(part, {"type": "object", "properties": {}})
            current = node.setdefault("properties", {})


def _field_declaration(item: Mapping[str, Any]) -> dict[str, Any]:
    field_type = _text(item.get("type")).casefold()
    normalized = {
        "bool": "boolean",
        "boolean": "boolean",
        "decimal": "number",
        "double": "number",
        "float": "number",
        "int": "integer",
        "integer": "integer",
        "number": "number",
        "object": "object",
        "array": "array",
        "string": "string",
        "text": "string",
    }.get(field_type, "string")
    declaration: dict[str, Any] = {"type": normalized}
    if normalized == "array":
        declaration["items"] = {}
    description = _text(item.get("meaningTh"))
    if description:
        declaration["description"] = description
    return declaration


def _routing(action: CatalogAction) -> tuple[str, str, tuple[str, ...]]:
    components = [part.casefold() for part in action.path_template.split("/") if part]
    family = components[1] if components[:1] == ["upgrade"] and len(components) > 1 else (
        components[0] if components else "endpoint"
    )
    resource, thai_resource = RESOURCE_MAP.get(
        family,
        (re.sub(r"[^a-z0-9]+", "_", family).strip("_") or "endpoint", "ข้อมูลบัญชี"),
    )
    searchable = f"{action.operation_id} {action.path_template}".casefold()
    tokens = _routing_tokens(action.operation_id, action.path_template)
    if family == "dailyjournals" and "accountcode" in searchable:
        resource, thai_resource = "journal.account_code", "ผังบัญชี"
    intent, thai_intent = _intent(action.method.value, tokens)
    capability = f"{resource}.{intent}"
    effects: list[str] = [] if action.method.value == "GET" else ["writes_remote_data"]
    if action.method.value == "DELETE":
        effects.append("delete")
    if action.method.value != "GET":
        effects.extend(_high_risk_effects(tokens))
    return capability, f"{thai_intent}{thai_resource}", tuple(dict.fromkeys(effects))


def _intent(method: str, tokens: frozenset[str]) -> tuple[str, str]:
    if method == "GET":
        if tokens.intersection({"all", "list", "search"}):
            return "list", "ดูรายการ"
        return "get", "ดู"
    if method == "DELETE":
        return "delete", "ลบ"
    if "draft" in tokens:
        return "draft.create", "สร้างฉบับร่าง"
    if "void" in tokens and "payment" in tokens:
        return "payment.void", "ยกเลิกการชำระ"
    if tokens.intersection({"paid", "payment"}):
        return "payment.create", "บันทึกการชำระ"
    if tokens.intersection({"approval", "approve", "approved"}):
        return "approve", "อนุมัติ"
    if "void" in tokens:
        return "void", "ยกเลิก"
    if "email" in tokens:
        return "email.send", "ส่งอีเมล"
    if "share" in tokens:
        return "share.create", "สร้างลิงก์แชร์"
    if tokens.intersection({"invitation", "invite"}):
        return "create", "เชิญ"
    if tokens.intersection({"attachment", "upload"}):
        return "attachment.upload", "แนบไฟล์"
    if "status" in tokens:
        return "status.update", "เปลี่ยนสถานะ"
    if method == "PUT" or tokens.intersection({"edit", "update"}):
        return "update", "แก้ไข"
    if tokens.intersection({"delete", "remove"}):
        return "delete", "ลบ"
    if "upgrade" in tokens:
        return "upgrade", "แปลงเอกสาร"
    return "create", "สร้าง"


def _routing_tokens(operation_id: str, path: str) -> frozenset[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", operation_id)
    tokens = set(re.findall(r"[a-z0-9]+", f"{expanded} {path}".casefold()))
    for token in tuple(tokens):
        if token.startswith("sharedocument"):
            tokens.update(("share", "document"))
        if token.startswith("paidpayment"):
            tokens.update(("paid", "payment"))
        if token.startswith("voidpayment"):
            tokens.update(("void", "payment"))
        if token.startswith("withpayment"):
            tokens.update(("with", "payment"))
        if token.startswith("emaildocument"):
            tokens.update(("email", "document"))
    return frozenset(tokens)


def _high_risk_effects(tokens: frozenset[str]) -> tuple[str, ...]:
    effects: list[str] = []
    if "draft" not in tokens and tokens.intersection({"paid", "payment"}):
        effects.append("payment")
    if tokens.intersection({"approval", "approve", "approved"}):
        effects.append("approve")
    if "void" in tokens:
        effects.append("void")
    if "email" in tokens:
        effects.append("email")
    if "share" in tokens:
        effects.append("share")
    if tokens.intersection({"invitation", "invite"}):
        effects.append("invite")
    return tuple(effects)


def _variant_id(operation_id: str, input_schema: Mapping[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", operation_id.casefold()).strip("_") or "variant"
    schema_hash = hashlib.sha256(canonical_json(input_schema).encode("utf-8")).hexdigest()[:12]
    return f"{slug[:36]}_{schema_hash}"


def _flow_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("flowaccount_endpoint_path_invalid")
    path = value.split("?", 1)[0]
    path = re.sub(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}", r"{\1}", path)
    path = re.sub(r"/[0-9]{4,}(?=/|$)", "/{recordId}", path)
    return normalize_path(path)


def _flow_content_type(body: Any) -> str:
    mode = _text(body.get("mode")).casefold() if isinstance(body, Mapping) else ""
    return {
        "formdata": "multipart/form-data",
        "urlencoded": "application/x-www-form-urlencoded",
    }.get(mode, "application/json")


def _method(value: Any) -> str:
    method = _text(value).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("builtin_endpoint_method_invalid")
    return method


def _operation_id(name: str, method: str, path: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    if words:
        return words[0].casefold() + "".join(word.title() for word in words[1:])
    suffix = "".join(word.title() for word in re.findall(r"[A-Za-z0-9]+", path))
    return method.casefold() + (suffix or "Root")


def _aliases(*values: Any) -> tuple[str, ...]:
    aliases: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.split())
        if normalized and normalized != "[REDACTED]" and normalized not in aliases:
            aliases.append(normalized[:300])
    return tuple(aliases)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _update_wiki(path: Path, actions: Sequence[CatalogAction]) -> None:
    content = path.read_text(encoding="utf-8")
    start = content.find(GENERATED_START)
    end = content.find(GENERATED_END)
    if start >= 0 and end >= start:
        content = content[:start].rstrip() + "\n"
    lines = [
        GENERATED_START,
        "",
        "## Generated Mercury Action Catalog",
        "",
        "This section is generated from the sanitized built-in catalog. Each block binds",
        "endpoint knowledge to one immutable Mercury action identity.",
        "",
    ]
    for index, action in enumerate(actions, start=1):
        title = " ".join((action.description or action.operation_id).split())[:120]
        lines.extend(
            [
                f"### {index}. {title}",
                "",
                f"action_id: {action.action_id}",
                f"method: {action.method.value}",
                f"path: {action.path_template}",
                f"capability: {action.capability}",
                f"risk_tier: {int(action.risk_tier)}",
                f"confidence: {action.confidence.value}",
                f"source_citation: {action.source_uri}#{action.action_id}",
                "",
            ]
        )
    lines.append(GENERATED_END)
    _atomic_text_write(path, content.rstrip() + "\n\n" + "\n".join(lines) + "\n")


def _atomic_json_write(path: Path, value: Any) -> None:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    _atomic_text_write(path, text)


def _atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connector", required=True, choices=("flowaccount", "peak"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    build_catalog(args.connector, args.source, args.output)
    actions = json.loads((args.output / "actions.json").read_text(encoding="utf-8"))
    print(f"built {args.connector} catalog: {len(actions)} actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
