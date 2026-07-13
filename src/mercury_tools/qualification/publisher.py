"""Review and RAG projection contracts for endpoint validation knowledge."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.qualification.models import (
    QualificationReport,
    QualificationRunState,
    StrictSafeModel,
    ValidationKnowledge,
)
from mercury_tools.qualification.semantics import load_actions
from mercury_tools.rag.models import (
    KnowledgeDocument,
    project_approved_validation_metadata,
)

REVIEWER_ROLES = frozenset({"release_reviewer", "accountant_reviewer"})
EXPECTED_CONNECTOR_COVERAGE = MappingProxyType({"flowaccount": 190, "peak": 64})
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


class ReviewedValidationReport(StrictSafeModel):
    records: tuple[ValidationKnowledge, ...]
    reviewer_role: Literal["release_reviewer", "accountant_reviewer"]


class CatalogDefinitions:
    """Exact immutable catalog identities used by validation publication."""

    def __init__(self, actions: Sequence[CatalogAction]) -> None:
        if isinstance(actions, (str, bytes, bytearray)) or not isinstance(
            actions, Sequence
        ):
            raise ValueError("validation_catalog_invalid")
        indexed: dict[tuple[str, str, str], CatalogAction] = {}
        try:
            for raw_action in actions:
                action = revalidate_catalog_action(raw_action)
                if _CAPABILITY.fullmatch(action.capability) is None:
                    raise ValueError
                identity = (
                    action.connector_id,
                    action.action_id,
                    action.version_id,
                )
                if identity in indexed:
                    raise ValueError
                indexed[identity] = action
        except (AttributeError, TypeError, ValueError):
            raise ValueError("validation_catalog_invalid") from None
        self._actions = MappingProxyType(indexed)

    def capability_for(
        self,
        connector_id: str,
        action_id: str,
        version_id: str,
    ) -> str:
        action = self._actions.get((connector_id, action_id, version_id))
        if action is None:
            raise ValueError("validation_catalog_scope_mismatch")
        return action.capability

    def identities_for(self, connector_id: str) -> frozenset[tuple[str, str, str]]:
        return frozenset(
            identity for identity in self._actions if identity[0] == connector_id
        )


def load_catalog_definitions(catalog_root: Path) -> CatalogDefinitions:
    if not isinstance(catalog_root, Path) or not catalog_root.is_dir():
        raise ValueError("validation_catalog_invalid")
    action_paths = sorted(catalog_root.glob("*/actions.json"))
    if not action_paths:
        raise ValueError("validation_catalog_invalid")
    try:
        actions = [
            action
            for action_path in action_paths
            for action in load_actions(action_path)
        ]
        return CatalogDefinitions(actions)
    except (OSError, TypeError, ValueError):
        raise ValueError("validation_catalog_invalid") from None


def review_validation_report(
    report: QualificationReport | Any,
    *,
    reviewer_role: str,
    catalog: CatalogDefinitions,
) -> ReviewedValidationReport:
    if reviewer_role not in REVIEWER_ROLES:
        raise ValueError("validation_reviewer_role_invalid")
    if not isinstance(catalog, CatalogDefinitions):
        raise ValueError("validation_catalog_invalid")

    try:
        validated_report = QualificationReport.model_validate(report)
        records = tuple(
            ValidationKnowledge.model_validate(record)
            for record in validated_report.records
        )
    except (AttributeError, TypeError, ValueError):
        raise ValueError("validation_report_invalid") from None

    if validated_report.run_state is not QualificationRunState.COMPLETED:
        raise ValueError("validation_run_not_publishable")
    if any(record.run_state is not QualificationRunState.COMPLETED for record in records):
        raise ValueError("validation_run_not_publishable")
    if any(
        (
            record.connector_id,
            record.environment,
            record.run_id,
        )
        != (
            validated_report.connector_id,
            validated_report.environment,
            validated_report.run_id,
        )
        for record in records
    ):
        raise ValueError("validation_report_scope_mismatch")

    expected_count = EXPECTED_CONNECTOR_COVERAGE.get(validated_report.connector_id)
    record_identities = tuple(
        (record.connector_id, record.action_id, record.version_id)
        for record in records
    )
    catalog_identities = catalog.identities_for(validated_report.connector_id)
    if (
        expected_count is None
        or len(records) != expected_count
        or len(set(record_identities)) != expected_count
        or len(catalog_identities) != expected_count
        or set(record_identities) != catalog_identities
    ):
        raise ValueError("validation_coverage_incomplete")

    promoted: list[ValidationKnowledge] = []
    for record in sorted(
        records,
        key=lambda item: (item.connector_id, item.action_id, item.version_id),
    ):
        catalog.capability_for(
            record.connector_id,
            record.action_id,
            record.version_id,
        )
        try:
            promoted.append(
                ValidationKnowledge.model_validate(
                    {
                        **record.model_dump(mode="python"),
                        "approved_public": True,
                        "reviewed_by": reviewer_role,
                    }
                )
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("validation_publication_unsafe") from None

    return ReviewedValidationReport(
        records=tuple(promoted),
        reviewer_role=reviewer_role,
    )


def validation_documents(
    records: Sequence[ValidationKnowledge],
    *,
    catalog: CatalogDefinitions,
) -> tuple[KnowledgeDocument, ...]:
    if isinstance(records, (str, bytes, bytearray)) or not isinstance(
        records, Sequence
    ):
        raise ValueError("validation_evidence_invalid")
    if not isinstance(catalog, CatalogDefinitions):
        raise ValueError("validation_catalog_invalid")

    approved: list[ValidationKnowledge] = []
    try:
        for raw_record in records:
            record = ValidationKnowledge.model_validate(raw_record)
            if record.approved_public:
                if (
                    record.reviewed_by not in REVIEWER_ROLES
                    or record.run_state is not QualificationRunState.COMPLETED
                ):
                    raise ValueError("validation_evidence_not_reviewed")
                approved.append(record)
    except ValueError as error:
        if str(error) == "validation_evidence_not_reviewed":
            raise
        raise ValueError("validation_evidence_invalid") from None
    except (AttributeError, TypeError):
        raise ValueError("validation_evidence_invalid") from None

    documents: list[KnowledgeDocument] = []
    for record in sorted(
        approved,
        key=lambda item: (
            item.connector_id,
            item.action_id,
            item.version_id,
            item.environment,
            item.run_id,
        ),
    ):
        capability = catalog.capability_for(
            record.connector_id,
            record.action_id,
            record.version_id,
        )
        accounting_uses = list(record.semantic_contract.accounting_uses)
        body = "\n".join(
            (
                f"# {record.connector_id} {record.action_id}",
                f"Connector: {record.connector_id}",
                f"Action ID: {record.action_id}",
                f"Version ID: {record.version_id}",
                f"Environment: {record.environment}",
                f"Capability: {capability}",
                "Accounting uses: " + (", ".join(accounting_uses) or "none"),
                f"Validation status: {record.validation_status.value}",
                f"Evidence level: {record.evidence_level.value}",
                "Approval state: approved_public",
                f"Summary TH: {record.summary_th}",
                f"Summary EN: {record.summary_en}",
                f"Evidence ID: {record.opaque_evidence_id}",
                f"Evidence digest: {record.evidence_sha256}",
            )
        )
        document_uri = (
            f"mercury://wiki/validation/{record.connector_id}/"
            f"{record.action_id}/{record.version_id}/{record.run_id}"
        )
        metadata = {
            "jurisdiction": "TH",
            "connector": record.connector_id,
            "doc_type": "endpoint_validation",
            "review_status": "reviewed",
            "action_id": record.action_id,
            "version_id": record.version_id,
            "environment": record.environment,
            "capability": capability,
            "accounting_use": accounting_uses,
            "validation_status": record.validation_status.value,
            "evidence_level": record.evidence_level.value,
            "approval_state": "approved_public",
        }
        try:
            projected_metadata = project_approved_validation_metadata(metadata)
        except ValueError:
            raise ValueError("validation_evidence_invalid") from None
        if projected_metadata is None:
            raise ValueError("validation_evidence_invalid")
        documents.append(
            KnowledgeDocument(
                document_uri=document_uri,
                title=f"{record.connector_id} {record.action_id} validation",
                body=body,
                sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                source_uri=document_uri,
                source_title=f"{record.connector_id} endpoint validation",
                path=None,
                source_url=None,
                jurisdiction="TH",
                connector=record.connector_id,
                doc_type="endpoint_validation",
                review_status="reviewed",
                metadata=projected_metadata,
            )
        )
    return tuple(documents)
