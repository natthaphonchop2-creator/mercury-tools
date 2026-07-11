from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from mercury_tools.catalog.importers._common import (
    build_action,
    empty_input_schema,
    generated_operation_id,
    normalize_path,
    sort_actions,
)
from mercury_tools.catalog.models import CatalogAction, CatalogSource

_SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def parse_postman(
    document: dict[str, Any],
    source: CatalogSource,
    connector_id: str,
) -> list[CatalogAction]:
    actions: list[CatalogAction] = []
    for item in _request_items(document.get("item")):
        request = item.get("request")
        if not isinstance(request, Mapping):
            continue
        method = request.get("method")
        if not isinstance(method, str) or method.upper() not in _SUPPORTED_METHODS:
            continue
        method = method.upper()
        path = _request_path(request.get("url"))
        schema, examples = _request_schema(request)
        name = item.get("name")
        description = _description(request.get("description"), name)
        operation_id = _operation_id(name, method, path)
        actions.append(
            build_action(
                source=source,
                connector_id=connector_id,
                method=method,
                path_template=path,
                operation_id=operation_id,
                confidence="example_derived",
                description=description,
                input_schema=schema,
                examples=examples,
            )
        )
    if not actions:
        raise ValueError("spec_actions_empty")
    return sort_actions(actions)


def _request_items(value: Any):
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, Mapping):
            continue
        if "request" in item:
            yield item
        yield from _request_items(item.get("item"))


def _request_path(value: Any) -> str:
    if isinstance(value, str):
        return normalize_path(_path_from_raw(value))
    if not isinstance(value, Mapping):
        raise ValueError("postman_url_invalid")
    path = value.get("path")
    if isinstance(path, list) and all(isinstance(part, str) for part in path):
        return normalize_path("/" + "/".join(path))
    raw = value.get("raw")
    if isinstance(raw, str):
        return normalize_path(_path_from_raw(raw))
    raise ValueError("postman_url_invalid")


def _path_from_raw(raw: str) -> str:
    without_variable = re.sub(r"^\{\{[^{}]+\}\}", "", raw)
    if "://" in without_variable:
        return urlsplit(without_variable).path
    return without_variable.split("?", 1)[0]


def _request_schema(
    request: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    schema = empty_input_schema()
    headers = request.get("header")
    if isinstance(headers, list):
        for header in headers:
            if isinstance(header, Mapping) and isinstance(header.get("key"), str):
                schema["headers"][header["key"]] = {
                    "type": "string",
                    "description": _string(header.get("description")),
                }

    url = request.get("url")
    if isinstance(url, Mapping) and isinstance(url.get("query"), list):
        for parameter in url["query"]:
            if isinstance(parameter, Mapping) and isinstance(parameter.get("key"), str):
                schema["query"][parameter["key"]] = {
                    "type": "string",
                    "description": _string(parameter.get("description")),
                }

    path = _request_path(url)
    for name in re.findall(r"\{([^{}]+)\}", path):
        schema["path"][name] = {"type": "string"}

    examples: tuple[dict[str, Any], ...] = ()
    body = request.get("body")
    if isinstance(body, Mapping):
        mode = body.get("mode")
        if mode == "raw" and isinstance(body.get("raw"), str):
            try:
                decoded = json.loads(body["raw"])
            except json.JSONDecodeError:
                schema["body"] = {"type": "string"}
            else:
                schema["body"] = _infer_schema(decoded)
                if isinstance(decoded, Mapping):
                    examples = ({"body": dict(decoded)},)
        elif mode in {"urlencoded", "formdata"} and isinstance(body.get(mode), list):
            target = "files" if mode == "formdata" else "body"
            schema[target] = {"type": "object", "properties": {}}
            for field in body[mode]:
                if isinstance(field, Mapping) and isinstance(field.get("key"), str):
                    schema[target]["properties"][field["key"]] = {"type": "string"}
    return schema, examples


def _infer_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "properties": {str(key): _infer_schema(item) for key, item in value.items()},
        }
    if isinstance(value, list):
        item_schema = _infer_schema(value[0]) if value else {}
        return {"type": "array", "items": item_schema}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": "string"}


def _operation_id(name: Any, method: str, path: str) -> str:
    if isinstance(name, str) and name.strip():
        words = re.findall(r"[A-Za-z0-9]+", name)
        if words:
            return words[0].casefold() + "".join(word.title() for word in words[1:])
    return generated_operation_id(method, path)


def _description(value: Any, name: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("content"), str):
        return value["content"]
    return name if isinstance(name, str) else ""


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""
