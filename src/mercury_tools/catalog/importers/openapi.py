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
    if not isinstance(shared_parameters, list):
        raise ValueError("spec_parameters_invalid")
    parameters.extend(shared_parameters)
    operation_parameters = operation.get("parameters", [])
    if not isinstance(operation_parameters, list):
        raise ValueError("spec_parameters_invalid")
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
        required = _parameter_required(parameter, location)
        if required and location not in {"body", "formData", "header", "path", "query"}:
            raise ValueError("spec_required_parameter_location_unsupported")
        if location == "body":
            result["body"] = dict(schema)
            if required:
                result["body"]["x-mercury-required"] = True
        elif location == "formData":
            target = "files" if parameter.get("type") == "file" else "body"
            if target == "files":
                if required:
                    entry["required"] = True
                result["files"][name] = entry
            else:
                result["body"].setdefault("type", "object")
                result["body"].setdefault("properties", {})[name] = entry
                if required:
                    result["body"].setdefault("required", []).append(name)
        elif location in {"path", "query"}:
            if required:
                entry["required"] = True
            result[location][name] = entry
        elif location == "header":
            if required:
                entry["required"] = True
            result["headers"][name] = entry
        elif location == "cookie":
            result["headers"][name] = entry

    content_type = "application/json"
    if swagger:
        consumes = operation.get("consumes")
        if isinstance(consumes, list) and consumes and isinstance(consumes[0], str):
            content_type = consumes[0]
    else:
        request_body = operation.get("requestBody")
        if isinstance(request_body, Mapping):
            if "$ref" in request_body:
                raise ValueError("spec_request_body_reference_unsupported")
            body_required = _strict_required(
                request_body,
                error="spec_request_body_required_invalid",
            )
            content = request_body.get("content")
            if isinstance(content, Mapping) and content:
                selected = (
                    "application/json"
                    if "application/json" in content
                    else sorted(key for key in content if isinstance(key, str))[0]
                )
                media = content.get(selected)
                if isinstance(media, Mapping) and isinstance(media.get("schema"), Mapping):
                    body_schema = dict(media["schema"])
                    if selected.casefold() == "multipart/form-data":
                        body_schema, file_schema = _multipart_schema(body_schema)
                        result["files"] = file_schema
                    if body_required:
                        body_schema["x-mercury-required"] = True
                    result["body"] = body_schema
                content_type = selected
            if body_required and not result["body"]:
                raise ValueError("spec_required_request_body_schema_missing")
    _validate_object_required_contract(
        result["body"],
        error="spec_body_required_invalid",
    )
    return result, content_type


def _parameter_required(parameter: Mapping[str, Any], location: str) -> bool:
    required = _strict_required(parameter, error="spec_parameter_required_invalid")
    if location == "path" and not required:
        raise ValueError("spec_path_parameter_required")
    return required


def _strict_required(value: Mapping[str, Any], *, error: str) -> bool:
    if "required" not in value:
        return False
    required = value["required"]
    if not isinstance(required, bool):
        raise ValueError(error)
    return required


def _multipart_schema(schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_object_required_contract(
        schema,
        error="spec_multipart_required_invalid",
    )
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return schema, {}
    raw_required = schema.get("required", [])
    required = set(raw_required)
    body_properties: dict[str, Any] = {}
    files: dict[str, Any] = {}
    for name, declaration in properties.items():
        if not isinstance(name, str) or not isinstance(declaration, Mapping):
            raise ValueError("spec_multipart_schema_invalid")
        copied = dict(declaration)
        if copied.get("type") == "string" and copied.get("format") == "binary":
            if name in required:
                copied["required"] = True
            files[name] = copied
        else:
            body_properties[name] = copied
    body = dict(schema)
    body["properties"] = body_properties
    body_required = [name for name in raw_required if name in body_properties]
    if body_required:
        body["required"] = body_required
    else:
        body.pop("required", None)
    return body, files


def _validate_object_required_contract(schema: Any, *, error: str) -> None:
    if not isinstance(schema, Mapping):
        return
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError(error)
    if "required" in schema:
        required = schema["required"]
        if (
            not isinstance(required, list)
            or any(
                not isinstance(name, str)
                or not name
                or name != name.strip()
                for name in required
            )
            or len(required) != len(set(required))
            or any(name not in properties for name in required)
        ):
            raise ValueError(error)
    for declaration in properties.values():
        _validate_object_required_contract(declaration, error=error)
    _validate_object_required_contract(schema.get("items"), error=error)


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
