from mercury_tools.product import ConnectRequest, create_client_token
from mercury_tools.prompts import get_prompt
from mercury_tools.rag.embeddings import HashEmbeddingProvider


def test_mcp_server_imports_and_exposes_server() -> None:
    from mercury_tools.mcp.server import mcp

    assert mcp.name == "Mercury Tools"


def test_prompt_templates_exist() -> None:
    assert "VAT" in get_prompt("vat_summary_th")
    assert "โปรแกรมบัญชี" in get_prompt("connector_setup_guide_th")


def test_hash_embedding_dimension() -> None:
    vector = HashEmbeddingProvider(dimensions=1536).embed_query("vat")

    assert len(vector) == 1536
    assert all(isinstance(value, float) for value in vector)


def test_mcp_flow_tools_validate_and_dry_run() -> None:
    from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
    from mercury_tools.mcp.server import (
        check_flow_syntax,
        flow_cheat_sheet,
        inspect_flow_files,
        run_flow,
        run_flow_files,
        run_mercury_flow,
    )

    assert "Mercury Flow" in flow_cheat_sheet()["cheat_sheet"]

    syntax = check_flow_syntax(COMPANY_HEALTH_TEMPLATE)
    assert syntax["status"] == "ok"
    assert syntax["flow"]["command_count"] == 3

    result = run_flow(
        """name: Local Dry Run
---
- emitReport:
    title: "Local"
""",
        dry_run=True,
    )
    assert result["status"] == "planned"
    assert result["steps"][0]["command"] == "emitReport"

    parameterized = run_flow(
        """name: Env Override Smoke
env:
  month: "2026-01"
---
- emitReport:
    title: "Month ${month}"
""",
        dry_run=True,
        env={"month": "2026-10"},
    )
    assert parameterized["status"] == "planned"
    assert parameterized["variables"]["env"]["month"] == "2026-10"
    assert parameterized["artifacts"][0]["title"] == "Month 2026-10"

    suite = run_flow_files(
        {
            "main.yaml": """name: Main
tags: [accounting]
onFlowStart:
  - emitReport:
      title: "Start ${month}"
---
- runFlow:
    file: sub.yaml
    env:
      subMonth: "${month}"
    saveAs: subflow
- emitReport:
    title: "Main ${month}"
""",
            "sub.yaml": """name: Subflow
tags: [helper]
---
- emitReport:
    title: "Sub ${subMonth}"
""",
        },
        dry_run=True,
        env={"month": "2026-10"},
        include_tags=["accounting"],
    )
    assert suite["status"] == "planned"
    assert suite["flow_count"] == 2
    assert suite["selected_count"] == 1
    assert suite["skipped_count"] == 1
    assert suite["env_keys"] == ["month"]
    assert suite["flows"][0]["path"] == "main.yaml"
    assert suite["results"][0]["flow"]["path"] == "main.yaml"
    assert [step["source"] for step in suite["results"][0]["steps"]] == [
        "onFlowStart",
        "commands",
        "commands",
    ]
    assert suite["results"][0]["artifacts"][0]["title"] == "Start 2026-10"
    assert suite["results"][0]["artifacts"][1]["title"] == "Main 2026-10"
    assert suite["results"][0]["variables"]["subflow"]["flow"]["path"] == "sub.yaml"
    assert suite["results"][0]["variables"]["subflow"]["artifacts"][0]["title"] == "Sub 2026-10"

    config_suite = run_flow_files(
        {
            "flows/b.yaml": """name: B
tags: [accounting]
---
- emitReport:
    title: "B"
""",
            "flows/a.yaml": """name: A
tags: [accounting]
---
- emitReport:
    title: "A"
""",
        },
        config_yaml="""
flows: flows/**/*.yaml
includeTags: [accounting]
executionOrder:
  flowsOrder:
    - a
    - b
""",
        dry_run=True,
    )
    assert config_suite["status"] == "planned"
    assert config_suite["config_yaml_present"] is True
    assert [result["flow"]["name"] for result in config_suite["results"]] == ["A", "B"]

    manifest = inspect_flow_files(
        {
            "flows/main.yaml": """name: Main
tags: [accounting, smoke]
---
- emitReport:
    title: "Main"
""",
            "flows/wip.yaml": """name: WIP
tags: [disabled]
---
- emitReport:
    title: "WIP"
""",
        },
        config_yaml="flows: flows/**/*.yaml\nincludeTags: [accounting]\nexcludeTags: [disabled]\n",
    )
    assert manifest["status"] == "ok"
    assert manifest["surface"] == "mcp-cli"
    assert manifest["discovery"]["selected_count"] == 1
    assert manifest["discovery"]["skipped_count"] == 1
    assert manifest["execution"]["ordered_flow_paths"] == ["flows/main.yaml"]
    assert manifest["workspace"]["root"] == "."
    assert manifest["workspace"]["config_path"] == "config.yaml"
    assert manifest["flows"][0]["path"] == "flows/main.yaml"
    assert "mercury-flow-inspect" not in str(manifest)
    assert "run_flow_files" in manifest["agent_handoff"]["mcp_tools"]

    unified_yaml = run_mercury_flow(
        flow_yaml="""name: Unified
---
- emitReport:
    title: "Unified ${month}"
""",
        dry_run=True,
        env={"month": "2026-10"},
    )
    assert unified_yaml["entrypoint"] == "run_mercury_flow"
    assert unified_yaml["input_mode"] == "flow_yaml"
    assert unified_yaml["artifacts"][0]["title"] == "Unified 2026-10"

    unified_files = run_mercury_flow(
        flow_files={
            "flows/a.yaml": """name: A
tags: [accounting]
---
- emitReport:
    title: "A"
""",
            "flows/disabled.yaml": """name: Disabled
tags: [disabled]
---
- emitReport:
    title: "Disabled"
""",
        },
        config_yaml="flows: flows/**/*.yaml\nincludeTags: [accounting]\nexcludeTags: [disabled]\n",
        dry_run=True,
    )
    assert unified_files["entrypoint"] == "run_mercury_flow"
    assert unified_files["input_mode"] == "flow_files"
    assert unified_files["selected_count"] == 1

    invalid_unified = run_mercury_flow(flow_yaml="name: A\n---\n- emitReport: {}", flow_files={})
    assert invalid_unified["status"] == "error"
    assert "exactly one" in invalid_unified["message"]


def test_mcp_workspace_flow_tools_use_client_token(monkeypatch) -> None:
    from mercury_tools.config import Settings
    from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
    from mercury_tools.mcp import server

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    token = create_client_token(
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
        now=1783536613,
    )

    class FakeStore:
        recorded: list[dict] = []
        flow = {
            "flow_id": "workspace-company-health-12345678",
            "title": "Company Health Check",
            "name": "Company Health Check",
            "status": "draft",
            "command_count": 3,
            "tags": ["accounting"],
            "yaml": COMPANY_HEALTH_TEMPLATE,
        }

        def dashboard(self, _token_payload):
            return {
                "workspace": {"name": "Demo Co", "workspace_key": "demo"},
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "ready",
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
                        },
                    }
                ],
                "flows": [self.flow],
            }

        def get_flow(self, *, token_payload, flow_id):
            return self.flow if flow_id == self.flow["flow_id"] else None

        def save_flow(self, *, token_payload, title, flow_yaml, metadata):
            return self.flow

        def record_flow_run(
            self, *, token_payload, flow_id, title, result_payload, dry_run, env_keys
        ):
            row = {
                "run_id": "flow_run_1",
                "flow_id": flow_id,
                "title": title,
                "status": result_payload["status"],
                "dry_run": dry_run,
                "env_keys": env_keys,
            }
            self.recorded.append(row)
            return row

    audit_events: list[dict] = []

    def fake_audit(tool_name, input_payload, output_summary):
        audit_events.append(
            {
                "tool_name": tool_name,
                "input_payload": input_payload,
                "output_summary": output_summary,
            }
        )

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())
    monkeypatch.setattr(server, "_audit", fake_audit)

    listed = server.list_workspace_flows(token)
    saved = server.save_workspace_flow_tool(
        token,
        "Company Health Check",
        COMPANY_HEALTH_TEMPLATE,
        metadata={"source": "test"},
    )
    ran = server.run_workspace_flow_tool(
        token,
        "workspace-company-health-12345678",
        dry_run=True,
        env={"connector": "flowaccount", "environment": "production"},
    )

    assert listed["status"] == "ok"
    assert listed["flow_count"] == 1
    assert "yaml" not in listed["flows"][0]
    assert saved["status"] == "ok"
    assert saved["flow"]["flow_id"] == "workspace-company-health-12345678"
    assert "yaml" not in saved["flow"]
    assert ran["status"] == "planned"
    assert ran["workspace_flow"]["flow_id"] == "workspace-company-health-12345678"
    assert ran["steps"][0]["command"] == "connectorStatus"
    assert ran["variables"]["env"]["connector"] == "flowaccount"
    assert ran["variables"]["env"]["environment"] == "production"
    assert ran["run_record"]["env_keys"] == ["connector", "environment"]
    assert audit_events
    assert all(token not in str(event["input_payload"]) for event in audit_events)
    assert all("client_token_hash" in event["input_payload"] for event in audit_events)
