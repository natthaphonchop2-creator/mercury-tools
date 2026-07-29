"""Catalog-generated, Mercury-owned wrappers for qualified provider reads."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, TypeAlias
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, JsonValue, PrivateAttr, model_validator

from mercury_tools.catalog.identity import canonical_json
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.mcp.v1_errors import (
    MercuryV1ToolError,
    public_error_code,
    published_error_output_schema,
)
from mercury_tools.mcp.v1_schemas import ProviderReadEnvelope
from mercury_tools.providers.base import ProviderSchemaChanged

GeneratedReadExecutor: TypeAlias = Callable[..., Awaitable[ProviderReadEnvelope]]
PersistSchemaChange: TypeAlias = Callable[
    [ProviderMCPQualification, Context | object],
    Awaitable[Sequence[ProviderMCPQualification]] | Sequence[ProviderMCPQualification],
]
SchemaDriftAlert: TypeAlias = Callable[[dict[str, object]], Awaitable[object] | object]

_READ_CAPABILITIES = frozenset(
    {
        "provider_profile.get",
        "documents.invoice.list",
        "documents.invoice.get",
    }
)
_V1_GENERATED_META = {
    "mercury/surface": "v1",
    "mercury/error-schema": "mercury.v1.error.v1",
    "mercury/generated": "provider-read",
}
_CLOSED_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)
_PUBLIC_BUSINESS_IDENTIFIERS = frozenset(
    {
        "invoiceid",
        "documentid",
        "invoiceidentifier",
        "documentidentifier",
        "invoicenumber",
        "documentnumber",
        "invoicecode",
        "documentcode",
        "ordernumber",
        "referencenumber",
        "reference",
    }
)
_RESTRICTED_FIELD_PARTS = frozenset(
    {
        "access",
        "address",
        "authorization",
        "contact",
        "cookie",
        "credential",
        "email",
        "password",
        "phone",
        "secret",
        "session",
        "token",
    }
)
_RESTRICTED_IDENTIFIERS = frozenset(
    {
        "accountid",
        "apikey",
        "auth",
        "bearer",
        "companyid",
        "connectionid",
        "customerid",
        "externalid",
        "firstname",
        "fullname",
        "lastname",
        "merchantid",
        "personname",
        "providerid",
        "recipientname",
        "taxid",
        "taxidentifier",
        "taxnumber",
        "tin",
        "userid",
        "vatid",
        "vatnumber",
    }
)
_SCHEMA_ANNOTATIONS = frozenset(
    {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
)
_SUPPORTED_SCHEMA_KEYWORDS = (
    frozenset(
        {
            "$defs",
            "$ref",
            "$schema",
            "additionalProperties",
            "allOf",
            "anyOf",
            "const",
            "else",
            "enum",
            "exclusiveMaximum",
            "exclusiveMinimum",
            "format",
            "if",
            "items",
            "maxItems",
            "maxLength",
            "maxProperties",
            "maximum",
            "minItems",
            "minLength",
            "minProperties",
            "minimum",
            "multipleOf",
            "oneOf",
            "pattern",
            "properties",
            "required",
            "then",
            "type",
            "unevaluatedProperties",
            "uniqueItems",
        }
    )
    | _SCHEMA_ANNOTATIONS
)
_PUBLIC_SCALAR_KEYWORDS = frozenset(
    {
        "const",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "type",
        "uniqueItems",
    }
)
_REF_ANNOTATIONS = _SCHEMA_ANNOTATIONS | {"$ref"}
_REFRESH_NOTIFICATION_TIMEOUT_SECONDS = 1


def _schema_fingerprint(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(schema)).encode("utf-8")).hexdigest()


def _schema_copy(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Thaw a catalog schema without mutating its immutable authority record."""

    return json.loads(canonical_json(dict(schema)))


def _schema_allows_object(schema: Mapping[str, Any]) -> bool:
    value = schema.get("type")
    return (
        value == "object"
        or (isinstance(value, list | tuple) and "object" in value)
        or "properties" in schema
    )


def _reference_target(reference: str, root: Mapping[str, Any]) -> Mapping[str, Any]:
    if reference == "#":
        return root
    if not reference.startswith("#/"):
        raise ValueError("generated_schema_invalid")
    target: object = root
    for segment in reference[2:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(target, Mapping):
            target = target.get(key)
        elif isinstance(target, list | tuple) and key.isdigit():
            index = int(key)
            target = target[index] if index < len(target) else None
        else:
            target = None
        if target is None:
            break
    if not isinstance(target, Mapping):
        raise ValueError("generated_schema_invalid")
    return target


def _assert_closed_schema(schema: Mapping[str, Any]) -> None:
    def visit(node: object, references: frozenset[str] = frozenset()) -> None:
        if not isinstance(node, Mapping):
            raise ValueError("generated_schema_invalid")
        if set(node) - _SUPPORTED_SCHEMA_KEYWORDS:
            raise ValueError("generated_schema_invalid")
        reference = node.get("$ref")
        if reference is not None:
            if (
                not isinstance(reference, str)
                or set(node) - _REF_ANNOTATIONS
                or reference in references
            ):
                raise ValueError("generated_schema_invalid")
            visit(_reference_target(reference, schema), references | {reference})
            return
        if not any(
            key in node
            for key in (
                "type",
                "properties",
                "items",
                "allOf",
                "anyOf",
                "const",
                "enum",
                "oneOf",
                "if",
            )
        ):
            raise ValueError("generated_schema_invalid")
        if _schema_allows_object(node) and (
            node.get("additionalProperties") is not False
            or node.get("unevaluatedProperties", False) not in {False, None}
        ):
            raise ValueError("generated_schema_invalid")
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, Mapping) or any(
                not isinstance(name, str) or not isinstance(value, Mapping)
                for name, value in properties.items()
            ):
                raise ValueError("generated_schema_invalid")
            for value in properties.values():
                visit(value, references)
        required = node.get("required")
        if required is not None and (
            not isinstance(required, list | tuple)
            or any(not isinstance(item, str) for item in required)
        ):
            raise ValueError("generated_schema_invalid")
        definitions = node.get("$defs")
        if definitions is not None:
            if not isinstance(definitions, Mapping) or any(
                not isinstance(name, str) or not isinstance(value, Mapping)
                for name, value in definitions.items()
            ):
                raise ValueError("generated_schema_invalid")
            for value in definitions.values():
                visit(value, references)
        items = node.get("items")
        if items is not None:
            if not isinstance(items, Mapping):
                raise ValueError("generated_schema_invalid")
            visit(items, references)
        for keyword in ("allOf", "anyOf", "oneOf"):
            values = node.get(keyword)
            if values is None:
                continue
            if (
                not isinstance(values, list | tuple)
                or not values
                or any(not isinstance(item, Mapping) for item in values)
            ):
                raise ValueError("generated_schema_invalid")
            for item in values:
                visit(item, references)
        if_condition = node.get("if")
        if if_condition is not None:
            if not isinstance(if_condition, Mapping):
                raise ValueError("generated_schema_invalid")
            visit(if_condition, references)
            for keyword in ("then", "else"):
                branch = node.get(keyword)
                if branch is not None:
                    if not isinstance(branch, Mapping):
                        raise ValueError("generated_schema_invalid")
                    visit(branch, references)
        for keyword in ("const", "enum"):
            if keyword not in node:
                continue
            value = node.get(keyword)
            values = value if keyword == "enum" else (value,)
            if keyword == "enum" and not isinstance(value, list | tuple):
                raise ValueError("generated_schema_invalid")
            if any(isinstance(item, Mapping | list | tuple) for item in values):
                raise ValueError("generated_schema_invalid")

    try:
        Draft202012Validator.check_schema(dict(schema))
        visit(schema)
    except Exception:
        raise ValueError("generated_schema_invalid") from None


class _CatalogWireModel(BaseModel):
    """A frozen model whose wire contract is one reviewed catalog JSON Schema."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    _schema: ClassVar[dict[str, Any]]
    _validator: ClassVar[Draft202012Validator]
    _payload: dict[str, JsonValue] = PrivateAttr(default_factory=dict)

    @classmethod
    def model_validate(cls, obj: Any, **_kwargs: Any) -> _CatalogWireModel:
        if isinstance(obj, cls):
            source = obj._payload
        elif isinstance(obj, BaseModel):
            source = obj.model_dump(mode="json")
        else:
            source = obj
        if not isinstance(source, Mapping):
            raise ValueError("generated_schema_validation_failed")
        try:
            cls._validator.validate(dict(source))
        except Exception:
            raise ValueError("generated_schema_validation_failed") from None
        instance = cls.model_construct()
        object.__setattr__(instance, "_payload", copy.deepcopy(dict(source)))
        return instance

    @classmethod
    def model_json_schema(cls, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(cls._schema)

    def model_dump(self, **_kwargs: Any) -> dict[str, JsonValue]:
        return copy.deepcopy(self._payload)


_WIRE_MODEL_CACHE: dict[tuple[str, Literal["input", "output"]], type[_CatalogWireModel]] = {}


def catalog_wire_model(
    schema: Mapping[str, Any],
    *,
    kind: Literal["input", "output"],
) -> type[_CatalogWireModel]:
    """Return a stable internal model that validates one closed schema exactly."""

    checked = _schema_copy(schema)
    _assert_closed_schema(checked)
    fingerprint = _schema_fingerprint(checked)
    key = (fingerprint, kind)
    cached = _WIRE_MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    validation_schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **checked}
    model = type(
        f"MercuryCatalog{kind.title()}{fingerprint[:12]}",
        (_CatalogWireModel,),
        {
            "__module__": __name__,
            "__annotations__": {
                "_schema": ClassVar[dict[str, Any]],
                "_validator": ClassVar[Draft202012Validator],
            },
            "_schema": checked,
            "_validator": Draft202012Validator(
                validation_schema,
                format_checker=FormatChecker(),
            ),
        },
    )
    _WIRE_MODEL_CACHE[key] = model
    return model


def _normalized_field_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _is_restricted_field(name: str) -> bool:
    normalized = _normalized_field_name(name)
    if normalized in _PUBLIC_BUSINESS_IDENTIFIERS:
        return False
    if normalized in _RESTRICTED_IDENTIFIERS or normalized == "id":
        return True
    if normalized in {"taxamount", "vatamount", "taxrate", "vatrate"}:
        return False
    if "tax" in normalized or "vat" in normalized:
        return True
    return any(part in normalized for part in _RESTRICTED_FIELD_PARTS)


def _dereference(schema: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    if not isinstance(reference, str):
        raise ValueError("generated_schema_invalid")
    return _reference_target(reference, root)


def _schema_mentions_restricted_field(
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any],
    references: frozenset[str] = frozenset(),
) -> bool:
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or reference in references:
            raise ValueError("generated_schema_invalid")
        return _schema_mentions_restricted_field(
            _dereference(schema, root),
            root=root,
            references=references | {reference},
        )
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                raise ValueError("generated_schema_invalid")
            if _is_restricted_field(name) or _schema_mentions_restricted_field(
                child,
                root=root,
                references=references,
            ):
                return True
    for keyword in ("allOf", "anyOf", "oneOf"):
        values = schema.get(keyword, ())
        if not isinstance(values, list | tuple):
            continue
        if any(
            _schema_mentions_restricted_field(
                item,
                root=root,
                references=references,
            )
            for item in values
            if isinstance(item, Mapping)
        ):
            return True
    for keyword in ("if", "then", "else", "items"):
        child = schema.get(keyword)
        if isinstance(child, Mapping) and _schema_mentions_restricted_field(
            child,
            root=root,
            references=references,
        ):
            return True
    return False


def _public_schema_node(
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any],
    references: set[str],
) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or reference in references:
            raise ValueError("generated_schema_invalid")
        return _public_schema_node(
            _dereference(schema, root),
            root=root,
            references={*references, reference},
        )
    result = {
        key: copy.deepcopy(value) for key, value in schema.items() if key in _PUBLIC_SCALAR_KEYWORDS
    }
    if _schema_allows_object(schema):
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ValueError("generated_schema_invalid")
        public_properties = {
            str(name): _public_schema_node(value, root=root, references=references)
            for name, value in properties.items()
            if isinstance(name, str)
            and isinstance(value, Mapping)
            and not _is_restricted_field(name)
        }
        required = schema.get("required", [])
        if not isinstance(required, list | tuple) or any(
            not isinstance(item, str) for item in required
        ):
            raise ValueError("generated_schema_invalid")
        result.update(
            {
                "type": copy.deepcopy(schema.get("type", "object")),
                "properties": public_properties,
                "required": [item for item in required if item in public_properties],
                "additionalProperties": False,
            }
        )
    if "items" in schema:
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise ValueError("generated_schema_invalid")
        result["items"] = _public_schema_node(items, root=root, references=references)
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            values = schema[keyword]
            if not isinstance(values, list) or not all(
                isinstance(item, Mapping) for item in values
            ):
                raise ValueError("generated_schema_invalid")
            transformed = [
                _public_schema_node(item, root=root, references=references) for item in values
            ]
            # Removing restricted branch discriminators can make an exact source
            # ``oneOf`` ambiguous. The public contract is the safe branch union.
            result["anyOf" if keyword == "oneOf" else keyword] = transformed
    condition = schema.get("if")
    if condition is not None:
        if not isinstance(condition, Mapping):
            raise ValueError("generated_schema_invalid")
        branches = [
            _public_schema_node(branch, root=root, references=references)
            for keyword in ("then", "else")
            if isinstance((branch := schema.get(keyword)), Mapping)
        ]
        if _schema_mentions_restricted_field(condition, root=root):
            if branches:
                result.setdefault("allOf", []).append({"anyOf": branches})
        else:
            result["if"] = _public_schema_node(condition, root=root, references=references)
            for keyword in ("then", "else"):
                branch = schema.get(keyword)
                if isinstance(branch, Mapping):
                    result[keyword] = _public_schema_node(
                        branch,
                        root=root,
                        references=references,
                    )
    return result


def public_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Derive a closed public value contract from the exact reviewed output schema."""

    source = _schema_copy(schema)
    _assert_closed_schema(source)
    projected = _public_schema_node(source, root=source, references=set())
    _assert_closed_schema(projected)
    return projected


def _schema_nodes(
    schemas: Sequence[Mapping[str, Any]],
    *,
    root: Mapping[str, Any],
    references: frozenset[str] = frozenset(),
) -> tuple[Mapping[str, Any], ...]:
    nodes: list[Mapping[str, Any]] = []
    for schema in schemas:
        reference = schema.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or reference in references:
                raise ValueError("generated_output_invalid")
            nodes.extend(
                _schema_nodes(
                    (_dereference(schema, root),),
                    root=root,
                    references=references | {reference},
                )
            )
            continue
        nodes.append(schema)
        children: list[Mapping[str, Any]] = []
        for keyword in ("allOf", "anyOf", "oneOf"):
            values = schema.get(keyword)
            if isinstance(values, list | tuple):
                children.extend(item for item in values if isinstance(item, Mapping))
        for keyword in ("if", "then", "else"):
            child = schema.get(keyword)
            if isinstance(child, Mapping):
                children.append(child)
        if children:
            nodes.extend(_schema_nodes(children, root=root, references=references))
    return tuple(nodes)


def _project_value(
    value: JsonValue,
    schemas: Sequence[Mapping[str, Any]],
    *,
    root: Mapping[str, Any],
) -> JsonValue:
    nodes = _schema_nodes(schemas, root=root)
    properties: dict[str, list[Mapping[str, Any]]] = {}
    item_schemas: list[Mapping[str, Any]] = []
    for node in nodes:
        candidate_properties = node.get("properties")
        if isinstance(candidate_properties, Mapping):
            for name, child in candidate_properties.items():
                if isinstance(name, str) and isinstance(child, Mapping):
                    properties.setdefault(name, []).append(child)
        items = node.get("items")
        if isinstance(items, Mapping):
            item_schemas.append(items)
    if isinstance(value, Mapping):
        if not properties:
            return {}
        projected: dict[str, JsonValue] = {}
        for name, item in value.items():
            if not isinstance(name, str) or _is_restricted_field(name) or name not in properties:
                continue
            projected[name] = _project_value(
                item,
                tuple(properties[name]),
                root=root,
            )
        return projected
    if isinstance(value, list | tuple):
        if not item_schemas:
            return []
        return [_project_value(item, tuple(item_schemas), root=root) for item in value]
    return value


def project_provider_read_data(
    value: Mapping[str, JsonValue],
    *,
    output_schema: Mapping[str, Any],
) -> dict[str, JsonValue]:
    """Project a raw, already exact-schema-validated value into the public contract."""

    if not isinstance(value, Mapping):
        raise ValueError("generated_output_invalid")
    source = _schema_copy(output_schema)
    # Direct callers get the same exact authority validation as HostedReadService.
    raw_value = (
        catalog_wire_model(source, kind="output").model_validate(value).model_dump(mode="json")
    )
    public_model = catalog_wire_model(public_output_schema(source), kind="output")
    projected = _project_value(raw_value, (source,), root=source)
    if not isinstance(projected, Mapping):
        raise ValueError("generated_output_invalid")
    return public_model.model_validate(projected).model_dump(mode="json")


def _wrapper_name(qualification: ProviderMCPQualification) -> str:
    suffixes = {
        "provider_profile.get": "provider_profile_get",
        "documents.invoice.list": "invoice_list",
        "documents.invoice.get": "invoice_get",
    }
    suffix = suffixes.get(qualification.normalized_capability)
    if suffix is None:
        raise ValueError("generated_capability_invalid")
    return f"mercury_{qualification.provider}_{suffix}"


def _qualification_version_identity(
    qualification: ProviderMCPQualification,
) -> tuple[str, str, str, str, str]:
    return (
        qualification.provider,
        qualification.environment,
        qualification.provider_tool_name,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
    )


def _schema_change_is_persisted(
    qualifications: Sequence[ProviderMCPQualification],
    qualification: ProviderMCPQualification,
) -> bool:
    exact = [
        item
        for item in qualifications
        if _qualification_version_identity(item) == _qualification_version_identity(qualification)
    ]
    return bool(exact) and all(
        item.qualification_state in {QualificationState.DISABLED, QualificationState.SUPERSEDED}
        and item.disable_reason == "schema_changed"
        for item in exact
    )


def _rewrite_references(value: Any, names: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _rewrite_references(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_references(item, names) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        name = value.rsplit("/", 1)[-1]
        if name in names:
            return f"#/$defs/{names[name]}"
    return value


def _merge_schema_definitions(
    container: dict[str, Any],
    schema: Mapping[str, Any],
    *,
    prefix: str,
) -> dict[str, Any]:
    embedded = copy.deepcopy(dict(schema))
    definitions = embedded.pop("$defs", {})
    if not definitions:
        return embedded
    if not isinstance(definitions, Mapping) or not all(
        isinstance(name, str) and isinstance(value, Mapping) for name, value in definitions.items()
    ):
        raise ValueError("generated_schema_invalid")
    names = {name: f"{prefix}{name}" for name in definitions}
    target = container.setdefault("$defs", {})
    if not isinstance(target, dict) or set(target) & set(names.values()):
        raise ValueError("generated_schema_invalid")
    for name, definition in definitions.items():
        target[names[name]] = _rewrite_references(definition, names)
    return _rewrite_references(embedded, names)


@dataclass(frozen=True, slots=True)
class _GeneratedBranch:
    qualification: ProviderMCPQualification
    input_model: type[_CatalogWireModel]
    public_output_model: type[_CatalogWireModel]
    public_output_schema: dict[str, Any]

    @property
    def capability_version(self) -> str:
        return self.qualification.capability_version_sha256

    @property
    def identity(self) -> str:
        return canonical_json(
            {
                "provider": self.qualification.provider,
                "environment": self.qualification.environment,
                "capability": self.qualification.normalized_capability,
                "version": self.qualification.capability_version_sha256,
                "input": self.qualification.input_schema,
                "output": self.qualification.output_schema,
            }
        )


@dataclass(frozen=True, slots=True)
class _StagedTool:
    name: str
    branches: tuple[_GeneratedBranch, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    argument_model: type[_GeneratedArguments]

    @property
    def identity(self) -> str:
        return canonical_json(
            {
                "branches": [branch.identity for branch in self.branches],
                "input": self.input_schema,
                "output": self.output_schema,
            }
        )


def _branch_input_schema(branches: Sequence[_GeneratedBranch], name: str) -> dict[str, Any]:
    result: dict[str, Any] = {"$defs": {}}
    options: list[dict[str, str]] = []
    for index, branch in enumerate(branches):
        source = _schema_copy(branch.qualification.input_schema)
        _assert_closed_schema(source)
        embedded = _merge_schema_definitions(result, source, prefix=f"Input{index}_")
        properties = embedded.get("properties")
        required = embedded.get("required", [])
        if (
            embedded.get("type") != "object"
            or not isinstance(properties, Mapping)
            or not isinstance(required, list | tuple)
            or {"workspace_id", "connection_id", "capability_version"} & set(properties)
        ):
            raise ValueError("generated_schema_invalid")
        definition_name = f"Arguments{index}"
        definitions = result["$defs"]
        if not isinstance(definitions, dict):
            raise ValueError("generated_schema_invalid")
        definitions[definition_name] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "workspace_id": {"type": "string", "format": "uuid"},
                "connection_id": {"type": "string", "format": "uuid"},
                "capability_version": {"const": branch.capability_version},
                **copy.deepcopy(dict(properties)),
            },
            "required": ["workspace_id", "connection_id", "capability_version", *required],
            "title": f"{name}Version{index}Arguments",
        }
        options.append({"$ref": f"#/$defs/{definition_name}"})
    result["oneOf"] = options
    _assert_closed_schema(result)
    return result


def _assert_closed_generated_contract(schema: Mapping[str, Any]) -> None:
    """Check the static envelope union without narrowing its Pydantic JSON Schema."""

    def visit(node: object) -> None:
        if not isinstance(node, Mapping):
            return
        is_object = _schema_allows_object(node)
        if is_object and node.get("additionalProperties") is not False:
            raise ValueError("generated_schema_invalid")
        for value in node.values():
            if isinstance(value, Mapping):
                visit(value)
            elif isinstance(value, list | tuple):
                for item in value:
                    visit(item)

    visit(schema)


def _branch_output_schema(branches: Sequence[_GeneratedBranch]) -> dict[str, Any]:
    result: dict[str, Any] = {"$defs": {}}
    definitions = result["$defs"]
    if not isinstance(definitions, dict):
        raise ValueError("generated_schema_invalid")
    for index, branch in enumerate(branches):
        success = _merge_schema_definitions(
            result,
            ProviderReadEnvelope.model_json_schema(by_alias=True),
            prefix=f"Envelope{index}_",
        )
        properties = success.get("properties")
        if not isinstance(properties, dict):
            raise ValueError("generated_schema_invalid")
        properties["capability_version"] = {"const": branch.capability_version}
        properties["data"] = _merge_schema_definitions(
            result,
            branch.public_output_schema,
            prefix=f"PublicData{index}_",
        )
        success_name = f"Success{index}"
        definitions[success_name] = success
    error = published_error_output_schema()
    definitions["MercuryV1ErrorOutput"] = _merge_schema_definitions(
        result,
        error,
        prefix="Error_",
    )
    result["oneOf"] = [
        *[{"$ref": f"#/$defs/Success{index}"} for index in range(len(branches))],
        {"$ref": "#/$defs/MercuryV1ErrorOutput"},
    ]
    _assert_closed_generated_contract(result)
    return result


class _GeneratedArguments(ArgModelBase):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    arguments: dict[str, JsonValue]
    _validator: ClassVar[Draft202012Validator]

    @model_validator(mode="before")
    @classmethod
    def validate_catalog_arguments(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("generated_schema_validation_failed")
        try:
            cls._validator.validate(dict(value))
        except Exception:
            raise ValueError("generated_schema_validation_failed") from None
        return {"arguments": dict(value)}


def _arguments_model(schema: Mapping[str, Any], *, name: str) -> type[_GeneratedArguments]:
    return type(
        f"{name}Arguments",
        (_GeneratedArguments,),
        {
            "__module__": __name__,
            "__annotations__": {"_validator": ClassVar[Draft202012Validator]},
            "_validator": Draft202012Validator(dict(schema), format_checker=FormatChecker()),
        },
    )


class GeneratedProviderToolPublisher:
    """Atomically project catalog read authority into one stable Mercury wrapper."""

    def __init__(
        self,
        server: FastMCP,
        *,
        execute: GeneratedReadExecutor,
        persist_schema_change: PersistSchemaChange | None = None,
        schema_drift_alert: SchemaDriftAlert | None = None,
    ) -> None:
        self._server = server
        self._execute = execute
        self._persist_schema_change = persist_schema_change
        self._schema_drift_alert = schema_drift_alert
        self._published: dict[str, str] = {}
        self._refresh_lock = asyncio.Lock()
        self._requested_generation = 0
        self._committed_generation = 0
        self._refresh_sessions: dict[int, object] = {}
        self._quarantined_versions: set[tuple[str, str, str, str, str]] = set()

    async def publish(
        self,
        qualifications: Sequence[ProviderMCPQualification],
        *,
        context: Context | object | None = None,
    ) -> bool:
        """Stage every branch before one rollback-safe, generation-serialized swap."""

        staged = self._stage(qualifications)
        self._requested_generation += 1
        request_generation = self._requested_generation
        self._remember_session(context)
        async with self._refresh_lock:
            if request_generation < self._requested_generation:
                return False
            changed = self._swap(staged)
            self._committed_generation = request_generation
            if changed:
                await self._notify_refresh_sessions()
            return changed

    async def reconcile(
        self,
        qualifications: Sequence[ProviderMCPQualification],
        *,
        context: Context | object | None = None,
    ) -> bool:
        """Retry safe exact drift transitions before publishing current authority."""

        current = tuple(ProviderMCPQualification.model_validate(item) for item in qualifications)
        for qualification in tuple(current):
            if (
                qualification.qualification_state is not QualificationState.ENABLED
                or _qualification_version_identity(qualification) not in self._quarantined_versions
            ):
                continue
            try:
                current = await self._persist_schema_transition(
                    qualification,
                    context if context is not None else object(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._emit_schema_drift_alert(qualification)
            else:
                self._quarantined_versions.discard(_qualification_version_identity(qualification))
        return await self.publish(current, context=context)

    def clear(self) -> bool:
        """Remove generated tools during static V1 disablement only."""

        changed = False
        for name in tuple(self._published):
            if self._server._tool_manager.get_tool(name) is not None:
                self._server.remove_tool(name)
            self._published.pop(name, None)
            changed = True
        return changed

    def _stage(
        self,
        qualifications: Sequence[ProviderMCPQualification],
    ) -> dict[str, _StagedTool]:
        grouped: dict[str, dict[str, _GeneratedBranch]] = {}
        for item in qualifications:
            qualification = ProviderMCPQualification.model_validate(item)
            if (
                qualification.qualification_state is not QualificationState.ENABLED
                or qualification.normalized_capability not in _READ_CAPABILITIES
            ):
                continue
            public_schema = public_output_schema(qualification.output_schema)
            branch = _GeneratedBranch(
                qualification=qualification,
                input_model=catalog_wire_model(qualification.input_schema, kind="input"),
                public_output_model=catalog_wire_model(public_schema, kind="output"),
                public_output_schema=public_schema,
            )
            versions = grouped.setdefault(_wrapper_name(qualification), {})
            existing = versions.get(branch.capability_version)
            if existing is not None and existing.identity != branch.identity:
                raise ValueError("generated_capability_ambiguous")
            versions.setdefault(branch.capability_version, branch)
        staged: dict[str, _StagedTool] = {}
        for name, versions in grouped.items():
            branches = tuple(
                branch for _, branch in sorted(versions.items(), key=lambda item: item[0])
            )
            input_schema = _branch_input_schema(branches, name)
            staged[name] = _StagedTool(
                name=name,
                branches=branches,
                input_schema=input_schema,
                output_schema=_branch_output_schema(branches),
                argument_model=_arguments_model(input_schema, name=name),
            )
        return staged

    def _swap(self, staged: Mapping[str, _StagedTool]) -> bool:
        target_identities = {name: item.identity for name, item in staged.items()}
        if target_identities == self._published:
            return False
        managed_names = set(self._published) | set(staged)
        previous_tools = {name: self._server._tool_manager.get_tool(name) for name in managed_names}
        previous_published = dict(self._published)
        try:
            for name in sorted(managed_names):
                registered = self._server._tool_manager.get_tool(name)
                if registered is not None:
                    self._server.remove_tool(name)
            for name in sorted(staged):
                self._register(staged[name])
            self._published = target_identities
        except BaseException:
            for name in managed_names:
                if self._server._tool_manager.get_tool(name) is not None:
                    self._server.remove_tool(name)
            for name, registered in previous_tools.items():
                if registered is not None:
                    self._server._tool_manager._tools[name] = registered
            self._published = previous_published
            raise
        return True

    def _register(self, staged: _StagedTool) -> None:
        branches = {branch.capability_version: branch for branch in staged.branches}

        async def generated_read_tool(
            context: Context,
            arguments: dict[str, JsonValue],
        ) -> ProviderReadEnvelope:
            try:
                self._remember_session(context)
                values = dict(arguments)
                workspace_id = UUID(str(values.pop("workspace_id")))
                connection_id = UUID(str(values.pop("connection_id")))
                capability_version = str(values.pop("capability_version"))
                branch = branches.get(capability_version)
                if branch is None:
                    raise ValueError("generated_schema_validation_failed")
                if (
                    _qualification_version_identity(branch.qualification)
                    in self._quarantined_versions
                ):
                    raise MercuryV1ToolError("capability_unavailable")
                inputs = branch.input_model.model_validate(values)
                result = ProviderReadEnvelope.model_validate(
                    await self._execute(
                        context,
                        workspace_id=workspace_id,
                        connection_id=connection_id,
                        capability_id=branch.qualification.normalized_capability,
                        capability_version=branch.capability_version,
                        inputs=inputs,
                    )
                )
                if (
                    result.capability_id != branch.qualification.normalized_capability
                    or result.capability_version != branch.capability_version
                ):
                    raise ValueError("generated_output_invalid")
                data = branch.public_output_model.model_validate(result.data).model_dump(
                    mode="json"
                )
                return result.model_copy(update={"data": data})
            except ProviderSchemaChanged as error:
                await self._persist_and_refresh(branch, context)
                raise MercuryV1ToolError(public_error_code(error)) from None
            except Exception as error:
                raise MercuryV1ToolError(public_error_code(error)) from None

        generated_read_tool.__name__ = staged.name
        self._server.add_tool(
            generated_read_tool,
            name=staged.name,
            description=(
                "Changes: none. External contact: a qualified provider read. "
                "Omitted options: provider credentials, provider routing, and mutations "
                "are not accepted."
            ),
            annotations=_CLOSED_READ,
            meta=_V1_GENERATED_META,
            structured_output=True,
        )
        set_input = getattr(self._server, "set_tool_input_contract", None)
        set_output = getattr(self._server, "set_tool_output_contract", None)
        if not callable(set_input) or not callable(set_output):
            raise TypeError("generated_tool_server_invalid")
        set_input(staged.name, argument_model=staged.argument_model, schema=staged.input_schema)
        set_output(staged.name, schema=staged.output_schema)

    async def _persist_and_refresh(
        self,
        branch: _GeneratedBranch,
        context: Context | object,
    ) -> None:
        identity = _qualification_version_identity(branch.qualification)
        self._quarantined_versions.add(identity)
        try:
            refreshed = await self._persist_schema_transition(branch.qualification, context)
            await self.publish(refreshed, context=context)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._emit_schema_drift_alert(branch.qualification)
            raise MercuryV1ToolError("capability_unavailable") from None
        self._quarantined_versions.discard(identity)

    async def _persist_schema_transition(
        self,
        qualification: ProviderMCPQualification,
        context: Context | object,
    ) -> tuple[ProviderMCPQualification, ...]:
        if self._persist_schema_change is None:
            raise ValueError("catalog_schema_transition_unavailable")
        refreshed = await _await_value(self._persist_schema_change(qualification, context))
        if not isinstance(refreshed, Sequence):
            raise ValueError("catalog_schema_transition_unavailable")
        checked = tuple(ProviderMCPQualification.model_validate(item) for item in refreshed)
        if not _schema_change_is_persisted(checked, qualification):
            raise ValueError("catalog_schema_transition_unavailable")
        return checked

    async def _emit_schema_drift_alert(
        self,
        qualification: ProviderMCPQualification,
    ) -> None:
        if self._schema_drift_alert is None:
            return
        try:
            await _await_value(
                self._schema_drift_alert(
                    {
                        "tool_name": "generated_provider_read",
                        "input": {
                            "capability_id": qualification.normalized_capability,
                            "capability_version": qualification.capability_version_sha256,
                        },
                        "output_summary": {
                            "provider": qualification.provider,
                            "environment": qualification.environment,
                            "status_class": "schema_changed",
                            "dispatch_certainty": "not_dispatched",
                            "alert": "catalog_transition_unavailable",
                        },
                        "status": "error",
                        "metadata": {"runtime": "mcp", "surface": "v1"},
                    }
                )
            )
        except Exception:
            return

    def _remember_session(self, context: Context | object | None) -> None:
        session = getattr(context, "session", None)
        if callable(getattr(session, "send_tool_list_changed", None)):
            self._refresh_sessions[id(session)] = session

    async def _notify_refresh_sessions(self) -> None:
        """Bounded best-effort notification for request sessions seen by this server."""

        for key, session in tuple(self._refresh_sessions.items()):
            try:
                result = session.send_tool_list_changed()
                if inspect.isawaitable(result):
                    async with asyncio.timeout(_REFRESH_NOTIFICATION_TIMEOUT_SECONDS):
                        await result
            except asyncio.CancelledError:
                raise
            except Exception:
                self._refresh_sessions.pop(key, None)


async def _await_value(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "GeneratedProviderToolPublisher",
    "catalog_wire_model",
    "project_provider_read_data",
    "public_output_schema",
]
