"""Strict public schemas and admission rules for Mercury Cloud Brain."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import sanitize_document
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
