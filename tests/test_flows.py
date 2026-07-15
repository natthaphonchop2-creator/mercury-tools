import json
from pathlib import Path
from xml.etree import ElementTree

import httpx
import pytest

from mercury_tools.cli import main
from mercury_tools.flows import runner as flow_runner
from mercury_tools.flows.parser import FlowValidationError, parse_flow_text, validate_flow_text
from mercury_tools.flows.runner import MercuryFlowRunner
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE, FLOW_CHEAT_SHEET
from mercury_tools.flows.workspace import (
    discover_workspace_flows,
    run_workspace_flows,
    workspace_manifest,
)


def test_parse_flow_with_hooks_and_commands() -> None:
    flow = parse_flow_text(COMPANY_HEALTH_TEMPLATE)

    assert flow.name == "Company Health Check"
    assert flow.env["jurisdiction"] == "TH"
    assert flow.on_flow_start[0].name == "connectorStatus"
    assert flow.commands[0].name == "retrieveContextPack"
    assert flow.commands[-1].name == "emitReport"


def test_flow_validation_rejects_unknown_command() -> None:
    with pytest.raises(FlowValidationError, match="Unsupported command"):
        validate_flow_text(
            """
name: Bad Flow
---
- postJournal:
    amount: 100
"""
        )


def test_flow_runner_dry_run_interpolates_and_plans() -> None:
    result = MercuryFlowRunner(dry_run=True).run_text(
        COMPANY_HEALTH_TEMPLATE,
        env={"month": "2026-07"},
    )
    payload = result.as_dict()

    assert payload["status"] == "planned"
    assert len(payload["steps"]) == 4
    assert payload["steps"][0]["command"] == "connectorStatus"
    assert payload["steps"][1]["saved_as"] == "context"
    assert payload["variables"]["env"]["month"] == "2026-07"
    assert payload["variables"]["context"]["args"]["filters"]["connector"] == "flowaccount"
    assert payload["variables"]["skill"]["args"]["inputs"]["connector"] == "flowaccount"


def test_flow_runner_executes_with_injected_services() -> None:
    class FakeService:
        def context_pack(self, query, *, task, filters, max_chunks):
            class Pack:
                def as_dict(self):
                    return {"query": query, "task": task, "context": []}

            return Pack()

    runner = MercuryFlowRunner(
        rag_service_factory=lambda: FakeService(),
        connector_status_getter=lambda: {"status": "ok", "connectors": []},
        skill_runner=lambda skill_id, inputs, evidence_mode: {
            "status": "ok",
            "skill_id": skill_id,
            "inputs": inputs,
            "evidence_mode": evidence_mode,
        },
    )

    payload = runner.run_text(COMPANY_HEALTH_TEMPLATE).as_dict()

    assert payload["status"] == "ok"
    assert payload["artifacts"][0]["title"] == "Company health-check context pack"
    assert payload["variables"]["skill"]["skill_id"] == "company-health-check-th"


def test_flow_runner_routes_exact_validation_filters() -> None:
    captured = {}

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            captured["filters"] = filters
            return []

    result = MercuryFlowRunner(
        rag_service_factory=lambda: FakeService(),
    ).run_text(
        """
name: Exact Validation Search
---
- searchKnowledge:
    query: qualified evidence
    filters:
      action_id: act_1234567890abcdef12345678
      version_id: av_1111111111111111111111111111111111111111111111111111111111111111
      environment: sandbox
      capability: documents.invoice.list
      accounting_use: revenue_review
"""
    )

    assert result.status == "ok"
    filters = captured["filters"]
    assert filters.action_id == "act_1234567890abcdef12345678"
    assert filters.version_id == "av_" + "1" * 64
    assert filters.environment == "sandbox"
    assert filters.capability == "documents.invoice.list"
    assert filters.accounting_use == "revenue_review"


def test_flow_runner_rejects_unknown_search_filter_without_echo() -> None:
    calls = []

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            calls.append(filters)
            return []

    unsafe_value = "private-value-must-not-echo"
    runner = MercuryFlowRunner(rag_service_factory=lambda: FakeService())

    with pytest.raises(FlowValidationError, match="^knowledge_filters_invalid$") as raised:
        runner.run_text(
            f"""
name: Invalid Validation Search
---
- searchKnowledge:
    query: qualified evidence
    filters:
      raw_response: {unsafe_value}
"""
        )

    assert calls == []
    assert unsafe_value not in str(raised.value)


def test_flow_search_output_carries_only_approved_validation_metadata() -> None:
    from mercury_tools.rag.models import SearchResult

    metadata = {
        "jurisdiction": "TH",
        "connector": "flowaccount",
        "doc_type": "endpoint_validation",
        "review_status": "reviewed",
        "action_id": "act_1234567890abcdef12345678",
        "version_id": "av_" + "1" * 64,
        "environment": "sandbox",
        "capability": "documents.invoice.list",
        "accounting_use": ["revenue_review"],
        "validation_status": "contract_validated",
        "evidence_level": "contract_validated",
        "approval_state": "approved_public",
    }

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            return [
                SearchResult(
                    chunk_id="chunk-1",
                    document_id="document-1",
                    document_uri="mercury://wiki/validation/flowaccount/action/version/run",
                    chunk_uri=(
                        "mercury://wiki/validation/flowaccount/action/version/run#chunk-0"
                    ),
                    text="Qualified evidence",
                    score=1.0,
                    source_title="Validation",
                    source_uri="mercury://wiki/validation/flowaccount/action/version/run",
                    source_url=None,
                    source_path=None,
                    citation={},
                    metadata={**metadata, "raw_response": "private-value"},
                )
            ]

    payload = MercuryFlowRunner(
        rag_service_factory=lambda: FakeService(),
    ).run_text(
        """
name: Validation Metadata
---
- searchKnowledge:
    query: qualified evidence
    saveAs: evidence
"""
    ).as_dict()

    result = payload["variables"]["evidence"]["results"][0]
    assert result["metadata"] == metadata
    assert "private-value" not in str(payload)


def test_flow_context_pack_carries_only_safe_general_metadata() -> None:
    from mercury_tools.rag.models import ContextPack, SearchResult

    class FakeService:
        def context_pack(self, query, *, task, filters, max_chunks):
            return ContextPack(
                query=query,
                task=task,
                results=[
                    SearchResult(
                        chunk_id="chunk-1",
                        document_id="document-1",
                        document_uri="mercury://wiki/tax/vat",
                        chunk_uri="mercury://wiki/tax/vat#chunk-0",
                        text="VAT",
                        score=1.0,
                        source_title="VAT",
                        source_uri="mercury://wiki/tax/vat",
                        source_url=None,
                        source_path="/Users/operator/private/vat.md",
                        citation={
                            "heading": "VAT",
                            "provider_record_id": "provider-private-value",
                        },
                        metadata={
                            "jurisdiction": "TH",
                            "doc_type": "tax",
                            "review_status": "reviewed",
                            "provider_record_id": "provider-private-value",
                        },
                    )
                ],
            )

    payload = MercuryFlowRunner(
        rag_service_factory=lambda: FakeService(),
    ).run_text(
        """
name: Safe Context Metadata
---
- retrieveContextPack:
    query: VAT
    saveAs: evidence
"""
    ).as_dict()

    context = payload["variables"]["evidence"]["context"][0]
    assert context["metadata"] == {
        "jurisdiction": "TH",
        "doc_type": "tax",
        "review_status": "reviewed",
    }
    assert "source_path" not in context
    assert "provider-private-value" not in str(payload)


def test_flow_context_pack_rejects_partial_validation_metadata_without_echo() -> None:
    from mercury_tools.rag.models import ContextPack, SearchResult

    unsafe_value = "provider-private-value"
    validation_uri = "mercury://wiki/validation/flowaccount/action/version/run"

    class FakeService:
        def context_pack(self, query, *, task, filters, max_chunks):
            return ContextPack(
                query=query,
                task=task,
                results=[
                    SearchResult(
                        chunk_id="chunk-validation",
                        document_id="document-validation",
                        document_uri=validation_uri,
                        chunk_uri=f"{validation_uri}#chunk-0",
                        text="Unapproved validation",
                        score=1.0,
                        source_title="Validation",
                        source_uri=validation_uri,
                        source_url=None,
                        source_path=None,
                        citation={},
                        metadata={
                            "review_status": "reviewed",
                            "provider_record_id": unsafe_value,
                        },
                    )
                ],
            )

    runner = MercuryFlowRunner(rag_service_factory=lambda: FakeService())

    with pytest.raises(
        FlowValidationError,
        match="^knowledge_metadata_invalid$",
    ) as raised:
        runner.run_text(
            """
name: Invalid Context Metadata
---
- retrieveContextPack:
    query: qualified evidence
"""
        )

    assert unsafe_value not in str(raised.value)


def test_flow_runner_blocks_mutation_capability_before_connector_dispatch() -> None:
    calls: list[str] = []
    runner = MercuryFlowRunner(
        connector_status_getter=lambda: calls.append("connector") or {"status": "ok"},
    )

    payload = runner.run_text(
        """
name: Blocked Mutation
---
- connectorStatus:
    capability: documents.invoice.create
"""
    ).as_dict()

    assert payload["status"] == "blocked"
    assert payload["reason"] == "public_preview_read_only"
    assert payload["capability"] == "documents.invoice.create"
    assert payload["steps"][0]["status"] == "blocked"
    assert calls == []


def test_flow_runner_allows_declared_read_capability() -> None:
    calls: list[str] = []
    runner = MercuryFlowRunner(
        connector_status_getter=lambda: calls.append("connector") or {"status": "ok"},
    )

    payload = runner.run_text(
        """
name: Allowed Read
---
- connectorStatus:
    capability: documents.invoice.list
"""
    ).as_dict()

    assert payload["status"] == "ok"
    assert payload["steps"][0]["status"] == "ok"
    assert calls == ["connector"]


def test_flow_runner_can_disable_hosted_capability_gate() -> None:
    calls: list[str] = []
    runner = MercuryFlowRunner(
        capability_gate=None,
        connector_status_getter=lambda: calls.append("connector") or {"status": "ok"},
    )

    payload = runner.run_text(
        """
name: Local Preview Policy
---
- connectorStatus:
    capability: documents.invoice.create
"""
    ).as_dict()

    assert payload["status"] == "ok"
    assert payload["steps"][0]["status"] == "ok"
    assert calls == ["connector"]


@pytest.mark.parametrize(
    ("cloud_command", "cloud_calls"),
    [
        (
            """
- searchKnowledge:
    query: "invoice ${derived}"
    filters:
      connector: "${erp.result.reference}"
""",
            "search",
        ),
        (
            """
- retrieveContextPack:
    query: "invoice ${derived}"
    task: "${erp.result.reference}"
""",
            "context",
        ),
        (
            """
- getDocument:
    documentId: "${erp.result.reference}"
""",
            "document",
        ),
        (
            """
- runSkill:
    skillId: invoice-review-th
    inputs:
      source:
        - "${erp.result.reference}"
        - "invoice ${derived}"
""",
            "skill",
        ),
    ],
)
def test_flow_runner_blocks_erp_derived_values_before_cloud_dispatch(
    cloud_command: str,
    cloud_calls: str,
) -> None:
    calls: list[str] = []

    class FakeService:
        def search(self, *args, **kwargs):
            calls.append("search")
            return []

        def context_pack(self, *args, **kwargs):
            calls.append("context")

            class Pack:
                def as_dict(self):
                    return {"context": []}

            return Pack()

    runner = MercuryFlowRunner(
        rag_service_factory=lambda: FakeService(),
        document_getter=lambda _document_id: calls.append("document") or None,
        skill_runner=lambda *_args: calls.append("skill") or {"status": "ok"},
        erp_read_callback=lambda *_args: {
            "status": "ok",
            "result": {"reference": "erp-private-invoice-2026"},
        },
    )

    payload = runner.run_text(
        """
name: ERP Taint Boundary
---
- erpRead:
    actionId: erp.invoice.list
    saveAs: erp
- assert:
    exists: "${erp}"
    saveAs: derived
"""
        + cloud_command
    ).as_dict()

    assert payload["status"] == "blocked"
    assert payload["reason"] == "erp_to_cloud_taint"
    assert payload["steps"][-1]["status"] == "blocked"
    assert calls == []
    assert cloud_calls not in calls
    assert "erp-private-invoice-2026" not in str(payload)


@pytest.mark.parametrize(
    ("wrapper", "extra"),
    [
        ("runFlow", ""),
        ("repeat", "    times: 1\n"),
        ("retry", "    maxRetries: 0\n"),
    ],
)
def test_flow_runner_preserves_erp_taint_through_nested_flow_wrappers(
    wrapper: str,
    extra: str,
) -> None:
    calls: list[str] = []

    class FakeService:
        def search(self, *args, **kwargs):
            calls.append("search")
            return []

    runner = MercuryFlowRunner(
        rag_service_factory=lambda: FakeService(),
        erp_read_callback=lambda *_args: {
            "status": "ok",
            "result": {"reference": "erp-private-invoice-2026"},
        },
    )

    payload = runner.run_text(
        """
name: Nested ERP Taint Boundary
---
- erpRead:
    actionId: erp.invoice.list
    saveAs: erp
- """
        + wrapper
        + ":\n"
        + extra
        + """    env:
      copied: "${erp}"
    commands:
      - searchKnowledge:
          query: "${copied.result.reference}"
"""
    ).as_dict()

    assert payload["status"] == "blocked"
    assert payload["reason"] == "erp_to_cloud_taint"
    assert calls == []
    assert "erp-private-invoice-2026" not in str(payload)


def test_flow_runner_supports_when_conditions() -> None:
    result = MercuryFlowRunner(dry_run=True).run_text(
        """
name: Conditional Flow
env:
  environment: sandbox
  connector_status: ready
  optional_value: ""
---
- emitReport:
    when:
      equals:
        value: "${environment}"
        expected: production
    title: "Production {{ missing.value }}"
- emitReport:
    when:
      notEquals:
        value: "${environment}"
        expected: production
    title: "Sandbox branch"
- emitReport:
    when:
      exists: "${connector_status}"
      true: "yes"
    title: "Connector present"
- emitReport:
    when:
      notExists: "${optional_value}"
    title: "Optional value absent"
""",
    )

    payload = result.as_dict()

    assert [step["status"] for step in payload["steps"]] == [
        "skipped",
        "planned",
        "planned",
        "planned",
    ]
    assert payload["steps"][0]["output_summary"]["when"]["equals"]["value"] == "sandbox"
    assert [artifact["title"] for artifact in payload["artifacts"]] == [
        "Sandbox branch",
        "Connector present",
        "Optional value absent",
    ]


def test_flow_runner_rejects_unknown_when_condition() -> None:
    with pytest.raises(FlowValidationError, match="Unsupported when condition"):
        MercuryFlowRunner(dry_run=True).run_text(
            """
name: Bad Condition
---
- emitReport:
    when:
      visible: "not a Mercury condition"
    title: "Should not run"
"""
        )


def test_flow_runner_asserts_data_conditions() -> None:
    result = MercuryFlowRunner().run_text(
        """
name: Assert Data
env:
  connector_status: ok
  company_name: Thai Nutra sandbox
  rows:
    - invoice
    - vat
  summary:
    revenue: 104100
---
- assert:
    exists: "${company_name}"
    equals:
      value: "${connector_status}"
      expected: ok
    notEquals:
      value: "${connector_status}"
      expected: failed
    contains:
      value: "${company_name}"
      expected: Nutra
    status:
      value: "${connector_status}"
      expected: ok
    minCount:
      value: "${rows}"
      count: 2
    saveAs: checks
"""
    )

    payload = result.as_dict()

    assert payload["status"] == "ok"
    assert payload["variables"]["checks"]["status"] == "ok"
    assert payload["variables"]["checks"]["assertions"] == [
        "exists",
        "equals",
        "notEquals",
        "contains",
        "status",
        "minCount",
    ]


def test_flow_runner_assert_not_exists_and_dict_contains() -> None:
    result = MercuryFlowRunner().run_text(
        """
name: Assert Absent
env:
  optional_value: ""
  payload:
    connector: flowaccount
---
- assert:
    notExists: "${optional_value}"
    contains:
      value: "${payload}"
      expected: connector
    saveAs: checks
"""
    )

    payload = result.as_dict()

    assert payload["variables"]["checks"]["assertions"] == ["notExists", "contains"]


def test_flow_runner_assert_equals_failure_reports_values() -> None:
    with pytest.raises(FlowValidationError, match="assert equals failed"):
        MercuryFlowRunner().run_text(
            """
name: Assert Failure
---
- assert:
    equals:
      value: production
      expected: sandbox
"""
        )


def test_flow_runner_assert_status_failure() -> None:
    with pytest.raises(FlowValidationError, match="assert status failed"):
        MercuryFlowRunner().run_text(
            """
name: Status Failure
---
- assert:
    status:
      value: failed
      expected: ok
"""
        )


def test_flow_runner_assert_requires_condition() -> None:
    with pytest.raises(FlowValidationError, match="assert requires at least one assertion"):
        MercuryFlowRunner().run_text(
            """
name: Empty Assert
---
- assert:
    saveAs: empty
"""
        )


def test_flow_cli_run_json_handles_yaml_true_condition_key(tmp_path: Path, capsys) -> None:
    path = tmp_path / "condition.yaml"
    path.write_text(
        """
name: True Condition
---
- emitReport:
    when:
      true: yes
    title: "Runs"
""",
        encoding="utf-8",
    )

    assert main(["flow", "run", str(path), "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["status"] == "planned"
    assert payload["flow"]["commands"][0]["args"]["when"]["true"] is True


def test_flow_runner_supports_inline_run_flow_commands() -> None:
    result = MercuryFlowRunner(dry_run=True).run_text(
        """
name: Parent Flow
env:
  connector: flowaccount
  environment: production
---
- runFlow:
    label: Inline Review
    env:
      segment: weekly
    commands:
      - emitReport:
          title: "${connector} ${segment}"
          sections:
            - "${environment}"
      - emitReport:
          when:
            equals:
              value: "${environment}"
              expected: sandbox
          title: "Skipped {{ missing.value }}"
    saveAs: inline
- emitReport:
    title: "Parent saw {{ inline.flow.name }}"
""",
    )

    payload = result.as_dict()
    inline = payload["variables"]["inline"]

    assert payload["steps"][0]["command"] == "runFlow"
    assert payload["steps"][0]["status"] == "planned"
    assert inline["flow"]["name"] == "Inline Review"
    assert inline["steps"][0]["status"] == "planned"
    assert inline["steps"][1]["status"] == "skipped"
    assert inline["artifacts"][0]["title"] == "flowaccount weekly"
    assert payload["artifacts"][0]["title"] == "Parent saw Inline Review"


def test_flow_runner_executes_inline_run_flow_commands() -> None:
    result = MercuryFlowRunner().run_text(
        """
name: Inline Execute
---
- runFlow:
    commands:
      - assert:
          exists: true
      - emitReport:
          title: "Inline done"
    saveAs: nested
""",
    )

    payload = result.as_dict()

    assert payload["status"] == "ok"
    assert payload["variables"]["nested"]["status"] == "ok"
    assert payload["variables"]["nested"]["artifacts"][0]["title"] == "Inline done"


def test_flow_runner_repeats_inline_commands_with_iteration_context() -> None:
    result = MercuryFlowRunner(dry_run=True).run_text(
        """
name: Repeat Dry Run
---
- repeat:
    label: Monthly review
    times: 3
    commands:
      - emitReport:
          title: "Iteration ${repeat.iteration}"
          sections:
            - "index ${repeat.index}"
    saveAs: repeated
"""
    )

    payload = result.as_dict()
    repeated = payload["variables"]["repeated"]

    assert payload["status"] == "planned"
    assert payload["steps"][0]["command"] == "repeat"
    assert payload["steps"][0]["output_summary"]["iterations"] == 3
    assert repeated["iterations"] == 3
    assert repeated["max_iterations"] == 3
    assert repeated["iteration_history"][-1]["iteration"] == 3
    assert repeated["results"][0]["artifacts"][0]["title"] == "Iteration 1"
    assert repeated["results"][2]["artifacts"][0]["sections"] == ["index 2"]


def test_flow_runner_repeat_while_condition_stops_iterations() -> None:
    result = MercuryFlowRunner(dry_run=True).run_text(
        """
name: Repeat While
---
- repeat:
    times: 5
    while:
      notEquals:
        value: "${repeat.index}"
        expected: 2
    commands:
      - emitReport:
          title: "Loop ${repeat.iteration}"
    saveAs: repeated
"""
    )

    payload = result.as_dict()
    repeated = payload["variables"]["repeated"]

    assert repeated["iterations"] == 2
    assert repeated["stopped_reason"] == "while condition evaluated false"
    assert repeated["while"]["notEquals"]["value"] == 2
    assert [item["iteration"] for item in repeated["iteration_history"]] == [1, 2]


def test_flow_runner_rejects_repeat_without_bound_or_condition() -> None:
    with pytest.raises(FlowValidationError, match="repeat requires times or while"):
        MercuryFlowRunner(dry_run=True).run_text(
            """
name: Bad Repeat
---
- repeat:
    commands:
      - emitReport:
          title: "bad"
"""
        )


def test_flow_runner_rejects_repeat_too_many_times() -> None:
    with pytest.raises(FlowValidationError, match="repeat times must be between 0 and 100"):
        MercuryFlowRunner(dry_run=True).run_text(
            """
name: Bad Repeat Count
---
- repeat:
    times: 101
    commands:
      - emitReport:
          title: "bad"
"""
        )


def test_flow_runner_retries_inline_commands_until_success() -> None:
    calls = {"count": 0}

    def flaky_skill(skill_id, inputs, evidence_mode):
        calls["count"] += 1
        if calls["count"] == 1:
            raise FlowValidationError("temporary connector failure")
        return {
            "status": "ok",
            "skill_id": skill_id,
            "inputs": inputs,
            "evidence_mode": evidence_mode,
        }

    result = MercuryFlowRunner(skill_runner=flaky_skill).run_text(
        """
name: Retry Success
---
- retry:
    label: Retry Skill
    maxRetries: 2
    commands:
      - runSkill:
          skillId: invoice-review-th
          inputs:
            month: "2026-07"
          evidenceMode: true
          saveAs: skill
    saveAs: retryResult
"""
    )

    payload = result.as_dict()
    retry_result = payload["variables"]["retryResult"]

    assert calls["count"] == 2
    assert retry_result["status"] == "ok"
    assert retry_result["attempts"] == 2
    assert retry_result["max_retries"] == 2
    assert retry_result["attempt_history"][0]["message"] == "temporary connector failure"
    assert retry_result["result"]["variables"]["skill"]["skill_id"] == "invoice-review-th"
    assert payload["steps"][0]["output_summary"]["attempts"] == 2


def test_flow_runner_retry_fails_after_exhaustion() -> None:
    def failing_skill(skill_id, inputs, evidence_mode):
        raise FlowValidationError("still unavailable")

    with pytest.raises(FlowValidationError, match="retry failed after 2 attempt"):
        MercuryFlowRunner(skill_runner=failing_skill).run_text(
            """
name: Retry Failure
---
- retry:
    maxRetries: 1
    commands:
      - runSkill:
          skillId: connector-setup-guide-th
"""
        )


def test_flow_runner_retry_dry_run_plans_child_flow_once() -> None:
    result = MercuryFlowRunner(dry_run=True).run_text(
        """
name: Retry Dry Run
---
- retry:
    label: Planned Retry
    maxRetries: 3
    commands:
      - emitReport:
          title: "Planned child report"
    saveAs: retryPlan
"""
    )

    payload = result.as_dict()
    retry_plan = payload["variables"]["retryPlan"]

    assert payload["status"] == "planned"
    assert payload["steps"][0]["command"] == "retry"
    assert payload["steps"][0]["status"] == "planned"
    assert retry_plan["attempts"] == 1
    assert retry_plan["max_retries"] == 3
    assert retry_plan["result"]["status"] == "planned"
    assert retry_plan["result"]["artifacts"][0]["title"] == "Planned child report"


def test_flow_runner_rejects_retry_too_many_retries() -> None:
    with pytest.raises(FlowValidationError, match="retry maxRetries must be between 0 and 3"):
        MercuryFlowRunner(dry_run=True).run_text(
            """
name: Bad Retry
---
- retry:
    maxRetries: 4
    commands:
      - emitReport:
          title: "bad"
"""
        )


def test_flow_runner_rejects_run_flow_with_file_and_commands() -> None:
    with pytest.raises(FlowValidationError, match="either file/path or commands"):
        MercuryFlowRunner(dry_run=True).run_text(
            """
name: Bad Inline
---
- runFlow:
    file: child.yaml
    commands:
      - emitReport:
          title: "bad"
"""
        )


def test_flow_parser_supports_erp_commands_and_snake_case_aliases() -> None:
    flow = parse_flow_text(
        """
name: ERP command aliases
---
- erp_read:
    action_id: erp.invoice.list
    inputs:
      query:
        page: 1
- erp_write_preview:
    action_id: erp.expense.create
    inputs:
      body:
        reference: EXP-001
"""
    )

    assert [command.name for command in flow.commands] == ["erpRead", "erpWritePreview"]
    assert "erpRead" in FLOW_CHEAT_SHEET
    assert "erpWritePreview" in FLOW_CHEAT_SHEET


def test_flow_runner_calls_injected_erp_read_callback() -> None:
    calls: list[tuple[str, dict[str, object], str]] = []

    def run_read(action_id: str, inputs: dict[str, object], environment: str) -> dict[str, object]:
        calls.append((action_id, inputs, environment))
        return {"status": "ok", "results": [{"invoice_number": "INV-001"}]}

    result = MercuryFlowRunner(erp_read_callback=run_read).run_text(
        """
name: ERP read
env:
  environment: sandbox
---
- erpRead:
    actionId: erp.invoice.list
    inputs:
      query:
        page: 1
    saveAs: invoices
"""
    )

    assert result.status == "ok"
    assert calls == [("erp.invoice.list", {"query": {"page": 1}}, "sandbox")]
    assert result.variables["invoices"]["results"][0]["invoice_number"] == "INV-001"


def test_flow_write_preview_is_terminal_and_returns_only_public_summary() -> None:
    calls: list[tuple[str, dict[str, object], str]] = []

    def preview_write(
        action_id: str, inputs: dict[str, object], environment: str
    ) -> dict[str, object]:
        calls.append((action_id, inputs, environment))
        return {
            "request_id": "req_expense_001",
            "payload_hash": "a" * 64,
            "request_inputs": {"body": {"api_key": "must-not-leak"}},
            "sanitized_summary": {"reference": "EXP-001"},
        }

    result = MercuryFlowRunner(erp_write_preview_callback=preview_write).run_text(
        """
name: ERP write preview
env:
  environment: production
---
- erpWritePreview:
    actionId: erp.expense.create
    inputs:
      body:
        reference: EXP-001
    saveAs: preview
- emitReport:
    title: This must not run
"""
    )

    assert result.status == "confirmation_required"
    assert calls == [
        ("erp.expense.create", {"body": {"reference": "EXP-001"}}, "production")
    ]
    assert len(result.steps) == 1
    assert result.steps[0].status == "confirmation_required"
    assert result.steps[0].output_summary == {
        "status": "confirmation_required",
        "request_id": "req_expense_001",
        "payload_hash": "a" * 64,
    }
    assert result.variables["preview"] == result.steps[0].output_summary
    assert result.artifacts == []


def test_nested_flow_preview_propagates_the_public_confirmation_summary() -> None:
    runner = MercuryFlowRunner(
        erp_write_preview_callback=lambda *_: {
            "request_id": "req_nested_001",
            "payload_hash": "b" * 64,
            "request_inputs": {"body": {"secret": "must-not-leak"}},
        }
    )

    result = runner.run_text(
        """
name: Nested ERP write preview
---
- runFlow:
    commands:
      - erpWritePreview:
          actionId: erp.expense.create
          inputs:
            body:
              reference: EXP-NESTED-001
- emitReport:
    title: This must not run
"""
    )

    assert result.status == "confirmation_required"
    assert len(result.steps) == 1
    assert result.steps[0].command == "runFlow"
    assert result.steps[0].output_summary == {
        "status": "confirmation_required",
        "request_id": "req_nested_001",
        "payload_hash": "b" * 64,
    }


def test_flow_runner_rejects_write_preview_in_recursive_retry_before_callback(
    tmp_path: Path,
) -> None:
    (tmp_path / "retry.yaml").write_text(
        """
name: Retry child
---
- runFlow:
    file: repeat.yaml
""",
        encoding="utf-8",
    )
    (tmp_path / "repeat.yaml").write_text(
        """
name: Repeat child
---
- repeat:
    times: 1
    commands:
      - runFlow:
          file: preview.yaml
""",
        encoding="utf-8",
    )
    (tmp_path / "preview.yaml").write_text(
        """
name: Preview child
---
- erpWritePreview:
    actionId: erp.expense.create
    inputs:
      body:
        reference: EXP-RETRY-001
""",
        encoding="utf-8",
    )
    main = tmp_path / "main.yaml"
    main.write_text(
        """
name: Main retry
---
- retry:
    maxRetries: 2
    file: retry.yaml
""",
        encoding="utf-8",
    )
    calls: list[object] = []
    resolved_paths: list[str] = []
    repository_resolver = flow_runner.repository_flow_path_resolver(tmp_path)

    def resolve_nested_flow(base_dir: Path, raw_path: str) -> Path:
        resolved_paths.append(raw_path)
        return repository_resolver(base_dir, raw_path)

    runner = MercuryFlowRunner(
        erp_write_preview_callback=lambda *_: calls.append("preview") or {},
        flow_path_resolver=resolve_nested_flow,
    )

    with pytest.raises(FlowValidationError, match="erpWritePreview cannot run inside retry"):
        runner.run_path(main)

    assert calls == []
    assert resolved_paths == ["retry.yaml", "repeat.yaml", "preview.yaml"]


def test_flow_runner_resolver_rejects_nested_traversal_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("name: Outside\ncommands: []\n", encoding="utf-8")
    traversal = root / "traversal.yaml"
    traversal.write_text(
        """
name: Traversal
---
- runFlow:
    file: ../outside.yaml
""",
        encoding="utf-8",
    )
    symlink = root / "escape.yaml"
    symlink.symlink_to(outside)
    symlink_parent = root / "symlink.yaml"
    symlink_parent.write_text(
        """
name: Symlink
---
- runFlow:
    file: escape.yaml
""",
        encoding="utf-8",
    )
    runner = MercuryFlowRunner(
        flow_path_resolver=flow_runner.repository_flow_path_resolver(root)
    )

    with pytest.raises(FlowValidationError, match="path traversal"):
        runner.run_path(traversal)
    with pytest.raises(FlowValidationError, match="outside repository root"):
        runner.run_path(symlink_parent)


def test_repository_flow_loader_rejects_symlink_swap_before_top_level_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader_factory = getattr(flow_runner, "repository_flow_loader", None)
    assert callable(loader_factory), "local repository flow loader is required"

    root = tmp_path / "repository"
    root.mkdir()
    main = root / "main.yaml"
    main.write_text(
        'name: Main\n---\n- emitReport:\n    title: "Main"\n',
        encoding="utf-8",
    )
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        'name: Outside\n---\n- emitReport:\n    title: "Outside"\n',
        encoding="utf-8",
    )
    loader = loader_factory(root)
    original_open = flow_runner.os.open
    swapped = False

    def race_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == "main.yaml" and dir_fd is not None:
            main.unlink()
            main.symlink_to(outside)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(flow_runner.os, "open", race_open)

    with pytest.raises(FlowValidationError, match="flow_path_invalid"):
        loader.load_path("main.yaml")

    assert swapped is True


def test_repository_flow_loader_rejects_symlinked_child_directory_before_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader_factory = getattr(flow_runner, "repository_flow_loader", None)
    assert callable(loader_factory), "local repository flow loader is required"

    root = tmp_path / "repository"
    children = root / "children"
    root.mkdir()
    children.mkdir()
    (root / "main.yaml").write_text(
        'name: Main\n---\n- runFlow:\n    file: "children/child.yaml"\n',
        encoding="utf-8",
    )
    (children / "child.yaml").write_text(
        'name: Child\n---\n- emitReport:\n    title: "Child"\n',
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "child.yaml").write_text(
        """name: Outside
---
- erpRead:
    actionId: erp.invoice.list
""",
        encoding="utf-8",
    )
    loader = loader_factory(root)
    parent = loader.load_path("main.yaml")
    calls: list[str] = []
    runner = MercuryFlowRunner(
        flow_loader=loader,
        erp_read_callback=lambda *_args: calls.append("erp") or {"status": "ok"},
    )
    original_open = flow_runner.os.open
    swapped = False

    def race_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == "children" and dir_fd is not None:
            (children / "child.yaml").unlink()
            children.rmdir()
            children.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(flow_runner.os, "open", race_open)

    with pytest.raises(FlowValidationError, match="flow_path_invalid"):
        runner.run_flow(parent)

    assert swapped is True
    assert calls == []


@pytest.mark.parametrize(
    "base_factory",
    [
        lambda root, outside: root / "trusted" / "..",
        lambda root, outside: root / "trusted" / ".." / "trusted",
        lambda root, outside: root / ".." / outside.name,
        lambda root, outside: outside,
        lambda root, outside: root / "alias",
    ],
)
def test_repository_flow_loader_rejects_untrusted_base_dir_before_openat(
    tmp_path: Path,
    base_factory,
) -> None:
    root = tmp_path / "repository"
    trusted = root / "trusted"
    outside = tmp_path / "outside"
    root.mkdir()
    trusted.mkdir()
    outside.mkdir()
    (trusted / "child.yaml").write_text(
        'name: Child\n---\n- emitReport:\n    title: "Child"\n',
        encoding="utf-8",
    )
    (root / "child.yaml").write_text(
        'name: Root Child\n---\n- emitReport:\n    title: "Root Child"\n',
        encoding="utf-8",
    )
    (outside / "child.yaml").write_text(
        'name: Outside\n---\n- emitReport:\n    title: "Outside"\n',
        encoding="utf-8",
    )
    (root / "alias").symlink_to(trusted, target_is_directory=True)
    loader = flow_runner.repository_flow_loader(root)

    with pytest.raises(FlowValidationError, match="flow_path_invalid"):
        loader(base_factory(root, outside), "child.yaml")


@pytest.mark.parametrize(
    ("wrapper", "wrapper_options"),
    [
        ("runFlow", ""),
        ("repeat", "    times: 1\n"),
        ("retry", "    maxRetries: 0\n"),
    ],
)
@pytest.mark.parametrize(
    ("label", "outside_parent"),
    [
        ("../pivot", ".."),
        ("../pivot/", "../pivot"),
        ("nested/../../pivot", ".."),
    ],
)
def test_inline_flow_labels_cannot_escape_loader_or_dispatch_outside_callbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrapper: str,
    wrapper_options: str,
    label: str,
    outside_parent: str,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "outside.yaml").write_text(
        'name: Safe Child\n---\n- emitReport:\n    title: "Safe child"\n',
        encoding="utf-8",
    )
    main = root / "main.yaml"
    main.write_text(
        f"""name: Main
---
- {wrapper}:
{wrapper_options}    label: {label}
    commands:
      - runFlow:
          file: outside.yaml
""",
        encoding="utf-8",
    )
    outside = root / outside_parent / "outside.yaml"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside_marker = "Outside YAML must never be parsed"
    outside.write_text(
        f"""name: {outside_marker}
---
- erpRead:
    actionId: erp.outside.dispatch
- searchKnowledge:
    query: outside-cloud-dispatch
""",
        encoding="utf-8",
    )
    loader = flow_runner.repository_flow_loader(root)
    parent = loader.load_path("main.yaml")
    parsed_text: list[str] = []
    original_parse = flow_runner.parse_flow_text

    def parse_spy(text: str, *, path: Path | None = None):
        parsed_text.append(text)
        return original_parse(text, path=path)

    monkeypatch.setattr(flow_runner, "parse_flow_text", parse_spy)
    erp_calls: list[str] = []
    cloud_calls: list[str] = []

    class FakeService:
        def search(self, query, **_kwargs):
            cloud_calls.append(query)
            return []

    runner = MercuryFlowRunner(
        flow_loader=loader,
        rag_service_factory=lambda: FakeService(),
        erp_read_callback=lambda action_id, *_args: erp_calls.append(action_id) or {"status": "ok"},
        capability_gate=None,
    )

    result = runner.run_flow(parent)

    assert result.status == "ok"
    assert erp_calls == []
    assert cloud_calls == []
    assert all(outside_marker not in text for text in parsed_text)


def test_inline_flow_label_preserves_parent_taint_without_outside_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "main.yaml").write_text(
        """name: Main
---
- erpRead:
    actionId: erp.parent.read
    saveAs: parent_erp
- runFlow:
    label: ../pivot
    env:
      child_erp: "${parent_erp}"
    commands:
      - runFlow:
          file: child.yaml
""",
        encoding="utf-8",
    )
    (root / "child.yaml").write_text(
        """name: Child
---
- searchKnowledge:
    query: "${child_erp.reference}"
""",
        encoding="utf-8",
    )
    loader = flow_runner.repository_flow_loader(root)
    erp_calls: list[str] = []
    cloud_calls: list[str] = []

    class FakeService:
        def search(self, query, **_kwargs):
            cloud_calls.append(query)
            return []

    result = MercuryFlowRunner(
        flow_loader=loader,
        rag_service_factory=lambda: FakeService(),
        erp_read_callback=lambda action_id, *_args: erp_calls.append(action_id)
        or {"reference": "parent-private-reference"},
        capability_gate=None,
    ).run_flow(loader.load_path("main.yaml"))

    assert result.status == "blocked"
    assert result.reason == "erp_to_cloud_taint"
    assert erp_calls == ["erp.parent.read"]
    assert cloud_calls == []
    assert "parent-private-reference" not in str(result.as_dict())


def test_repository_flow_loader_rejects_symlink_swap_before_list_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    main = root / "main.yaml"
    main.write_text(
        'name: Main\n---\n- emitReport:\n    title: "Main"\n',
        encoding="utf-8",
    )
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        'name: Outside\n---\n- emitReport:\n    title: "Outside"\n',
        encoding="utf-8",
    )
    loader = flow_runner.repository_flow_loader(root)
    original_open = flow_runner.os.open
    swapped = False

    def race_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == "main.yaml" and dir_fd is not None:
            main.unlink()
            main.symlink_to(outside)
            swapped = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(flow_runner.os, "open", race_open)

    with pytest.raises(FlowValidationError, match="flow_path_invalid"):
        loader.list_flows()

    assert swapped is True


@pytest.mark.parametrize(
    "path",
    ["", ".", "../outside.yaml", "flows/../main.yaml", "flows//main.yaml"],
)
def test_repository_flow_loader_rejects_invalid_components(tmp_path: Path, path: str) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    loader = flow_runner.repository_flow_loader(root)

    with pytest.raises(FlowValidationError, match="flow_path_invalid"):
        loader.load_path(path)


def test_flow_cli_validate_and_dry_run(tmp_path: Path, capsys) -> None:
    path = tmp_path / "company-health.yaml"
    path.write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    assert main(["flow", "validate", str(path)]) == 0
    assert "Flow valid: Company Health Check" in capsys.readouterr().out

    assert main(["flow", "run", str(path), "--dry-run"]) == 0
    assert "Flow planned: Company Health Check" in capsys.readouterr().out


def test_flow_cli_run_accepts_env_overrides(tmp_path: Path, capsys) -> None:
    path = tmp_path / "param-flow.yaml"
    path.write_text(
        """
name: Param Flow
env:
  month: "2026-01"
  connector: flowaccount
---
- emitReport:
    title: "Report ${month}"
    sections:
      - "Connector ${connector}"
      - "Scope ${scope}"
""",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "flow",
                "run",
                str(path),
                "--dry-run",
                "-e",
                "month=2026-09",
                "--env",
                "scope=weekly",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["variables"]["env"]["month"] == "2026-09"
    assert payload["variables"]["env"]["scope"] == "weekly"
    assert payload["artifacts"][0]["title"] == "Report 2026-09"
    assert payload["artifacts"][0]["sections"] == [
        "Connector flowaccount",
        "Scope weekly",
    ]


def test_flow_cli_run_rejects_invalid_env_override(tmp_path: Path, capsys) -> None:
    path = tmp_path / "company-health.yaml"
    path.write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    assert main(["flow", "run", str(path), "--dry-run", "-e", "month"]) == 1

    assert "Environment override must be KEY=value" in capsys.readouterr().out


def test_flow_cli_init_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "flow.yaml"

    assert main(["flow", "init", str(path), "--template", "vat-summary"]) == 0
    assert path.exists()
    assert main(["flow", "init", str(path), "--template", "vat-summary"]) == 1


def test_flow_cli_init_workspace_creates_runnable_suite(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"

    assert main(["flow", "init-workspace", str(workspace), "--month", "2026-08"]) == 0

    output = capsys.readouterr().out
    assert "Created Mercury flow workspace" in output
    assert (workspace / "config.yaml").exists()
    assert (workspace / "flows" / "company-health.yaml").exists()
    assert (workspace / "flows" / "vat-summary.yaml").exists()
    assert (workspace / "README.md").exists()
    assert 'month: "2026-08"' in (workspace / "config.yaml").read_text(encoding="utf-8")

    assert main(["flow", "list", str(workspace)]) == 0
    assert "2 discovered, 2 selected" in capsys.readouterr().out

    assert main(["flow", "run-suite", str(workspace), "--dry-run"]) == 0
    suite_output = capsys.readouterr().out
    assert "Flow suite planned: 2 selected / 2 discovered" in suite_output
    assert "report:" in suite_output
    report_path = workspace / ".mercury" / "reports" / "suite-report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "planned"
    assert report["report_path"] == str(report_path)
    assert report["workspace"]["execution_order"] == [
        "flows/company-health.yaml",
        "flows/vat-summary.yaml",
    ]

    assert main(["flow", "watch", str(workspace), "--dry-run", "--max-runs", "1"]) == 0
    assert "Run 1:" in capsys.readouterr().out


def test_flow_cli_init_workspace_refuses_overwrite(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "workspace"

    assert main(["flow", "init-workspace", str(workspace)]) == 0
    assert main(["flow", "init-workspace", str(workspace)]) == 1

    assert "Workspace init failed" in capsys.readouterr().out


def test_flow_workspace_discovers_and_filters_by_config(tmp_path: Path) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows:
  - flows/**/*.yaml
includeTags: [smoke]
excludeTags: [disabled]
env:
  month: "2026-07"
execution:
  sequential: true
""",
        encoding="utf-8",
    )
    (flows / "company.yaml").write_text(
        COMPANY_HEALTH_TEMPLATE.replace(
            "tags: [accounting, endpoint-capable, flowaccount]",
            "tags: [accounting, endpoint-capable, flowaccount, smoke]",
        ),
        encoding="utf-8",
    )
    (flows / "disabled.yaml").write_text(
        COMPANY_HEALTH_TEMPLATE.replace(
            "name: Company Health Check",
            "name: Disabled Check",
        ).replace(
            "tags: [accounting, endpoint-capable, flowaccount]",
            "tags: [accounting, disabled]",
        ),
        encoding="utf-8",
    )

    workspace = discover_workspace_flows(tmp_path)

    assert workspace.config.config_path == tmp_path / "config.yaml"
    assert workspace.config.env["month"] == "2026-07"
    assert len(workspace.records) == 2
    assert [record.name for record in workspace.selected] == ["Company Health Check"]


def test_flow_workspace_manifest_is_agent_facing_without_env_values(tmp_path: Path) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
includeTags: [accounting]
excludeTags: [disabled]
env:
  month: "2026-07"
  client_secret: "do-not-return"
executionOrder:
  flowsOrder:
    - company
""",
        encoding="utf-8",
    )
    (flows / "company.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    manifest = workspace_manifest(discover_workspace_flows(tmp_path))

    assert manifest["surface"] == "mcp-cli"
    assert manifest["runtime_boundary"]["primary_runtime"] == "MCP tools and CLI"
    assert manifest["discovery"]["selected_count"] == 1
    assert manifest["workspace"]["env_keys"] == ["client_secret", "month"]
    assert "do-not-return" not in json.dumps(manifest)
    assert "inspect_flow_files" in manifest["agent_handoff"]["mcp_tools"]


def test_flow_cli_manifest_outputs_agent_handoff(tmp_path: Path, capsys) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        "flows: flows/**/*.yaml\nincludeTags: [accounting]\n",
        encoding="utf-8",
    )
    (flows / "company.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    assert main(["flow", "manifest", str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["surface"] == "mcp-cli"
    assert payload["discovery"]["tags"] == [
        "accounting",
        "endpoint-capable",
        "flowaccount",
    ]
    assert payload["agent_handoff"]["cli_examples"][1].startswith("mercury-tools flow manifest")


def test_flow_workspace_run_suite_dry_run(tmp_path: Path) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "mercury.yaml").write_text(
        """
flows: flows/**/*.yaml
env:
  month: "2026-07"
""",
        encoding="utf-8",
    )
    (flows / "company.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    suite = run_workspace_flows(tmp_path, dry_run=True)
    payload = suite.as_dict()

    assert payload["status"] == "planned"
    assert payload["workspace"]["selected_count"] == 1
    assert payload["results"][0]["variables"]["env"]["month"] == "2026-07"


def test_flow_cli_run_suite_env_overrides_workspace_config(tmp_path: Path, capsys) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
env:
  month: "2026-07"
  connector: flowaccount
""",
        encoding="utf-8",
    )
    (flows / "monthly.yaml").write_text(
        """
name: Monthly Report
---
- emitReport:
    title: "Month ${month}"
    metadata:
      connector: "${connector}"
""",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "flow",
                "run-suite",
                str(tmp_path),
                "--dry-run",
                "-e",
                "month=2026-10",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["workspace"]["config"]["env"]["month"] == "2026-10"
    assert payload["results"][0]["variables"]["env"]["month"] == "2026-10"
    assert payload["results"][0]["artifacts"][0]["title"] == "Month 2026-10"


def test_flow_workspace_respects_execution_order(tmp_path: Path) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
includeTags: [accounting]
env:
  month: "2026-07"
executionOrder:
  flowsOrder:
    - vat-summary
    - company-health
""",
        encoding="utf-8",
    )
    (flows / "company-health.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")
    (flows / "vat-summary.yaml").write_text(
        COMPANY_HEALTH_TEMPLATE.replace("name: Company Health Check", "name: VAT Summary"),
        encoding="utf-8",
    )

    suite = run_workspace_flows(tmp_path, dry_run=True)
    payload = suite.as_dict()

    assert payload["workspace"]["execution_order"] == [
        "flows/vat-summary.yaml",
        "flows/company-health.yaml",
    ]
    assert [result["flow"]["name"] for result in payload["results"]] == [
        "VAT Summary",
        "Company Health Check",
    ]


def test_flow_workspace_continue_on_failure_records_error(tmp_path: Path) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
includeTags: [accounting]
executionOrder:
  continueOnFailure: true
  flowsOrder:
    - bad
    - good
""",
        encoding="utf-8",
    )
    (flows / "bad.yaml").write_text(
        """
name: Bad
tags: [accounting]
---
- assert:
    exists: false
""",
        encoding="utf-8",
    )
    (flows / "good.yaml").write_text(
        """
name: Good
tags: [accounting]
---
- emitReport:
    title: "Good report"
""",
        encoding="utf-8",
    )

    suite = run_workspace_flows(tmp_path, runner=MercuryFlowRunner())
    payload = suite.as_dict()

    assert payload["status"] == "failed"
    assert [result["status"] for result in payload["results"]] == ["error", "ok"]


def test_flow_workspace_continue_on_failure_false_raises(tmp_path: Path) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
includeTags: [accounting]
executionOrder:
  continueOnFailure: false
""",
        encoding="utf-8",
    )
    (flows / "bad.yaml").write_text(
        """
name: Bad
tags: [accounting]
---
- assert:
    exists: false
""",
        encoding="utf-8",
    )

    with pytest.raises(FlowValidationError, match="assert exists failed"):
        run_workspace_flows(tmp_path, runner=MercuryFlowRunner())


def test_flow_cli_run_suite_writes_junit_and_fails_ci_on_failed_suite(
    tmp_path: Path,
    capsys,
) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
includeTags: [accounting]
executionOrder:
  continueOnFailure: true
  flowsOrder:
    - bad
    - good
""",
        encoding="utf-8",
    )
    (flows / "bad.yaml").write_text(
        """
name: Bad
tags: [accounting]
---
- assert:
    exists: false
""",
        encoding="utf-8",
    )
    (flows / "good.yaml").write_text(
        """
name: Good
tags: [accounting]
---
- emitReport:
    title: "Good report"
""",
        encoding="utf-8",
    )
    output = tmp_path / "junit.xml"

    assert (
        main(
            [
                "flow",
                "run-suite",
                str(tmp_path),
                "--format",
                "junit",
                "--output",
                str(output),
            ]
        )
        == 1
    )

    cli_output = capsys.readouterr().out
    assert "Flow suite failed" in cli_output
    assert f"junit: {output.resolve()}" in cli_output

    root = ElementTree.parse(output).getroot()
    assert root.tag == "testsuite"
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "1"
    assert root.find("./testcase/failure") is not None


def test_flow_cli_run_suite_writes_html_report(tmp_path: Path, capsys) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
includeTags: [accounting]
""",
        encoding="utf-8",
    )
    (flows / "good.yaml").write_text(
        """
name: Good HTML
tags: [accounting]
---
- emitReport:
    title: "Good HTML report"
    sections:
      - "Readable handoff artifact"
""",
        encoding="utf-8",
    )
    output = tmp_path / "report.html"

    assert (
        main(
            [
                "flow",
                "run-suite",
                str(tmp_path),
                "--format",
                "html",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    cli_output = capsys.readouterr().out
    html = output.read_text(encoding="utf-8")
    assert f"html: {output.resolve()}" in cli_output
    assert "Mercury Flow Suite Report" in html
    assert "Good HTML" in html
    assert "Good HTML report" in html
    assert "Status" in html


def test_flow_cli_run_suite_allow_failures_keeps_zero_exit(tmp_path: Path) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        """
flows: flows/**/*.yaml
includeTags: [accounting]
executionOrder:
  continueOnFailure: true
""",
        encoding="utf-8",
    )
    (flows / "bad.yaml").write_text(
        """
name: Bad
tags: [accounting]
---
- assert:
    exists: false
""",
        encoding="utf-8",
    )

    assert main(["flow", "run-suite", str(tmp_path), "--allow-failures"]) == 0


def test_flow_cli_lists_and_runs_suite(tmp_path: Path, capsys) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text("flows: flows/**/*.yaml\n", encoding="utf-8")
    (flows / "company.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    assert main(["flow", "list", str(tmp_path)]) == 0
    assert "1 discovered, 1 selected" in capsys.readouterr().out

    assert main(["flow", "run-suite", str(tmp_path), "--dry-run"]) == 0
    assert "Flow suite planned: 1 selected / 1 discovered" in capsys.readouterr().out


def test_flow_cli_push_dry_run_uses_workspace_selection(tmp_path: Path, capsys) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text(
        "flows: flows/**/*.yaml\nincludeTags: [accounting]\n",
        encoding="utf-8",
    )
    (flows / "company.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    assert main(["flow", "push", str(tmp_path), "--dry-run"]) == 0

    assert "Flow push planned: 1 flows" in capsys.readouterr().out


def test_flow_cli_push_posts_import_payload(tmp_path: Path, monkeypatch, capsys) -> None:
    flows = tmp_path / "flows"
    flows.mkdir()
    (tmp_path / "config.yaml").write_text("flows: flows/**/*.yaml\n", encoding="utf-8")
    (flows / "company.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    captured: dict = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "imported_count": 1,
                "flows": [{"flow_id": "flow-1", "title": "Company Health Check"}],
            },
        )

    monkeypatch.setattr("mercury_tools.cli.httpx.post", fake_post)

    assert (
        main(
            [
                "flow",
                "push",
                str(tmp_path),
                "--url",
                "https://mercury.example.com",
                "--client-token",
                "mc_" + "a" * 24 + "." + "b" * 24,
            ]
        )
        == 0
    )

    assert captured["url"] == "https://mercury.example.com/api/flows/import"
    assert captured["headers"]["Authorization"].startswith("Bearer mc_")
    assert captured["json"]["flows"][0]["metadata"]["relative_path"] == "flows/company.yaml"
    assert "Flow push ok: 1 flows" in capsys.readouterr().out


def test_flow_workspace_run_suite_rejects_invalid_selected_flow(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(
        """
name: Bad
---
- unknownCommand
""",
        encoding="utf-8",
    )

    with pytest.raises(FlowValidationError, match="Invalid workspace flow"):
        run_workspace_flows(tmp_path, dry_run=True)


def test_flow_workspace_run_suite_reports_empty_selection(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "flows: '*.yaml'\nincludeTags: [missing]\n",
        encoding="utf-8",
    )
    (tmp_path / "company.yaml").write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    suite = run_workspace_flows(tmp_path, dry_run=True)

    assert suite.as_dict()["status"] == "empty"
    assert suite.as_dict()["workspace"]["selected_count"] == 0
