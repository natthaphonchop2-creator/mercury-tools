from __future__ import annotations

import hashlib
import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from mercury_tools.catalog.models import (
    ProviderMCPQualification,
    QualificationState,
)
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.qualification.artifacts import build_qualification_artifact
from mercury_tools.qualification.provider_mcp import (
    CapabilityQualificationGate,
    CapabilitySelection,
    OwnerAuthorizedCanary,
    transition_qualification,
)
from mercury_tools.qualification.semantics import load_actions

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def _definition(
    *,
    provider: str = "flowaccount",
    environment: str = "sandbox",
    normalized_capability: str = "documents.invoice.get",
    provider_tool_name: str = "get_invoice",
    input_schema: dict[str, object] | None = None,
    output_schema: dict[str, object] | None = None,
) -> ProviderMCPQualification:
    return ProviderMCPQualification.discovered(
        provider=provider,
        environment=environment,
        provider_tool_name=provider_tool_name,
        normalized_capability=normalized_capability,
        input_schema=input_schema or {"type": "object", "properties": {}},
        output_schema=output_schema or {"type": "object", "properties": {"id": {"type": "string"}}},
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )


def _artifact(
    definition: ProviderMCPQualification,
    *,
    environment: str | None = None,
) -> object:
    return build_qualification_artifact(
        definition=definition,
        environment=environment or definition.environment,
        company_sha256="b" * 64,
        runner_version="test-runner-v1",
        evaluated_at=NOW,
        input_sha256="c" * 64,
        sanitized_result_identifier="result_test_001",
        checks={"schema_matches": True, "response_shape_matches": True},
        reviewer="release_reviewer",
        evidence_expires_at=NOW + timedelta(days=7),
        passed=True,
    )


def _qualified(
    definition: ProviderMCPQualification,
) -> ProviderMCPQualification:
    artifact = _artifact(definition)
    schema_validated = transition_qualification(
        definition,
        QualificationState.SCHEMA_VALIDATED,
        now=NOW,
    )
    nonproduction = transition_qualification(
        schema_validated,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=artifact,
        now=NOW,
    )
    return transition_qualification(
        nonproduction,
        QualificationState.ENABLED,
        evidence=artifact,
        now=NOW,
    )


def _selection(definition: ProviderMCPQualification, **updates: object) -> CapabilitySelection:
    values: dict[str, object] = {
        "provider": definition.provider,
        "environment": definition.environment,
        "normalized_capability": definition.normalized_capability,
        "provider_tool_name": definition.provider_tool_name,
        "capability_version_sha256": definition.capability_version_sha256,
    }
    values.update(updates)
    return CapabilitySelection.model_validate(values)


def test_generic_capability_gate_does_not_accept_a_bootstrap_company_bypass() -> None:
    definition = _definition()
    gate = CapabilityQualificationGate(
        (_qualified(definition),),
        artifacts=(_artifact(definition),),
    )

    with pytest.raises(TypeError):
        gate.bind(
            _selection(definition),
            company_sha256="a" * 64,
            bootstrap=True,
            now=NOW,
        )


def test_existing_catalog_rows_keep_their_immutable_ids() -> None:
    for connector, expected_count in (("flowaccount", 190), ("peak", 64)):
        actions = load_actions(ROOT / "catalog" / "global" / connector / "actions.json")

        assert len(actions) == expected_count
        assert len({(action.action_id, action.version_id) for action in actions}) == expected_count


def test_source_controlled_manifests_supply_all_seed_identities_without_evidence() -> None:
    expected = {
        "provider_profile.get",
        "documents.invoice.list",
        "documents.invoice.get",
        "documents.invoice.create",
    }
    for provider in ("flowaccount", "peak"):
        manifest = load_provider_manifest(ROOT / "catalog" / "global" / provider / "driver.json")

        assert {
            mapping.normalized_capability for mapping in manifest.discovery_mappings
        } == expected


def test_only_approved_lifecycle_transitions_are_allowed() -> None:
    definition = _definition()
    schema_validated = transition_qualification(
        definition,
        QualificationState.SCHEMA_VALIDATED,
        now=NOW,
    )
    nonproduction = transition_qualification(
        schema_validated,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=_artifact(definition),
        now=NOW,
    )
    enabled = transition_qualification(
        nonproduction,
        QualificationState.ENABLED,
        evidence=_artifact(definition),
        now=NOW,
    )

    assert enabled.qualification_state is QualificationState.ENABLED
    assert (
        transition_qualification(
            enabled,
            QualificationState.DISABLED,
            disable_reason="reviewed_regression",
            now=NOW,
        ).qualification_state
        is QualificationState.DISABLED
    )
    assert (
        transition_qualification(
            enabled,
            QualificationState.SUPERSEDED,
            disable_reason="new_immutable_version",
            now=NOW,
        ).qualification_state
        is QualificationState.SUPERSEDED
    )
    with pytest.raises(ValueError, match="^qualification_transition_invalid$"):
        transition_qualification(
            definition,
            QualificationState.ENABLED,
            now=NOW,
        )


def test_schema_change_creates_a_new_unqualified_immutable_version() -> None:
    enabled = _qualified(_definition())
    changed = _definition(
        input_schema={
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        }
    )

    assert changed.capability_version_sha256 != enabled.capability_version_sha256
    assert changed.qualification_state is QualificationState.DISCOVERED_UNREVIEWED
    assert (
        CapabilityQualificationGate([enabled, changed], artifacts=(_artifact(enabled),))
        .resolve(_selection(changed), company_sha256="b" * 64)
        .status
        == "insufficient_evidence"
    )


def test_seed_discovery_rag_skills_and_legacy_observations_cannot_authorize_execution() -> None:
    definition = _definition()
    gate = CapabilityQualificationGate([definition])
    unqualified = gate.resolve(_selection(definition), company_sha256="b" * 64)

    assert unqualified.status == "insufficient_evidence"
    assert (
        gate.resolve(
            _selection(definition, provider_tool_name="discovery_suggested_tool"),
            company_sha256="b" * 64,
        ).status
        == "capability_unavailable"
    )


@pytest.mark.parametrize(
    ("capability", "tool", "permissions"),
    [
        ("ledger.journal.create", "create_journal", ("journals.create",)),
        ("payments.transfer.create", "create_transfer", ("payments.create",)),
    ],
)
def test_only_reads_and_document_creates_can_be_enabled(
    capability: str,
    tool: str,
    permissions: tuple[str, ...],
) -> None:
    definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name=tool,
        normalized_capability=capability,
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        response_shape_hash="a" * 64,
        required_permissions=permissions,
    )
    schema_validated = transition_qualification(
        definition,
        QualificationState.SCHEMA_VALIDATED,
        now=NOW,
    )
    nonproduction = transition_qualification(
        schema_validated,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=_artifact(definition),
        now=NOW,
    )

    with pytest.raises(ValueError, match="^qualification_operation_not_allowed$"):
        transition_qualification(
            nonproduction,
            QualificationState.ENABLED,
            now=NOW,
        )


def test_production_enablement_requires_exact_nonproduction_evidence_and_owner_canary() -> None:
    sandbox = _definition()
    sandbox_schema_validated = transition_qualification(
        sandbox,
        QualificationState.SCHEMA_VALIDATED,
        now=NOW,
    )
    sandbox_nonproduction = transition_qualification(
        sandbox_schema_validated,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=_artifact(sandbox),
        now=NOW,
    )
    production = _definition(environment="production")
    production_schema_validated = transition_qualification(
        production,
        QualificationState.SCHEMA_VALIDATED,
        now=NOW,
    )
    production_artifact = _artifact(production)
    production_nonproduction = transition_qualification(
        production_schema_validated,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=production_artifact,
        nonproduction_evidence=(sandbox_nonproduction,),
        nonproduction_artifacts=(_artifact(sandbox),),
        now=NOW,
    )

    with pytest.raises(ValueError, match="^production_canary_required$"):
        transition_qualification(
            production_nonproduction,
            QualificationState.ENABLED,
            evidence=production_artifact,
            nonproduction_evidence=(sandbox_nonproduction,),
            nonproduction_artifacts=(_artifact(sandbox),),
            now=NOW,
        )

    enabled = transition_qualification(
        production_nonproduction,
        QualificationState.ENABLED,
        evidence=production_artifact,
        nonproduction_evidence=(sandbox_nonproduction,),
        nonproduction_artifacts=(_artifact(sandbox),),
        canary=OwnerAuthorizedCanary(
            provider="flowaccount",
            environment="production",
            normalized_capability="documents.invoice.get",
            provider_tool_name="get_invoice",
            capability_version_sha256=production.capability_version_sha256,
            owner_authorized_by="workspace_owner",
            authorized_at=NOW,
        ),
        now=NOW,
    )

    assert enabled.qualification_state is QualificationState.ENABLED


def test_execution_lookup_is_exact_and_fails_closed_for_expired_evidence() -> None:
    enabled = _qualified(_definition())
    expired = enabled.model_copy(update={"evidence_expires_at": NOW - timedelta(seconds=1)})
    gate = CapabilityQualificationGate([enabled], artifacts=(_artifact(enabled),))

    assert gate.resolve(_selection(enabled), company_sha256="b" * 64, now=NOW).status == "enabled"
    assert (
        gate.resolve(
            _selection(enabled, environment="production"),
            company_sha256="b" * 64,
        ).status
        == "capability_unavailable"
    )
    assert (
        CapabilityQualificationGate([expired], artifacts=(_artifact(enabled),))
        .resolve(
            _selection(expired),
            company_sha256="b" * 64,
            now=NOW,
        )
        .status
        == "insufficient_evidence"
    )


def test_evidence_uri_is_bound_to_the_exact_provider_catalog() -> None:
    enabled = _qualified(_definition())
    values = enabled.model_dump(mode="python")
    values["qualification_evidence_uri"] = (
        f"catalog://global/peak/qualifications/{enabled.capability_version_sha256}-"
        f"{enabled.evidence_revision_sha256}.json"
    )

    with pytest.raises(ValueError, match="provider_mcp_qualification_evidence_required"):
        ProviderMCPQualification.model_validate(values)


def test_enabled_catalog_qualification_is_the_only_source_of_runtime_binding() -> None:
    enabled = _qualified(_definition())
    gate = CapabilityQualificationGate([enabled], artifacts=(_artifact(enabled),))

    binding = gate.bind(_selection(enabled), company_sha256="b" * 64, now=NOW)
    verified = gate.verify(
        _selection(enabled),
        resource_uri_sha256="d" * 64,
        company_sha256="b" * 64,
        now=NOW,
    )

    assert binding.provider_tool == enabled.provider_tool_name
    assert binding.qualification_hash == enabled.evidence_revision_sha256
    assert verified.capability_version == enabled.capability_version_sha256
    with pytest.raises(ValueError):
        CapabilitySelection.model_validate(
            {**_selection(enabled).model_dump(), "rag_recommendation": "approved"}
        )


def test_enabled_resolution_requires_the_exact_artifact_revision_and_subject() -> None:
    definition = _definition()
    artifact = _artifact(definition)

    assert artifact.evidence_revision_sha256
    qualified = _qualified(definition)
    tampered = qualified.model_copy(
        update={
            "company_sha256": "c" * 64,
            "qualification_evidence_uri": artifact.catalog_uri,
        }
    )

    assert (
        CapabilityQualificationGate([tampered], artifacts=(artifact,))
        .resolve(
            _selection(tampered),
            company_sha256="c" * 64,
            now=NOW,
        )
        .status
        == "insufficient_evidence"
    )


def test_production_enablement_revalidates_nonproduction_evidence_and_rejects_future_times() -> (
    None
):
    sandbox = _qualified(_definition())
    production = _definition(environment="production")
    production = transition_qualification(
        production,
        QualificationState.SCHEMA_VALIDATED,
        now=NOW,
    )
    production = transition_qualification(
        production,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=_artifact(_definition(environment="production")),
        nonproduction_evidence=(sandbox,),
        nonproduction_artifacts=(_artifact(_definition()),),
        now=NOW,
    )

    revoked = transition_qualification(
        sandbox,
        QualificationState.DISABLED,
        disable_reason="reviewed_regression",
        now=NOW,
    )
    with pytest.raises(ValueError, match="^nonproduction_evidence_required$"):
        transition_qualification(
            production,
            QualificationState.ENABLED,
            evidence=_artifact(_definition(environment="production")),
            nonproduction_evidence=(revoked,),
            nonproduction_artifacts=(_artifact(_definition()),),
            canary=OwnerAuthorizedCanary(
                provider="flowaccount",
                environment="production",
                normalized_capability=production.normalized_capability,
                provider_tool_name=production.provider_tool_name,
                capability_version_sha256=production.capability_version_sha256,
                owner_authorized_by="workspace_owner",
                authorized_at=NOW + timedelta(seconds=1),
            ),
            now=NOW,
        )


def test_document_create_can_graduate_when_qualified() -> None:
    create = _definition(
        normalized_capability="documents.invoice.create",
        provider_tool_name="create_invoice",
    )

    assert _qualified(create).qualification_state is QualificationState.ENABLED


def test_provider_mcp_catalog_definition_hashes_are_deterministic() -> None:
    definition = _definition()
    payload = definition.model_dump(mode="json")
    payload.pop("capability_version_sha256")
    payload.pop("qualification_state")
    payload.pop("qualification_evidence_uri")
    payload.pop("evidence_expires_at")
    payload.pop("production_canary_at")
    payload.pop("disable_reason")

    assert (
        definition.schema_hash
        == hashlib.sha256(
            json.dumps(
                {
                    "input_schema": definition.input_schema,
                    "output_schema": definition.output_schema,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )


def test_catalog_store_loads_only_validated_provider_mcp_qualification_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = importlib.import_module("mercury_tools.db.catalog")
    definition = _qualified(_definition())
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200
        text = "[{}]"

        def json(self):
            return [definition.model_dump(mode="json")]

    def request(method: str, url: str, **kwargs: object) -> Response:
        calls.append({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr(catalog.httpx, "request", request)
    store = catalog.SupabaseCatalogStore(
        SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="test-service-role-key",
            supabase_configured=True,
        )
    )

    assert store.list_provider_mcp_qualifications() == [definition]
    assert calls[0]["method"] == "GET"
    assert str(calls[0]["url"]).endswith("/mercury_provider_capability_qualifications")


def test_catalog_store_persists_idempotent_exact_schema_changed_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = importlib.import_module("mercury_tools.db.catalog")
    enabled = _qualified(_definition()).model_copy(
        update={"id": UUID("11111111-1111-4111-8111-111111111111")}
    )
    unaffected = _qualified(
        _definition(
            normalized_capability="documents.invoice.list",
            provider_tool_name="list_invoices",
        )
    ).model_copy(update={"id": UUID("22222222-2222-4222-8222-222222222222")})
    rows = [enabled, unaffected]
    transitions: list[ProviderMCPQualification] = []
    store = catalog.SupabaseCatalogStore(
        SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="test-service-role-key",
            supabase_configured=True,
        )
    )

    def listed():
        return list(rows)

    def published(candidate, *, artifact=None):
        assert artifact is None
        transitions.append(candidate)
        rows[:] = [candidate if item.id == candidate.id else item for item in rows]
        return str(candidate.id)

    monkeypatch.setattr(store, "list_provider_mcp_qualifications", listed)
    monkeypatch.setattr(store, "publish_provider_mcp_qualification", published)

    first = store.disable_provider_mcp_capability_version(enabled)
    second = store.disable_provider_mcp_capability_version(enabled)

    assert len(transitions) == 1
    assert transitions[0].qualification_state is QualificationState.DISABLED
    assert transitions[0].disable_reason == "schema_changed"
    assert [item.qualification_state for item in first] == [
        QualificationState.DISABLED,
        QualificationState.ENABLED,
    ]
    assert second == first
