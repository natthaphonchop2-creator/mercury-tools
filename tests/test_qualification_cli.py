from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationRunState,
    ValidationStatus,
)


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


def assert_terminal_preflight_payload(
    payload: dict[str, object],
    *,
    forbidden: tuple[str, ...] = (),
) -> None:
    assert payload.get("error") != "qualification_failed"
    assert payload["run_state"] == QualificationRunState.FAILED.value
    assert payload["total"] == 190
    records = payload["records"]
    assert isinstance(records, list)
    assert len(records) == 190
    identities = {(record["action_id"], record["version_id"]) for record in records}
    assert len(identities) == 190
    assert {record["validation_status"] for record in records} == {
        ValidationStatus.BLOCKED_MISSING_PREREQUISITE.value
    }
    assert {record["evidence_level"] for record in records} == {EvidenceLevel.DOCUMENTED.value}
    assert {record["execution_eligibility"] for record in records} == {
        ExecutionEligibility.BLOCKED.value
    }
    assert payload["http_attempts"] == 0
    assert payload["mutation_attempts"] == 0
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    for value in forbidden:
        assert value not in serialized


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


def test_catalog_qualify_catalog_failure_prints_190_terminal_records_not_generic_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    root = Path(__file__).resolve().parents[1]
    source_root = tmp_path / "source"
    catalog_target = source_root / "catalog" / "global" / "flowaccount"
    catalog_target.parent.mkdir(parents=True)
    shutil.copytree(root / "catalog" / "global" / "flowaccount", catalog_target)
    (catalog_target / "actions.json").write_text("[]\n", encoding="utf-8")
    args = SimpleNamespace(
        connector="flowaccount",
        environment="sandbox",
        all=True,
        sandbox_writes=False,
        dry_run=True,
        repo_root=str(source_root),
    )

    assert cli.cmd_catalog_qualify(args) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload.get("error") != "qualification_failed"
    assert payload["run_state"] == QualificationRunState.FAILED.value
    assert payload["total"] == 190
    assert len(payload["records"]) == 190
    assert not (source_root / ".mercury").exists()


@pytest.mark.parametrize("root_kind", ["missing", "symlink_loop"])
def test_catalog_qualify_invalid_or_missing_root_terminalizes_frozen_coverage(
    root_kind: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    repository_root = tmp_path / f"sensitive-{root_kind}-root"
    if root_kind == "symlink_loop":
        repository_root.symlink_to(repository_root)
    args = SimpleNamespace(
        connector="flowaccount",
        environment="sandbox",
        all=True,
        sandbox_writes=False,
        dry_run=True,
        repo_root=str(repository_root),
    )

    assert cli.cmd_catalog_qualify(args) == 1

    payload = json.loads(capsys.readouterr().out)
    assert_terminal_preflight_payload(
        payload,
        forbidden=(str(repository_root), "sensitive-missing-root", "sensitive-symlink_loop-root"),
    )
    assert not (tmp_path / ".mercury").exists()


def test_catalog_qualify_factory_exception_terminalizes_instead_of_generic_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    def fail_factory(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError(
            "factory-sensitive https://factory.invalid /Users/private/factory.json"
        )

    monkeypatch.setattr(cli, "create_flowaccount_qualification_runner", fail_factory)
    args = SimpleNamespace(
        connector="flowaccount",
        environment="sandbox",
        all=True,
        sandbox_writes=False,
        dry_run=True,
        repo_root=str(tmp_path),
    )

    assert cli.cmd_catalog_qualify(args) == 1

    payload = json.loads(capsys.readouterr().out)
    assert_terminal_preflight_payload(
        payload,
        forbidden=("factory-sensitive", "factory.invalid", "/Users/private", str(tmp_path)),
    )


def test_catalog_qualify_does_not_rewrite_exception_after_runner_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    class PostRunnerFailure:
        async def qualify_all(self, **_kwargs: object) -> Any:
            raise RuntimeError("post-runner-sensitive")

    monkeypatch.setattr(
        cli,
        "create_flowaccount_qualification_runner",
        lambda *_args, **_kwargs: PostRunnerFailure(),
    )
    args = SimpleNamespace(
        connector="flowaccount",
        environment="sandbox",
        all=True,
        sandbox_writes=False,
        dry_run=False,
        repo_root=str(tmp_path),
    )

    with pytest.raises(RuntimeError, match="post-runner-sensitive"):
        cli.cmd_catalog_qualify(args)

    assert capsys.readouterr().out == ""
