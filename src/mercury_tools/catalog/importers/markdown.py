from __future__ import annotations

import re

from mercury_tools.catalog.identity import validate_credential_safe_path
from mercury_tools.catalog.importers._common import (
    build_action,
    empty_input_schema,
    generated_operation_id,
    normalize_path,
    sort_actions,
)
from mercury_tools.catalog.models import CatalogAction, CatalogSource

_ENDPOINT = re.compile(
    r"^\s*(?:\|\s*)?(GET|POST|PUT|PATCH|DELETE)\s+(?:\|\s*)?(\/[^\s|]+)"
    r"(?:\s*(?:\||-|:)\s*(.*?))?\s*\|?\s*$",
    re.IGNORECASE,
)


def parse_markdown(text: str, source: CatalogSource, connector_id: str) -> list[CatalogAction]:
    actions: list[CatalogAction] = []
    for line in text.splitlines():
        match = _ENDPOINT.fullmatch(line)
        if not match:
            continue
        method = match.group(1).upper()
        validate_credential_safe_path(match.group(2).strip("`"))
        path = normalize_path(match.group(2).strip("`"))
        description = (match.group(3) or "").strip().strip("|").strip()
        schema = empty_input_schema()
        for name in re.findall(r"\{([^{}]+)\}", path):
            schema["path"][name] = {"type": "string"}
        actions.append(
            build_action(
                source=source,
                connector_id=connector_id,
                method=method,
                path_template=path,
                operation_id=generated_operation_id(method, path),
                confidence="inferred",
                description=description,
                input_schema=schema,
            )
        )
    if not actions:
        raise ValueError("unknown_spec_format")
    return sort_actions(actions)


def has_explicit_endpoints(text: str) -> bool:
    found = False
    for line in text.splitlines():
        match = _ENDPOINT.fullmatch(line)
        if match:
            validate_credential_safe_path(match.group(2).strip("`"))
            found = True
    return found
