"""Schema-bound, credential-free request construction for catalog actions."""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from mercury_tools.catalog.identity import deep_freeze
from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.drivers.models import AuthContext
from mercury_tools.execution.models import canonical_payload_hash, render_action_path
from mercury_tools.execution.policy import effective_risk

_INPUT_SECTIONS = frozenset({"path", "query", "headers", "body", "files"})
_FORBIDDEN_HEADERS = frozenset(
    {"authorization", "cookie", "host", "proxy-authorization", "set-cookie"}
)
_PATH_VARIABLE = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_AUTH_TOKEN = re.compile(
    r"(?:^|[_\-.])(?:api[_-]?key|auth|authorization|credential|password|secret|token)"
    r"(?:$|[_\-.])",
    re.IGNORECASE,
)
_MAX_FILE_BYTES = 25 * 1024 * 1024


class RequestBuildError(ValueError):
    """A stable, credential-safe request construction failure."""


@dataclass(frozen=True, repr=False)
class _BoundFile:
    field_name: str
    path: Path
    root_index: int
    relative_path: str
    sha256: str
    filename: str
    content_type: str
    size: int


@dataclass(frozen=True, repr=False)
class RequestTemplate:
    """An immutable, credential-free request template bound to catalog identity."""

    action_id: str
    version_id: str
    connector_id: str
    method: str
    path_template: str
    final_path: str
    base_url: str
    repository_id: str | None
    environment: str | None
    _request_inputs: Mapping[str, Any] = field(repr=False)
    _body_present: bool = field(default=False, repr=False)
    _files: tuple[_BoundFile, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_request_inputs", deep_freeze(self._request_inputs))

    @property
    def request_inputs(self) -> dict[str, Any]:
        copied = _json_copy(self._request_inputs)
        if not isinstance(copied, dict):
            raise RequestBuildError("invalid_request_inputs")
        return copied

    @property
    def sanitized_summary(self) -> dict[str, Any]:
        return {
            "operation": self.action_id,
            "path": self.path_template,
            "query": {"count": len(self._request_inputs["query"])},
            "headers": {"count": len(self._request_inputs["headers"])},
            "body": _body_summary(
                self._request_inputs["body"],
                present=self._body_present,
            ),
            "files": {"count": len(self._files)},
        }

    def public_summary(self) -> dict[str, Any]:
        return deepcopy(self.sanitized_summary)

    def binding_payload(self) -> dict[str, Any]:
        if not self.repository_id or not self.environment:
            raise RequestBuildError("request_binding_context_required")
        risk = self._request_inputs["_risk"]
        return {
            "repository_id": self.repository_id,
            "connector_id": self.connector_id,
            "environment": self.environment,
            "action_id": self.action_id,
            "version_id": self.version_id,
            "method": self.method,
            "final_path": self.final_path,
            "request_inputs": self.request_inputs,
            "risk_tier": risk["tier"],
            "required_confirmations": risk["required_confirmations"],
        }

    def payload_hash(self) -> str:
        return canonical_payload_hash(self.binding_payload())

    def to_httpx_request(self, auth: AuthContext) -> httpx.Request:
        if not isinstance(auth, AuthContext):
            raise RequestBuildError("invalid_auth_context")
        query = dict(self._request_inputs["query"])
        headers = dict(self._request_inputs["headers"])
        if set(map(str.casefold, headers)) & set(map(str.casefold, auth.headers)):
            raise RequestBuildError("authentication_override_forbidden")
        if set(query) & set(auth.query):
            raise RequestBuildError("authentication_override_forbidden")
        headers.update(auth.headers)
        query.update(auth.query)

        url = self.base_url.rstrip("/") + self.final_path
        body = _json_copy(self._request_inputs["body"])
        if self._files:
            files: dict[str, tuple[str, bytes, str]] = {}
            for item in self._files:
                content = _read_bound_file(item)
                files[item.field_name] = (item.filename, content, item.content_type)
            data = body if isinstance(body, Mapping) else {}
            return httpx.Request(
                self.method,
                url,
                params=query,
                headers=headers,
                data=dict(data),
                files=files,
            )
        if not self._body_present:
            return httpx.Request(self.method, url, params=query, headers=headers)
        return httpx.Request(
            self.method,
            url,
            params=query,
            headers=headers,
            json=body,
        )


def build_request(
    action: CatalogAction,
    base_url: str,
    inputs: Mapping[str, Any],
    roots: Sequence[Path],
    *,
    repository_id: str | None = None,
    environment: str | None = None,
    _bound_inputs: bool = False,
) -> RequestTemplate:
    """Build one exact catalog request without loading or accepting auth data."""

    try:
        action = revalidate_catalog_action(action)
    except (AttributeError, TypeError, ValueError):
        raise RequestBuildError("invalid_catalog_action") from None
    if environment is not None and environment not in action.environments:
        raise RequestBuildError("action_environment_not_supported")
    normalized_base = _normalize_base_url(base_url)
    supplied = _input_mapping(inputs)
    if _bound_inputs:
        _validate_bound_target(supplied, normalized_base)
    elif "_target" in supplied or "_risk" in supplied:
        raise RequestBuildError("reserved_request_input")

    unknown_sections = set(supplied) - _INPUT_SECTIONS - (
        {"_target", "_risk"} if _bound_inputs else set()
    )
    if unknown_sections:
        raise RequestBuildError("undeclared_request_input")

    schema = action.input_schema
    if not isinstance(schema, Mapping) or set(schema) != _INPUT_SECTIONS:
        raise RequestBuildError("invalid_action_input_schema")

    path = _section_mapping(supplied.get("path", {}), "path")
    query = _section_mapping(supplied.get("query", {}), "query")
    headers = _section_mapping(supplied.get("headers", {}), "headers")
    files = _section_mapping(supplied.get("files", {}), "files")
    body_present = "body" in supplied
    body = _json_copy(supplied.get("body", {}))

    _reject_auth_overrides(query, headers)
    _validate_declared_mapping(path, schema["path"], "path")
    _validate_declared_mapping(query, schema["query"], "query")
    idempotency_header = _idempotency_header(action)
    headers_for_schema = {
        name: value
        for name, value in headers.items()
        if idempotency_header is None or name.casefold() != idempotency_header.casefold()
    }
    if idempotency_header is not None and not _bound_inputs and len(headers_for_schema) != len(
        headers
    ):
        raise RequestBuildError("idempotency_override_forbidden")
    _validate_declared_mapping(headers_for_schema, schema["headers"], "headers")
    _validate_body(body, schema["body"], present=body_present)
    _apply_idempotency(
        action,
        path=path,
        query=query,
        headers=headers,
        body=body,
        already_bound=_bound_inputs,
    )

    placeholders = frozenset(_PATH_VARIABLE.findall(action.path_template))
    if set(path) != placeholders:
        if placeholders - set(path):
            raise RequestBuildError("unresolved_path_parameter")
        raise RequestBuildError("undeclared_request_input")
    try:
        final_path = render_action_path(action.path_template, path)
    except ValueError:
        raise RequestBuildError("path_traversal") from None

    resolved_roots = _resolve_roots(roots)
    bound_files = _bound_files(
        files,
        schema["files"],
        resolved_roots,
        already_bound=_bound_inputs,
    )
    normalized_files = {
        item.field_name: {
            "root_index": item.root_index,
            "relative_path": item.relative_path,
            "sha256": item.sha256,
            "filename": item.filename,
            "content_type": item.content_type,
            "size": item.size,
        }
        for item in bound_files
    }
    risk = effective_risk(action)
    normalized_inputs = {
        "path": _json_copy(path),
        "query": _json_copy(query),
        "headers": _json_copy(headers),
        "body": _json_copy(body),
        "files": normalized_files,
        "_target": {"base_url": normalized_base},
        "_risk": {
            "tier": int(risk.tier),
            "required_confirmations": risk.required_confirmations,
        },
    }
    return RequestTemplate(
        action_id=action.action_id,
        version_id=action.version_id,
        connector_id=action.connector_id,
        method=action.method.value,
        path_template=action.path_template,
        final_path=final_path,
        base_url=normalized_base,
        repository_id=repository_id,
        environment=environment,
        _request_inputs=normalized_inputs,
        _body_present=body_present,
        _files=bound_files,
    )


def rebuild_bound_request(
    action: CatalogAction,
    base_url: str,
    inputs: Mapping[str, Any],
    roots: Sequence[Path],
    *,
    repository_id: str,
    environment: str,
) -> RequestTemplate:
    return build_request(
        action,
        base_url,
        inputs,
        roots,
        repository_id=repository_id,
        environment=environment,
        _bound_inputs=True,
    )


def _input_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RequestBuildError("invalid_request_inputs")
    return dict(value)


def _section_mapping(value: Any, section: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RequestBuildError(f"invalid_{section}_inputs")
    return dict(value)


def _normalize_base_url(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise RequestBuildError("invalid_base_url")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise RequestBuildError("invalid_base_url") from None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or "\\" in value
    ):
        raise RequestBuildError("invalid_base_url")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise RequestBuildError("invalid_base_url")
    if parsed.netloc.endswith(":"):
        raise RequestBuildError("invalid_base_url")
    path = parsed.path.rstrip("/")
    _reject_base_path(path)
    host = parsed.hostname.rstrip(".").casefold()
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    port = "" if (parsed.port or default_port) == default_port else f":{parsed.port}"
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme.casefold()}://{rendered_host}{port}{path}"


def _validate_bound_target(inputs: Mapping[str, Any], base_url: str) -> None:
    target = inputs.get("_target")
    if not isinstance(target, Mapping) or set(target) != {"base_url"}:
        raise RequestBuildError("bound_target_missing")
    stored = target.get("base_url")
    if not isinstance(stored, str) or stored != base_url:
        raise RequestBuildError("bound_target_changed")


def _validate_declared_mapping(values: Mapping[str, Any], schema: Any, section: str) -> None:
    if not isinstance(schema, Mapping):
        raise RequestBuildError("invalid_action_input_schema")
    unknown = set(values) - set(schema)
    if unknown:
        raise RequestBuildError("undeclared_request_input")
    required: set[str] = set()
    for name, declaration in schema.items():
        if not isinstance(name, str) or not isinstance(declaration, Mapping):
            raise RequestBuildError("invalid_action_input_schema")
        marker = declaration.get("required", False)
        if not isinstance(marker, bool):
            raise RequestBuildError("invalid_action_input_schema")
        if marker:
            required.add(name)
    if required - set(values):
        error = {
            "path": "unresolved_path_parameter",
            "query": "required_query_parameter_missing",
            "headers": "required_header_parameter_missing",
        }[section]
        raise RequestBuildError(error)
    for name, value in values.items():
        declaration = schema[name]
        if isinstance(declaration, Mapping):
            _validate_type(value, declaration, section)


def _validate_body(value: Any, schema: Any, *, present: bool) -> None:
    if not isinstance(schema, Mapping):
        raise RequestBuildError("invalid_action_input_schema")
    _validate_body_required_contract(schema, top_level=True)
    if schema.get("x-mercury-required", False) and not present:
        raise RequestBuildError("required_body_missing")
    if not present:
        return
    if not schema:
        if value not in ({}, None):
            raise RequestBuildError("undeclared_request_input")
        return
    _validate_type(value, schema, "body")
    if isinstance(value, Mapping):
        required = schema.get("required", ())
        if any(name not in value for name in required):
            raise RequestBuildError("required_body_field_missing")
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            if schema.get("additionalProperties") is False and set(value) - set(properties):
                raise RequestBuildError("undeclared_request_input")
            for name, item in value.items():
                declaration = properties.get(name)
                if isinstance(declaration, Mapping):
                    _validate_type(item, declaration, "body")


def _validate_body_required_contract(schema: Mapping[str, Any], *, top_level: bool) -> None:
    if "x-mercury-required" in schema:
        marker = schema["x-mercury-required"]
        if not top_level or not isinstance(marker, bool):
            raise RequestBuildError("invalid_action_input_schema")
    if "required" in schema:
        required = schema["required"]
        properties = schema.get("properties")
        if (
            not isinstance(required, (list, tuple))
            or any(not isinstance(name, str) for name in required)
            or len(required) != len(set(required))
            or not isinstance(properties, Mapping)
            or any(name not in properties for name in required)
        ):
            raise RequestBuildError("invalid_action_input_schema")
    properties = schema.get("properties", {})
    if isinstance(properties, Mapping):
        for declaration in properties.values():
            if isinstance(declaration, Mapping):
                _validate_body_required_contract(declaration, top_level=False)
    items = schema.get("items")
    if isinstance(items, Mapping):
        _validate_body_required_contract(items, top_level=False)


def _validate_type(value: Any, schema: Mapping[str, Any], section: str) -> None:
    expected = schema.get("type")
    valid = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, (list, tuple)),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value)),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(expected, str) and expected in valid and not valid[expected]:
        raise RequestBuildError(f"invalid_{section}_value")
    enum = schema.get("enum")
    if isinstance(enum, (list, tuple)) and value not in enum:
        raise RequestBuildError(f"invalid_{section}_value")


def _reject_auth_overrides(query: Mapping[str, Any], headers: Mapping[str, Any]) -> None:
    for name, value in headers.items():
        if (
            name.casefold() in _FORBIDDEN_HEADERS
            or _looks_like_auth_key(name)
            or not isinstance(value, str)
            or "\r" in name
            or "\n" in name
            or "\r" in value
            or "\n" in value
        ):
            raise RequestBuildError("authentication_override_forbidden")
    if any(_looks_like_auth_key(name) for name in query):
        raise RequestBuildError("authentication_override_forbidden")


def _idempotency_header(action: CatalogAction) -> str | None:
    header = action.idempotency.get("header_name")
    source = action.idempotency.get("source")
    if header is None and source is None:
        return None
    if (
        not isinstance(header, str)
        or _HEADER_NAME.fullmatch(header) is None
        or header.casefold() in _FORBIDDEN_HEADERS
        or _looks_like_auth_key(header)
        or not isinstance(source, str)
        or not source
    ):
        raise RequestBuildError("invalid_idempotency_binding")
    return header


def _apply_idempotency(
    action: CatalogAction,
    *,
    path: Mapping[str, Any],
    query: Mapping[str, Any],
    headers: dict[str, Any],
    body: Any,
    already_bound: bool,
) -> None:
    header = _idempotency_header(action)
    if header is None:
        return
    source = action.idempotency["source"]
    root_name, separator, remainder = source.partition(".")
    roots = {"path": path, "query": query, "body": body}
    if not separator or root_name not in roots:
        raise RequestBuildError("invalid_idempotency_binding")
    current: Any = roots[root_name]
    for part in remainder.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise RequestBuildError("idempotency_binding_missing")
        current = current[part]
    if (
        isinstance(current, bool)
        or not isinstance(current, (str, int))
        or not str(current)
        or len(str(current).encode("utf-8")) > 256
    ):
        raise RequestBuildError("invalid_idempotency_binding")
    expected = str(current)
    existing_names = [name for name in headers if name.casefold() == header.casefold()]
    if already_bound:
        if len(existing_names) != 1 or headers[existing_names[0]] != expected:
            raise RequestBuildError("bound_idempotency_changed")
        if existing_names[0] != header:
            headers[header] = headers.pop(existing_names[0])
        return
    if existing_names:
        raise RequestBuildError("idempotency_override_forbidden")
    headers[header] = expected


def _looks_like_auth_key(value: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).casefold()
    return _AUTH_TOKEN.search(normalized) is not None


def _resolve_roots(roots: Sequence[Path]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for root in roots:
        try:
            path = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise RequestBuildError("invalid_mcp_root") from None
        if not path.is_dir():
            raise RequestBuildError("invalid_mcp_root")
        resolved.append(path)
    return tuple(resolved)


def _bound_files(
    values: Mapping[str, Any],
    schema: Any,
    roots: tuple[Path, ...],
    *,
    already_bound: bool,
) -> tuple[_BoundFile, ...]:
    if not isinstance(schema, Mapping):
        raise RequestBuildError("invalid_action_input_schema")
    if set(values) - set(schema):
        raise RequestBuildError("undeclared_request_input")
    required: set[str] = set()
    for field_name, declaration in schema.items():
        if not isinstance(field_name, str) or not isinstance(declaration, Mapping):
            raise RequestBuildError("invalid_action_input_schema")
        marker = declaration.get("required", False)
        if not isinstance(marker, bool):
            raise RequestBuildError("invalid_action_input_schema")
        if marker:
            required.add(field_name)
    if required - set(values):
        raise RequestBuildError("required_file_missing")
    bound: list[_BoundFile] = []
    for field_name, value in sorted(values.items()):
        if already_bound:
            bound.append(_restore_bound_file(field_name, value, roots))
        else:
            bound.append(_bind_file(field_name, value, roots))
    return tuple(bound)


def _bind_file(field_name: str, value: Any, roots: tuple[Path, ...]) -> _BoundFile:
    if not isinstance(value, (str, Path)):
        raise RequestBuildError("invalid_file_input")
    try:
        candidate = Path(value).expanduser()
        if candidate.is_symlink():
            raise RequestBuildError("file_symlink_forbidden")
        path = candidate.resolve(strict=True)
    except RequestBuildError:
        raise
    except (OSError, RuntimeError):
        raise RequestBuildError("invalid_file_input") from None
    if not path.is_file():
        raise RequestBuildError("invalid_file_input")
    for index, root in enumerate(roots):
        if path.is_relative_to(root):
            relative = path.relative_to(root).as_posix()
            return _file_record(field_name, path, index, relative)
    raise RequestBuildError("file_outside_roots")


def _restore_bound_file(
    field_name: str,
    value: Any,
    roots: tuple[Path, ...],
) -> _BoundFile:
    expected = {
        "root_index",
        "relative_path",
        "sha256",
        "filename",
        "content_type",
        "size",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RequestBuildError("invalid_bound_file")
    root_index = value["root_index"]
    relative_path = value["relative_path"]
    if (
        isinstance(root_index, bool)
        or not isinstance(root_index, int)
        or not 0 <= root_index < len(roots)
        or not isinstance(relative_path, str)
    ):
        raise RequestBuildError("invalid_bound_file")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise RequestBuildError("path_traversal")
    candidate = roots[root_index] / relative
    try:
        if candidate.is_symlink():
            raise RequestBuildError("file_symlink_forbidden")
        path = candidate.resolve(strict=True)
    except RequestBuildError:
        raise
    except (OSError, RuntimeError):
        raise RequestBuildError("bound_file_changed") from None
    if not path.is_file() or not path.is_relative_to(roots[root_index]):
        raise RequestBuildError("bound_file_changed")
    current = _file_record(field_name, path, root_index, relative.as_posix())
    stored = {
        "root_index": root_index,
        "relative_path": relative_path,
        "sha256": value["sha256"],
        "filename": value["filename"],
        "content_type": value["content_type"],
        "size": value["size"],
    }
    actual = {
        "root_index": current.root_index,
        "relative_path": current.relative_path,
        "sha256": current.sha256,
        "filename": current.filename,
        "content_type": current.content_type,
        "size": current.size,
    }
    if stored != actual:
        raise RequestBuildError("bound_file_changed")
    return current


def _file_record(
    field_name: str,
    path: Path,
    root_index: int,
    relative_path: str,
) -> _BoundFile:
    digest, size = _hash_file(path)
    return _BoundFile(
        field_name=field_name,
        path=path,
        root_index=root_index,
        relative_path=relative_path,
        sha256=digest,
        filename=path.name,
        content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size=size,
    )


def _hash_file(path: Path) -> tuple[str, int]:
    _, digest, size = _read_file_snapshot(path)
    return digest, size


def _read_file_snapshot(path: Path) -> tuple[bytes, str, int]:
    digest = hashlib.sha256()
    size = 0
    chunks: list[bytes] = []
    file_fd = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(path, flags)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink < 1:
            raise RequestBuildError("invalid_file_input")
        while True:
            chunk = os.read(file_fd, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > _MAX_FILE_BYTES:
                raise RequestBuildError("file_too_large")
            digest.update(chunk)
        after = os.fstat(file_fd)
        fingerprint_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        fingerprint_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if fingerprint_before != fingerprint_after or size != after.st_size:
            raise RequestBuildError("bound_file_changed")
    except RequestBuildError:
        raise
    except OSError:
        raise RequestBuildError("invalid_file_input") from None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
    return b"".join(chunks), digest.hexdigest(), size


def _read_bound_file(item: _BoundFile) -> bytes:
    content, digest, size = _read_file_snapshot(item.path)
    if digest != item.sha256 or size != item.size:
        raise RequestBuildError("bound_file_changed")
    return content


def _reject_base_path(path: str) -> None:
    if "//" in path:
        raise RequestBuildError("path_traversal")
    for encoded in path.split("/"):
        decoded = encoded
        for _ in range(len(encoded) + 2):
            if (
                decoded in {".", ".."}
                or "/" in decoded
                or "\\" in decoded
                or any(unicodedata.category(character) == "Cc" for character in decoded)
            ):
                raise RequestBuildError("path_traversal")
            next_value = unquote(decoded, encoding="utf-8", errors="strict")
            if next_value == decoded:
                break
            decoded = next_value
        else:
            raise RequestBuildError("path_traversal")


def _json_copy(value: Any) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError):
        raise RequestBuildError("request_input_not_json") from None


def _body_summary(value: Any, *, present: bool) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {"has_body": present, "count": len(value)}
    if isinstance(value, (list, tuple)):
        return {"has_body": present, "count": len(value)}
    return {"has_body": present, "count": 0}


__all__ = [
    "RequestBuildError",
    "RequestTemplate",
    "build_request",
    "rebuild_bound_request",
]
