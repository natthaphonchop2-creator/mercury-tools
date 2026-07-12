from __future__ import annotations

from typing import Any

from starlette.testclient import TestClient

from mercury_tools.config import Settings
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
from mercury_tools.product import ConnectRequest, create_client_token
from mercury_tools.rag.models import ContextPack, SearchResult


def make_client_token() -> str:
    return create_client_token(
        Settings(
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role",
            openai_api_key="",
            connect_signing_secret="signing-secret",
        ),
        ConnectRequest(
            email="owner@example.com",
            company="Demo Co",
            host_app="codex",
            invite_code="invite",
        ),
    )


def make_workspace_id() -> str:
    return "mw_publiccontestworkspace001"


def configure_product_env(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")


def assert_key_fragments_absent(payload: dict[str, Any], fragments: list[str]) -> None:
    keys: list[str] = []

    def collect_keys(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                keys.append(str(key))
                collect_keys(item)
        elif isinstance(value, list | tuple):
            for item in value:
                collect_keys(item)

    collect_keys(payload)
    for fragment in fragments:
        assert all(fragment not in key for key in keys)


def rag_result(
    chunk_id: str,
    *,
    doc_type: str,
    score: float,
    connector: str | None = None,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=f"document-{chunk_id}",
        document_uri=f"mercury://wiki/{doc_type}/{chunk_id}",
        chunk_uri=f"mercury://wiki/{doc_type}/{chunk_id}#chunk-0",
        text=f"{doc_type} context",
        score=score,
        source_title=f"{doc_type} source",
        source_uri=f"mercury://wiki/{doc_type}/{chunk_id}",
        source_url="https://example.com/source",
        source_path=f"wiki/{doc_type}/{chunk_id}.md",
        citation={"heading": "Context"},
        metadata={"doc_type": doc_type, "connector": connector},
    )


def test_list_connectors_exposes_setup_targets_without_secrets() -> None:
    from mercury_tools.mcp.server import list_connectors

    payload = list_connectors()

    assert payload["status"] == "ok"
    assert {item["connector_id"] for item in payload["connectors"]} >= {
        "flowaccount",
        "peak",
        "express",
        "custom",
    }
    assert "super-secret" not in str(payload)
    assert "client_secret_value" not in str(payload)
    flowaccount = next(
        item for item in payload["connectors"] if item["connector_id"] == "flowaccount"
    )
    assert flowaccount["required_secret_fields"] == ["client_id", "client_secret"]
    assert flowaccount["preset"]["token_url"] == (
        "https://openapi.flowaccount.com/v1/token"
    )


def test_connector_capabilities_returns_public_policy() -> None:
    from mercury_tools.mcp.server import connector_capabilities

    payload = connector_capabilities("flowaccount")

    assert payload["status"] == "ok"
    assert payload["connector_id"] == "flowaccount"
    assert "documents.invoice.list" in payload["capabilities"]
    assert "documents.invoice.list" in payload["read_capabilities"]
    assert "documents.invoice.create" in payload["blocked_capabilities"]
    assert payload["public_policy"] == "read_only_validation"


def test_start_connector_setup_requires_valid_connector(monkeypatch) -> None:
    from mercury_tools.mcp.server import start_connector_setup

    configure_product_env(monkeypatch)

    invalid = start_connector_setup(
        workspace_id=make_workspace_id(),
        connector_id="unknown",
        environment="production",
    )

    assert invalid["status"] == "error"
    assert "Unknown connector" in invalid["message"]


def test_start_connector_setup_returns_redacted_profile(monkeypatch) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    class FakeStore:
        def start_connector_setup(
            self,
            *,
            token_payload: dict[str, Any],
            connector_id: str,
            environment: str,
            company_name: str | None = None,
        ) -> dict[str, Any]:
            assert token_payload["jti"] == make_workspace_id()
            return {
                "connector_id": connector_id,
                "environment": environment,
                "company_name": company_name,
                "status": "requires_credentials",
                "metadata": {
                    "required_secret_fields": ["client_id", "client_secret"],
                    "preset": {
                        "token_url": "https://openapi.flowaccount.com/v1/token",
                    },
                    "client_secret": "super-secret-value",
                },
            }

    monkeypatch.setattr(server, "_product_store", lambda settings: FakeStore())

    payload = server.start_connector_setup(
        workspace_id=make_workspace_id(),
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Co Books",
    )

    assert payload["status"] == "ok"
    assert payload["profile"]["connector_id"] == "flowaccount"
    assert payload["profile"]["company_name"] == "Demo Co Books"
    assert payload["profile"]["metadata"]["required_secret_fields"] == [
        "client_id",
        "client_secret",
    ]
    assert payload["profile"]["metadata"]["client_secret"] == "[REDACTED]"
    assert payload["profile"]["metadata"]["preset"]["token_url"] == (
        "https://openapi.flowaccount.com/v1/token"
    )
    assert "super-secret-value" not in str(payload)


def ready_connector_profile(
    *,
    connector_id: str = "flowaccount",
    environment: str = "production",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    metadata = {
        "setup_state": "ready",
        "enabled_capabilities": (
            ["company.info.read"] if capabilities is None else capabilities
        ),
    }
    return {
        "connector_id": connector_id,
        "environment": environment,
        "status": "connected",
        "metadata": metadata,
    }


CONNECTOR_RAW_FLOW = """name: FlowAccount Raw
tags: [accounting, flowaccount]
env:
  connector: flowaccount
  environment: production
  required_capabilities: [company.info.read]
---
- emitReport:
    title: "FlowAccount raw"
"""


NON_CONNECTOR_RAW_FLOW = """name: Local Raw
---
- emitReport:
    title: "Local raw"
"""


def test_run_flow_requires_workspace_for_connector_backed_raw_yaml() -> None:
    from mercury_tools.mcp import server

    payload = server.run_flow(CONNECTOR_RAW_FLOW, dry_run=True)

    assert payload["status"] == "requires_workspace"
    assert payload["next_tool"] == "create_public_workspace"


def test_run_flow_preserves_non_connector_raw_yaml_without_workspace() -> None:
    from mercury_tools.mcp import server

    payload = server.run_flow(NON_CONNECTOR_RAW_FLOW, dry_run=True)

    assert payload["status"] == "planned"
    assert payload["artifacts"][0]["title"] == "Local raw"


def test_run_flow_blocks_declared_mutation_before_workspace_setup() -> None:
    from mercury_tools.mcp import server

    payload = server.run_flow(
        """
name: Blocked Connector Mutation
tags: [accounting, flowaccount]
env:
  connector: flowaccount
  environment: production
  required_capabilities:
    - documents.invoice.create
---
- connectorStatus: {}
""",
        dry_run=False,
    )

    assert payload == {
        "status": "blocked",
        "reason": "public_preview_read_only",
        "capability": "documents.invoice.create",
    }


def test_run_flow_blocks_connector_backed_raw_yaml_when_workspace_unready(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    class FakeStore:
        def public_dashboard(self, workspace_id):
            assert workspace_id == make_workspace_id()
            return {
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "credentials_configured",
                        "metadata": {"setup_state": "credentials_received"},
                    }
                ]
            }

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_flow(
        CONNECTOR_RAW_FLOW,
        dry_run=True,
        workspace_id=make_workspace_id(),
    )

    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]


def test_run_flow_files_requires_workspace_for_connector_backed_raw_yaml() -> None:
    from mercury_tools.mcp import server

    payload = server.run_flow_files(
        {"flows/flowaccount.yaml": CONNECTOR_RAW_FLOW},
        dry_run=True,
    )

    assert payload["status"] == "requires_workspace"
    assert payload["selected_count"] == 1
    assert payload["next_tool"] == "create_public_workspace"


def test_run_mercury_flow_requires_workspace_for_connector_backed_flow_yaml() -> None:
    from mercury_tools.mcp import server

    payload = server.run_mercury_flow(
        flow_yaml=CONNECTOR_RAW_FLOW,
        dry_run=True,
    )

    assert payload["status"] == "requires_workspace"
    assert payload["entrypoint"] == "run_mercury_flow"
    assert payload["input_mode"] == "flow_yaml"


def test_workspace_connector_ready_blocks_missing_or_empty_profiles() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    assert (
        workspace_connector_ready(
            {"workspace": {"name": "Demo Co"}},
            connector_id="flowaccount",
            environment="production",
        )
        is False
    )
    assert (
        workspace_connector_ready(
            {"connector_profiles": []},
            connector_id="flowaccount",
            environment="production",
        )
        is False
    )


def test_workspace_connector_ready_blocks_ready_profile_without_capabilities() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    profile = ready_connector_profile(capabilities=[])

    assert (
        workspace_connector_ready(
            {"connector_profiles": [profile]},
            connector_id="flowaccount",
            environment="production",
        )
        is False
    )


def test_workspace_connector_ready_accepts_flowaccount_required_capability() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    dashboard = {
        "connector_profiles": [
            ready_connector_profile(capabilities=["company.info.read", "contacts.list"])
        ]
    }

    assert workspace_connector_ready(
        dashboard,
        connector_id="flowaccount",
        environment="production",
        required_capabilities=["company.info.read"],
    )


def test_workspace_connector_ready_uses_selected_connector_and_environment() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    dashboard = {"connector_profiles": [ready_connector_profile()]}

    assert workspace_connector_ready(
        dashboard,
        connector_id="flowaccount",
        environment="production",
    )
    assert not workspace_connector_ready(
        dashboard,
        connector_id="peak",
        environment="production",
    )
    assert not workspace_connector_ready(
        dashboard,
        connector_id="flowaccount",
        environment="sandbox",
    )


def test_workspace_connector_ready_blocks_selected_connector_without_environment() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    dashboard = {"connector_profiles": [ready_connector_profile()]}

    assert not workspace_connector_ready(dashboard, connector_id="flowaccount")


def test_workspace_connector_ready_blocks_setup_targets_even_if_profile_claims_ready() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    for connector_id, environment in (
        ("express", "local"),
        ("custom", "production"),
    ):
        dashboard = {
            "connector_profiles": [
                ready_connector_profile(
                    connector_id=connector_id,
                    environment=environment,
                    capabilities=["company.info.read", "made.up.capability"],
                )
            ]
        }

        assert not workspace_connector_ready(
            dashboard,
            connector_id=connector_id,
            environment=environment,
            required_capabilities=["company.info.read"],
        )


def test_workspace_connector_ready_accepts_peak_endpoint_capability() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    dashboard = {
        "connector_profiles": [
            ready_connector_profile(
                connector_id="peak",
                environment="production",
                capabilities=["user.info.read", "documents.invoice.create"],
            )
        ]
    }

    assert workspace_connector_ready(
        dashboard,
        connector_id="peak",
        environment="production",
        required_capabilities=["documents.invoice.create"],
    )


def test_workspace_connector_ready_blocks_peak_capability_outside_manifest() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    dashboard = {
        "connector_profiles": [
            ready_connector_profile(
                connector_id="peak",
                environment="production",
                capabilities=["user.info.read", "company.info.read"],
            )
        ]
    }

    assert not workspace_connector_ready(
        dashboard,
        connector_id="peak",
        environment="production",
        required_capabilities=["company.info.read"],
    )


def test_workspace_connector_ready_blocks_capabilities_outside_manifest() -> None:
    from mercury_tools.mcp.server import workspace_connector_ready

    dashboard = {
        "connector_profiles": [
            ready_connector_profile(
                capabilities=["company.info.read", "made.up.capability"],
            )
        ]
    }

    assert not workspace_connector_ready(
        dashboard,
        connector_id="flowaccount",
        environment="production",
        required_capabilities=["company.info.read"],
    )


def test_retrieve_workspace_context_pack_uses_active_connector(monkeypatch) -> None:
    from mercury_tools.mcp import server
    from mercury_tools.mcp.server import retrieve_workspace_context_pack

    configure_product_env(monkeypatch)
    captured: dict[str, Any] = {}
    audit_events: list[dict[str, Any]] = []

    class FakeStore:
        def public_dashboard(self, workspace_id):
            assert workspace_id == make_workspace_id()
            return {
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "connected",
                        "metadata": {
                            "setup_state": "ready",
                            "enabled_capabilities": ["documents.invoice.list"],
                            "credential_storage": "encrypted_server_vault",
                            "credential_fields": ["client_id", "client_secret"],
                            "credential_fingerprints": {
                                "client_id": "client-id-fp",
                                "client_secret": "client-secret-fp",
                            },
                            "credentials_configured": True,
                        },
                    }
                ]
            }

    class FakeService:
        def context_pack(self, query, *, task=None, filters=None, max_chunks=12):
            captured["query"] = query
            captured["task"] = task
            captured["filters"] = filters
            captured["max_chunks"] = max_chunks
            return type(
                "Pack",
                (),
                {
                    "results": [],
                    "as_dict": lambda self: {
                        "query": query,
                        "task": task,
                        "context": [],
                        "connector_context": "service value should not win",
                    },
                },
            )()

    def fake_audit(tool_name, input_payload, output_summary):
        audit_events.append(
            {
                "tool_name": tool_name,
                "input_payload": input_payload,
                "output_summary": output_summary,
            }
        )

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())
    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", fake_audit)

    workspace_id = make_workspace_id()
    payload = retrieve_workspace_context_pack(
        workspace_id=workspace_id,
        query="สรุปรายได้อาทิตย์นี้",
        task="weekly_revenue",
        max_chunks=7,
    )

    assert payload["status"] == "no_relevant_knowledge"
    assert payload["minimum_score"] == 0.20
    assert payload["retrieval_scopes"] == ["connector:flowaccount"]
    assert captured["query"] == "สรุปรายได้อาทิตย์นี้"
    assert captured["task"] == "weekly_revenue"
    assert captured["max_chunks"] == 7
    assert captured["filters"].connector == "flowaccount"
    assert captured["filters"].review_status == "reviewed"
    assert payload["connector_context"]["connector_id"] == "flowaccount"
    assert payload["connector_context"]["environment"] == "production"
    assert workspace_id not in str(audit_events)
    assert audit_events[-1]["tool_name"] == "retrieve_workspace_context_pack"
    assert audit_events[-1]["input_payload"]["workspace_id_hash"]
    assert audit_events[-1]["output_summary"]["connector_id"] == "flowaccount"


def test_workspace_vat_context_merges_connector_and_tax_scopes(monkeypatch) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)
    calls: list[dict[str, Any]] = []

    class FakeStore:
        def public_dashboard(self, workspace_id):
            assert workspace_id == make_workspace_id()
            return {
                "connector_profiles": [
                    ready_connector_profile(
                        capabilities=["documents.invoice.list"],
                    )
                ]
            }

    class FakeService:
        def context_pack(self, query, *, task=None, filters=None, max_chunks=12):
            calls.append(
                {
                    "query": query,
                    "task": task,
                    "filters": filters,
                    "max_chunks": max_chunks,
                }
            )
            if filters.doc_type == "tax":
                results = [
                    rag_result("tax-1", doc_type="tax", score=0.91),
                    rag_result("shared", doc_type="tax", score=0.70),
                ]
            else:
                results = [
                    rag_result(
                        "connector-1",
                        doc_type="endpoint_dictionary",
                        score=0.95,
                        connector="flowaccount",
                    ),
                    rag_result(
                        "shared",
                        doc_type="endpoint_dictionary",
                        score=0.60,
                        connector="flowaccount",
                    ),
                ]
            return ContextPack(query=query, task=task, results=results)

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())
    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.retrieve_workspace_context_pack(
        workspace_id=make_workspace_id(),
        query="สรุป VAT ภาษีซื้อ ภาษีขาย",
        task="vat_summary_th",
        max_chunks=4,
    )

    assert payload["status"] == "ok"
    assert payload["retrieval_scopes"] == ["connector:flowaccount", "tax:TH"]
    assert len(calls) == 2
    assert calls[0]["filters"].connector == "flowaccount"
    assert calls[0]["filters"].review_status == "reviewed"
    assert calls[0]["max_chunks"] == 2
    assert calls[1]["filters"].connector is None
    assert calls[1]["filters"].jurisdiction == "TH"
    assert calls[1]["filters"].doc_type == "tax"
    assert calls[1]["filters"].review_status == "reviewed"
    assert calls[1]["max_chunks"] == 2
    assert [row["chunk_id"] for row in payload["context"]] == [
        "connector-1",
        "tax-1",
        "shared",
    ]


def test_workspace_standard_context_does_not_filter_standard_by_connector(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)
    filters_seen: list[Any] = []

    class FakeStore:
        def public_dashboard(self, workspace_id):
            return {
                "connector_profiles": [
                    ready_connector_profile(
                        connector_id="peak",
                        capabilities=["documents.invoice.list"],
                    )
                ]
            }

    class FakeService:
        def context_pack(self, query, *, task=None, filters=None, max_chunks=12):
            del max_chunks
            filters_seen.append(filters)
            return ContextPack(
                query=query,
                task=task,
                results=[
                    rag_result(
                        f"row-{len(filters_seen)}",
                        doc_type=filters.doc_type or "endpoint_dictionary",
                        score=0.90,
                        connector=filters.connector,
                    )
                ],
            )

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())
    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.retrieve_workspace_context_pack(
        workspace_id=make_workspace_id(),
        query="TFRS 15 การรับรู้รายได้",
        max_chunks=6,
    )

    assert payload["retrieval_scopes"] == ["connector:peak", "accounting_standard:TH"]
    assert filters_seen[0].connector == "peak"
    assert filters_seen[1].connector is None
    assert filters_seen[1].doc_type == "accounting_standard"


def test_retrieve_workspace_context_pack_requires_setup_without_ready_available_connector(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server
    from mercury_tools.mcp.server import retrieve_workspace_context_pack

    configure_product_env(monkeypatch)
    audit_events: list[dict[str, Any]] = []

    class FakeStore:
        def public_dashboard(self, workspace_id):
            assert workspace_id == make_workspace_id()
            return {
                "connector_profiles": [
                    {
                        "connector_id": "peak",
                        "environment": "production",
                        "status": "ready",
                        "metadata": {
                            "setup_state": "ready",
                            "enabled_capabilities": ["company.info.read"],
                        },
                    },
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "credentials_configured",
                        "metadata": {"setup_state": "credentials_received"},
                    },
                ]
            }

    class FakeService:
        def context_pack(self, *args, **kwargs):
            raise AssertionError("setup-required workspace should not hit RAG")

    def fake_audit(tool_name, input_payload, output_summary):
        audit_events.append(
            {
                "tool_name": tool_name,
                "input_payload": input_payload,
                "output_summary": output_summary,
            }
        )

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())
    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", fake_audit)

    workspace_id = make_workspace_id()
    payload = retrieve_workspace_context_pack(
        workspace_id=workspace_id,
        query="สรุปรายได้อาทิตย์นี้",
    )

    assert payload["status"] == "requires_setup"
    assert payload["next_tool"] == "start_connector_setup"
    assert payload["next_skill"] == "connector-credential-setup-th"
    assert "connector credential setup" in payload["message"]
    assert workspace_id not in str(audit_events)
    assert audit_events[-1]["tool_name"] == "retrieve_workspace_context_pack"
    assert audit_events[-1]["output_summary"]["status"] == "requires_setup"


def test_connector_status_returns_workspace_scoped_sanitized_profiles(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)
    audit_events: list[dict[str, Any]] = []

    class FakeStore:
        def public_dashboard(self, workspace_id):
            assert workspace_id == make_workspace_id()
            return {
                "workspace": {"name": "Demo Co"},
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "connected",
                        "metadata": {
                            "setup_state": "ready",
                            "enabled_capabilities": ["company.info.read"],
                            "credential_storage": "encrypted_server_vault",
                            "credential_fields": ["client_id", "client_secret"],
                            "credential_fingerprints": {
                                "client_id": "client-id-fp",
                                "client_secret": "client-secret-fp",
                            },
                            "credentials_configured": True,
                            "server_vault": {
                                "ciphertext": "encrypted-secret-derived-value"
                            },
                        },
                    }
                ],
            }

    def fake_audit(tool_name, input_payload, output_summary):
        audit_events.append(
            {
                "tool_name": tool_name,
                "input_payload": input_payload,
                "output_summary": output_summary,
            }
        )

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())
    monkeypatch.setattr(server, "_audit", fake_audit)

    workspace_id = make_workspace_id()
    payload = server.connector_status(workspace_id=workspace_id)

    assert payload["status"] == "ok"
    assert payload["setup_required"] is False
    assert payload["active_connector"]["connector_id"] == "flowaccount"
    assert payload["connector_profiles"][0]["status"] == "connected"
    assert "'server_vault':" not in str(payload)
    assert "ciphertext" not in str(payload)
    assert "encrypted-secret-derived-value" not in str(payload)
    assert workspace_id not in str(audit_events)
    assert audit_events[-1]["tool_name"] == "connector_status"
    assert audit_events[-1]["input_payload"]["workspace_id_hash"]


def test_connector_status_requires_setup_without_ready_profile(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    configure_product_env(monkeypatch)

    class FakeStore:
        def public_dashboard(self, workspace_id):
            return {
                "workspace": {"name": "Demo Co"},
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "credentials_configured",
                        "metadata": {"setup_state": "credentials_received"},
                    }
                ],
            }

    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.connector_status(workspace_id=make_workspace_id())

    assert payload["status"] == "requires_setup"
    assert payload["setup_required"] is True
    assert payload["next_tool"] == "start_connector_setup"
    assert payload["next_skill"] == "connector-credential-setup-th"


def test_run_workspace_flow_requires_ready_connector(monkeypatch) -> None:
    from mercury_tools.mcp import server

    class FakeStore:
        def public_dashboard(self, workspace_id):
            return {
                "workspace": {"name": "Demo Co"},
                "flows": [
                    {
                        "flow_id": "workspace-revenue",
                        "title": "Revenue",
                        "yaml": COMPANY_HEALTH_TEMPLATE,
                    }
                ],
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "credentials_configured",
                        "metadata": {"setup_state": "credentials_received"},
                    }
                ],
            }

        def get_flow(self, token_payload, flow_id):
            raise AssertionError("blocked connector setup should not load the flow")

    configure_product_env(monkeypatch)
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_workspace_flow_tool(
        workspace_id=make_workspace_id(),
        flow_id="workspace-revenue",
        dry_run=False,
    )

    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]


def test_run_workspace_flow_blocks_selected_connector_without_environment(monkeypatch) -> None:
    from mercury_tools.mcp import server

    connector_flow_missing_environment = """name: FlowAccount Missing Environment
tags: [accounting, endpoint-capable, flowaccount]
env:
  connector: flowaccount
---
- connectorStatus:
    saveAs: connectorState
- emitReport:
    title: "Connector handoff"
    sections:
      - "Ready"
"""

    class FakeStore:
        def public_dashboard(self, workspace_id):
            return {
                "workspace": {"name": "Demo Co"},
                "flows": [
                    {
                        "flow_id": "workspace-missing-env",
                        "title": "Missing Environment",
                        "yaml": connector_flow_missing_environment,
                    }
                ],
                "connector_profiles": [ready_connector_profile()],
            }

        def get_flow(self, *, token_payload, flow_id):
            raise AssertionError("missing environment should block before loading the flow")

    configure_product_env(monkeypatch)
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_workspace_flow_tool(
        workspace_id=make_workspace_id(),
        flow_id="workspace-missing-env",
        dry_run=False,
    )

    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]


def test_run_workspace_flow_connector_status_uses_public_workspace_state(
    monkeypatch,
    tmp_path,
) -> None:
    from mercury_tools.mcp import server

    workspace_id = make_workspace_id()
    flow = {
        "flow_id": "workspace-public-status",
        "title": "Public Connector Status",
        "yaml": """name: Public Connector Status
tags: [accounting, flowaccount]
env:
  connector: flowaccount
  environment: production
  required_capabilities: [company.info.read]
---
- connectorStatus:
    saveAs: connectorState
""",
    }

    class FakeStore:
        def public_dashboard(self, requested_workspace_id):
            assert requested_workspace_id == workspace_id
            return {
                "status": "ok",
                "workspace": {"name": "Public Demo Co"},
                "flows": [flow],
                "connector_profiles": [ready_connector_profile()],
            }

        def get_flow(self, *, token_payload, flow_id):
            return flow if flow_id == flow["flow_id"] else None

    configure_product_env(monkeypatch)
    monkeypatch.setenv("MERCURY_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        '{"selected_connector":"local-only","environment":"production"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_workspace_flow_tool(
        workspace_id=workspace_id,
        flow_id=flow["flow_id"],
        dry_run=False,
    )

    connector_state = payload["variables"]["connectorState"]
    assert connector_state["status"] == "ok"
    assert connector_state["active_connector"]["connector_id"] == "flowaccount"
    assert "home" not in connector_state
    assert str(tmp_path) not in str(payload)


def test_run_workspace_flow_blocks_selected_connector_mismatch(monkeypatch) -> None:
    from mercury_tools.mcp import server

    class FakeStore:
        def public_dashboard(self, workspace_id):
            return {
                "workspace": {"name": "Demo Co"},
                "flows": [
                    {
                        "flow_id": "workspace-revenue",
                        "title": "Revenue",
                        "yaml": COMPANY_HEALTH_TEMPLATE,
                    }
                ],
                "connector_profiles": [ready_connector_profile()],
            }

        def get_flow(self, *, token_payload, flow_id):
            raise AssertionError("connector mismatch should not load the flow")

    configure_product_env(monkeypatch)
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_workspace_flow_tool(
        workspace_id=make_workspace_id(),
        flow_id="workspace-revenue",
        dry_run=False,
        env={"connector": "peak", "environment": "production"},
    )

    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]


def test_run_workspace_flow_blocks_peak_profile_with_unknown_capability(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    class FakeStore:
        def public_dashboard(self, workspace_id):
            return {
                "workspace": {"name": "Demo Co"},
                "flows": [
                    {
                        "flow_id": "workspace-revenue",
                        "title": "Revenue",
                        "yaml": COMPANY_HEALTH_TEMPLATE,
                    }
                ],
                "connector_profiles": [
                    ready_connector_profile(
                        connector_id="peak",
                        capabilities=["company.info.read", "made.up.capability"],
                    )
                ],
            }

        def get_flow(self, *, token_payload, flow_id):
            raise AssertionError(
                "PEAK profile with unsupported capabilities should not load the flow"
            )

    configure_product_env(monkeypatch)
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    payload = server.run_workspace_flow_tool(
        workspace_id=make_workspace_id(),
        flow_id="workspace-revenue",
        dry_run=False,
        env={"connector": "peak", "environment": "production"},
    )

    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]


def test_http_workspace_flow_run_requires_ready_connector(monkeypatch) -> None:
    from mercury_tools.mcp import server

    class FakeStore:
        def dashboard(self, token_payload):
            return {
                "workspace": {"name": "Demo Co"},
                "flows": [
                    {
                        "flow_id": "workspace-revenue",
                        "title": "Revenue",
                        "yaml": COMPANY_HEALTH_TEMPLATE,
                    }
                ],
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "credentials_configured",
                        "metadata": {"setup_state": "credentials_received"},
                    }
                ],
            }

        def get_flow(self, *, token_payload, flow_id):
            raise AssertionError("blocked HTTP connector setup should not load the flow")

    configure_product_env(monkeypatch)
    monkeypatch.setenv("MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API", "true")
    monkeypatch.setattr(server, "_product_store", lambda settings=None: FakeStore())

    client = TestClient(server.create_http_app(require_auth=True), raise_server_exceptions=False)
    response = client.post(
        "/api/flows/run",
        headers={"Authorization": f"Bearer {make_client_token()}"},
        json={"flow_id": "workspace-revenue", "dry_run": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]
