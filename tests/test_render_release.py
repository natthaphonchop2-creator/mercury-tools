from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from mercury_tools.release.models import HostedSurfaceScanResult

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_render_release import (  # noqa: E402
    EXPECTED_HOSTED_TOOLS,
    LiveRenderProbe,
    McpReleaseEvidence,
    RenderReleaseError,
    verify_render_release,
)

VERSION = "0.3.0"
COMMIT = "a" * 40
V030_HOSTED_TOOLS = {
    "check_flow_syntax",
    "connector_capabilities",
    "connector_status",
    "create_public_workspace",
    "flow_cheat_sheet",
    "get_accounting_skill_schema",
    "get_connector_setup",
    "get_document",
    "get_public_workspace",
    "inspect_flow_files",
    "link_connector_profile",
    "list_accounting_skills",
    "list_connectors",
    "list_workspace_flows",
    "retrieve_context_pack",
    "retrieve_workspace_context_pack",
    "run_accounting_skill",
    "run_flow_files",
    "run_inline_flow",
    "run_workspace_flow",
    "save_workspace_flow",
    "search_knowledge",
    "unlink_connector_profile",
    "validate_connector_connection",
}


def test_render_release_uses_the_reviewed_v030_hosted_tool_contract() -> None:
    assert EXPECTED_HOSTED_TOOLS == V030_HOSTED_TOOLS


def test_live_render_probe_rejects_unsafe_owner_id() -> None:
    with pytest.raises(RenderReleaseError, match="^render_owner_id_invalid$"):
        LiveRenderProbe(
            base_url="https://mercury.example",
            mcp_token=None,
            render_api_url="https://api.render.example",
            render_owner_id="owner/unsafe",
            render_service_id="srv-safe",
            render_token="operator-token",
        )


def test_live_render_probe_accepts_clean_http_log_receipts_without_command_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = LiveRenderProbe(
        base_url="https://mercury.example",
        mcp_token=None,
        render_api_url="https://api.render.example",
        render_owner_id="tea-safe",
        render_service_id="srv-safe",
        render_token="operator-token",
    )
    monkeypatch.setattr(
        "scripts.verify_render_release.build_hosted_clients",
        lambda _config: {"render_build_and_runtime_logs": object()},
    )
    monkeypatch.setattr(
        "scripts.verify_render_release.scan_hosted_surface",
        lambda *_args: HostedSurfaceScanResult(
            surface="render_build_and_runtime_logs",
            scanner_version="1.0.0",
            evidence_hashes=("a" * 64, "b" * 64),
            exit_codes=(),
        ),
    )

    assert probe.scan_logs() is True


def _tools() -> tuple[dict[str, object], ...]:
    return tuple(
        {"name": name, "inputSchema": {"type": "object", "properties": {}}}
        for name in sorted(EXPECTED_HOSTED_TOOLS)
    )


def _mcp_evidence() -> McpReleaseEvidence:
    def payload(connector: str, field: str) -> dict[str, object]:
        citation = {
            "source_uri": f"mercury://wiki/validation/{connector}/invoice",
            "heading": "Invoices",
        }
        return {
            "status": "ok",
            field: [
                {
                    "citation": citation,
                    "metadata": {
                        "connector": connector,
                        "doc_type": "endpoint_validation",
                        "review_status": "reviewed",
                    },
                }
            ],
        }

    return McpReleaseEvidence(
        server_name="Mercury Tools",
        tools=_tools(),
        searches={
            connector: payload(connector, "results")
            for connector in ("flowaccount", "peak")
        },
        contexts={
            connector: payload(connector, "context")
            for connector in ("flowaccount", "peak")
        },
    )


class FakeProbe:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.health_payload: object = {"status": "ok"}
        self.status_payload: object = {
            "status": "ok",
            "version": VERSION,
            "deployment_commit": COMMIT,
            "mcp_endpoint": "https://mercury.example/mcp",
        }
        self.catalog_payload: object = {"actions": [{} for _ in range(254)]}
        self.mcp_payload = _mcp_evidence()
        self.logs_clean = True

    def health(self) -> object:
        self.events.append("healthz")
        return self.health_payload

    def status(self) -> object:
        self.events.append("status")
        return self.status_payload

    def catalog(self) -> object:
        self.events.append("catalog")
        return self.catalog_payload

    def mcp(self, endpoint: str) -> McpReleaseEvidence:
        assert endpoint == "https://mercury.example/mcp"
        self.events.append("mcp")
        return self.mcp_payload

    def scan_logs(self) -> bool:
        self.events.append("logs")
        return self.logs_clean


def test_render_release_requires_every_exact_gate() -> None:
    probe = FakeProbe()

    report = verify_render_release(probe, version=VERSION, commit=COMMIT)

    assert report.passed is True
    assert report.version == VERSION
    assert report.commit == COMMIT
    assert report.catalog_count == 254
    assert report.tool_count == 24
    assert probe.events == ["healthz", "status", "catalog", "mcp", "logs"]


def test_render_release_never_substitutes_status_for_healthz() -> None:
    probe = FakeProbe()
    probe.health_payload = {"status": "degraded"}

    with pytest.raises(RenderReleaseError, match="healthz_required"):
        verify_render_release(probe, version=VERSION, commit=COMMIT)

    assert probe.events == ["healthz"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("version", "0.2.0", "status_version_mismatch"),
        ("deployment_commit", "b" * 40, "status_commit_mismatch"),
        ("mcp_endpoint", "http://mercury.example/mcp", "status_mcp_endpoint_invalid"),
    ),
)
def test_render_release_compares_exact_status_identity(
    field: str,
    value: str,
    error: str,
) -> None:
    probe = FakeProbe()
    assert isinstance(probe.status_payload, dict)
    probe.status_payload[field] = value

    with pytest.raises(RenderReleaseError, match=error):
        verify_render_release(probe, version=VERSION, commit=COMMIT)


def test_render_release_requires_exact_catalog_count() -> None:
    probe = FakeProbe()
    probe.catalog_payload = {"actions": [{} for _ in range(253)]}

    with pytest.raises(RenderReleaseError, match="catalog_count_mismatch"):
        verify_render_release(probe, version=VERSION, commit=COMMIT)


@pytest.mark.parametrize(
    ("collection", "result_field"),
    (("searches", "results"), ("contexts", "context")),
)
@pytest.mark.parametrize("connector", ("flowaccount", "peak"))
def test_render_release_requires_connector_bound_rag_citations(
    collection: str,
    result_field: str,
    connector: str,
) -> None:
    probe = FakeProbe()
    payloads = dict(getattr(probe.mcp_payload, collection))
    payloads[connector] = {"status": "ok", result_field: [{}]}
    probe.mcp_payload = replace(probe.mcp_payload, **{collection: payloads})

    with pytest.raises(
        RenderReleaseError,
        match=f"rag_{collection}_{connector}_citation_missing",
    ):
        verify_render_release(probe, version=VERSION, commit=COMMIT)


def test_render_release_rejects_cross_connector_rag_result() -> None:
    probe = FakeProbe()
    searches = dict(probe.mcp_payload.searches)
    peak = dict(searches["peak"])
    rows = [dict(row) for row in peak["results"]]
    rows[0]["metadata"] = {**rows[0]["metadata"], "connector": "flowaccount"}
    peak["results"] = rows
    searches["peak"] = peak
    probe.mcp_payload = replace(probe.mcp_payload, searches=searches)

    with pytest.raises(RenderReleaseError, match="rag_searches_peak_citation_missing"):
        verify_render_release(probe, version=VERSION, commit=COMMIT)


def test_render_release_rejects_public_credential_surface() -> None:
    probe = FakeProbe()
    tools = list(probe.mcp_payload.tools)
    tools[0] = {
        "name": tools[0]["name"],
        "inputSchema": {
            "type": "object",
            "properties": {"client_secret": {"type": "string"}},
        },
    }
    probe.mcp_payload = replace(probe.mcp_payload, tools=tuple(tools))

    with pytest.raises(RenderReleaseError, match="public_credential_surface"):
        verify_render_release(probe, version=VERSION, commit=COMMIT)


def test_render_release_rejects_arbitrary_write_tool() -> None:
    probe = FakeProbe()
    tools = list(probe.mcp_payload.tools)
    tools[-1] = {"name": "write_arbitrary_url", "inputSchema": {"type": "object"}}
    probe.mcp_payload = replace(probe.mcp_payload, tools=tuple(tools))

    with pytest.raises(RenderReleaseError, match="hosted_tool_surface_mismatch"):
        verify_render_release(probe, version=VERSION, commit=COMMIT)


def test_render_release_requires_scanned_build_and_runtime_logs() -> None:
    probe = FakeProbe()
    probe.logs_clean = False

    with pytest.raises(RenderReleaseError, match="render_log_scan_blocked"):
        verify_render_release(probe, version=VERSION, commit=COMMIT)
