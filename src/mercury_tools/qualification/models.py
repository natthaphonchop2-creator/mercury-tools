"""Pydantic contracts for endpoint qualification evidence."""

from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping, Sequence, Set
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import (
    FrozenDict,
    validate_credential_safe,
    validate_credential_safe_paths,
)

_MAX_PUBLIC_CONTENT_DEPTH = 8
_MAX_PUBLIC_CONTAINER_ITEMS = 128
_MAX_PUBLIC_TOTAL_ITEMS = 512
_MAX_PUBLIC_STRING_LENGTH = 512
_PUBLIC_URL_LIKE = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*:[/\\]+|(?<!:)//[^/\s]+|\bwww\.|"
    r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?=$|[/?#:\s]))"
)
_PUBLIC_ABSOLUTE_PATH_LIKE = re.compile(
    r"(?i)(?:^|[\s(])(?:~[/\\]|/(?!/)[^\s]*|[a-z]:[/\\]|\\\\[^\s\\]+\\)"
)
_PUBLIC_RELATIVE_PATH_LIKE = re.compile(
    r"(?i)(?<![A-Za-z0-9])\.{1,2}[/\\][^\s]*"
)
_PUBLIC_SLASH_TOKEN = re.compile(r"\S*[/\\]\S*")
_PUBLIC_SLASH_TERM_ALLOWLIST = frozenset({"debit/credit", "input/output"})
_PUBLIC_SLASH_TOKEN_BOUNDARY = "\"'()[]{}<>,;!?.:"
_PUBLIC_FORBIDDEN_LABEL_TOKENS = frozenset(
    {
        "auth",
        "authentication",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "oauth",
        "password",
        "payload",
        "raw",
        "secret",
        "source",
        "token",
    }
)
_PUBLIC_FORBIDDEN_LABEL_GROUPS = (
    frozenset({"access", "key"}),
    frozenset({"api", "key"}),
    frozenset({"auth", "header"}),
    frozenset({"client", "id"}),
)
_PUBLIC_CAMEL_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_PUBLIC_CAMEL_WORD_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_PUBLIC_WORD = re.compile(r"[A-Za-z]+")
_PUBLIC_CAPABILITY_SEGMENT = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_PUBLIC_CAPABILITY_PATHS = frozenset(
    {
        ("semantic_contract", "optional_external_capabilities"),
        ("semantic_contract", "required_external_capabilities"),
    }
)
_INTERNAL_PUBLIC_ID_PATTERNS = {
    "opaque_evidence_id": re.compile(r"^ev_[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE),
    "run_id": re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$", re.IGNORECASE),
    "action_id": re.compile(r"^act_[0-9a-f]{24}$"),
    "version_id": re.compile(r"^av_[0-9a-f]{64}$"),
    "evidence_sha256": re.compile(r"^[0-9a-f]{64}$"),
}
_INTERNAL_NEXT_ACTION_ID = re.compile(r"^act_[0-9a-f]{24}$")


class ValidationStatus(StrEnum):
    LIVE_SUCCESS = "live_success"
    LIVE_FAILED = "live_failed"
    CONTRACT_VALIDATED = "contract_validated"
    BLOCKED_MISSING_CREDENTIALS = "blocked_missing_credentials"
    BLOCKED_MISSING_PREREQUISITE = "blocked_missing_prerequisite"
    BLOCKED_EXTERNAL_EFFECT = "blocked_external_effect"
    UNSUPPORTED_BY_SANDBOX = "unsupported_by_sandbox"
    OUTCOME_UNKNOWN = "outcome_unknown"


class EvidenceLevel(StrEnum):
    DOCUMENTED = "documented"
    CONTRACT_VALIDATED = "contract_validated"
    SANDBOX_OBSERVED = "sandbox_observed"
    ACCOUNTANT_REVIEWED = "accountant_reviewed"


class ExecutionEligibility(StrEnum):
    DISCOVERY_ONLY = "discovery_only"
    SANDBOX_READ = "sandbox_read"
    SANDBOX_WRITE_WITH_APPROVAL = "sandbox_write_with_approval"
    PRODUCTION_PENDING_VALIDATION = "production_pending_validation"
    BLOCKED = "blocked"


class QualificationRunState(StrEnum):
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class StrictSafeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        values = {
            field_name: (
                _deep_copy_for_validation(getattr(self, field_name))
                if deep
                else getattr(self, field_name)
            )
            for field_name in type(self).model_fields
        }
        if update:
            values.update(
                {
                    key: _deep_copy_for_validation(value) if deep else value
                    for key, value in update.items()
                }
            )
        copied = type(self).model_validate(values)
        fields_set = self.model_fields_set.copy()
        if update:
            fields_set.update(update)
        object.__setattr__(copied, "__pydantic_fields_set__", fields_set)
        return copied

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: Any) -> Any:
        validate_credential_safe(value)
        validate_credential_safe_paths(value)
        return value

    @model_validator(mode="after")
    def freeze_nested_content(self) -> StrictSafeModel:
        for field_name in type(self).model_fields:
            object.__setattr__(
                self,
                field_name,
                _canonical_deep_freeze(getattr(self, field_name)),
            )
        return self


class SemanticContract(StrictSafeModel):
    business_object: str
    operation: str
    accounting_uses: tuple[str, ...] = ()
    output_semantics: dict[str, str] = Field(default_factory=dict)
    join_keys: tuple[str, ...] = ()
    next_action_ids: tuple[str, ...] = ()
    required_external_capabilities: tuple[str, ...] = ()
    optional_external_capabilities: tuple[str, ...] = ()
    fallbacks: tuple[str, ...] = ()


class ValidationKnowledge(StrictSafeModel):
    opaque_evidence_id: str
    run_id: str
    action_id: str
    version_id: str
    connector_id: str
    environment: str
    validation_status: ValidationStatus
    evidence_level: EvidenceLevel
    execution_eligibility: ExecutionEligibility
    approved_public: bool = False
    summary_th: str
    summary_en: str
    prerequisites: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    recommended_next_step: str
    response_shape: dict[str, Any] = Field(default_factory=dict)
    status_class: str
    latency_ms: int | None = Field(default=None, ge=0)
    semantic_contract: SemanticContract
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_by: str
    runner_version: str
    run_state: QualificationRunState
    evaluated_at: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_approved_public_content(self) -> ValidationKnowledge:
        if not self.approved_public:
            return self

        from mercury_tools.qualification.response_shape import (
            _validate_approved_public_response_shape,
        )
        from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

        if (
            self.summary_th != SUMMARY_TH[self.validation_status]
            or self.summary_en != SUMMARY_EN[self.validation_status]
        ):
            raise ValueError("approved_public_summary_not_controlled")
        _validate_approved_public_response_shape(self.response_shape)
        _validate_approved_public_content(self)
        return self


class QualificationReport(StrictSafeModel):
    connector_id: str
    environment: str
    run_id: str
    run_state: QualificationRunState
    records: tuple[ValidationKnowledge, ...]

    @property
    def total(self) -> int:
        return len(self.records)


def _validate_approved_public_content(record: ValidationKnowledge) -> None:
    remaining_items = [_MAX_PUBLIC_TOTAL_ITEMS]
    _validate_public_value(record, depth=0, path=(), remaining_items=remaining_items)


def _validate_public_value(
    value: Any, *, depth: int, path: tuple[str, ...], remaining_items: list[int]
) -> None:
    if depth > _MAX_PUBLIC_CONTENT_DEPTH:
        raise ValueError("approved_public_content_unsafe")

    if isinstance(value, str):
        if _is_unsafe_public_string(value, path):
            raise ValueError("approved_public_content_unsafe")
        return

    if isinstance(value, StrictSafeModel):
        items = tuple(
            (field_name, getattr(value, field_name)) for field_name in type(value).model_fields
        )
        _validate_public_items(
            items, depth=depth, path=path, remaining_items=remaining_items
        )
        return

    if isinstance(value, Mapping):
        _validate_public_items(
            tuple(value.items()), depth=depth, path=path, remaining_items=remaining_items
        )
        return

    if isinstance(value, (Sequence, Set)) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_PUBLIC_CONTAINER_ITEMS:
            raise ValueError("approved_public_content_unsafe")
        for item in value:
            _consume_public_item(remaining_items)
            _validate_public_value(
                item,
                depth=depth + 1,
                path=path,
                remaining_items=remaining_items,
            )
        return

    if value is None or isinstance(value, (bool, int, float, datetime)):
        return

    raise ValueError("approved_public_content_unsafe")


def _validate_public_items(
    items: tuple[tuple[Any, Any], ...],
    *,
    depth: int,
    path: tuple[str, ...],
    remaining_items: list[int],
) -> None:
    if len(items) > _MAX_PUBLIC_CONTAINER_ITEMS:
        raise ValueError("approved_public_content_unsafe")
    for key, item in items:
        if not isinstance(key, str):
            raise ValueError("approved_public_content_unsafe")
        _consume_public_item(remaining_items)
        _validate_public_value(
            key,
            depth=depth + 1,
            path=(*path, "<key>"),
            remaining_items=remaining_items,
        )
        _consume_public_item(remaining_items)
        _validate_public_value(
            item,
            depth=depth + 1,
            path=(*path, key),
            remaining_items=remaining_items,
        )


def _consume_public_item(remaining_items: list[int]) -> None:
    remaining_items[0] -= 1
    if remaining_items[0] < 0:
        raise ValueError("approved_public_content_unsafe")


def _is_internal_public_identifier(value: str, path: tuple[str, ...]) -> bool:
    if len(path) == 1:
        pattern = _INTERNAL_PUBLIC_ID_PATTERNS.get(path[0])
        return pattern is not None and pattern.fullmatch(value) is not None
    return path == ("semantic_contract", "next_action_ids") and bool(
        _INTERNAL_NEXT_ACTION_ID.fullmatch(value)
    )


def _is_controlled_summary_path(path: tuple[str, ...]) -> bool:
    return len(path) == 1 and path[0] in {"summary_en", "summary_th"}


def _is_controlled_validation_status(value: str, path: tuple[str, ...]) -> bool:
    return path == ("validation_status",) and value in ValidationStatus


def _is_unsafe_public_string(value: str, path: tuple[str, ...]) -> bool:
    if (
        not value
        or len(value) > _MAX_PUBLIC_STRING_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "@" in value
        or (
            _has_long_numeric_identifier(value)
            and not _is_internal_public_identifier(value, path)
        )
    ):
        return True

    if path in _PUBLIC_CAPABILITY_PATHS:
        return not _is_strict_dotted_capability(value)

    return bool(
        _PUBLIC_URL_LIKE.search(value)
        or _PUBLIC_ABSOLUTE_PATH_LIKE.search(value)
        or _PUBLIC_RELATIVE_PATH_LIKE.search(value)
        or _has_unsafe_public_path_token(value)
        or (
            not _is_controlled_summary_path(path)
            and not _is_controlled_validation_status(value, path)
            and _has_forbidden_public_label(value)
        )
    )


def _is_strict_dotted_capability(value: str) -> bool:
    parts = value.split(".")
    return (
        3 <= len(parts) <= 6
        and all(
            1 <= len(part) <= 32 and _PUBLIC_CAPABILITY_SEGMENT.fullmatch(part)
            for part in parts
        )
        and not _has_forbidden_public_label(value)
    )


def _has_unsafe_public_path_token(value: str) -> bool:
    for match in _PUBLIC_SLASH_TOKEN.finditer(value):
        token = match.group(0).strip(_PUBLIC_SLASH_TOKEN_BOUNDARY)
        if "\\" in token:
            return True
        if token.casefold() in _PUBLIC_SLASH_TERM_ALLOWLIST:
            continue
        parts = token.split("/")
        if len(parts) > 1 and all(part.isdecimal() for part in parts):
            continue
        return True
    return False


def _has_long_numeric_identifier(value: str) -> bool:
    digits = 0
    for character in value:
        if character.isdecimal():
            digits += 1
            if digits >= 9:
                return True
        elif digits and (
            character.isspace()
            or unicodedata.category(character).startswith("P")
            or character == "\N{MINUS SIGN}"
        ):
            continue
        else:
            digits = 0
    return False


def _has_forbidden_public_label(value: str) -> bool:
    tokens = _public_content_tokens(value)
    return not tokens.isdisjoint(_PUBLIC_FORBIDDEN_LABEL_TOKENS) or any(
        group <= tokens for group in _PUBLIC_FORBIDDEN_LABEL_GROUPS
    )


def _public_content_tokens(value: str) -> frozenset[str]:
    separated = _PUBLIC_CAMEL_ACRONYM_BOUNDARY.sub(r"\1_\2", value)
    separated = _PUBLIC_CAMEL_WORD_BOUNDARY.sub(r"\1_\2", separated)
    return frozenset(token.casefold() for token in _PUBLIC_WORD.findall(separated))


def _canonical_deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict(
            {
                key: _canonical_deep_freeze(item)
                for key, item in sorted(
                    value.items(), key=lambda pair: _canonical_mapping_key(pair[0])
                )
            }
        )
    if isinstance(value, (tuple, list)):
        return tuple(_canonical_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_canonical_deep_freeze(item) for item in value)
    return value


def _canonical_mapping_key(key: Any) -> tuple[str, str, str]:
    key_type = type(key)
    return key_type.__module__, key_type.__qualname__, repr(key)


def _deep_copy_for_validation(value: Any) -> Any:
    if isinstance(value, StrictSafeModel):
        return value.model_copy(deep=True)
    if isinstance(value, Mapping):
        return {
            copy.deepcopy(key): _deep_copy_for_validation(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_deep_copy_for_validation(item) for item in value)
    if isinstance(value, list):
        return [_deep_copy_for_validation(item) for item in value]
    if isinstance(value, set):
        return {_deep_copy_for_validation(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_deep_copy_for_validation(item) for item in value)
    return copy.deepcopy(value)
