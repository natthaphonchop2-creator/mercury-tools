"""RAG data models."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from mercury_tools.safety.redaction import redact_absolute_paths, redact_text

_SELECTOR = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_ACTION_ID = re.compile(r"^act_[0-9a-f]{24}$")
_VERSION_ID = re.compile(r"^av_[0-9a-f]{64}$")
_DOTTED_TERM = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_ENVIRONMENTS = frozenset({"sandbox", "test", "uat", "production"})
_VALIDATION_STATUSES = frozenset(
    {
        "live_success",
        "live_failed",
        "contract_validated",
        "blocked_missing_credentials",
        "blocked_missing_prerequisite",
        "blocked_external_effect",
        "unsupported_by_sandbox",
    }
)
_EVIDENCE_LEVELS = frozenset(
    {
        "documented",
        "contract_validated",
        "sandbox_observed",
        "accountant_reviewed",
    }
)
VALIDATION_METADATA_FIELDS = (
    "jurisdiction",
    "connector",
    "doc_type",
    "review_status",
    "action_id",
    "version_id",
    "environment",
    "capability",
    "accounting_use",
    "validation_status",
    "evidence_level",
    "approval_state",
)
GENERAL_PUBLIC_METADATA_FIELDS = (
    "jurisdiction",
    "connector",
    "doc_type",
    "review_status",
    "effective_date",
    "action_id",
)
PUBLIC_CITATION_FIELDS = (
    "chunk_id",
    "source_title",
    "source_uri",
    "source_url",
    "heading",
    "chunk_index",
    "page",
    "section",
)
_VALIDATION_ONLY_METADATA_FIELDS = frozenset(VALIDATION_METADATA_FIELDS) - frozenset(
    GENERAL_PUBLIC_METADATA_FIELDS
)
_VALIDATION_URI_PREFIX = "mercury://wiki/validation/"
DOCUMENTED_SEARCH_FILTER_FIELDS = frozenset(
    {
        "jurisdiction",
        "connector",
        "doc_type",
        "review_status",
        "effective_date",
        "action_id",
        "version_id",
        "environment",
        "capability",
        "accounting_use",
    }
)
VALIDATION_CONTEXT_FILTER_FIELDS = frozenset(
    {
        "action_id",
        "version_id",
        "environment",
        "capability",
        "accounting_use",
    }
)


@dataclass(frozen=True)
class SearchFilters:
    jurisdiction: str | None = None
    connector: str | None = None
    doc_type: str | None = None
    review_status: str | None = None
    effective_date: str | None = None
    action_id: str | None = None
    version_id: str | None = None
    environment: str | None = None
    capability: str | None = None
    accounting_use: str | None = None

    def __post_init__(self) -> None:
        selectors = (
            self.jurisdiction,
            self.connector,
            self.doc_type,
            self.review_status,
        )
        if any(
            value is not None
            and (not isinstance(value, str) or _SELECTOR.fullmatch(value) is None)
            for value in selectors
        ):
            raise ValueError("search_filters_invalid")
        if self.effective_date is not None:
            try:
                if date.fromisoformat(self.effective_date).isoformat() != self.effective_date:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("search_filters_invalid") from None
        if self.action_id is not None and (
            not isinstance(self.action_id, str)
            or _ACTION_ID.fullmatch(self.action_id) is None
        ):
            raise ValueError("search_filters_invalid")
        if self.version_id is not None and (
            not isinstance(self.version_id, str)
            or _VERSION_ID.fullmatch(self.version_id) is None
        ):
            raise ValueError("search_filters_invalid")
        if self.environment is not None and (
            not isinstance(self.environment, str)
            or self.environment not in _ENVIRONMENTS
        ):
            raise ValueError("search_filters_invalid")
        if self.capability is not None and (
            not isinstance(self.capability, str)
            or _DOTTED_TERM.fullmatch(self.capability) is None
        ):
            raise ValueError("search_filters_invalid")
        if self.accounting_use is not None and (
            not isinstance(self.accounting_use, str)
            or _DOTTED_TERM.fullmatch(self.accounting_use) is None
        ):
            raise ValueError("search_filters_invalid")

    def to_rpc_payload(self) -> dict[str, Any]:
        return {
            "filter_jurisdiction": self.jurisdiction,
            "filter_connector": self.connector,
            "filter_doc_type": self.doc_type,
            "filter_review_status": self.review_status,
            "filter_effective_date": self.effective_date,
            "filter_action_id": self.action_id,
            "filter_version_id": self.version_id,
            "filter_environment": self.environment,
            "filter_capability": self.capability,
            "filter_accounting_use": self.accounting_use,
        }


def is_validation_metadata_candidate(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(
        value.get("doc_type") == "endpoint_validation"
        or set(value).intersection(_VALIDATION_ONLY_METADATA_FIELDS)
    )


def is_validation_knowledge(
    value: Any,
    *,
    document_uri: str | None = None,
    source_uri: str | None = None,
    doc_type: str | None = None,
) -> bool:
    return bool(
        doc_type == "endpoint_validation"
        or is_validation_metadata_candidate(value)
        or _is_validation_uri(document_uri)
        or _is_validation_uri(source_uri)
    )


def project_approved_validation_metadata(value: Any) -> dict[str, Any] | None:
    """Return only typed approved validation metadata; never infer approval."""
    if not is_validation_metadata_candidate(value):
        return None
    return _require_approved_validation_metadata(value)


def _require_approved_validation_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("validation_metadata_invalid")

    try:
        projected = {field: value[field] for field in VALIDATION_METADATA_FIELDS}
        accounting_uses = projected["accounting_use"]
        if (
            projected["jurisdiction"] != "TH"
            or projected["connector"] not in {"flowaccount", "peak"}
            or projected["doc_type"] != "endpoint_validation"
            or projected["review_status"] != "reviewed"
            or projected["approval_state"] != "approved_public"
            or not isinstance(accounting_uses, Sequence)
            or isinstance(accounting_uses, (str, bytes, bytearray))
            or len(accounting_uses) > 128
            or len(set(accounting_uses)) != len(accounting_uses)
            or projected["validation_status"] not in _VALIDATION_STATUSES
            or projected["evidence_level"] not in _EVIDENCE_LEVELS
        ):
            raise ValueError
        for accounting_use in accounting_uses:
            SearchFilters(accounting_use=accounting_use)
        SearchFilters(
            jurisdiction=projected["jurisdiction"],
            connector=projected["connector"],
            doc_type=projected["doc_type"],
            review_status=projected["review_status"],
            action_id=projected["action_id"],
            version_id=projected["version_id"],
            environment=projected["environment"],
            capability=projected["capability"],
        )
        projected["accounting_use"] = list(accounting_uses)
        return projected
    except (KeyError, TypeError, ValueError):
        raise ValueError("validation_metadata_invalid") from None


def project_public_knowledge_metadata(
    value: Any,
    *,
    document_uri: str | None = None,
    source_uri: str | None = None,
    doc_type: str | None = None,
) -> dict[str, Any]:
    """Project one fail-closed public metadata shape without arbitrary fields."""
    validation = is_validation_knowledge(
        value,
        document_uri=document_uri,
        source_uri=source_uri,
        doc_type=doc_type,
    )
    try:
        if validation:
            return _require_approved_validation_metadata(value)
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError
        projected = {
            field: value[field]
            for field in GENERAL_PUBLIC_METADATA_FIELDS
            if field in value and value[field] is not None
        }
        SearchFilters(
            jurisdiction=projected.get("jurisdiction"),
            connector=projected.get("connector"),
            doc_type=projected.get("doc_type"),
            review_status=projected.get("review_status"),
            effective_date=projected.get("effective_date"),
            action_id=projected.get("action_id"),
        )
        return projected
    except (KeyError, TypeError, ValueError):
        raise ValueError("public_knowledge_metadata_invalid") from None


def public_search_result_payload(
    result: Any,
    *,
    include_document_id: bool = False,
) -> dict[str, Any]:
    """Serialize one search result through public field and metadata allowlists."""
    try:
        metadata = project_public_knowledge_metadata(
            result.metadata,
            document_uri=result.document_uri,
            source_uri=result.source_uri,
        )
        validation = is_validation_knowledge(
            result.metadata,
            document_uri=result.document_uri,
            source_uri=result.source_uri,
        )
        score = result.score
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValueError
        payload: dict[str, Any] = {
            "chunk_id": _public_result_text(result.chunk_id),
            "document_uri": _public_result_text(result.document_uri),
            "score": score,
            "text": _public_result_text(result.text),
            "citation": _public_citation(result.citation, validation=validation),
            "metadata": metadata,
            "source_title": _public_result_text(result.source_title),
            "source_uri": _public_result_text(result.source_uri),
        }
        if include_document_id:
            payload["document_id"] = _public_result_text(result.document_id)
        if not validation:
            payload["source_url"] = (
                _public_result_text(result.source_url)
                if result.source_url is not None
                else None
            )
        return payload
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("public_knowledge_metadata_invalid") from None


def _public_citation(value: Any, *, validation: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError
    projected: dict[str, Any] = {}
    for citation_field in PUBLIC_CITATION_FIELDS:
        if citation_field not in value or (
            validation and citation_field == "source_url"
        ):
            continue
        item = value[citation_field]
        if isinstance(item, str):
            projected[citation_field] = _public_result_text(item)
        elif item is None or (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ):
            projected[citation_field] = item
        else:
            raise ValueError
    return projected


def _public_result_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError
    return redact_absolute_paths(redact_text(value))


def _is_validation_uri(value: Any) -> bool:
    return isinstance(value, str) and value.casefold().startswith(
        _VALIDATION_URI_PREFIX
    )


@dataclass(frozen=True)
class KnowledgeDocument:
    document_uri: str
    title: str
    body: str
    sha256: str
    source_uri: str
    source_title: str
    path: Path | None = None
    source_url: str | None = None
    jurisdiction: str | None = None
    connector: str | None = None
    doc_type: str = "wiki"
    review_status: str = "draft"
    effective_date: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeChunk:
    document_uri: str
    chunk_uri: str
    chunk_index: int
    text: str
    source_title: str
    source_uri: str
    source_url: str | None
    source_path: str | None
    heading: str | None
    citation: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    document_id: str
    document_uri: str
    chunk_uri: str
    text: str
    score: float
    source_title: str
    source_uri: str
    source_url: str | None
    source_path: str | None
    citation: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextPack:
    query: str
    task: str | None
    results: list[SearchResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "task": self.task,
            "context": [
                public_search_result_payload(result, include_document_id=True)
                for result in self.results
            ],
        }
