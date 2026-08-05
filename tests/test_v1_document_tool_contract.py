from __future__ import annotations

from collections.abc import Mapping

import pytest

DOCUMENT_TOOL_NAMES = {
    "prepare_document_create",
    "render_document_preview",
    "confirm_document_create",
    "get_operation_status",
}


def _resolve(schema: Mapping[str, object], value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    reference = value.get("$ref")
    if reference is None:
        return value
    assert isinstance(reference, str) and reference.startswith("#/$defs/")
    definitions = schema.get("$defs")
    assert isinstance(definitions, Mapping)
    resolved = definitions.get(reference.rsplit("/", 1)[-1])
    assert isinstance(resolved, Mapping)
    return resolved


def _property(schema: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        value = properties.get(name)
        if isinstance(value, Mapping):
            return value
    for keyword in ("oneOf", "anyOf", "allOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            for variant in variants:
                found = _property(_resolve(schema, variant), name)
                if found is not None:
                    return found
    definitions = schema.get("$defs")
    if isinstance(definitions, Mapping):
        for definition in definitions.values():
            if isinstance(definition, Mapping):
                found = _property(definition, name)
                if found is not None:
                    return found
    return None


@pytest.mark.asyncio
async def test_v1_publishes_the_complete_document_operation_surface() -> None:
    """Removing any lifecycle tool must break the user-visible V1 workflow."""

    from mercury_tools.mcp.contracts import V1_HOSTED_TOOL_NAMES
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 documents")
    configure_v1_tools(server, enabled=True)

    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) >= DOCUMENT_TOOL_NAMES
    assert DOCUMENT_TOOL_NAMES <= V1_HOSTED_TOOL_NAMES


@pytest.mark.asyncio
async def test_preview_confirmation_and_status_arguments_are_unambiguous() -> None:
    """Broad objects or optional routing IDs would recreate the plugin warning."""

    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 document schemas")
    configure_v1_tools(server, enabled=True)
    tools = {tool.name: tool for tool in await server.list_tools()}

    expected_required = {
        "render_document_preview": {"workspace_id", "preview_id"},
        "confirm_document_create": {
            "workspace_id",
            "preview_id",
            "state_version",
            "confirmation",
        },
        "get_operation_status": {"workspace_id", "operation_id"},
    }
    for name, required in expected_required.items():
        schema = tools[name].inputSchema
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", ())) == required

    for name, identifier in (
        ("render_document_preview", "workspace_id"),
        ("render_document_preview", "preview_id"),
        ("confirm_document_create", "workspace_id"),
        ("confirm_document_create", "preview_id"),
        ("get_operation_status", "workspace_id"),
        ("get_operation_status", "operation_id"),
    ):
        field = _property(tools[name].inputSchema, identifier)
        assert field is not None
        assert field.get("format") == "uuid"

    confirmation = _property(tools["confirm_document_create"].inputSchema, "confirmation")
    assert confirmation is not None
    assert confirmation.get("const") == "CONFIRM_CREATE"
    state_version = _property(tools["confirm_document_create"].inputSchema, "state_version")
    assert state_version is not None
    assert state_version.get("minimum") == 1

    confirm_names = set(tools["confirm_document_create"].inputSchema["properties"])
    assert confirm_names == expected_required["confirm_document_create"]
    assert not confirm_names & {
        "document",
        "documents",
        "payload",
        "provider_arguments",
        "provider_arguments_json",
    }


@pytest.mark.asyncio
async def test_document_tool_annotations_match_external_effects() -> None:
    """A provider create must never be advertised as a harmless local read."""

    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 document annotations")
    configure_v1_tools(server, enabled=True)
    tools = {tool.name: tool for tool in await server.list_tools()}

    expected = {
        "prepare_document_create": (False, False, True, False),
        "render_document_preview": (True, False, None, False),
        "confirm_document_create": (False, True, True, True),
        "get_operation_status": (False, False, False, False),
    }
    for name, values in expected.items():
        annotations = tools[name].annotations
        assert annotations is not None
        assert (
            annotations.readOnlyHint,
            annotations.destructiveHint,
            annotations.idempotentHint,
            annotations.openWorldHint,
        ) == values
