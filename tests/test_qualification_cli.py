from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury_tools.qualification.models import QualificationRunState


class FakeReport:
    def __init__(self, state: QualificationRunState) -> None:
        self.run_state = state
        self.public_calls = 0

    def public_dict(self) -> dict[str, object]:
        self.public_calls += 1
        return {
            "connector_id": "flowaccount",
            "environment": "sandbox",
            "run_id": "run_01J00000000000000000000000",
            "run_state": self.run_state.value,
            "total": 190,
            "records": [],
        }


class FakeRunner:
    def __init__(self, report: FakeReport) -> None:
        self.report = report
        self.calls: list[dict[str, object]] = []

    async def qualify_all(self, *, approval: object, dry_run: bool | None = None) -> FakeReport:
        self.calls.append({"approval": approval, "dry_run": dry_run})
        return self.report


def test_catalog_qualify_parser_exposes_exact_supported_command() -> None:
    from mercury_tools.cli import build_parser

    args = build_parser().parse_args(
        [
            "catalog",
            "qualify",
            "--connector",
            "flowaccount",
            "--env",
            "sandbox",
            "--all",
            "--sandbox-writes",
            "--dry-run",
            "--repo-root",
            "/tmp/qualification-root",
        ]
    )

    assert args.catalog_command == "qualify"
    assert args.connector == "flowaccount"
    assert args.environment == "sandbox"
    assert args.all is True
    assert args.sandbox_writes is True
    assert args.dry_run is True
    assert args.repo_root == "/tmp/qualification-root"
    assert callable(args.func)


@pytest.mark.parametrize(
    "args",
    [
        argparse.Namespace(
            connector="peak",
            environment="sandbox",
            all=True,
            sandbox_writes=False,
            dry_run=True,
            repo_root=".",
        ),
        argparse.Namespace(
            connector="flowaccount",
            environment="production",
            all=True,
            sandbox_writes=False,
            dry_run=False,
            repo_root=".",
        ),
        argparse.Namespace(
            connector="flowaccount",
            environment="sandbox",
            all=False,
            sandbox_writes=False,
            dry_run=False,
            repo_root=".",
        ),
    ],
)
def test_catalog_qualify_unsupported_scope_is_constant_json_exit_two(
    args: argparse.Namespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    monkeypatch.setattr(
        cli,
        "create_flowaccount_qualification_runner",
        lambda _root, **_kwargs: (_ for _ in ()).throw(AssertionError("runner_created")),
    )

    assert cli.cmd_catalog_qualify(args) == 2

    assert json.loads(capsys.readouterr().out) == {
        "error": "qualification_scope_not_supported",
        "status": "error",
    }


@pytest.mark.parametrize(
    ("state", "expected_exit"),
    [
        (QualificationRunState.COMPLETED, 0),
        (QualificationRunState.FAILED, 1),
        (QualificationRunState.QUARANTINED, 1),
    ],
)
def test_catalog_qualify_prints_only_public_report_and_maps_exit_state(
    state: QualificationRunState,
    expected_exit: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    report = FakeReport(state)
    runner = FakeRunner(report)
    factory_calls: list[tuple[Path, bool]] = []

    def create(root: Path, *, dry_run: bool = False) -> FakeRunner:
        factory_calls.append((root, dry_run))
        return runner

    monkeypatch.setattr(cli, "create_flowaccount_qualification_runner", create)
    args = SimpleNamespace(
        connector="flowaccount",
        environment="sandbox",
        all=True,
        sandbox_writes=True,
        dry_run=True,
        repo_root=str(tmp_path),
    )

    assert cli.cmd_catalog_qualify(args) == expected_exit

    payload = json.loads(capsys.readouterr().out)
    assert factory_calls == [(tmp_path, True)]
    assert runner.calls == [
        {
            "approval": runner.calls[0]["approval"],
            "dry_run": True,
        }
    ]
    approval = runner.calls[0]["approval"]
    assert approval.reads is True
    assert approval.writes is True
    assert approval.dry_run is True
    assert report.public_calls == 1
    assert payload == report.public_dict()
    assert report.public_calls == 2
    assert str(tmp_path) not in json.dumps(payload)
