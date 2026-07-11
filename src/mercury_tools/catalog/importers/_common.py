from __future__ import annotations

import re
from typing import Any

from mercury_tools.catalog.identity import build_action_id, build_version_id
from mercury_tools.catalog.models import CatalogAction, CatalogSource, RiskTier

_METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}


def build_action(
    *,
    source: CatalogSource,
    connector_id: str,
    method: str,
    path_template: str,
    operation_id: str,
    confidence: str,
    description: str = "",
    content_type: str = "application/json",
    input_schema: dict[str, Any] | None = None,
    examples: tuple[dict[str, Any], ...] = (),
    success_codes: tuple[int, ...] = (),
    error_codes: tuple[int, ...] = (),
) -> CatalogAction:
    risk_tier = risk_for_method(method)
    normalized_operation = operation_id or generated_operation_id(method, path_template)
    aliases_en = (description,) if description else ()
    values: dict[str, Any] = {
        "action_id": "",
        "version_id": "",
        "connector_id": connector_id,
        "environments": ("production", "sandbox"),
        "method": method,
        "path_template": normalize_path(path_template),
        "operation_id": normalized_operation,
        "variant_id": "default",
        "content_type": content_type,
        "aliases_th": (),
        "aliases_en": aliases_en,
        "capability": capability_for(normalized_operation),
        "input_schema": input_schema or empty_input_schema(),
        "examples": examples,
        "risk_tier": risk_tier,
        "required_confirmations": int(risk_tier),
        "side_effects": side_effects_for(method),
        "preflight_action_ids": (),
        "idempotency": {},
        "success_rules": {"status_codes": success_codes} if success_codes else {},
        "error_rules": {"status_codes": error_codes} if error_codes else {},
        "response_redaction": (),
        "source_uri": source.source_uri,
        "source_hash": source.source_hash,
        "confidence": confidence,
        "observed_state": "untested",
        "description": description,
    }
    base = CatalogAction.model_validate(values)
    identified = base.model_copy(update={"action_id": build_action_id(base)})
    return identified.model_copy(update={"version_id": build_version_id(identified)})


def empty_input_schema() -> dict[str, Any]:
    return {"path": {}, "query": {}, "headers": {}, "body": {}, "files": {}}


def normalize_path(path: str) -> str:
    clean = path.strip()
    clean = re.sub(r"^\{\{[^{}]+\}\}", "", clean)
    clean = clean.split("?", 1)[0].split("#", 1)[0]
    if "://" in clean:
        from urllib.parse import urlsplit

        clean = urlsplit(clean).path
    clean = re.sub(r"(?<=/)\:([A-Za-z_][A-Za-z0-9_-]*)", r"{\1}", clean)
    if not clean.startswith("/"):
        clean = "/" + clean
    clean = re.sub(r"/{2,}", "/", clean)
    return clean or "/"


def generated_operation_id(method: str, path: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", path)
    suffix = "".join(word[:1].upper() + word[1:] for word in words) or "Root"
    return method.casefold() + suffix


def capability_for(operation_id: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1.\2", operation_id)
    words = re.sub(r"[^A-Za-z0-9]+", ".", words).strip(".").casefold()
    return f"imported.{words or 'endpoint'}"


def risk_for_method(method: str) -> RiskTier:
    if method == "GET":
        return RiskTier.SAFE_READ
    if method == "DELETE":
        return RiskTier.HIGH_RISK
    return RiskTier.STANDARD_WRITE


def side_effects_for(method: str) -> tuple[str, ...]:
    if method == "GET":
        return ()
    if method == "DELETE":
        return ("deletes_remote_data",)
    return ("writes_remote_data",)


def sort_actions(actions: list[CatalogAction]) -> list[CatalogAction]:
    return sorted(
        actions,
        key=lambda action: (
            action.path_template,
            _METHOD_ORDER[action.method.value],
            action.operation_id,
            action.action_id,
        ),
    )
