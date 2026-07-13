"""RAG data models."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

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
        "outcome_unknown",
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
    return isinstance(value, Mapping) and (
        value.get("doc_type") == "endpoint_validation" or "approval_state" in value
    )


def project_approved_validation_metadata(value: Any) -> dict[str, Any] | None:
    """Return only typed approved validation metadata; never infer approval."""
    if not is_validation_metadata_candidate(value):
        return None
    if (
        value.get("review_status") != "reviewed"
        or value.get("approval_state") != "approved_public"
    ):
        return None

    try:
        projected = {field: value[field] for field in VALIDATION_METADATA_FIELDS}
        accounting_uses = projected["accounting_use"]
        if (
            projected["jurisdiction"] != "TH"
            or projected["connector"] not in {"flowaccount", "peak"}
            or projected["doc_type"] != "endpoint_validation"
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
                {
                    "chunk_id": result.chunk_id,
                    "document_id": result.document_id,
                    "document_uri": result.document_uri,
                    "text": result.text,
                    "score": result.score,
                    "citation": result.citation,
                    "source_title": result.source_title,
                    "source_uri": result.source_uri,
                    "source_url": result.source_url,
                    "source_path": result.source_path,
                    "metadata": result.metadata,
                }
                for result in self.results
            ],
        }
