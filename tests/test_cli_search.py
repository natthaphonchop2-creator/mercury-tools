from __future__ import annotations

import argparse
import json
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
                    citation={
                        "chunk_id": "chunk-1",
                        "provider_record_id": "provider-private-value",
                    },
                    metadata={
                        "connector": "flowaccount",
                        "provider_record_id": "provider-private-value",
                        "raw_payload": {"email": "person@example.test"},
                    },
                    source_title="FlowAccount Endpoint Dictionary",
                    source_uri="mercury://wiki/connectors/flowaccount",
                    source_url=None,
                    source_path="/Users/operator/private/flowaccount.md",
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
    assert payload["results"][0]["metadata"] == {"connector": "flowaccount"}
    assert "source_path" not in payload["results"][0]
    assert "provider-private-value" not in str(payload)
    assert "person@example.test" not in str(payload)


def test_cli_search_rejects_partial_validation_metadata_without_echo(
    monkeypatch,
    capsys,
) -> None:
    from mercury_tools import cli

    unsafe_value = "provider-private-value"
    validation_uri = "mercury://wiki/validation/flowaccount/action/version/run"

    class FakeService:
        def __init__(self, *, store, embedder):
            pass

        def search(self, query, *, filters, top_k, mode):
            return [
                SimpleNamespace(
                    chunk_id="chunk-validation",
                    document_id="document-validation",
                    document_uri=validation_uri,
                    score=1.0,
                    text="Unapproved validation",
                    citation={},
                    metadata={
                        "review_status": "reviewed",
                        "provider_record_id": unsafe_value,
                    },
                    source_title="Validation",
                    source_uri=validation_uri,
                    source_url=None,
                    source_path=None,
                )
            ]

    monkeypatch.setattr(cli, "load_settings", lambda: object())
    monkeypatch.setattr(cli, "SupabaseRagStore", lambda settings: object())
    monkeypatch.setattr(cli, "_embedder", lambda args: object())
    monkeypatch.setattr(cli, "RagService", FakeService)

    with pytest.raises(
        ValueError,
        match="^public_knowledge_metadata_invalid$",
    ) as raised:
        cli.cmd_search(
            argparse.Namespace(
                query="qualified evidence",
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

    assert unsafe_value not in str(raised.value)
    assert unsafe_value not in capsys.readouterr().out


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


def test_cli_parser_exposes_one_hosted_mcp_server_command() -> None:
    from mercury_tools.cli import build_parser

    parser = build_parser()
    help_text = parser.format_help()
    parsed = parser.parse_args(["mcp", "serve", "--transport", "stdio"])

    for command in ("catalog", "ingest", "mcp", "remote", "flow", "search"):
        assert command in help_text
    assert callable(parsed.func)
