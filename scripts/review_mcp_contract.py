"""Review the hosted Mercury MCP contract for marketplace submission clarity."""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

SUCCESS_MESSAGE = "Mercury MCP review: 0 unclear arguments; annotations verified"

class ToolBehavior(NamedTuple):
    """Reviewed annotations and workspace scope for one hosted tool."""

    read_only: bool
    destructive: bool
    idempotent: bool | None
    open_world: bool
    requires_workspace: bool

    @property
    def annotation_values(self) -> tuple[bool, bool, bool | None, bool]:
        return (
            self.read_only,
            self.destructive,
            self.idempotent,
            self.open_world,
        )


_CLOSED_READ = (True, False, None, False)
_CLOSED_CREATE = (False, False, False, False)
_CLOSED_IDEMPOTENT_WRITE = (False, False, True, False)
_CLOSED_DESTRUCTIVE_IDEMPOTENT = (False, True, True, False)

BEHAVIOR_MATRIX: dict[str, ToolBehavior] = {
    "search_knowledge": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "retrieve_context_pack": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "retrieve_workspace_context_pack": ToolBehavior(
        *_CLOSED_READ, requires_workspace=True
    ),
    "get_document": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "create_public_workspace": ToolBehavior(
        *_CLOSED_CREATE, requires_workspace=False
    ),
    "get_public_workspace": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "list_connectors": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "get_connector_setup": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "link_connector_profile": ToolBehavior(*_CLOSED_CREATE, requires_workspace=True),
    "validate_connector_connection": ToolBehavior(
        *_CLOSED_IDEMPOTENT_WRITE, requires_workspace=True
    ),
    "connector_capabilities": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "unlink_connector_profile": ToolBehavior(
        *_CLOSED_DESTRUCTIVE_IDEMPOTENT, requires_workspace=True
    ),
    "connector_status": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "list_accounting_skills": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "get_accounting_skill_schema": ToolBehavior(
        *_CLOSED_READ, requires_workspace=False
    ),
    "run_accounting_skill": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "flow_cheat_sheet": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "check_flow_syntax": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "inspect_flow_files": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "run_inline_flow": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "run_flow_files": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "list_workspace_flows": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "run_workspace_flow": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "save_workspace_flow": ToolBehavior(
        *_CLOSED_IDEMPOTENT_WRITE, requires_workspace=True
    ),
}

V1_BEHAVIOR_MATRIX: dict[str, ToolBehavior] = {
    "get_mercury_context": ToolBehavior(*_CLOSED_IDEMPOTENT_WRITE, requires_workspace=False),
    "list_accounting_providers": ToolBehavior(*_CLOSED_READ, requires_workspace=False),
    "start_provider_connection": ToolBehavior(
        False,
        False,
        False,
        True,
        requires_workspace=True,
    ),
    "list_provider_connections": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "connector_status": ToolBehavior(False, False, False, False, requires_workspace=True),
    "list_provider_capabilities": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "get_capability_schema": ToolBehavior(*_CLOSED_READ, requires_workspace=True),
    "disconnect_provider": ToolBehavior(
        *_CLOSED_DESTRUCTIVE_IDEMPOTENT,
        requires_workspace=True,
    ),
}

_MUTUALLY_EXCLUSIVE_SOURCE_FIELDS = frozenset(
    {"flow_yaml", "flow_files", "workspace_flow_id"}
)
_CAMEL_CASE_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_NON_ALPHANUMERIC_RE = re.compile(r"[^A-Za-z0-9]+")
_POINTER_ARRAY_INDEX_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_CREDENTIAL_SINGLE_TOKENS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "passphrase",
        "passwd",
        "password",
        "secret",
    }
)
_CREDENTIAL_TOKEN_SEQUENCES = frozenset(
    {
        ("access", "key"),
        ("access", "token"),
        ("api", "key"),
        ("api", "token"),
        ("auth", "token"),
        ("bearer", "token"),
        ("client", "secret"),
        ("id", "token"),
        ("oauth", "token"),
        ("private", "key"),
        ("refresh", "key"),
        ("refresh", "token"),
        ("secret", "key"),
        ("service", "role", "key"),
    }
)
_CREDENTIAL_COMPACT_TOKENS = frozenset(
    "".join(sequence) for sequence in _CREDENTIAL_TOKEN_SEQUENCES
)
_METADATA_TOKEN_SEQUENCES = frozenset(
    {
        ("api", "token", "count"),
        ("password", "policy"),
        ("policy", "password"),
        ("passwd", "policy"),
        ("policy", "passwd"),
        ("passphrase", "policy"),
        ("policy", "passphrase"),
    }
)
_ANNOTATION_FIELDS = (
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)
_MISSING = object()
_COMPOSITION_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf"})
_NON_CONSTRAINT_KEYS = frozenset({"$defs", "definitions"})
_JSON_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_ATOMIC_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_MAX_REF_DEPTH = 32
_MAX_SCHEMA_DEPTH = 64
_MAX_EXPANDED_BRANCHES = 256


class _SchemaNode(NamedTuple):
    schema: object
    path: tuple[str, ...]


class _SchemaFragment(NamedTuple):
    schema: Mapping[str, Any]
    path: tuple[str, ...]


class _RootBranch(NamedTuple):
    path: tuple[str, ...]
    properties: frozenset[str]
    required: frozenset[str]


def _value(source: object, name: str, default: object = _MISSING) -> object:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _argument_path(tool_name: str, path: tuple[str, ...]) -> str:
    parts = list(path)
    if not parts:
        parts.append("<root>")
    elif parts[0] in _COMPOSITION_KEYWORDS or parts[0] == "$ref":
        parts.insert(0, "<root>")
    rendered = tool_name
    for part in parts:
        if part.startswith("["):
            rendered += part
        else:
            rendered += f".{part}"
    return rendered


def _types_for_value(value: object) -> set[str]:
    if value is None:
        return {"null"}
    if isinstance(value, bool):
        return {"boolean"}
    if isinstance(value, int):
        return {"integer"}
    if isinstance(value, float):
        return {"number"}
    if isinstance(value, str):
        return {"string"}
    if isinstance(value, list):
        return {"array"}
    if isinstance(value, Mapping):
        return {"object"}
    return set()


def _declared_types(value: object) -> set[str]:
    raw_types = [value] if isinstance(value, str) else value
    if not isinstance(raw_types, list):
        return set()
    declared: set[str] = set()
    for raw_type in raw_types:
        if raw_type not in _JSON_TYPES:
            continue
        if raw_type == "number":
            declared.update({"integer", "number"})
        else:
            declared.add(raw_type)
    return declared


def _normalized_field_name_tokens(field_name: str) -> tuple[str, ...]:
    """Split public field names into exact lowercase identifier tokens."""
    with_boundaries = _CAMEL_CASE_BOUNDARY_RE.sub(" ", field_name)
    return tuple(
        part.lower()
        for part in _NON_ALPHANUMERIC_RE.split(with_boundaries)
        if part
    )


def _contains_token_sequence(
    tokens: tuple[str, ...],
    sequence: tuple[str, ...],
) -> bool:
    width = len(sequence)
    return any(tokens[index : index + width] == sequence for index in range(len(tokens)))


def _is_credential_field_name(field_name: str) -> bool:
    """Reject credential payload names while allowing clearly named metadata."""
    tokens = _normalized_field_name_tokens(field_name)
    if not tokens or tokens in _METADATA_TOKEN_SEQUENCES:
        return False
    if any(token in _CREDENTIAL_SINGLE_TOKENS for token in tokens):
        return True
    if any(token in _CREDENTIAL_COMPACT_TOKENS for token in tokens):
        return True
    return any(
        _contains_token_sequence(tokens, sequence)
        for sequence in _CREDENTIAL_TOKEN_SEQUENCES
    )


class _SchemaReviewer:
    def __init__(self, tool_name: str, root: Mapping[str, Any]) -> None:
        self.tool_name = tool_name
        self.root = root
        self.issues: list[str] = []
        self._issue_set: set[str] = set()
        self.root_branches: list[_RootBranch] = []

    def add(self, path: tuple[str, ...], message: str) -> None:
        issue = f"{_argument_path(self.tool_name, path)}: {message}"
        if issue not in self._issue_set:
            self._issue_set.add(issue)
            self.issues.append(issue)

    def review(self, behavior: ToolBehavior | None) -> list[str]:
        self._analyze((_SchemaNode(self.root, ()),), (), root=True)

        source_fields = sorted(
            set().union(*(branch.properties for branch in self.root_branches))
            & _MUTUALLY_EXCLUSIVE_SOURCE_FIELDS
        )
        if len(source_fields) > 1:
            self.add(
                (),
                "split mutually exclusive source fields into separate tools: "
                + ", ".join(source_fields),
            )

        if behavior is not None and behavior.requires_workspace:
            if not self.root_branches or any(
                "workspace_id" not in branch.properties
                for branch in self.root_branches
            ):
                self.add(
                    ("workspace_id",),
                    "workspace-scoped tool must define workspace_id",
                )
            if not self.root_branches or any(
                "workspace_id" not in branch.required for branch in self.root_branches
            ):
                self.add(
                    ("workspace_id",),
                    "workspace-scoped tool must require workspace_id",
                )
        return self.issues

    def _resolve_local_ref(
        self,
        reference: str,
        path: tuple[str, ...],
    ) -> Mapping[str, Any] | None:
        if reference != "#" and not reference.startswith("#/"):
            self.add(path, f"$ref {reference!r} must be a local JSON pointer")
            return None
        target: object = self.root
        if reference.startswith("#/"):
            for raw_part in reference[2:].split("/"):
                part = self._decode_pointer_part(reference, raw_part, path)
                if part is None:
                    return None
                if isinstance(target, Mapping):
                    if part not in target:
                        self.add(path, f"local $ref {reference!r} does not resolve")
                        return None
                    target = target[part]
                    continue
                if isinstance(target, Sequence) and not isinstance(
                    target, (str, bytes, bytearray)
                ):
                    if not _POINTER_ARRAY_INDEX_RE.fullmatch(part):
                        self.add(
                            path,
                            f"local $ref {reference!r} array index {part!r} is not valid; "
                            "expected a nonnegative integer",
                        )
                        return None
                    index = int(part)
                    if index >= len(target):
                        self.add(
                            path,
                            f"local $ref {reference!r} array index {index} is out of range",
                        )
                        return None
                    target = target[index]
                    continue
                self.add(
                    path,
                    f"local $ref {reference!r} cannot traverse {part!r} through a scalar value",
                )
                return None
        if not isinstance(target, Mapping):
            self.add(path, f"local $ref {reference!r} must resolve to a schema object")
            return None
        return target

    def _decode_pointer_part(
        self,
        reference: str,
        raw_part: str,
        path: tuple[str, ...],
    ) -> str | None:
        decoded: list[str] = []
        index = 0
        while index < len(raw_part):
            character = raw_part[index]
            if character != "~":
                decoded.append(character)
                index += 1
                continue
            if index + 1 >= len(raw_part) or raw_part[index + 1] not in {"0", "1"}:
                self.add(
                    path,
                    f"local $ref {reference!r} has invalid JSON Pointer escape "
                    f"in component {raw_part!r}",
                )
                return None
            decoded.append("~" if raw_part[index + 1] == "0" else "/")
            index += 2
        return "".join(decoded)

    def _cross(
        self,
        left: list[tuple[_SchemaFragment, ...]],
        right: list[tuple[_SchemaFragment, ...]],
        path: tuple[str, ...],
    ) -> list[tuple[_SchemaFragment, ...]]:
        if not left or not right:
            return []
        if len(left) * len(right) > _MAX_EXPANDED_BRANCHES:
            self.add(
                path,
                f"schema composition exceeds {_MAX_EXPANDED_BRANCHES} accepting branches",
            )
            return []
        return [left_branch + right_branch for left_branch in left for right_branch in right]

    def _expand(
        self,
        node: _SchemaNode,
        *,
        ref_stack: tuple[str, ...] = (),
        depth: int = 0,
    ) -> list[tuple[_SchemaFragment, ...]]:
        if depth > _MAX_SCHEMA_DEPTH:
            self.add(node.path, f"schema nesting exceeds {_MAX_SCHEMA_DEPTH} levels")
            return []
        if not isinstance(node.schema, Mapping):
            self.add(node.path, "schema must be a JSON object")
            return []

        direct = {
            key: value
            for key, value in node.schema.items()
            if key not in _COMPOSITION_KEYWORDS
            and key not in _NON_CONSTRAINT_KEYS
            and key != "$ref"
        }
        branches = [(_SchemaFragment(direct, node.path),)]

        if "$ref" in node.schema:
            reference = node.schema["$ref"]
            ref_path = (*node.path, "$ref")
            if not isinstance(reference, str):
                self.add(ref_path, "$ref must be a local JSON pointer string")
                return []
            if reference in ref_stack:
                self.add(ref_path, f"cyclic local $ref detected at {reference!r}")
                return []
            if len(ref_stack) >= _MAX_REF_DEPTH:
                self.add(ref_path, f"local $ref depth exceeds {_MAX_REF_DEPTH}")
                return []
            target = self._resolve_local_ref(reference, ref_path)
            if target is None:
                return []
            resolved = self._expand(
                _SchemaNode(target, node.path),
                ref_stack=(*ref_stack, reference),
                depth=depth + 1,
            )
            branches = self._cross(branches, resolved, node.path)

        variants = node.schema.get("allOf")
        if variants is not None:
            if not isinstance(variants, list) or not variants:
                self.add((*node.path, "allOf"), "allOf must be a non-empty array")
                return []
            for index, variant in enumerate(variants):
                variant_path = (*node.path, "allOf", f"[{index}]")
                expanded = self._expand(
                    _SchemaNode(variant, variant_path),
                    ref_stack=ref_stack,
                    depth=depth + 1,
                )
                branches = self._cross(branches, expanded, node.path)

        for keyword in ("anyOf", "oneOf"):
            variants = node.schema.get(keyword)
            if variants is None:
                continue
            if not isinstance(variants, list) or not variants:
                self.add(
                    (*node.path, keyword),
                    f"{keyword} must be a non-empty array",
                )
                return []
            alternatives: list[tuple[_SchemaFragment, ...]] = []
            for index, variant in enumerate(variants):
                variant_path = (*node.path, keyword, f"[{index}]")
                alternatives.extend(
                    self._expand(
                        _SchemaNode(variant, variant_path),
                        ref_stack=ref_stack,
                        depth=depth + 1,
                    )
                )
            branches = self._cross(branches, alternatives, node.path)
        return branches

    def _expand_nodes(
        self,
        nodes: tuple[_SchemaNode, ...],
        path: tuple[str, ...],
    ) -> list[tuple[_SchemaFragment, ...]]:
        branches: list[tuple[_SchemaFragment, ...]] = [()]
        for node in nodes:
            branches = self._cross(branches, self._expand(node), path)
        return branches

    @staticmethod
    def _possible_types(branch: tuple[_SchemaFragment, ...]) -> set[str]:
        possible = set(_ATOMIC_TYPES)
        for fragment in branch:
            schema = fragment.schema
            if "type" in schema:
                possible.intersection_update(_declared_types(schema["type"]))
            enum = schema.get("enum")
            if isinstance(enum, list):
                enum_types: set[str] = set()
                for value in enum:
                    enum_types.update(_types_for_value(value))
                possible.intersection_update(enum_types)
            if "const" in schema:
                possible.intersection_update(_types_for_value(schema["const"]))
        return possible

    @staticmethod
    def _has_concrete_constraint(branch: tuple[_SchemaFragment, ...]) -> bool:
        for fragment in branch:
            schema = fragment.schema
            if "type" in schema and bool(_declared_types(schema["type"])):
                return True
            if isinstance(schema.get("enum"), list) and bool(schema["enum"]):
                return True
            if "const" in schema:
                return True
        return False

    @staticmethod
    def _has_explicit_enum(branch: tuple[_SchemaFragment, ...]) -> bool:
        return any(
            (
                isinstance(fragment.schema.get("enum"), list)
                and bool(fragment.schema["enum"])
            )
            or "const" in fragment.schema
            for fragment in branch
        )

    @staticmethod
    def _branch_path(
        branch: tuple[_SchemaFragment, ...],
        fallback: tuple[str, ...],
    ) -> tuple[str, ...]:
        candidates: list[tuple[str, ...]] = []
        for fragment in branch:
            for index, part in enumerate(fragment.path):
                if (
                    index >= len(fallback)
                    and part in {"anyOf", "oneOf"}
                    and index + 1 < len(fragment.path)
                ):
                    candidates.append(fragment.path[: index + 2])
        return max(candidates, key=len) if candidates else fallback

    def _object_view(
        self,
        branch: tuple[_SchemaFragment, ...],
    ) -> tuple[dict[str, list[_SchemaNode]], set[str], bool]:
        properties: dict[str, list[_SchemaNode]] = {}
        required: set[str] = set()
        closed = False
        for fragment in branch:
            schema = fragment.schema
            raw_properties = schema.get("properties", _MISSING)
            if raw_properties is not _MISSING:
                if not isinstance(raw_properties, Mapping):
                    self.add(fragment.path, "object properties must be a mapping")
                else:
                    for raw_name, field_schema in raw_properties.items():
                        field_name = str(raw_name)
                        field_path = (*fragment.path, field_name)
                        if _is_credential_field_name(field_name):
                            self.add(
                                field_path,
                                "credential-bearing input field names are prohibited",
                            )
                        properties.setdefault(field_name, []).append(
                            _SchemaNode(field_schema, field_path)
                        )
            raw_pattern_properties = schema.get("patternProperties", _MISSING)
            if raw_pattern_properties is not _MISSING:
                pattern_path = (*fragment.path, "patternProperties")
                if not isinstance(raw_pattern_properties, Mapping):
                    self.add(pattern_path, "patternProperties must be an empty mapping")
                elif raw_pattern_properties:
                    self.add(
                        pattern_path,
                        "patternProperties may introduce undeclared input keys",
                    )
            raw_required = schema.get("required", _MISSING)
            if raw_required is not _MISSING:
                if not isinstance(raw_required, list) or not all(
                    isinstance(item, str) for item in raw_required
                ):
                    self.add(fragment.path, "object required must be an array of names")
                else:
                    required.update(raw_required)
            if schema.get("additionalProperties") is False:
                closed = True
        return properties, required, closed

    def _review_object(
        self,
        branch: tuple[_SchemaFragment, ...],
        path: tuple[str, ...],
        *,
        root: bool,
        properties: dict[str, list[_SchemaNode]],
        required: set[str],
        closed: bool,
    ) -> None:
        if not properties and (not root or required):
            self.add(path, "object must define named properties")
        if not closed:
            self.add(path, "object must set additionalProperties=false")
        for field_name, field_nodes in properties.items():
            self._analyze(
                tuple(field_nodes),
                field_nodes[0].path,
                field_name=field_name,
            )

    def _review_array(
        self,
        branch: tuple[_SchemaFragment, ...],
        path: tuple[str, ...],
    ) -> None:
        item_nodes: list[_SchemaNode] = []
        finite_max = False
        for fragment in branch:
            schema = fragment.schema
            if "items" in schema:
                item_nodes.append(
                    _SchemaNode(schema["items"], (*fragment.path, "items"))
                )
            maximum = schema.get("maxItems", _MISSING)
            if (
                not isinstance(maximum, bool)
                and isinstance(maximum, int)
                and maximum >= 0
            ):
                finite_max = True
        if not item_nodes:
            self.add(path, "array must define a typed items schema")
        else:
            self._analyze(tuple(item_nodes), item_nodes[0].path)
        if not finite_max:
            self.add(path, "array must define a finite maxItems")

    def _analyze(
        self,
        nodes: tuple[_SchemaNode, ...],
        path: tuple[str, ...],
        *,
        root: bool = False,
        field_name: str | None = None,
    ) -> None:
        issue_count = len(self.issues)
        branches = self._expand_nodes(nodes, path)
        accepting = 0
        for branch in branches:
            possible_types = self._possible_types(branch)
            if not possible_types:
                continue
            accepting += 1
            branch_path = self._branch_path(branch, path)
            properties, required, closed = self._object_view(branch)

            if root:
                self.root_branches.append(
                    _RootBranch(
                        path=branch_path,
                        properties=frozenset(properties),
                        required=frozenset(required),
                    )
                )
                if possible_types != {"object"}:
                    self.add(
                        branch_path,
                        "root inputSchema must resolve to a strict object",
                    )
            elif not self._has_concrete_constraint(branch):
                self.add(
                    branch_path,
                    "schema branch must define a concrete type or enum/const",
                )
                continue

            if field_name == "environment":
                scalar_types = possible_types - {"array", "null"}
                if scalar_types and not self._has_explicit_enum(branch):
                    self.add(
                        branch_path,
                        "environment must expose an explicit enum",
                    )

            if "object" in possible_types:
                self._review_object(
                    branch,
                    branch_path,
                    root=root,
                    properties=properties,
                    required=required,
                    closed=closed,
                )
            if "array" in possible_types:
                self._review_array(branch, branch_path)

        if accepting == 0 and len(self.issues) == issue_count:
            self.add(path, "schema has no accepting branch")


def _schema_issues(
    tool_name: str,
    schema: Mapping[str, Any],
    behavior: ToolBehavior | None,
) -> list[str]:
    return _SchemaReviewer(tool_name, schema).review(behavior)


def _annotation_issues(
    tool: object,
    behavior: ToolBehavior | None,
) -> list[str]:
    tool_name = str(_value(tool, "name", "<unnamed>"))
    if behavior is None:
        return [
            f"{tool_name}.annotations: tool has no reviewed behavior-matrix entry"
        ]

    annotations = _value(tool, "annotations", None)
    if annotations is None:
        return [
            f"{tool_name}.annotations: required behavior annotations are missing; "
            "expected "
            f"{dict(zip(_ANNOTATION_FIELDS, behavior.annotation_values, strict=True))}"
        ]

    issues: list[str] = []
    for field_name, expected_value in zip(
        _ANNOTATION_FIELDS,
        behavior.annotation_values,
        strict=True,
    ):
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
        metadata = _value(tool, "meta", {})
        if isinstance(metadata, Mapping) and metadata.get("mercury/surface") == "v1":
            behavior = V1_BEHAVIOR_MATRIX.get(tool_name)
        else:
            behavior = BEHAVIOR_MATRIX.get(tool_name)
        input_schema = _value(tool, "inputSchema", None)
        if not isinstance(input_schema, Mapping):
            issues.append(
                f"{tool_name}.<root>: root inputSchema must resolve to a strict object"
            )
        else:
            issues.extend(_schema_issues(tool_name, input_schema, behavior))
        issues.extend(_annotation_issues(tool, behavior))
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
