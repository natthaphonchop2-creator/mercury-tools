from __future__ import annotations

import argparse
import json
from types import SimpleNamespace


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
