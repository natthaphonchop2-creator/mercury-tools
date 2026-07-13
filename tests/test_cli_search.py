from __future__ import annotations

import argparse
import builtins
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_cli_search_applies_connector_inference(monkeypatch, capsys) -> None:
    from mercury_tools import cli

    captured = {}

    class FakeService:
        def __init__(self, *, store, embedder):
            pass

        def search(self, query, *, filters, top_k, mode):
            captured["connector"] = filters.connector
            return [
                SimpleNamespace(
                    chunk_id="chunk-1",
                    document_uri="mercury://wiki/connectors/flowaccount",
                    score=1.0,
                    text="FlowAccount endpoint",
                    citation={"chunk_id": "chunk-1"},
                    metadata={"connector": "flowaccount"},
                    source_title="FlowAccount Endpoint Dictionary",
                    source_uri="mercury://wiki/connectors/flowaccount",
                    source_url=None,
                    source_path="wiki/connectors/flowaccount.md",
                )
            ]

    monkeypatch.setattr(cli, "load_settings", lambda: object())
    monkeypatch.setattr(cli, "SupabaseRagStore", lambda settings: object())
    monkeypatch.setattr(cli, "_embedder", lambda args: object())
    monkeypatch.setattr(cli, "RagService", FakeService)

    exit_code = cli.cmd_search(
        argparse.Namespace(
            query="FlowAccount invoice endpoint",
            jurisdiction=None,
            connector=None,
            doc_type=None,
            review_status=None,
            effective_date=None,
            action_id=None,
            version_id=None,
            environment=None,
            capability=None,
            accounting_use=None,
            top_k=8,
            mode="hybrid",
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured["connector"] == "flowaccount"
    assert payload["inferred_connector"] == "flowaccount"
    assert payload["results"][0]["metadata"]["connector"] == "flowaccount"


def test_cli_search_routes_exact_validation_filters(monkeypatch, capsys) -> None:
    from mercury_tools import cli

    captured = {}

    class FakeService:
        def __init__(self, *, store, embedder):
            pass

        def search(self, query, *, filters, top_k, mode):
            captured["filters"] = filters
            return []

    monkeypatch.setattr(cli, "load_settings", lambda: object())
    monkeypatch.setattr(cli, "SupabaseRagStore", lambda settings: object())
    monkeypatch.setattr(cli, "_embedder", lambda args: object())
    monkeypatch.setattr(cli, "RagService", FakeService)

    exit_code = cli.cmd_search(
        argparse.Namespace(
            query="qualified evidence",
            jurisdiction=None,
            connector="flowaccount",
            doc_type="endpoint_validation",
            review_status="reviewed",
            effective_date=None,
            action_id="act_1234567890abcdef12345678",
            version_id="av_" + "1" * 64,
            environment="sandbox",
            capability="documents.invoice.list",
            accounting_use="revenue_review",
            top_k=8,
            mode="hybrid",
            json=True,
        )
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["results"] == []
    filters = captured["filters"]
    assert filters.action_id == "act_1234567890abcdef12345678"
    assert filters.version_id == "av_" + "1" * 64
    assert filters.environment == "sandbox"
    assert filters.capability == "documents.invoice.list"
    assert filters.accounting_use == "revenue_review"


def test_cli_parser_exposes_local_credential_contract_without_importing_cloud_mcp() -> None:
    from mercury_tools.cli import build_parser

    parser = build_parser()
    help_text = parser.format_help()
    parsed = parser.parse_args(["mcp", "serve-local"])
    project = tomllib.loads(Path("pyproject.toml").read_text())

    for command in ("credentials", "connector", "mcp", "flow", "search", "doctor"):
        assert command in help_text
    assert callable(parsed.func)
    assert project["project"]["scripts"]["mercury"] == "mercury_tools.cli:main"


def test_mcp_serve_local_handles_only_a_missing_local_runtime(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    requested: list[str] = []
    original_import = builtins.__import__

    def missing_local_runtime(name: str, *args: object, **kwargs: object) -> object:
        requested.append(name)
        if name == "mercury_tools.mcp.local_server":
            raise ModuleNotFoundError(
                "No module named 'mercury_tools.mcp.local_server'",
                name="mercury_tools.mcp.local_server",
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_local_runtime)

    assert cli.cmd_mcp_serve_local(argparse.Namespace()) == 1

    assert "mercury_tools.mcp.local_server" in requested
    assert json.loads(capsys.readouterr().out) == {
        "error": "local_runtime_unavailable",
        "status": "error",
    }


def test_mcp_serve_local_reraises_missing_nested_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools import cli

    original_import = builtins.__import__

    def missing_nested_dependency(name: str, *args: object, **kwargs: object) -> object:
        if name == "mercury_tools.mcp.local_server":
            raise ModuleNotFoundError(
                "No module named 'future_nested_dependency'",
                name="future_nested_dependency",
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_nested_dependency)

    with pytest.raises(ModuleNotFoundError, match="future_nested_dependency"):
        cli.cmd_mcp_serve_local(argparse.Namespace())
