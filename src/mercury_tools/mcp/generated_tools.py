"""Catalog-generated, Mercury-owned wrappers for qualified provider reads."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
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

_READ_CAPABILITIES = frozenset(
    {
        "provider_profile.get",
        "documents.invoice.list",
        "documents.invoice.get",
    }
)
_SENSITIVE_FIELD = re.compile(
    r"(?:^|_)(?:id|identifier|email|phone|contact|address|tax|tin|vat)(?:$|_)",
    re.IGNORECASE,
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


def _schema_fingerprint(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(schema)).encode("utf-8")).hexdigest()


def _schema_copy(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Thaw a catalog schema without mutating its deep-frozen authority record."""

    return json.loads(canonical_json(dict(schema)))


def _assert_closed_schema(schema: Mapping[str, Any]) -> None:
    seen: set[int] = set()

    def visit(node: object) -> None:
        if not isinstance(node, Mapping):
            return
        marker = id(node)
        if marker in seen:
            return
        seen.add(marker)
        if "$id" in node:
            raise ValueError("generated_schema_invalid")
        reference = node.get("$ref")
        if reference is not None and (
            not isinstance(reference, str) or not reference.startswith("#/$defs/")
        ):
            raise ValueError("generated_schema_invalid")
        schema_type = node.get("type")
        is_object = schema_type == "object" or "properties" in node
        pattern_properties = node.get("patternProperties")
        if is_object and (
            node.get("additionalProperties") is not False
            or node.get("unevaluatedProperties", False) not in {False, None}
            or (pattern_properties is not None and pattern_properties != {})
        ):
            raise ValueError("generated_schema_invalid")
        for value in node.values():
            if isinstance(value, Mapping):
                visit(value)
            elif isinstance(value, list | tuple):
                for item in value:
                    visit(item)

    try:
        Draft202012Validator.check_schema(dict(schema))
        visit(schema)
    except Exception:
        raise ValueError("generated_schema_invalid") from None


class _CatalogWireModel(BaseModel):
    """A frozen model whose wire contract is the reviewed catalog JSON Schema."""

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
    """Return a stable internal model that validates one immutable catalog schema."""

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


def sanitize_provider_read_data(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Mask provider identifiers and personal or tax data before the public envelope."""

    def sanitize(item: Any, *, key: str | None = None) -> JsonValue:
        if key is not None and _SENSITIVE_FIELD.search(key):
            return "[REDACTED]"
        if isinstance(item, Mapping):
            return {
                str(child_key): sanitize(child, key=str(child_key))
                for child_key, child in item.items()
            }
        if isinstance(item, list | tuple):
            return [sanitize(child) for child in item]
        return item

    sanitized = sanitize(value)
    if not isinstance(sanitized, dict):
        raise ValueError("generated_output_invalid")
    return sanitized


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
    if not isinstance(definitions, Mapping):
        raise ValueError("generated_schema_invalid")
    renamed = {name: f"{prefix}{name}" for name in definitions}

    def rewrite(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str) and value.startswith("#/$defs/"):
            name = value.rsplit("/", 1)[-1]
            if name in renamed:
                return f"#/$defs/{renamed[name]}"
        return value

    target_definitions = container.setdefault("$defs", {})
    if not isinstance(target_definitions, dict):
        raise ValueError("generated_schema_invalid")
    for name, definition in definitions.items():
        target_definitions[renamed[name]] = rewrite(definition)
    return rewrite(embedded)


def _input_schema(qualification: ProviderMCPQualification) -> dict[str, Any]:
    source = _schema_copy(qualification.input_schema)
    _assert_closed_schema(source)
    properties = source.get("properties")
    required = source.get("required", [])
    if (
        source.get("type") != "object"
        or not isinstance(properties, Mapping)
        or not isinstance(required, list | tuple)
        or {"workspace_id", "connection_id"} & set(properties)
    ):
        raise ValueError("generated_schema_invalid")
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "workspace_id": {"type": "string", "format": "uuid"},
            "connection_id": {"type": "string", "format": "uuid"},
            **copy.deepcopy(dict(properties)),
        },
        "required": ["workspace_id", "connection_id", *required],
        "title": f"{_wrapper_name(qualification)}Arguments",
    }
    _merge_schema_definitions(schema, source, prefix="MercuryInput")
    return schema


def _output_schema(qualification: ProviderMCPQualification) -> dict[str, Any]:
    source = _schema_copy(qualification.output_schema)
    catalog_wire_model(source, kind="output")
    success = ProviderReadEnvelope.model_json_schema(by_alias=True)
    properties = success.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("generated_schema_invalid")
    data_schema = _merge_schema_definitions(success, source, prefix="MercuryData")
    definitions = success.setdefault("$defs", {})
    if not isinstance(definitions, dict):
        raise ValueError("generated_schema_invalid")
    data_definition = f"MercuryData{qualification.capability_version_sha256[:12]}"
    definitions[data_definition] = data_schema
    properties["data"] = {"$ref": f"#/$defs/{data_definition}"}
    error = published_error_output_schema()
    success_definitions = success.pop("$defs", {})
    error_definitions = error.pop("$defs", {})
    if (
        not isinstance(success_definitions, Mapping)
        or not isinstance(error_definitions, Mapping)
        or set(success_definitions) & set(error_definitions)
    ):
        raise ValueError("generated_schema_invalid")
    return {
        "$defs": {
            **success_definitions,
            **error_definitions,
            "Success": success,
            "MercuryV1ErrorOutput": error,
        },
        "oneOf": [
            {"$ref": "#/$defs/Success"},
            {"$ref": "#/$defs/MercuryV1ErrorOutput"},
        ],
    }


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
            "_validator": Draft202012Validator(
                dict(schema),
                format_checker=FormatChecker(),
            ),
        },
    )


class GeneratedProviderToolPublisher:
    """Publish only exact enabled read versions and remove superseded wrappers."""

    def __init__(self, server: FastMCP, *, execute: GeneratedReadExecutor) -> None:
        self._server = server
        self._execute = execute
        self._published: dict[str, tuple[str, str]] = {}
        self._drifted_versions: set[str] = set()

    async def publish(
        self,
        qualifications: Sequence[ProviderMCPQualification],
        *,
        context: Context | object | None = None,
    ) -> bool:
        desired = self._desired(qualifications)
        changed = False
        for name in sorted(set(self._published) - set(desired)):
            if self._server._tool_manager.get_tool(name) is not None:
                self._server.remove_tool(name)
            self._published.pop(name, None)
            changed = True
        for name, qualification in desired.items():
            identity = (
                qualification.capability_version_sha256,
                _schema_fingerprint(qualification.input_schema),
            )
            if self._published.get(name) == identity:
                continue
            if self._server._tool_manager.get_tool(name) is not None:
                self._server.remove_tool(name)
            self._register(name, qualification)
            self._published[name] = identity
            changed = True
        if changed and context is not None:
            await self._notify(context)
        return changed

    def clear(self) -> bool:
        changed = False
        for name in tuple(self._published):
            if self._server._tool_manager.get_tool(name) is not None:
                self._server.remove_tool(name)
            self._published.pop(name, None)
            changed = True
        return changed

    def _desired(
        self,
        qualifications: Sequence[ProviderMCPQualification],
    ) -> dict[str, ProviderMCPQualification]:
        selected: dict[str, ProviderMCPQualification] = {}
        for item in qualifications:
            qualification = ProviderMCPQualification.model_validate(item)
            if (
                qualification.qualification_state is not QualificationState.ENABLED
                or qualification.normalized_capability not in _READ_CAPABILITIES
                or qualification.capability_version_sha256 in self._drifted_versions
            ):
                continue
            name = _wrapper_name(qualification)
            if name in selected:
                raise ValueError("generated_capability_ambiguous")
            catalog_wire_model(qualification.input_schema, kind="input")
            catalog_wire_model(qualification.output_schema, kind="output")
            selected[name] = qualification
        return selected

    def _register(self, name: str, qualification: ProviderMCPQualification) -> None:
        public_input_schema = _input_schema(qualification)
        public_output_schema = _output_schema(qualification)
        input_model = catalog_wire_model(qualification.input_schema, kind="input")
        argument_model = _arguments_model(public_input_schema, name=name)

        async def generated_read_tool(
            context: Context,
            arguments: dict[str, JsonValue],
        ) -> ProviderReadEnvelope:
            try:
                workspace_id = UUID(str(arguments.pop("workspace_id")))
                connection_id = UUID(str(arguments.pop("connection_id")))
                inputs = input_model.model_validate(arguments)
                return await self._execute(
                    context,
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    qualification=qualification,
                    inputs=inputs,
                )
            except ProviderSchemaChanged as error:
                await self._unpublish_drifted(
                    name,
                    qualification.capability_version_sha256,
                    context,
                )
                raise MercuryV1ToolError(public_error_code(error)) from None
            except Exception as error:
                raise MercuryV1ToolError(public_error_code(error)) from None

        generated_read_tool.__name__ = name
        self._server.add_tool(
            generated_read_tool,
            name=name,
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
        set_input(name, argument_model=argument_model, schema=public_input_schema)
        set_output(name, schema=public_output_schema)

    async def _unpublish_drifted(
        self,
        name: str,
        capability_version: str,
        context: Context,
    ) -> None:
        asyncio.get_running_loop().call_soon(
            self._remove_drifted,
            name,
            capability_version,
            context,
        )

    def _remove_drifted(
        self,
        name: str,
        capability_version: str,
        context: Context,
    ) -> None:
        published = self._published.get(name)
        if published is None or published[0] != capability_version:
            return
        if self._server._tool_manager.get_tool(name) is not None:
            self._server.remove_tool(name)
        self._published.pop(name, None)
        self._drifted_versions.add(capability_version)
        session = getattr(context, "session", None)
        notifier = getattr(session, "send_tool_list_changed", None)
        if not callable(notifier):
            return
        notification = notifier()
        if inspect.isawaitable(notification):
            asyncio.get_running_loop().create_task(notification)

    @staticmethod
    async def _notify(context: Context | object) -> None:
        session = getattr(context, "session", None)
        notifier = getattr(session, "send_tool_list_changed", None)
        if not callable(notifier):
            return
        result = notifier()
        if inspect.isawaitable(result):
            await result


__all__ = [
    "GeneratedProviderToolPublisher",
    "catalog_wire_model",
    "sanitize_provider_read_data",
]
