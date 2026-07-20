from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mercury_tools.catalog.identity import build_action_id, build_version_id
from mercury_tools.catalog.models import CatalogAction, CatalogSource, RiskTier
from mercury_tools.local.repository import RepositoryContext, ensure_repository_state


@pytest.fixture
def repository_context(tmp_path: Path) -> RepositoryContext:
    return ensure_repository_state(tmp_path)


@pytest.fixture
def action_factory() -> Callable[..., CatalogAction]:
    def build(**overrides: Any) -> CatalogAction:
        values: dict[str, Any] = {
            "action_id": "",
            "version_id": "",
            "connector_id": "flowaccount",
            "environments": ("production", "sandbox"),
            "method": "POST",
            "path_template": "/invoices",
            "operation_id": "createInvoice",
            "variant_id": "simple",
            "content_type": "application/json",
            "aliases_th": ("สร้างใบแจ้งหนี้",),
            "aliases_en": ("create invoice",),
            "capability": "documents.invoice.create",
            "input_schema": {
                "path": {},
                "query": {},
                "headers": {},
                "body": {"type": "object"},
                "files": {},
            },
            "examples": (),
            "risk_tier": RiskTier.STANDARD_WRITE,
            "required_confirmations": 1,
            "side_effects": ("creates_document",),
            "preflight_action_ids": (),
            "idempotency": {},
            "success_rules": {},
            "error_rules": {},
            "response_redaction": (),
            "source_uri": "https://example.test/openapi.json",
            "source_hash": "a" * 64,
            "confidence": "exact",
            "observed_state": "success",
            "description": "Create invoice",
        }
        values.update(overrides)
        base = CatalogAction(**values)
        action_id = build_action_id(base)
        identified = base.model_copy(update={"action_id": action_id})
        return identified.model_copy(update={"version_id": build_version_id(identified)})

    return build


@pytest.fixture
def catalog_action(action_factory: Callable[..., CatalogAction]) -> CatalogAction:
    return action_factory()


@pytest.fixture
def catalog_source() -> CatalogSource:
    return CatalogSource.from_document(
        uri="https://example.test/openapi.json",
        connector_id="flowaccount",
        document={"openapi": "3.0.0", "info": {"version": "2026-07"}},
        report={"status": "imported"},
    )
