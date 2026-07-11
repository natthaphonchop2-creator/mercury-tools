from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mercury_tools.catalog.importers._common import build_action, empty_input_schema, sort_actions
from mercury_tools.catalog.models import CatalogAction, CatalogSource

_METHODS = ("get", "post", "put", "patch", "delete")


def parse_openapi(
    document: dict[str, Any],
    source: CatalogSource,
    connector_id: str,
    *,
    swagger: bool | None = None,
) -> list[CatalogAction]:
    if swagger is None:
        swagger = document.get("swagger") == "2.0"
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("spec_paths_invalid")
    actions: list[CatalogAction] = []
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path, str) or not isinstance(path_item, Mapping):
            raise ValueError("spec_path_item_invalid")
        shared_parameters = path_item.get("parameters", [])
        for method in _METHODS:
            operation = path_item.get(method)
            if operation is None:
                continue
            if not isinstance(operation, Mapping):
                raise ValueError("spec_operation_invalid")
            schema, content_type = _input_schema(
                shared_parameters,
                operation,
                swagger=swagger,
            )
            success_codes, error_codes = _response_codes(operation.get("responses"))
            description = _description(operation)
            actions.append(
                build_action(
                    source=source,
                    connector_id=connector_id,
                    method=method.upper(),
                    path_template=path,
                    operation_id=_string(operation.get("operationId")),
                    confidence="exact",
                    description=description,
                    content_type=content_type,
                    input_schema=schema,
                    success_codes=success_codes,
                    error_codes=error_codes,
                )
            )
    if not actions:
        raise ValueError("spec_actions_empty")
    return sort_actions(actions)


def security_driver_suggestion(
    document: dict[str, Any],
    *,
    swagger: bool | None = None,
) -> dict[str, Any]:
    if swagger is None:
        swagger = document.get("swagger") == "2.0"
    if swagger:
        definitions = document.get("securityDefinitions", {})
    else:
        components = document.get("components", {})
        definitions = (
            components.get("securitySchemes", {})
            if isinstance(components, Mapping)
            else {}
        )
    if not isinstance(definitions, Mapping):
        return {}

    preferred = _referenced_security_names(document)
    names = preferred + [name for name in sorted(definitions) if name not in preferred]
    for name in names:
        scheme = definitions.get(name)
        if not isinstance(scheme, Mapping):
            continue
        suggestion = _security_scheme_suggestion(scheme, swagger=swagger)
        if suggestion:
            return suggestion
    return {}


def _input_schema(
    shared_parameters: Any,
    operation: Mapping[str, Any],
    *,
    swagger: bool,
) -> tuple[dict[str, Any], str]:
    result = empty_input_schema()
    parameters: list[Any] = []
    if isinstance(shared_parameters, list):
        parameters.extend(shared_parameters)
    operation_parameters = operation.get("parameters", [])
    if isinstance(operation_parameters, list):
        parameters.extend(operation_parameters)

    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            raise ValueError("spec_parameter_invalid")
        name = parameter.get("name")
        location = parameter.get("in")
        if not isinstance(name, str) or not isinstance(location, str):
            raise ValueError("spec_parameter_invalid")
        schema = parameter.get("schema")
        if not isinstance(schema, Mapping):
            schema = {
                key: parameter[key]
                for key in ("type", "format", "items", "enum", "description")
                if key in parameter
            }
        entry = dict(schema)
        if isinstance(parameter.get("description"), str):
            entry.setdefault("description", parameter["description"])
        if location == "body":
            result["body"] = dict(schema)
        elif location == "formData":
            target = "files" if parameter.get("type") == "file" else "body"
            result[target].setdefault("type", "object")
            result[target].setdefault("properties", {})[name] = entry
        elif location in {"path", "query"}:
            result[location][name] = entry
        elif location in {"header", "cookie"}:
            result["headers"][name] = entry

    content_type = "application/json"
    if swagger:
        consumes = operation.get("consumes")
        if isinstance(consumes, list) and consumes and isinstance(consumes[0], str):
            content_type = consumes[0]
    else:
        request_body = operation.get("requestBody")
        if isinstance(request_body, Mapping):
            content = request_body.get("content")
            if isinstance(content, Mapping) and content:
                selected = (
                    "application/json"
                    if "application/json" in content
                    else sorted(key for key in content if isinstance(key, str))[0]
                )
                media = content.get(selected)
                if isinstance(media, Mapping) and isinstance(media.get("schema"), Mapping):
                    result["body"] = dict(media["schema"])
                content_type = selected
    return result, content_type


def _response_codes(responses: Any) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if not isinstance(responses, Mapping):
        return (), ()
    success: set[int] = set()
    errors: set[int] = set()
    for raw_code in responses:
        try:
            code = int(raw_code)
        except (TypeError, ValueError):
            continue
        if 200 <= code < 300:
            success.add(code)
        elif 400 <= code < 600:
            errors.add(code)
    return tuple(sorted(success)), tuple(sorted(errors))


def _description(operation: Mapping[str, Any]) -> str:
    description = operation.get("description") or operation.get("summary")
    return description if isinstance(description, str) else ""


def _referenced_security_names(document: Mapping[str, Any]) -> list[str]:
    security = document.get("security")
    names: list[str] = []
    if isinstance(security, list):
        for requirement in security:
            if isinstance(requirement, Mapping):
                names.extend(name for name in requirement if isinstance(name, str))
    return sorted(set(names))


def _security_scheme_suggestion(
    scheme: Mapping[str, Any],
    *,
    swagger: bool,
) -> dict[str, Any]:
    scheme_type = _string(scheme.get("type")).casefold()
    auth_settings: dict[str, Any] = {}
    if scheme_type == "http":
        http_scheme = _string(scheme.get("scheme")).casefold()
        if http_scheme == "bearer":
            return _driver("bearer", {"key_name": "Authorization"})
        if http_scheme == "basic":
            return _driver("basic", {})
    if swagger and scheme_type == "basic":
        return _driver("basic", {})
    if scheme_type == "apikey":
        location = _string(scheme.get("in")).casefold()
        driver_id = {"header": "api_key_header", "query": "api_key_query"}.get(location)
        name = scheme.get("name")
        if driver_id and isinstance(name, str) and name:
            return _driver(driver_id, {"key_name": name})
    if scheme_type == "oauth2":
        token_url = ""
        scopes: Mapping[Any, Any] = {}
        if swagger and _string(scheme.get("flow")).casefold() == "application":
            token_url = _string(scheme.get("tokenUrl"))
            candidate_scopes = scheme.get("scopes")
            scopes = candidate_scopes if isinstance(candidate_scopes, Mapping) else {}
        elif not swagger:
            flows = scheme.get("flows")
            client_flow = flows.get("clientCredentials") if isinstance(flows, Mapping) else None
            if isinstance(client_flow, Mapping):
                token_url = _string(client_flow.get("tokenUrl"))
                candidate_scopes = client_flow.get("scopes")
                scopes = candidate_scopes if isinstance(candidate_scopes, Mapping) else {}
        if token_url:
            auth_settings = {
                "client_id_name": "client_id",
                "client_secret_name": "client_secret",
                "grant_type": "client_credentials",
                "token_url": token_url,
            }
            if scopes:
                auth_settings["scope"] = " ".join(sorted(str(scope) for scope in scopes))
            return _driver("oauth_client_credentials", auth_settings)
    return {}


def _driver(driver_id: str, auth_settings: dict[str, Any]) -> dict[str, Any]:
    return {"driver_id": driver_id, "auth_settings": auth_settings}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""
