"""Strict public schemas and admission rules for Mercury Cloud Brain."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import sanitize_document, validate_action_identity
from mercury_tools.catalog.schema_contract import (
    is_canonical_schema_name,
    validate_required_schema_contract,
)
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    SemanticContract,
    ValidationStatus,
)
from mercury_tools.rag.models import project_approved_validation_metadata
from mercury_tools.safety.redaction import (
    is_safe_public_http_url,
    redact_absolute_paths,
    redact_json,
    redact_text,
)

PUBLIC_RESPONSE_VALIDATION_ERROR = "cloud_public_response_invalid"

_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,198}[A-Za-z0-9])?$")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_PUBLIC_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_CATALOG_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_ACTION_ID_RE = re.compile(r"^act_[0-9a-f]{24}$")
_ACTION_VERSION_ID_RE = re.compile(r"^av_[0-9a-f]{64}$")
_OPAQUE_EVIDENCE_ID_RE = re.compile(
    r"^ev_[0-9A-HJKMNP-TV-Z]{26}$",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_WIKI_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$")
_CHUNK_FRAGMENT_RE = re.compile(r"^chunk-[0-9]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_PUBLIC_KEY_RE = re.compile(
    r"(?i)(?:(?:repository|source)_?path|credential)"
)
_LOCAL_TEMPLATE_ROOTS = {
    "users",
    "volumes",
    "app",
    "data",
    "etc",
    "home",
    "mnt",
    "opt",
    "private",
    "root",
    "run",
    "srv",
    "tmp",
    "usr",
    "var",
}
_MAX_PUBLIC_TEXT_BYTES = 64 * 1024
_MAX_PATH_TEMPLATE_BYTES = 2_048
_MAX_PATH_TEMPLATE_DECODE_DEPTH = 2
_CATALOG_STRING_FIELDS = {
    "action_id",
    "version_id",
    "connector_id",
    "method",
    "path_template",
    "operation_id",
    "variant_id",
    "content_type",
    "capability",
    "source_uri",
    "source_hash",
    "confidence",
    "observed_state",
    "description",
}
_CATALOG_STRING_LIST_FIELDS = {
    "environments",
    "aliases_th",
    "aliases_en",
    "side_effects",
    "preflight_action_ids",
    "response_redaction",
}
_CATALOG_MAPPING_FIELDS = {
    "input_schema",
    "idempotency",
    "success_rules",
    "error_rules",
}
_CATALOG_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_CATALOG_CONFIDENCE_VALUES = {"exact", "example_derived", "inferred"}
_CATALOG_OBSERVED_STATE_VALUES = {
    "untested",
    "success",
    "failed",
    "outcome_unknown",
}
_INPUT_SCHEMA_SECTIONS = {"path", "query", "headers", "body", "files"}
_SCHEMA_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}
_SCALAR_SCHEMA_TYPES = _SCHEMA_TYPES - {"array", "object"}
_PARAMETER_SCHEMA_KEYS = {"description", "enum", "required", "type"}
_BODY_SCHEMA_KEYS = {
    "additionalProperties",
    "description",
    "enum",
    "items",
    "properties",
    "required",
    "type",
    "x-mercury-required",
    "x-mercury-property-descriptions",
}
_IDEMPOTENCY_KEYS = {
    "duplicate_action_id",
    "failure_values",
    "header_name",
    "preflight_inputs",
    "source",
    "status_action_id",
    "status_inputs",
    "status_result_path",
    "success_values",
}
_REQUEST_INPUT_SECTIONS = {"path", "query", "headers", "body", "files"}
_HEADER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_DATA_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
_REDACTION_SELECTOR_RE = re.compile(
    r"^(?:[A-Za-z0-9_-]+|\*)(?:\.(?:[A-Za-z0-9_-]+|\*))*$"
)
_AUTH_IDENTIFIER_RE = re.compile(
    r"(?:api[_-]?key|auth(?:entication|orization)?|cookie|credential|password|"
    r"proxy[_-]?authorization|secret|set[_-]?cookie|token)",
    re.IGNORECASE,
)
_MAX_SCHEMA_DEPTH = 20
_MAX_SCHEMA_NODES = 8_192
_MAX_RULE_STRING_BYTES = 2_048
_BLOCKING_CONDITION_RE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")


def sanitize_public_text(value: str, *, redact_paths: bool = True) -> str:
    """Return one deterministic, idempotent public representation of text."""

    text = str(sanitize_document(value))
    text = redact_text(text)
    return redact_absolute_paths(text) if redact_paths else text


def is_canonical_skill_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_SKILL_ID_RE.fullmatch(value))


def is_canonical_public_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_PUBLIC_ID_RE.fullmatch(value))


def is_canonical_catalog_identity(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and _CATALOG_IDENTITY_RE.fullmatch(value)
        and sanitize_public_text(value, redact_paths=False) == value
    )


def validate_public_catalog_identity(action: Any) -> None:
    if (
        not isinstance(action.action_id, str)
        or not _ACTION_ID_RE.fullmatch(action.action_id)
        or not is_canonical_catalog_identity(action.connector_id)
        or not is_canonical_catalog_identity(action.operation_id)
        or not is_canonical_catalog_identity(action.variant_id)
        or any(
            not isinstance(item, str) or not _ACTION_ID_RE.fullmatch(item)
            for item in action.preflight_action_ids
        )
    ):
        raise ValueError("cloud_catalog_identity_invalid")
    validate_public_api_path_template(action.path_template)


def validate_raw_catalog_action_payload(value: Any) -> None:
    """Reject malformed public catalog JSON before coercive model normalization."""

    if not isinstance(value, Mapping) or set(value) != set(_catalog_action_fields()):
        raise ValueError("cloud_catalog_invalid")
    if any(not isinstance(value[field], str) for field in _CATALOG_STRING_FIELDS):
        raise ValueError("cloud_catalog_invalid")
    if (
        value["method"] not in _CATALOG_METHODS
        or value["confidence"] not in _CATALOG_CONFIDENCE_VALUES
        or value["observed_state"] not in _CATALOG_OBSERVED_STATE_VALUES
    ):
        raise ValueError("cloud_catalog_invalid")
    if any(
        not isinstance(value[field], list)
        or any(not isinstance(item, str) for item in value[field])
        for field in _CATALOG_STRING_LIST_FIELDS
    ):
        raise ValueError("cloud_catalog_invalid")
    if any(not isinstance(value[field], dict) for field in _CATALOG_MAPPING_FIELDS):
        raise ValueError("cloud_catalog_invalid")
    if not isinstance(value["examples"], list) or any(
        not isinstance(item, dict) for item in value["examples"]
    ):
        raise ValueError("cloud_catalog_invalid")
    if (
        not isinstance(value["risk_tier"], int)
        or isinstance(value["risk_tier"], bool)
        or value["risk_tier"] not in {0, 1, 2}
        or not isinstance(value["required_confirmations"], int)
        or isinstance(value["required_confirmations"], bool)
    ):
        raise ValueError("cloud_catalog_invalid")
    _validate_public_catalog_contract_payload(value)


def validate_public_catalog_action(action: Any) -> None:
    """Admit one canonical executable action without changing its identity."""

    try:
        validate_action_identity(action)
        validate_public_catalog_identity(action)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("cloud_catalog_projection_invalid") from None
    payload = action.model_dump(mode="json")
    _validate_public_catalog_contract_payload(payload)
    if (
        not _ACTION_VERSION_ID_RE.fullmatch(action.version_id)
        or not _SHA256_RE.fullmatch(action.source_hash)
        or not _is_public_catalog_source_uri(action.source_uri)
    ):
        raise ValueError("cloud_catalog_projection_invalid")


def _validate_public_catalog_contract_payload(payload: Mapping[str, Any]) -> None:
    try:
        _validate_public_catalog_text_fields(payload)
        validate_public_api_path_template(payload["path_template"])
        _validate_public_input_schema(payload["input_schema"])
        if payload["examples"] != []:
            raise ValueError
        _validate_public_idempotency(
            payload["idempotency"],
            input_schema=payload["input_schema"],
            preflight_action_ids=payload["preflight_action_ids"],
        )
        _validate_public_response_rules(payload["success_rules"], error=False)
        _validate_public_response_rules(payload["error_rules"], error=True)
        _validate_response_redaction(payload["response_redaction"])
    except (KeyError, OverflowError, RecursionError, TypeError, ValueError):
        raise ValueError("cloud_catalog_projection_invalid") from None


def _validate_public_catalog_text_fields(payload: Mapping[str, Any]) -> None:
    for field in _CATALOG_STRING_FIELDS - {"path_template"}:
        _validate_safe_text(payload[field], max_bytes=_MAX_PUBLIC_TEXT_BYTES)
    for field in _CATALOG_STRING_LIST_FIELDS:
        for item in payload[field]:
            _validate_safe_text(item, max_bytes=_MAX_PUBLIC_TEXT_BYTES)


def _validate_public_input_schema(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != _INPUT_SCHEMA_SECTIONS:
        raise ValueError
    budget = [_MAX_SCHEMA_NODES]
    for section in ("path", "query", "headers", "files"):
        declarations = value[section]
        if not isinstance(declarations, dict):
            raise ValueError
        for name, declaration in declarations.items():
            _validate_schema_name(name)
            _validate_parameter_schema(
                declaration,
                section=section,
                depth=0,
                budget=budget,
            )
    body = value["body"]
    if not isinstance(body, dict):
        raise ValueError
    if body:
        validate_required_schema_contract(body, allow_frozen_required=False)
        _validate_body_schema(body, depth=0, budget=budget)
        if body.get("type") != "object":
            raise ValueError


def _validate_parameter_schema(
    value: Any,
    *,
    section: str,
    depth: int,
    budget: list[int],
) -> None:
    _consume_schema_budget(depth, budget)
    allowed = _PARAMETER_SCHEMA_KEYS | ({"format"} if section == "files" else set())
    if not isinstance(value, dict) or not value or set(value) - allowed:
        raise ValueError
    schema_type = value.get("type")
    if section == "files":
        if schema_type != "string" or value.get("format") != "binary":
            raise ValueError
    elif schema_type not in _SCALAR_SCHEMA_TYPES:
        raise ValueError
    _validate_schema_description(value)
    _validate_schema_enum(value, schema_type)
    if "required" in value and not isinstance(value["required"], bool):
        raise ValueError


def _validate_body_schema(value: Any, *, depth: int, budget: list[int]) -> None:
    _consume_schema_budget(depth, budget)
    if not isinstance(value, dict) or not value or set(value) - _BODY_SCHEMA_KEYS:
        raise ValueError
    schema_type = value.get("type")
    if schema_type is None and set(value) == {"properties"}:
        schema_type = "object"
    if schema_type not in _SCHEMA_TYPES:
        raise ValueError
    _validate_schema_description(value)
    _validate_schema_enum(value, schema_type)
    if "x-mercury-required" in value and (
        depth != 0 or not isinstance(value["x-mercury-required"], bool)
    ):
        raise ValueError

    object_only = {
        "additionalProperties",
        "properties",
        "required",
        "x-mercury-property-descriptions",
    }
    if schema_type != "object" and set(value) & object_only:
        raise ValueError
    if schema_type != "array" and "items" in value:
        raise ValueError
    if schema_type in {"array", "object"} and "enum" in value:
        raise ValueError

    if schema_type == "array":
        if "items" not in value:
            raise ValueError
        _validate_body_schema(value["items"], depth=depth + 1, budget=budget)
        return
    if schema_type != "object":
        return

    properties = value.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError
    for name, declaration in properties.items():
        _validate_schema_name(name)
        _validate_body_schema(declaration, depth=depth + 1, budget=budget)
    if "additionalProperties" in value and value["additionalProperties"] is not False:
        raise ValueError
    required = value.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(name, str) for name in required)
        or len(required) != len(set(required))
        or any(name not in properties for name in required)
    ):
        raise ValueError
    descriptions = value.get("x-mercury-property-descriptions", [])
    if not isinstance(descriptions, list):
        raise ValueError
    seen: set[str] = set()
    for item in descriptions:
        if not isinstance(item, dict) or set(item) != {"description", "name"}:
            raise ValueError
        name = item["name"]
        _validate_schema_name(name)
        _validate_safe_text(item["description"], max_bytes=_MAX_PUBLIC_TEXT_BYTES)
        if name not in properties or name in seen:
            raise ValueError
        seen.add(name)


def _consume_schema_budget(depth: int, budget: list[int]) -> None:
    if depth > _MAX_SCHEMA_DEPTH or budget[0] <= 0:
        raise ValueError
    budget[0] -= 1


def _validate_schema_name(value: Any) -> None:
    if not is_canonical_schema_name(value):
        raise ValueError


def _validate_schema_description(value: Mapping[str, Any]) -> None:
    if "description" in value:
        _validate_safe_text(value["description"], max_bytes=_MAX_PUBLIC_TEXT_BYTES)


def _validate_schema_enum(value: Mapping[str, Any], schema_type: Any) -> None:
    if "enum" not in value:
        return
    enum = value["enum"]
    if not isinstance(enum, list) or not enum:
        raise ValueError
    for index, item in enumerate(enum):
        if not _scalar_matches_schema_type(item, schema_type):
            raise ValueError
        _validate_safe_scalar(item)
        if any(type(item) is type(previous) and item == previous for previous in enum[:index]):
            raise ValueError


def _scalar_matches_schema_type(value: Any, schema_type: Any) -> bool:
    matches = {
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
        "number": isinstance(value, int | float)
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value)),
        "string": isinstance(value, str),
    }
    return bool(matches.get(schema_type, False))


def _validate_public_idempotency(
    value: Any,
    *,
    input_schema: Mapping[str, Any],
    preflight_action_ids: Any,
) -> None:
    if not isinstance(value, dict) or set(value) - _IDEMPOTENCY_KEYS:
        raise ValueError
    if not isinstance(preflight_action_ids, list):
        raise ValueError
    header = value.get("header_name")
    source = value.get("source")
    if (header is None) != (source is None):
        raise ValueError
    if header is not None:
        if (
            not isinstance(header, str)
            or not _HEADER_NAME_RE.fullmatch(header)
            or _AUTH_IDENTIFIER_RE.search(header)
            or not isinstance(source, str)
            or not _DATA_PATH_RE.fullmatch(source)
        ):
            raise ValueError
        declaration = _idempotency_source_schema(input_schema, source)
        if declaration.get("type") not in {"integer", "string"}:
            raise ValueError

    for key in ("duplicate_action_id", "status_action_id"):
        if key in value and (
            not isinstance(value[key], str) or not _ACTION_ID_RE.fullmatch(value[key])
        ):
            raise ValueError
    duplicate_action_id = value.get("duplicate_action_id")
    if duplicate_action_id is not None and duplicate_action_id not in preflight_action_ids:
        raise ValueError

    preflight_inputs = value.get("preflight_inputs", {})
    if not isinstance(preflight_inputs, dict):
        raise ValueError
    for action_id, inputs in preflight_inputs.items():
        if action_id not in preflight_action_ids or not _ACTION_ID_RE.fullmatch(action_id):
            raise ValueError
        _validate_catalog_request_inputs(inputs)

    status_fields = {
        "failure_values",
        "status_inputs",
        "status_result_path",
        "success_values",
    }
    if set(value) & status_fields and "status_action_id" not in value:
        raise ValueError
    if "status_inputs" in value:
        _validate_catalog_request_inputs(value["status_inputs"])
    if "status_result_path" in value and (
        not isinstance(value["status_result_path"], str)
        or not _DATA_PATH_RE.fullmatch(value["status_result_path"])
    ):
        raise ValueError
    for key in ("success_values", "failure_values"):
        if key in value:
            items = value[key]
            if not isinstance(items, list) or not items:
                raise ValueError
            for item in items:
                _validate_safe_scalar(item)


def _idempotency_source_schema(
    input_schema: Mapping[str, Any], source: str
) -> Mapping[str, Any]:
    root, *parts = source.split(".")
    if root not in {"path", "query", "body"} or not parts:
        raise ValueError
    current: Any = input_schema[root]
    if root in {"path", "query"}:
        current = current.get(parts.pop(0)) if isinstance(current, dict) else None
    while parts:
        if not isinstance(current, dict) or current.get("type") != "object":
            raise ValueError
        properties = current.get("properties")
        current = properties.get(parts.pop(0)) if isinstance(properties, dict) else None
    if not isinstance(current, dict):
        raise ValueError
    return current


def _validate_catalog_request_inputs(value: Any) -> None:
    if not isinstance(value, dict) or set(value) - _REQUEST_INPUT_SECTIONS:
        raise ValueError
    for section, item in value.items():
        if section == "body":
            _validate_safe_json_value(item, depth=0)
            continue
        if not isinstance(item, dict):
            raise ValueError
        if section == "files" and item:
            raise ValueError
        _validate_safe_json_value(item, depth=0)
    if redact_json(value) != value:
        raise ValueError


def _validate_public_response_rules(value: Any, *, error: bool) -> None:
    allowed = {"body", "status_codes"} if error else {"status_codes"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError
    if "status_codes" in value:
        codes = value["status_codes"]
        if (
            not isinstance(codes, list)
            or not codes
            or any(
                not isinstance(code, int)
                or isinstance(code, bool)
                or not 100 <= code <= 599
                for code in codes
            )
            or len(codes) != len(set(codes))
        ):
            raise ValueError
    if "body" in value:
        body = value["body"]
        if not isinstance(body, dict) or set(body) != {"equals", "path"}:
            raise ValueError
        if not isinstance(body["path"], str) or not _DATA_PATH_RE.fullmatch(body["path"]):
            raise ValueError
        _validate_safe_scalar(body["equals"])


def _validate_response_redaction(value: Any) -> None:
    if (
        not isinstance(value, list)
        or len(value) != len(set(value))
        or any(
            not isinstance(selector, str)
            or not _within_limit(selector, _MAX_RULE_STRING_BYTES)
            or not _REDACTION_SELECTOR_RE.fullmatch(selector)
            for selector in value
        )
    ):
        raise ValueError


def _validate_safe_json_value(value: Any, *, depth: int) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        raise ValueError
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError
        return
    if isinstance(value, str):
        _validate_safe_text(value, max_bytes=_MAX_PUBLIC_TEXT_BYTES)
        return
    if isinstance(value, list):
        for item in value:
            _validate_safe_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_schema_name(key)
            _validate_safe_json_value(item, depth=depth + 1)
        return
    raise ValueError


def _validate_safe_scalar(value: Any) -> None:
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError
    if isinstance(value, str):
        _validate_safe_text(value, max_bytes=_MAX_RULE_STRING_BYTES)
        return
    raise ValueError


def _validate_safe_text(value: Any, *, max_bytes: int) -> None:
    if (
        not isinstance(value, str)
        or not _within_limit(value, max_bytes)
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
        or sanitize_public_text(value) != value
    ):
        raise ValueError


def _is_public_catalog_source_uri(value: Any) -> bool:
    if not isinstance(value, str) or not value or not _within_limit(value, 2_048):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme in {"http", "https"}:
        return bool(
            not parsed.fragment
            and is_safe_public_http_url(value)
            and sanitize_public_text(value) == value
        )
    if (
        parsed.scheme != "mercury"
        or parsed.netloc != "catalog"
        or parsed.query
        or parsed.fragment
        or "%" in value
        or "\\" in value
        or not parsed.path.startswith("/")
    ):
        return False
    segments = parsed.path.removeprefix("/").split("/")
    return bool(
        segments
        and all(_WIKI_SEGMENT_RE.fullmatch(segment) for segment in segments)
        and value == f"mercury://catalog/{'/'.join(segments)}"
    )


def _catalog_action_fields() -> tuple[str, ...]:
    from mercury_tools.catalog.models import CatalogAction

    return tuple(CatalogAction.model_fields)


def is_canonical_public_wiki_uri(value: Any, *, allow_chunk: bool = False) -> bool:
    if not isinstance(value, str) or not value or len(value) > 520:
        return False
    if "%" in value or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (
        parsed.scheme != "mercury"
        or parsed.netloc != "wiki"
        or parsed.query
        or not parsed.path.startswith("/")
    ):
        return False
    if parsed.fragment:
        if not allow_chunk or not _CHUNK_FRAGMENT_RE.fullmatch(parsed.fragment):
            return False
    elif allow_chunk:
        return False
    segments = parsed.path.removeprefix("/").split("/")
    if not segments or any(not _WIKI_SEGMENT_RE.fullmatch(item) for item in segments):
        return False
    canonical = f"mercury://wiki/{'/'.join(segments)}"
    if parsed.fragment:
        canonical = f"{canonical}#{parsed.fragment}"
    return canonical == value


def is_canonical_document_identifier(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return is_canonical_public_wiki_uri(value)


def validate_public_api_path_template(value: Any) -> None:
    """Admit only relative API path templates, including safe placeholders."""

    if not isinstance(value, str) or not value or not _within_limit(
        value, _MAX_PATH_TEMPLATE_BYTES
    ):
        raise ValueError("cloud_path_template_invalid")
    if sanitize_public_text(value, redact_paths=False) != value:
        raise ValueError("cloud_path_template_invalid")

    candidate = value
    for depth in range(_MAX_PATH_TEMPLATE_DECODE_DEPTH + 1):
        if not _valid_decoded_path_template(candidate):
            raise ValueError("cloud_path_template_invalid")
        if depth == _MAX_PATH_TEMPLATE_DECODE_DEPTH or "%" not in candidate:
            break
        decoded = unquote(candidate)
        if decoded == candidate or not _within_limit(decoded, _MAX_PATH_TEMPLATE_BYTES):
            break
        candidate = decoded
    if "%" in candidate and unquote(candidate) != candidate:
        raise ValueError("cloud_path_template_invalid")


def _valid_decoded_path_template(value: str) -> bool:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or "?" in value
        or "#" in value
        or "\x00" in value
        or any(character.isspace() for character in value)
        or value.casefold().startswith("file:")
        or "://" in value
    ):
        return False
    segments = value.removeprefix("/").split("/")
    if segments and segments[-1] == "":
        segments.pop()
    if not segments or any(not segment for segment in segments):
        return False
    if any(segment in {".", ".."} for segment in segments):
        return False
    return segments[0].casefold() not in _LOCAL_TEMPLATE_ROOTS


class StrictPublicModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )

    @model_validator(mode="after")
    def validate_public_values(self) -> StrictPublicModel:
        _validate_public_value(self.model_dump(mode="python"))
        return self


class PublicConnector(StrictPublicModel):
    connector_id: str
    capabilities: list[str]
    environments: list[str]

    @model_validator(mode="after")
    def validate_connector(self) -> PublicConnector:
        if not _PUBLIC_NAME_RE.fullmatch(self.connector_id):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        if any(not item or len(item) > 200 for item in self.capabilities):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        if any(not _PUBLIC_NAME_RE.fullmatch(item) for item in self.environments):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicSkill(StrictPublicModel):
    skill_id: str
    title: str
    category: str
    summary: str
    status: str
    version: str
    required_connectors: list[str]
    tags: list[str]

    @model_validator(mode="after")
    def validate_skill(self) -> PublicSkill:
        if (
            not is_canonical_skill_id(self.skill_id)
            or not _PUBLIC_NAME_RE.fullmatch(self.category)
            or not _PUBLIC_NAME_RE.fullmatch(self.status)
            or not _VERSION_RE.fullmatch(self.version)
            or any(not _PUBLIC_NAME_RE.fullmatch(item) for item in self.required_connectors)
            or any(not _PUBLIC_NAME_RE.fullmatch(item) for item in self.tags)
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicSkillDetail(PublicSkill):
    markdown: str


class PublicCitation(StrictPublicModel):
    chunk_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_title: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_uri: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    heading: str | None = Field(default=None, exclude_if=lambda value: value is None)
    chunk_index: int | None = Field(default=None, exclude_if=lambda value: value is None)
    page: int | None = Field(default=None, exclude_if=lambda value: value is None)
    section: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_citation(self) -> PublicCitation:
        if self.chunk_id is not None and not is_canonical_public_id(self.chunk_id):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        if self.source_uri is not None and not is_canonical_public_wiki_uri(
            self.source_uri
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        if self.source_url is not None and not _is_public_http_url(self.source_url):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicValidationMetadata(StrictPublicModel):
    jurisdiction: Literal["TH"]
    connector: Literal["flowaccount", "peak"]
    doc_type: Literal["endpoint_validation"]
    review_status: Literal["reviewed"]
    action_id: str
    version_id: str
    environment: Literal["sandbox", "test", "uat", "production"]
    capability: str
    accounting_use: list[str]
    validation_status: Literal[
        "live_success",
        "live_failed",
        "contract_validated",
        "blocked_missing_credentials",
        "blocked_missing_prerequisite",
        "blocked_external_effect",
        "unsupported_by_sandbox",
    ]
    evidence_level: Literal[
        "documented",
        "contract_validated",
        "sandbox_observed",
        "accountant_reviewed",
    ]
    approval_state: Literal["approved_public"]

    @model_validator(mode="after")
    def validate_metadata(self) -> PublicValidationMetadata:
        try:
            projected = project_approved_validation_metadata(
                self.model_dump(mode="python")
            )
        except ValueError:
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR) from None
        if projected is None:
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicEvidenceRequest(StrictPublicModel):
    connector_id: str
    action_id: str
    version_id: str
    environment: Literal["sandbox", "test", "uat", "production"]

    @model_validator(mode="after")
    def validate_exact_scope(self) -> PublicEvidenceRequest:
        if (
            not is_canonical_catalog_identity(self.connector_id)
            or _ACTION_ID_RE.fullmatch(self.action_id) is None
            or _ACTION_VERSION_ID_RE.fullmatch(self.version_id) is None
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self

    @property
    def scope_key(self) -> tuple[str, str, str, str]:
        return (
            self.connector_id,
            self.action_id,
            self.version_id,
            self.environment,
        )


class PublicValidationEvidence(StrictPublicModel):
    action_id: str
    version_id: str
    connector_id: str
    environment: Literal["sandbox", "test", "uat", "production"]
    validation_status: ValidationStatus
    evidence_level: EvidenceLevel
    execution_eligibility: ExecutionEligibility
    summary_th: str
    summary_en: str
    limitations: tuple[str, ...]
    prerequisites: tuple[str, ...]
    recommended_next_step: str
    semantic_contract: SemanticContract
    opaque_evidence_id: str
    evidence_sha256: str
    evaluated_at: datetime
    expires_at: datetime | None

    @model_validator(mode="after")
    def validate_evidence(self) -> PublicValidationEvidence:
        if (
            _ACTION_ID_RE.fullmatch(self.action_id) is None
            or _ACTION_VERSION_ID_RE.fullmatch(self.version_id) is None
            or not is_canonical_catalog_identity(self.connector_id)
            or _OPAQUE_EVIDENCE_ID_RE.fullmatch(self.opaque_evidence_id) is None
            or _SHA256_RE.fullmatch(self.evidence_sha256) is None
            or self.evaluated_at.tzinfo is None
            or self.evaluated_at.utcoffset() is None
            or (
                self.expires_at is not None
                and (
                    self.expires_at.tzinfo is None
                    or self.expires_at.utcoffset() is None
                    or self.expires_at <= self.evaluated_at
                )
            )
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self

    def is_admissible_at(self, now: datetime) -> bool:
        """Return whether this evidence may be exposed as the selected result now."""

        normalized_now = _normalize_public_evidence_time(now)
        evaluated_at = self.evaluated_at.astimezone(UTC)
        expires_at = self.expires_at.astimezone(UTC) if self.expires_at is not None else None
        if evaluated_at > normalized_now or (
            expires_at is not None and expires_at <= normalized_now
        ):
            return False
        if self.validation_status is ValidationStatus.CONTRACT_VALIDATED:
            return (
                self.evidence_level is EvidenceLevel.CONTRACT_VALIDATED
                and self.execution_eligibility is ExecutionEligibility.DISCOVERY_ONLY
            )
        if self.validation_status is not ValidationStatus.LIVE_SUCCESS:
            return False
        return (
            self.environment == "sandbox"
            and self.evidence_level
            in {EvidenceLevel.SANDBOX_OBSERVED, EvidenceLevel.ACCOUNTANT_REVIEWED}
            and self.execution_eligibility
            in {
                ExecutionEligibility.SANDBOX_READ,
                ExecutionEligibility.SANDBOX_WRITE_WITH_APPROVAL,
            }
        )


class PublicEvidenceSelection(StrictPublicModel):
    selected: PublicValidationEvidence | None
    blocking_conditions: tuple[str, ...]

    @model_validator(mode="after")
    def validate_selection(self) -> PublicEvidenceSelection:
        if (
            (self.selected is None) == (not self.blocking_conditions)
            or len(set(self.blocking_conditions)) != len(self.blocking_conditions)
            or any(
                _BLOCKING_CONDITION_RE.fullmatch(condition) is None
                for condition in self.blocking_conditions
            )
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


def _normalize_public_evidence_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
    return value.astimezone(UTC)


class PublicValidationResolveRequest(StrictPublicModel):
    requests: tuple[PublicEvidenceRequest, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_requests(self) -> PublicValidationResolveRequest:
        scopes = tuple(request.scope_key for request in self.requests)
        if len(set(scopes)) != len(scopes):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicEvidenceSelectionsEnvelope(StrictPublicModel):
    selections: tuple[PublicEvidenceSelection, ...] = Field(
        min_length=1,
        max_length=100,
    )


class PublicSearchResult(StrictPublicModel):
    chunk_id: str
    document_id: str
    document_uri: str
    chunk_uri: str
    text: str
    score: float = Field(allow_inf_nan=False)
    source_title: str
    source_uri: str
    source_url: str | None
    citation: PublicCitation
    metadata: PublicValidationMetadata | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_search_result(self) -> PublicSearchResult:
        if (
            not is_canonical_public_id(self.chunk_id)
            or not is_canonical_public_id(self.document_id)
            or not is_canonical_public_wiki_uri(self.document_uri)
            or not is_canonical_public_wiki_uri(self.source_uri)
            or not is_canonical_public_wiki_uri(self.chunk_uri, allow_chunk=True)
            or not self.chunk_uri.startswith(f"{self.document_uri}#")
            or not math.isfinite(self.score)
            or (self.source_url is not None and not _is_public_http_url(self.source_url))
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicDocumentSource(StrictPublicModel):
    title: str
    source_uri: str
    source_url: str | None

    @model_validator(mode="after")
    def validate_source(self) -> PublicDocumentSource:
        if not is_canonical_public_wiki_uri(self.source_uri) or (
            self.source_url is not None and not _is_public_http_url(self.source_url)
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicDocument(StrictPublicModel):
    id: str
    document_uri: str
    title: str
    body: str
    sha256: str
    source: PublicDocumentSource
    metadata: PublicValidationMetadata | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_document(self) -> PublicDocument:
        if (
            not is_canonical_document_identifier(self.id)
            or not is_canonical_public_wiki_uri(self.document_uri)
            or not _SHA256_RE.fullmatch(self.sha256)
        ):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return self


class PublicConnectorsEnvelope(StrictPublicModel):
    connectors: list[PublicConnector]


class PublicSkillsEnvelope(StrictPublicModel):
    skills: list[PublicSkill]


class PublicSearchEnvelope(StrictPublicModel):
    results: list[PublicSearchResult]


def validate_skill_identity(requested: str, skill: PublicSkillDetail) -> None:
    if skill.skill_id != requested:
        raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)


def validate_document_identity(requested: str, document: PublicDocument) -> None:
    if is_canonical_public_wiki_uri(requested):
        matches = document.document_uri == requested
    else:
        matches = document.id == requested
    if not matches:
        raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)


def _validate_public_value(value: Any) -> None:
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return
    if isinstance(value, str):
        if not _within_limit(value, _MAX_PUBLIC_TEXT_BYTES):
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        sanitized = sanitize_public_text(value)
        if sanitized != value or sanitize_public_text(sanitized) != sanitized:
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        return
    if isinstance(value, Mapping):
        if redact_json(value) != value:
            raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
        for key, item in value.items():
            if not isinstance(key, str) or _PRIVATE_PUBLIC_KEY_RE.search(key):
                raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)
            _validate_public_value(key)
            _validate_public_value(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for item in value:
            _validate_public_value(item)
        return
    raise ValueError(PUBLIC_RESPONSE_VALIDATION_ERROR)


def _is_public_http_url(value: str) -> bool:
    return bool(
        is_safe_public_http_url(value)
        and sanitize_public_text(value) == value
    )


def _within_limit(value: str, limit: int) -> bool:
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False
