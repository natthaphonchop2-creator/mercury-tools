from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_mcp_contract.py"
SUCCESS = "Mercury MCP review: 0 unclear arguments; annotations verified"


def _review_module() -> ModuleType:
    assert SCRIPT.is_file(), f"MCP review linter is missing: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("review_mcp_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _annotations(
    *,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool | None = None,
    open_world: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


def _tool(
    name: str = "search_knowledge",
    *,
    schema: dict | None = None,
    annotations: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        inputSchema=schema
        or {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        annotations=annotations or _annotations(),
    )


def _issues(tool: SimpleNamespace) -> list[str]:
    return _review_module().review_tools([tool])


def test_hosted_mcp_review_passes_with_exact_success_output() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{SUCCESS}\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("nested_schema", "expected"),
    [
        (
            {"type": "object", "properties": {}, "additionalProperties": False},
            "search_knowledge.filters",
        ),
        (
            {"type": "object", "properties": {"status": {"type": "string"}}},
            "search_knowledge.filters",
        ),
    ],
)
def test_review_rejects_unnamed_or_open_objects(
    nested_schema: dict,
    expected: str,
) -> None:
    schema = {
        "type": "object",
        "properties": {"filters": nested_schema},
        "required": ["filters"],
        "additionalProperties": False,
    }

    issues = _issues(_tool(schema=schema))

    assert any(expected in issue for issue in issues), issues


def test_review_allows_a_strict_no_argument_root_schema() -> None:
    issues = _issues(
        _tool(
            name="list_connectors",
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
    )

    assert issues == []


def test_review_rejects_an_empty_root_with_phantom_required_arguments() -> None:
    issues = _issues(
        _tool(
            name="list_connectors",
            schema={
                "type": "object",
                "properties": {},
                "required": ["query"],
                "additionalProperties": False,
            },
        )
    )

    assert any("list_connectors.<root>" in issue for issue in issues), issues


def test_review_requires_workspace_id_on_workspace_scoped_tools() -> None:
    issues = _issues(
        _tool(
            name="connector_status",
            schema={
                "type": "object",
                "properties": {"connector_id": {"type": "string"}},
                "additionalProperties": False,
            },
        )
    )

    assert any("connector_status.workspace_id" in issue for issue in issues), issues


def test_review_requires_scalar_environment_enums() -> None:
    issues = _issues(
        _tool(
            name="link_connector_profile",
            schema={
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string"},
                    "environment": {"type": "string"},
                },
                "required": ["workspace_id", "environment"],
                "additionalProperties": False,
            },
            annotations=_annotations(read_only=False, idempotent=False),
        )
    )

    assert any("link_connector_profile.environment" in issue for issue in issues), issues


@pytest.mark.parametrize(
    "array_schema",
    [
        {"type": "array", "maxItems": 10},
        {"type": "array", "items": {"type": "string"}},
    ],
)
def test_review_requires_typed_bounded_lists(array_schema: dict) -> None:
    schema = {
        "type": "object",
        "properties": {"tags": array_schema},
        "required": ["tags"],
        "additionalProperties": False,
    }

    issues = _issues(_tool(schema=schema))

    assert any("search_knowledge.tags" in issue for issue in issues), issues


def test_review_rejects_mutually_exclusive_top_level_source_fields() -> None:
    schema = {
        "type": "object",
        "properties": {
            "workspace_id": {"type": "string"},
            "flow_yaml": {"type": "string"},
            "flow_files": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 10,
            },
        },
        "required": ["workspace_id"],
        "additionalProperties": False,
    }

    issues = _issues(_tool(name="run_inline_flow", schema=schema))

    assert any("run_inline_flow.<root>" in issue for issue in issues), issues


@pytest.mark.parametrize(
    "annotations",
    [
        None,
        _annotations(read_only=False),
        _annotations(destructive=True),
        _annotations(open_world=True),
    ],
)
def test_review_rejects_missing_or_incorrect_behavior_annotations(
    annotations: SimpleNamespace | None,
) -> None:
    tool = _tool()
    tool.annotations = annotations

    issues = _issues(tool)

    assert any("search_knowledge.annotations" in issue for issue in issues), issues


def test_review_rejects_nested_credential_bearing_field_names() -> None:
    schema = {
        "type": "object",
        "properties": {
            "profile": {
                "type": "object",
                "properties": {"client_secret": {"type": "string"}},
                "required": ["client_secret"],
                "additionalProperties": False,
            }
        },
        "required": ["profile"],
        "additionalProperties": False,
    }

    issues = _issues(_tool(schema=schema))

    assert any("search_knowledge.profile.client_secret" in issue for issue in issues), issues


@pytest.mark.asyncio
async def test_hosted_tool_descriptions_disclose_changes_contacts_and_omissions() -> None:
    from mercury_tools.mcp.server import mcp

    tools = await mcp.list_tools()

    assert tools
    for tool in tools:
        assert "Changes:" in tool.description, tool.name
        assert "External contact:" in tool.description, tool.name
        assert "Omitted options:" in tool.description, tool.name


@pytest.mark.asyncio
async def test_hosted_unknown_root_fields_are_rejected_without_echoing_values() -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    from mercury_tools.mcp.server import mcp

    marker = "task10-unknown-field-secret-marker"
    with pytest.raises(ToolError) as exc_info:
        await mcp.call_tool("list_connectors", {"client_secret": marker})

    assert "extra_forbidden" in str(exc_info.value)
    assert marker not in str(exc_info.value)
