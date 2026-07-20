"""Review the hosted Mercury MCP contract for marketplace submission clarity."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any

SUCCESS_MESSAGE = "Mercury MCP review: 0 unclear arguments; annotations verified"

_CLOSED_READ = (True, False, None, False)
_CLOSED_CREATE = (False, False, False, False)
_CLOSED_IDEMPOTENT_WRITE = (False, False, True, False)
_CLOSED_DESTRUCTIVE_IDEMPOTENT = (False, True, True, False)

BEHAVIOR_MATRIX: dict[str, tuple[bool, bool, bool | None, bool]] = {
    "search_knowledge": _CLOSED_READ,
    "retrieve_context_pack": _CLOSED_READ,
    "retrieve_workspace_context_pack": _CLOSED_READ,
    "get_document": _CLOSED_READ,
    "create_public_workspace": _CLOSED_CREATE,
    "get_public_workspace": _CLOSED_READ,
    "list_connectors": _CLOSED_READ,
    "get_connector_setup": _CLOSED_READ,
    "link_connector_profile": _CLOSED_CREATE,
    "validate_connector_connection": _CLOSED_IDEMPOTENT_WRITE,
    "connector_capabilities": _CLOSED_READ,
    "unlink_connector_profile": _CLOSED_DESTRUCTIVE_IDEMPOTENT,
    "connector_status": _CLOSED_READ,
    "list_accounting_skills": _CLOSED_READ,
    "get_accounting_skill_schema": _CLOSED_READ,
    "run_accounting_skill": _CLOSED_READ,
    "flow_cheat_sheet": _CLOSED_READ,
    "check_flow_syntax": _CLOSED_READ,
    "inspect_flow_files": _CLOSED_READ,
    "run_inline_flow": _CLOSED_READ,
    "run_flow_files": _CLOSED_READ,
    "list_workspace_flows": _CLOSED_READ,
    "run_workspace_flow": _CLOSED_READ,
    "save_workspace_flow": _CLOSED_IDEMPOTENT_WRITE,
}

_WORKSPACE_SCOPED_TOOLS = frozenset(
    {
        "retrieve_workspace_context_pack",
        "get_public_workspace",
        "link_connector_profile",
        "validate_connector_connection",
        "connector_capabilities",
        "unlink_connector_profile",
        "connector_status",
        "run_accounting_skill",
        "run_inline_flow",
        "run_flow_files",
        "list_workspace_flows",
        "run_workspace_flow",
        "save_workspace_flow",
    }
)
_MUTUALLY_EXCLUSIVE_SOURCE_FIELDS = frozenset(
    {"flow_yaml", "flow_files", "workspace_flow_id"}
)
_CREDENTIAL_FIELD_RE = re.compile(
    r"(?:api_?key|authorization|bearer|client_?secret|cookies?|credentials?|"
    r"password|private_?key|secret|service_?role_?key|tokens?)",
    re.IGNORECASE,
)
_ANNOTATION_FIELDS = (
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)
_MISSING = object()


def _value(source: object, name: str, default: object = _MISSING) -> object:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _argument_path(tool_name: str, path: tuple[str, ...]) -> str:
    return f"{tool_name}.{'.'.join(path) if path else '<root>'}"


def _resolve_ref(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any] | None:
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
        return None
    target: object = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or part not in target:
            return None
        target = target[part]
    return target if isinstance(target, Mapping) else None


def _schema_types(
    schema: object,
    root: Mapping[str, Any],
    seen_refs: frozenset[str] = frozenset(),
) -> set[str]:
    if not isinstance(schema, Mapping):
        return set()
    schema_type = schema.get("type")
    types = {schema_type} if isinstance(schema_type, str) else set()
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in seen_refs:
        resolved = _resolve_ref(schema, root)
        if resolved is not None:
            types.update(_schema_types(resolved, root, seen_refs | {reference}))
    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            for variant in variants:
                types.update(_schema_types(variant, root, seen_refs))
    return types


def _has_explicit_enum(
    schema: object,
    root: Mapping[str, Any],
    seen_refs: frozenset[str] = frozenset(),
) -> bool:
    if not isinstance(schema, Mapping):
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and bool(enum):
        return True
    if "const" in schema:
        return True
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in seen_refs:
        resolved = _resolve_ref(schema, root)
        if resolved is not None and _has_explicit_enum(
            resolved,
            root,
            seen_refs | {reference},
        ):
            return True
    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(keyword)
        if isinstance(variants, list) and any(
            _has_explicit_enum(variant, root, seen_refs) for variant in variants
        ):
            return True
    return False


def _has_typed_items(schema: object, root: Mapping[str, Any]) -> bool:
    if not isinstance(schema, Mapping) or not schema:
        return False
    if _schema_types(schema, root):
        return True
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return True
    return "const" in schema


def _schema_issues(tool_name: str, schema: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []

    def add(path: tuple[str, ...], message: str) -> None:
        issues.append(f"{_argument_path(tool_name, path)}: {message}")

    def visit(node: object, path: tuple[str, ...], *, root: bool = False) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, (*path, f"[{index}]"))
            return
        if not isinstance(node, Mapping):
            return

        is_object = node.get("type") == "object" or "properties" in node
        if is_object:
            properties = node.get("properties")
            strict_no_argument_root = root and not properties and not node.get("required")
            if not isinstance(properties, Mapping) or (
                not properties and not strict_no_argument_root
            ):
                add(path, "object must define named properties")
            if node.get("additionalProperties") is not False:
                add(path, "object must set additionalProperties=false")

        if node.get("type") == "array":
            if not _has_typed_items(node.get("items"), schema):
                add(path, "array must define a typed items schema")
            maximum = node.get("maxItems")
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
                add(path, "array must define a finite maxItems")

        properties = node.get("properties")
        if isinstance(properties, Mapping):
            for field_name, field_schema in properties.items():
                field_path = (*path, str(field_name))
                if _CREDENTIAL_FIELD_RE.search(str(field_name)):
                    add(field_path, "credential-bearing input field names are prohibited")
                if str(field_name) == "environment":
                    types = _schema_types(field_schema, schema)
                    if types != {"array"} and not _has_explicit_enum(field_schema, schema):
                        add(field_path, "environment must expose an explicit enum")
                visit(field_schema, field_path)

        for keyword, value in node.items():
            if keyword == "properties":
                continue
            if keyword == "$defs" and isinstance(value, Mapping):
                for definition_name, definition in value.items():
                    visit(definition, ("$defs", str(definition_name)))
                continue
            if keyword in {"anyOf", "oneOf", "allOf", "items"}:
                visit(value, (*path, keyword))

    visit(schema, (), root=True)

    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        source_fields = sorted(set(properties) & _MUTUALLY_EXCLUSIVE_SOURCE_FIELDS)
        if len(source_fields) > 1:
            add(
                (),
                "split mutually exclusive source fields into separate tools: "
                + ", ".join(source_fields),
            )

    if tool_name in _WORKSPACE_SCOPED_TOOLS:
        required = schema.get("required")
        if not isinstance(properties, Mapping) or "workspace_id" not in properties:
            add(("workspace_id",), "workspace-scoped tool must define workspace_id")
        if not isinstance(required, list) or "workspace_id" not in required:
            add(("workspace_id",), "workspace-scoped tool must require workspace_id")

    return issues


def _annotation_issues(tool: object) -> list[str]:
    tool_name = str(_value(tool, "name", "<unnamed>"))
    expected = BEHAVIOR_MATRIX.get(tool_name)
    if expected is None:
        return [
            f"{tool_name}.annotations: tool has no reviewed behavior-matrix entry"
        ]

    annotations = _value(tool, "annotations", None)
    if annotations is None:
        return [
            f"{tool_name}.annotations: required behavior annotations are missing; "
            f"expected {dict(zip(_ANNOTATION_FIELDS, expected, strict=True))}"
        ]

    issues: list[str] = []
    for field_name, expected_value in zip(_ANNOTATION_FIELDS, expected, strict=True):
        actual = _value(annotations, field_name)
        if actual is _MISSING:
            issues.append(
                f"{tool_name}.annotations.{field_name}: required annotation is missing"
            )
        elif actual != expected_value:
            issues.append(
                f"{tool_name}.annotations.{field_name}: expected {expected_value!r}, "
                f"found {actual!r}"
            )
    return issues


def review_tools(tools: Iterable[object]) -> list[str]:
    """Return stable, actionable findings for a hosted MCP tool collection."""
    issues: list[str] = []
    for tool in sorted(tools, key=lambda item: str(_value(item, "name", ""))):
        tool_name = str(_value(tool, "name", "<unnamed>"))
        input_schema = _value(tool, "inputSchema", None)
        if not isinstance(input_schema, Mapping):
            issues.append(f"{tool_name}.<root>: inputSchema must be an object schema")
        else:
            issues.extend(_schema_issues(tool_name, input_schema))
        issues.extend(_annotation_issues(tool))
    return issues


async def _hosted_tools() -> list[object]:
    from mercury_tools.mcp.server import mcp

    return list(await mcp.list_tools())


def review_hosted_contract() -> list[str]:
    """Introspect the hosted FastMCP registry and return all review findings."""
    return review_tools(asyncio.run(_hosted_tools()))


def main() -> int:
    issues = review_hosted_contract()
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        print(f"Mercury MCP review: {len(issues)} contract finding(s)", file=sys.stderr)
        return 1
    print(SUCCESS_MESSAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
