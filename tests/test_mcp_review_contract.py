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
    schema: object | None = None,
    annotations: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        inputSchema=(
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            }
            if schema is None
            else schema
        ),
        annotations=annotations or _annotations(),
    )


def _issues(tool: SimpleNamespace) -> list[str]:
    return _review_module().review_tools([tool])


def _assert_issue(
    tool: SimpleNamespace,
    path: str,
    message: str | None = None,
) -> list[str]:
    issues = _issues(tool)
    prefix = f"{tool.name}.{path}:"
    matches = [issue for issue in issues if issue.startswith(prefix)]
    assert matches, issues
    if message is not None:
        assert any(message in issue for issue in matches), matches
    return issues


def _strict_root(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


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
    "schema",
    [
        {},
        {"title": "Unconstrained input"},
        {"type": "string"},
        {
            "type": ["object", "null"],
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        [],
    ],
)
def test_review_rejects_empty_nonobject_or_nonexclusive_object_roots(
    schema: object,
) -> None:
    _assert_issue(
        _tool(schema=schema),
        "<root>",
        "root inputSchema must resolve to a strict object",
    )


def test_review_rejects_root_objects_that_allow_extra_keys() -> None:
    _assert_issue(
        _tool(
            schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            }
        ),
        "<root>",
        "additionalProperties=false",
    )


def test_review_rejects_empty_nested_property_schemas() -> None:
    _assert_issue(
        _tool(schema=_strict_root({"filters": {}})),
        "filters",
        "concrete type or enum/const",
    )


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


@pytest.mark.parametrize("definitions_key", ["$defs", "definitions"])
def test_review_resolves_strict_root_refs(definitions_key: str) -> None:
    schema = {
        "$ref": f"#/{definitions_key}/Input",
        definitions_key: {
            "Input": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            }
        },
    }

    assert _issues(_tool(schema=schema)) == []


@pytest.mark.parametrize("definitions_key", ["$defs", "definitions"])
def test_review_rejects_unconstrained_nested_refs_at_the_use_path(
    definitions_key: str,
) -> None:
    schema = _strict_root(
        {"filters": {"$ref": f"#/{definitions_key}/LooseFilter"}}
    )
    schema[definitions_key] = {"LooseFilter": {}}

    _assert_issue(
        _tool(schema=schema),
        "filters",
        "concrete type or enum/const",
    )


def test_review_rejects_cyclic_local_refs_with_an_actionable_use_path() -> None:
    schema = _strict_root({"filters": {"$ref": "#/$defs/First"}})
    schema["$defs"] = {
        "First": {"$ref": "#/$defs/Second"},
        "Second": {"$ref": "#/$defs/First"},
    }

    _assert_issue(_tool(schema=schema), "filters.$ref", "cyclic local $ref")


def test_review_caps_local_ref_depth_with_an_actionable_use_path() -> None:
    definitions = {
        f"Level{index}": {"$ref": f"#/$defs/Level{index + 1}"}
        for index in range(33)
    }
    definitions["Level33"] = {"type": "string"}
    schema = _strict_root({"filters": {"$ref": "#/$defs/Level0"}})
    schema["$defs"] = definitions

    _assert_issue(_tool(schema=schema), "filters.$ref", "local $ref depth exceeds")


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_review_rejects_unconstrained_root_alternatives(keyword: str) -> None:
    schema = {
        keyword: [
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
            {},
        ]
    }

    _assert_issue(
        _tool(schema=schema),
        f"<root>.{keyword}[1]",
        "root inputSchema must resolve to a strict object",
    )


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_review_rejects_unconstrained_nested_alternatives(keyword: str) -> None:
    schema = _strict_root(
        {
            "mode": {
                keyword: [
                    {"type": "string", "enum": ["safe"]},
                    {},
                ]
            }
        }
    )

    _assert_issue(
        _tool(schema=schema),
        f"mode.{keyword}[1]",
        "concrete type or enum/const",
    )


def test_review_accepts_allof_constraints_combined_as_an_intersection() -> None:
    schema = {
        "allOf": [
            {"type": "object"},
            {
                "properties": {
                    "query": {
                        "allOf": [
                            {},
                            {"type": "string"},
                        ]
                    }
                },
                "required": ["query"],
            },
            {"additionalProperties": False},
        ]
    }

    assert _issues(_tool(schema=schema)) == []


def test_review_does_not_let_allof_wrappers_suppress_nested_review() -> None:
    schema = _strict_root(
        {
            "profile": {
                "allOf": [
                    {"type": "object"},
                    {"properties": {"display_name": {}}},
                    {"additionalProperties": False},
                ]
            }
        }
    )

    _assert_issue(
        _tool(schema=schema),
        "profile.allOf[1].display_name",
        "concrete type or enum/const",
    )


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


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_review_requires_environment_enum_in_every_scalar_alternative(
    keyword: str,
) -> None:
    schema = _strict_root(
        {
            "environment": {
                keyword: [
                    {"type": "string", "enum": ["sandbox", "production"]},
                    {"type": "null"},
                    {"type": "string"},
                ]
            }
        }
    )

    _assert_issue(
        _tool(schema=schema),
        f"environment.{keyword}[2]",
        "environment must expose an explicit enum",
    )


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


def test_review_rejects_unconstrained_array_item_ref_alternatives() -> None:
    schema = _strict_root({"tags": {"$ref": "#/$defs/Tags"}})
    schema["$defs"] = {
        "Tags": {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {},
                ]
            },
            "maxItems": 10,
        }
    }

    _assert_issue(
        _tool(schema=schema),
        "tags.items.anyOf[1]",
        "concrete type or enum/const",
    )


def test_review_accepts_array_guarantees_combined_through_allof() -> None:
    schema = _strict_root(
        {
            "tags": {
                "allOf": [
                    {"type": "array"},
                    {
                        "items": {
                            "allOf": [
                                {},
                                {"type": "string"},
                            ]
                        }
                    },
                    {"maxItems": 10},
                ]
            }
        }
    )

    assert _issues(_tool(schema=schema)) == []


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


def test_review_rejects_mutually_exclusive_sources_split_across_allof() -> None:
    schema = {
        "allOf": [
            {"type": "object"},
            {"properties": {"flow_yaml": {"type": "string"}}},
            {
                "properties": {
                    "flow_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    }
                }
            },
            {"additionalProperties": False},
        ]
    }

    _assert_issue(
        _tool(schema=schema),
        "<root>",
        "split mutually exclusive source fields into separate tools",
    )


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


def test_review_reports_the_exact_missing_annotation_path() -> None:
    annotations = _annotations()
    del annotations.openWorldHint

    _assert_issue(
        _tool(annotations=annotations),
        "annotations.openWorldHint",
        "required annotation is missing",
    )


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


def test_review_rejects_credential_names_reached_through_definitions_refs() -> None:
    schema = _strict_root({"profile": {"$ref": "#/definitions/Profile"}})
    schema["definitions"] = {
        "Profile": {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
            "required": ["api_key"],
            "additionalProperties": False,
        }
    }

    _assert_issue(
        _tool(schema=schema),
        "profile.api_key",
        "credential-bearing input field names are prohibited",
    )


def test_behavior_matrix_is_the_only_workspace_scope_registry() -> None:
    module = _review_module()

    assert not hasattr(module, "_WORKSPACE_SCOPED_TOOLS")
    assert module.BEHAVIOR_MATRIX
    for tool_name, behavior in module.BEHAVIOR_MATRIX.items():
        assert isinstance(behavior.requires_workspace, bool), tool_name


def test_future_workspace_matrix_entry_requires_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _review_module()
    behavior_type = getattr(module, "ToolBehavior", None)
    assert behavior_type is not None, "BEHAVIOR_MATRIX needs mandatory workspace metadata"
    monkeypatch.setitem(
        module.BEHAVIOR_MATRIX,
        "future_workspace_tool",
        behavior_type(
            read_only=True,
            destructive=False,
            idempotent=None,
            open_world=False,
            requires_workspace=True,
        ),
    )
    tool = _tool(
        name="future_workspace_tool",
        schema=_strict_root({"query": {"type": "string"}}),
    )

    issues = module.review_tools([tool])

    assert any(
        issue.startswith("future_workspace_tool.workspace_id:")
        and "must require workspace_id" in issue
        for issue in issues
    ), issues


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
