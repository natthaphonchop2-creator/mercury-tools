import json
from pathlib import Path

import httpx
import pytest

from mercury_tools.cli import main
from mercury_tools.flows.parser import FlowValidationError, parse_flow_text, validate_flow_text
from mercury_tools.flows.runner import MercuryFlowRunner
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
from mercury_tools.flows.workspace import discover_workspace_flows, run_workspace_flows


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
            "tags: [accounting, read-only, flowaccount]",
            "tags: [accounting, read-only, flowaccount, smoke]",
        ),
        encoding="utf-8",
    )
    (flows / "disabled.yaml").write_text(
        COMPANY_HEALTH_TEMPLATE.replace(
            "name: Company Health Check",
            "name: Disabled Check",
        ).replace(
            "tags: [accounting, read-only, flowaccount]",
            "tags: [accounting, disabled]",
        ),
        encoding="utf-8",
    )

    workspace = discover_workspace_flows(tmp_path)

    assert workspace.config.config_path == tmp_path / "config.yaml"
    assert workspace.config.env["month"] == "2026-07"
    assert len(workspace.records) == 2
    assert [record.name for record in workspace.selected] == ["Company Health Check"]


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
