"""Bounded, value-free response shape extraction."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MAX_DEPTH = 6
MAX_FIELDS = 128
MAX_PUBLIC_FIELD_NAME_LENGTH = 64

_TYPE_DESCRIPTORS = frozenset(
    {"boolean", "integer", "null", "number", "string", "truncated", "unknown"}
)
_PUBLIC_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "apisecret",
        "authorization",
        "authorizationheader",
        "authtoken",
        "authheader",
        "authenticationheader",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "idtoken",
        "password",
        "payload",
        "providerdocumentid",
        "providerid",
        "providerrecordid",
        "raw",
        "rawpayload",
        "rawresponse",
        "refreshtoken",
        "request",
        "requestbody",
        "response",
        "responsebody",
        "secret",
        "token",
    }
)
_PUBLIC_FORBIDDEN_FIELD_TOKENS = frozenset(
    {
        "authorization",
        "auth",
        "authentication",
        "cookie",
        "credential",
        "credentials",
        "directory",
        "filepath",
        "filename",
        "folder",
        "header",
        "headers",
        "local",
        "password",
        "path",
        "payload",
        "raw",
        "request",
        "response",
        "secret",
        "source",
        "token",
    }
)
_PUBLIC_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*$")
_PUBLIC_EMBEDDED_NUMERIC_ID = re.compile(r"\d{6,}")
_CAMEL_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_WORD_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def extract_response_shape(value: Any, *, depth: int = 0) -> dict[str, Any] | str:
    if depth >= MAX_DEPTH:
        return "truncated"
    if isinstance(value, Mapping):
        keys = sorted(value, key=str)[:MAX_FIELDS]
        return {
            str(key): extract_response_shape(value[key], depth=depth + 1)
            for key in keys
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "items": extract_response_shape(value[0], depth=depth + 1) if value else "unknown",
        }
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _validate_approved_public_response_shape(value: Any) -> None:
    if not _is_approved_public_response_shape(value, remaining_fields=[MAX_FIELDS]):
        raise ValueError("approved_public_response_shape_unsafe")


def _is_approved_public_response_shape(
    value: Any, *, depth: int = 0, remaining_fields: list[int] | None = None
) -> bool:
    if remaining_fields is None:
        remaining_fields = [MAX_FIELDS]
    if isinstance(value, str):
        return depth <= MAX_DEPTH and value in _TYPE_DESCRIPTORS
    if not isinstance(value, Mapping) or depth >= MAX_DEPTH or len(value) > MAX_FIELDS:
        return False

    if set(value) == {"type", "items"}:
        return value["type"] == "array" and _is_approved_public_response_shape(
            value["items"], depth=depth + 1, remaining_fields=remaining_fields
        )

    for field_name, field_shape in value.items():
        remaining_fields[0] -= 1
        if (
            remaining_fields[0] < 0
            or not _is_allowed_public_field_name(field_name)
            or not _is_approved_public_response_shape(
                field_shape,
                depth=depth + 1,
                remaining_fields=remaining_fields,
            )
        ):
            return False
    return True


def _is_allowed_public_field_name(field_name: Any) -> bool:
    if (
        not isinstance(field_name, str)
        or not 0 < len(field_name) <= MAX_PUBLIC_FIELD_NAME_LENGTH
        or _PUBLIC_FIELD_NAME.fullmatch(field_name) is None
        or _PUBLIC_EMBEDDED_NUMERIC_ID.search(field_name)
    ):
        return False

    normalized = "".join(character for character in field_name.lower() if character.isalnum())
    tokens = _public_field_name_tokens(field_name)
    return (
        normalized not in _PUBLIC_FORBIDDEN_FIELD_NAMES
        and tokens.isdisjoint(_PUBLIC_FORBIDDEN_FIELD_TOKENS)
        and not ({"provider", "id"} <= tokens)
    )


def _public_field_name_tokens(field_name: str) -> frozenset[str]:
    separated = _CAMEL_ACRONYM_BOUNDARY.sub(r"\1_\2", field_name)
    separated = _CAMEL_WORD_BOUNDARY.sub(r"\1_\2", separated)
    return frozenset(token.casefold() for token in separated.split("_") if token)
