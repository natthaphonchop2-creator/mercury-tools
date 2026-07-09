from pathlib import Path

import pytest

from mercury_tools.cli import main
from mercury_tools.flows.parser import FlowValidationError, parse_flow_text, validate_flow_text
from mercury_tools.flows.runner import MercuryFlowRunner
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE


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


def test_flow_cli_validate_and_dry_run(tmp_path: Path, capsys) -> None:
    path = tmp_path / "company-health.yaml"
    path.write_text(COMPANY_HEALTH_TEMPLATE, encoding="utf-8")

    assert main(["flow", "validate", str(path)]) == 0
    assert "Flow valid: Company Health Check" in capsys.readouterr().out

    assert main(["flow", "run", str(path), "--dry-run"]) == 0
    assert "Flow planned: Company Health Check" in capsys.readouterr().out


def test_flow_cli_init_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "flow.yaml"

    assert main(["flow", "init", str(path), "--template", "vat-summary"]) == 0
    assert path.exists()
    assert main(["flow", "init", str(path), "--template", "vat-summary"]) == 1
