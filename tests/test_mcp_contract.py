import asyncio
import inspect

import pytest

from mercury_tools.prompts import get_prompt
from mercury_tools.rag.embeddings import HashEmbeddingProvider


def test_mcp_server_imports_and_exposes_server() -> None:
    from mercury_tools.mcp.server import mcp

    assert mcp.name == "Mercury Tools"


def test_local_mcp_is_a_separate_one_server_surface() -> None:
    from mercury_tools.mcp.local_server import local_mcp
    from mercury_tools.mcp.server import mcp

    assert local_mcp.name == "Mercury Finance"
    assert local_mcp is not mcp


def test_public_mcp_tools_have_submission_annotations() -> None:
    from mercury_tools.mcp.server import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    expected_annotations = {
        "search_knowledge": (True, False, None, False),
        "retrieve_context_pack": (True, False, None, False),
        "retrieve_workspace_context_pack": (True, False, None, False),
        "get_document": (True, False, None, False),
        "get_public_workspace": (True, False, None, False),
        "list_connectors": (True, False, None, False),
        "get_connector_setup": (True, False, None, False),
        "create_public_workspace": (False, False, False, False),
        "link_connector_profile": (False, False, False, False),
        "validate_connector_connection": (False, False, True, False),
        "connector_capabilities": (True, False, None, False),
        "connector_status": (True, False, None, False),
        "unlink_connector_profile": (False, True, True, False),
        "run_accounting_skill": (True, False, None, False),
        "flow_cheat_sheet": (True, False, None, False),
        "check_flow_syntax": (True, False, None, False),
        "inspect_flow_files": (True, False, None, False),
        "list_workspace_flows": (True, False, None, False),
        "run_inline_flow": (True, False, None, False),
        "run_flow_files": (True, False, None, False),
        "run_workspace_flow": (True, False, None, False),
        "save_workspace_flow": (False, False, True, False),
    }

    assert set(tools) == set(expected_annotations)
    for name, expected in expected_annotations.items():
        annotations = tools[name].annotations
        assert annotations is not None
        actual = (
            annotations.readOnlyHint,
            annotations.destructiveHint,
            annotations.idempotentHint,
            annotations.openWorldHint,
        )
        assert actual == expected


def test_public_storage_tools_reject_secret_bearing_inputs_before_persistence() -> None:
    from mercury_tools.mcp.server import (
        create_public_workspace,
        link_connector_profile,
        save_workspace_flow_tool,
    )

    workspace = create_public_workspace("client_secret=do-not-store-this-value")
    connector = link_connector_profile(
        "workspace-demo",
        "flowaccount",
        "api_driver",
        "production",
        company_name="private@example.com",
    )
    flow = save_workspace_flow_tool(
        "workspace-demo",
        "Demo",
        "name: Demo\n---\n- emitReport:\n    title: api_key=do-not-store-this-value\n",
    )

    for payload in (workspace, connector, flow):
        assert payload["status"] == "error"
        assert "Public Mercury storage does not accept" in payload["message"]
        assert "do-not-store-this-value" not in str(payload)
        assert "private@example.com" not in str(payload)


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
        run_inline_flow,
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
        workspace_id="mw_publiccontestworkspace001",
        flow_files=[
            {
                "path": "main.yaml",
                "flow_yaml": """name: Main
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
            },
            {
                "path": "sub.yaml",
                "flow_yaml": """name: Subflow
tags: [helper]
---
- emitReport:
    title: "Sub ${subMonth}"
""",
            },
        ],
        dry_run=True,
        environment=[{"name": "month", "value": "2026-10"}],
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
        workspace_id="mw_publiccontestworkspace001",
        flow_files=[
            {
                "path": "flows/b.yaml",
                "flow_yaml": """name: B
tags: [accounting]
---
- emitReport:
    title: "B"
""",
            },
            {
                "path": "flows/a.yaml",
                "flow_yaml": """name: A
tags: [accounting]
---
- emitReport:
    title: "A"
""",
            },
        ],
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
        [
            {
                "path": "flows/main.yaml",
                "flow_yaml": """name: Main
tags: [accounting, smoke]
---
- emitReport:
    title: "Main"
""",
            },
            {
                "path": "flows/wip.yaml",
                "flow_yaml": """name: WIP
tags: [disabled]
---
- emitReport:
    title: "WIP"
""",
            },
        ],
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

    inline = run_inline_flow(
        workspace_id="mw_publiccontestworkspace001",
        flow_yaml="""name: Explicit Inline
---
- emitReport:
    title: "Inline ${month}"
""",
        environment=[{"name": "month", "value": "2026-10"}],
        dry_run=True,
    )
    assert inline["status"] == "planned"
    assert inline["artifacts"][0]["title"] == "Inline 2026-10"

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


def test_mcp_workspace_flow_tools_use_public_workspace_id(monkeypatch) -> None:
    from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
    from mercury_tools.mcp import server

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    workspace_id = "mw_publiccontestworkspace001"

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

        def public_dashboard(self, supplied_workspace_id):
            assert supplied_workspace_id == workspace_id
            return {
                "workspace": {"name": "Demo Co", "workspace_key": "demo"},
                "connector_profiles": [
                        {
                            "connector_id": "flowaccount",
                            "connection_mode": "api_driver",
                            "environment": "production",
                            "status": "ready_read_only",
                            "capability_states": {"company.info.read": "observed"},
                            "evidence_source": "api_driver_safe_probe",
                            "validated_at": "2026-07-19T12:00:00+00:00",
                            "metadata": {
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

    listed = server.list_workspace_flows(workspace_id)
    saved = server.save_workspace_flow_tool(
        workspace_id,
        "Company Health Check",
        COMPANY_HEALTH_TEMPLATE,
        metadata={"source": "test"},
    )
    ran = server.run_workspace_flow_tool(
        workspace_id,
        "workspace-company-health-12345678",
        dry_run=True,
        environment=[
            {"name": "connector", "value": "flowaccount"},
            {"name": "environment", "value": "production"},
        ],
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
    assert all(workspace_id not in str(event["input_payload"]) for event in audit_events)
    assert all("workspace_id_hash" in event["input_payload"] for event in audit_events)


@pytest.mark.asyncio
async def test_public_mcp_tool_schemas_use_workspace_id() -> None:
    from mercury_tools.mcp.server import mcp

    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert "create_public_workspace" in tools
    assert "get_public_workspace" in tools
    assert "workspace_connector_status" not in tools
    assert "start_connector_setup" not in tools
    for name in {
        "retrieve_workspace_context_pack",
        "link_connector_profile",
        "validate_connector_connection",
        "connector_status",
        "connector_capabilities",
        "unlink_connector_profile",
        "list_workspace_flows",
        "run_inline_flow",
        "run_flow_files",
        "run_workspace_flow",
        "save_workspace_flow",
    }:
        properties = tools[name].inputSchema["properties"]
        assert "workspace_id" in properties
        assert "client_token" not in properties

    for name in {"run_inline_flow", "run_flow_files", "run_workspace_flow"}:
        assert "workspace_id" in tools[name].inputSchema["required"]

    for tool in tools.values():
        assert "client_token" not in tool.inputSchema.get("properties", {}), tool.name


@pytest.mark.asyncio
async def test_public_mcp_tool_schemas_are_explicit_for_plugin_review() -> None:
    from mercury_tools.mcp.server import mcp

    tools = {tool.name: tool for tool in await mcp.list_tools()}

    search_schema = tools["search_knowledge"].inputSchema
    assert search_schema["properties"]["mode"]["enum"] == [
        "hybrid",
        "keyword",
        "vector",
    ]
    search_filter_ref = search_schema["properties"]["filters"]["anyOf"][0]["$ref"]
    search_filter_schema = search_schema["$defs"][search_filter_ref.rsplit("/", 1)[-1]]
    assert search_filter_schema["additionalProperties"] is False
    assert {
        "jurisdiction",
        "connector",
        "doc_type",
        "review_status",
        "effective_date",
        "environment",
        "capability",
    } <= set(search_filter_schema["properties"])

    context_schema = tools["retrieve_context_pack"].inputSchema
    context_filter_ref = context_schema["properties"]["filters"]["anyOf"][0]["$ref"]
    assert context_filter_ref.rsplit("/", 1)[-1] in context_schema["$defs"]

    link_schema = tools["link_connector_profile"].inputSchema
    assert link_schema["properties"]["environment"]["enum"] == [
        "production",
        "sandbox",
        "uat",
        "local",
        "gateway",
        "user_supplied",
    ]

    status_schema = tools["connector_status"].inputSchema
    assert "workspace_id" in status_schema["required"]

    skill_schema = tools["run_accounting_skill"].inputSchema
    skill_input_ref = skill_schema["properties"]["inputs"]["$ref"]
    skill_input_schema = skill_schema["$defs"][skill_input_ref.rsplit("/", 1)[-1]]
    assert skill_input_schema["additionalProperties"] is False
    assert {"query", "connector_id", "environment", "period_start", "period_end"} <= set(
        skill_input_schema["properties"]
    )

    for tool_name in {"inspect_flow_files", "run_flow_files"}:
        flow_schema = tools[tool_name].inputSchema
        assert flow_schema["properties"]["flow_files"]["type"] == "array"
        assert "$ref" in flow_schema["properties"]["flow_files"]["items"]
        for field_name in {"include_tags", "exclude_tags"}:
            tag_schema = flow_schema["properties"][field_name]
            assert tag_schema["type"] == "array"
            assert tag_schema["maxItems"] == 100
            assert tag_schema["items"] == {"maxLength": 100, "minLength": 1, "type": "string"}

    run_schemas = {
        "run_inline_flow": tools["run_inline_flow"].inputSchema,
        "run_flow_files": tools["run_flow_files"].inputSchema,
        "run_workspace_flow": tools["run_workspace_flow"].inputSchema,
    }
    assert set(run_schemas["run_inline_flow"]["properties"]) == {
        "workspace_id",
        "flow_yaml",
        "environment",
        "dry_run",
    }
    assert set(run_schemas["run_flow_files"]["properties"]) == {
        "workspace_id",
        "flow_files",
        "config_yaml",
        "environment",
        "include_tags",
        "exclude_tags",
        "continue_on_failure",
        "dry_run",
    }
    assert set(run_schemas["run_workspace_flow"]["properties"]) == {
        "workspace_id",
        "flow_id",
        "environment",
        "dry_run",
    }
    for run_schema in run_schemas.values():
        assert "env" not in run_schema["properties"]
        environment_schema = run_schema["properties"]["environment"]
        assert environment_schema["default"] == []
        assert environment_schema["type"] == "array"
        assert environment_schema["maxItems"] == 100
        item_schema = environment_schema["items"]
        assert item_schema["additionalProperties"] is False
        assert item_schema["required"] == ["name", "value"]
        assert item_schema["type"] == "object"
        assert set(item_schema["properties"]) == {"name", "value"}
        assert item_schema["properties"]["name"] == {
            "pattern": "^[A-Za-z][A-Za-z0-9_]{0,99}$",
            "title": "Name",
            "type": "string",
        }
        assert item_schema["properties"]["value"] == {
            "maxLength": 10000,
            "title": "Value",
            "type": "string",
        }
        assert "additionalProperties" not in environment_schema

    assert "run_flow" not in tools
    assert "run_mercury_flow" not in tools

    def assert_no_untyped_object(schema: object) -> None:
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert "properties" in schema, schema
            for value in schema.values():
                assert_no_untyped_object(value)
        elif isinstance(schema, list):
            for value in schema:
                assert_no_untyped_object(value)

    for name in {"inspect_flow_files", *run_schemas, "save_workspace_flow"}:
        assert_no_untyped_object(tools[name].inputSchema)

    save_schema = tools["save_workspace_flow"].inputSchema
    metadata_ref = save_schema["properties"]["metadata"]["anyOf"][0]["$ref"]
    metadata_schema = save_schema["$defs"][metadata_ref.rsplit("/", 1)[-1]]
    assert metadata_schema["title"] == "WorkspaceFlowMetadata"
    assert metadata_schema["additionalProperties"] is False
    assert {"source", "connector_id", "environment", "required_capabilities"} <= set(
        metadata_schema["properties"]
    )


@pytest.mark.asyncio
async def test_public_connector_lifecycle_contract_is_exact_and_secretless() -> None:
    from mercury_tools.mcp import server
    from mercury_tools.mcp.schemas import ConnectorValidationEvidence

    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    expected = {
        "list_connectors",
        "get_connector_setup",
        "link_connector_profile",
        "validate_connector_connection",
        "connector_status",
        "connector_capabilities",
        "unlink_connector_profile",
    }
    assert expected <= set(tools)
    assert "start_connector_setup" not in tools
    connector_tool_names = {
        name for name in tools if "connector" in name or name == "list_connectors"
    }
    assert connector_tool_names == expected

    expected_parameters = {
        "get_connector_setup": ("connector_id", "connection_mode"),
        "link_connector_profile": (
            "workspace_id",
            "connector_id",
            "connection_mode",
            "environment",
            "company_ref",
            "company_name",
            "external_server_name",
        ),
        "validate_connector_connection": (
            "workspace_id",
            "connector_id",
            "connection_mode",
            "environment",
            "evidence",
        ),
        "connector_status": ("workspace_id", "connector_id"),
        "connector_capabilities": (
            "workspace_id",
            "connector_id",
            "connection_mode",
            "environment",
        ),
        "unlink_connector_profile": (
            "workspace_id",
            "connector_id",
            "connection_mode",
            "environment",
            "confirm",
        ),
    }
    for name, parameters in expected_parameters.items():
        assert tuple(inspect.signature(getattr(server, name)).parameters) == parameters

    assert (
        inspect.signature(server.get_connector_setup).parameters["connection_mode"].default
        is None
    )
    assert (
        inspect.signature(server.unlink_connector_profile).parameters["confirm"].default
        == "unlink"
    )
    confirm_schema = tools["unlink_connector_profile"].inputSchema["properties"]["confirm"]
    assert confirm_schema.get("const") == "unlink" or confirm_schema.get("enum") == ["unlink"]

    evidence_schema = tools["validate_connector_connection"].inputSchema["properties"][
        "evidence"
    ]
    expected_evidence_schema = ConnectorValidationEvidence.model_json_schema()
    expected_capability_schemas = expected_evidence_schema.pop("$defs")
    assert evidence_schema == expected_evidence_schema
    assert tools["validate_connector_connection"].inputSchema["$defs"][
        "CapabilityObservation"
    ] == expected_capability_schemas["CapabilityObservation"]

    forbidden = {
        "client_id",
        "client_secret",
        "api_key",
        "access_token",
        "authorization",
        "credentials",
        "metadata",
        "response_body",
        "lan_address",
    }

    def assert_strict(schema: object) -> None:
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if isinstance(properties, dict):
                assert not (set(properties) & forbidden)
            if schema.get("type") == "object" and properties is None:
                pytest.fail(f"unconstrained object schema: {schema}")
            for value in schema.values():
                assert_strict(value)
        elif isinstance(schema, list):
            for item in schema:
                assert_strict(item)

    for name in expected:
        assert_strict(tools[name].inputSchema)

    for name in expected - {"list_connectors", "get_connector_setup"}:
        assert "workspace_id" in tools[name].inputSchema["required"]


@pytest.mark.asyncio
async def test_run_inline_flow_schema_executes_through_fastmcp() -> None:
    from mercury_tools.mcp.server import mcp

    _content, structured = await mcp.call_tool(
        "run_inline_flow",
        {
            "workspace_id": "mw_publiccontestworkspace001",
            "flow_yaml": (
                "name: Typed schema smoke\n"
                "---\n"
                "- emitReport:\n"
                "    title: Typed schema smoke ${month}\n"
            ),
            "environment": [{"name": "month", "value": "2026-10"}],
            "dry_run": True,
        },
    )

    assert structured["status"] == "planned"
    assert structured["artifacts"][0]["title"] == "Typed schema smoke 2026-10"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "tool_arguments"),
    [
        (
            "run_inline_flow",
            {
                "workspace_id": "mw_publiccontestworkspace001",
                "flow_yaml": "name: Secret boundary\n---\n- emitReport: {}\n",
            },
        ),
        (
            "run_flow_files",
            {
                "workspace_id": "mw_publiccontestworkspace001",
                "flow_files": [
                    {
                        "path": "flows/secret-boundary.yaml",
                        "flow_yaml": "name: Secret boundary\n---\n- emitReport: {}\n",
                    }
                ],
            },
        ),
        (
            "run_workspace_flow",
            {
                "workspace_id": "mw_publiccontestworkspace001",
                "flow_id": "workspace-secret-boundary",
            },
        ),
    ],
)
@pytest.mark.parametrize(
    ("environment", "marker"),
    [
        (
            [{"name": "client_secret_marker", "value": "2026-10"}],
            "client_secret_marker",
        ),
        (
            [{"name": "month", "value": "Bearer hosted_flow_secret_marker_value"}],
            "hosted_flow_secret_marker_value",
        ),
        (
            [
                {
                    "name": "month",
                    "value": "2026-10",
                    "client_secret": "hosted_flow_extra_marker_value",
                }
            ],
            "hosted_flow_extra_marker_value",
        ),
    ],
)
async def test_hosted_flow_environment_rejection_is_sanitized_before_parse_and_audit(
    monkeypatch,
    tool_name,
    tool_arguments,
    environment,
    marker,
) -> None:
    from mercury_tools.mcp import server

    audit_events: list[dict[str, object]] = []

    def fake_audit(
        tool_name: str,
        input_payload: dict[str, object],
        output_summary: dict[str, object],
    ) -> None:
        audit_events.append(
            {
                "tool_name": tool_name,
                "input_payload": input_payload,
                "output_summary": output_summary,
            }
        )

    def fail_if_parsed(*_args, **_kwargs):
        pytest.fail("invalid hosted environment reached flow parsing")

    monkeypatch.setattr(server, "_audit", fake_audit)
    monkeypatch.setattr(server, "parse_flow_text", fail_if_parsed)

    content, structured = await server.mcp.call_tool(
        tool_name,
        {
            **tool_arguments,
            "environment": environment,
            "dry_run": True,
        },
    )

    assert structured == {
        "status": "error",
        "message": "Hosted flow environment is invalid.",
        "dry_run": True,
    }
    assert len(audit_events) == 1
    audit_event = audit_events[0]
    assert audit_event["tool_name"] == tool_name
    assert audit_event["input_payload"]["workspace_id_prefix"] == "mw_pub"
    assert set(audit_event["input_payload"]) == {
        "workspace_id_prefix",
        "workspace_id_hash",
        "dry_run",
        "environment_status",
    }
    assert audit_event["input_payload"]["dry_run"] is True
    assert audit_event["input_payload"]["environment_status"] == "invalid"
    assert audit_event["output_summary"] == {
        "status": "error",
        "reason": "invalid_environment",
        "dry_run": True,
    }
    assert marker not in str((content, structured, audit_events))
    assert "input_value" not in str(content)


@pytest.mark.asyncio
async def test_validate_connector_connection_sanitizes_invalid_evidence_through_fastmcp(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    audit_events: list[dict[str, object]] = []

    def fake_audit(
        tool_name: str,
        input_payload: dict[str, object],
        output_summary: dict[str, object],
    ) -> None:
        audit_events.append(
            {
                "tool_name": tool_name,
                "input_payload": input_payload,
                "output_summary": output_summary,
            }
        )

    marker = "provider_body_marker_must_not_leak_from_fastmcp_1234"
    monkeypatch.setattr(server, "_audit", fake_audit)

    content, structured = await server.mcp.call_tool(
        "validate_connector_connection",
        {
            "workspace_id": "mw_publiccontestworkspace001",
            "connector_id": "flowaccount",
            "connection_mode": "native_mcp",
            "environment": "production",
            "evidence": {
                "source": "native_mcp_safe_read",
                "status": "succeeded",
                "observed_at": "2026-07-19T12:00:00Z",
                "evidence_ref": "evidence_fastmcp_input_1234",
                "capabilities": [
                    {"capability": "company.info.read", "state": "observed"}
                ],
                "provider_body": marker,
            },
        },
    )

    assert structured == {
        "status": "error",
        "message": "Connector validation evidence is invalid.",
    }
    assert marker not in str((content, structured))
    assert "input_value" not in str((content, structured))
    assert len(audit_events) == 1
    assert audit_events[0]["tool_name"] == "validate_connector_connection"
    assert audit_events[0]["output_summary"] == structured
    assert marker not in str(audit_events)
    assert "input_value" not in str(audit_events)
