"""Dry-run-first cleanup for legacy Cloud ERP secrets and write requests."""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

CONFIRMATION = "DELETE_SERVER_ERP_SECRETS"
PAGE_SIZE = 500
PROFILE_SECRET_KEYS = frozenset(
    {
        "server_vault",
        "credential_fingerprints",
        "credential_fields",
        "credentials_configured",
        "credentials_configured_at",
        "credential_storage",
    }
)
SCAN_TARGETS: dict[str, tuple[str, ...]] = {
    "mercury_skill_uploads": ("markdown",),
    "knowledge_documents": ("body",),
    "knowledge_chunks": ("chunk_text",),
    "mercury_product_events": ("summary", "metadata"),
    "mcp_audit_events": ("output_summary", "metadata"),
}


class PurgeClient(Protocol):
    def list_rows(self, table: str, select: str) -> list[dict[str, Any]]: ...

    def patch_row(self, table: str, row_id: str, payload: dict[str, Any]) -> None: ...

    def delete_row(self, table: str, row_id: str) -> None: ...


@dataclass(frozen=True)
class SupabaseRestClient:
    base_url: str
    service_role_key: str

    @classmethod
    def from_environment(cls) -> SupabaseRestClient:
        base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not base_url or not service_role_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")
        return cls(f"{base_url}/rest/v1", service_role_key)

    def _request(self, method: str, table: str, **kwargs: Any) -> httpx.Response:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
            **kwargs.pop("headers", {}),
        }
        response = httpx.request(
            method,
            f"{self.base_url}/{table}",
            headers=headers,
            timeout=60,
            **kwargs,
        )
        if response.status_code >= 300:
            raise RuntimeError(f"Supabase purge request failed: HTTP {response.status_code}")
        return response

    def list_rows(self, table: str, select: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self._request(
                "GET",
                table,
                params={
                    "select": select,
                    "limit": str(PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            page = response.json()
            if not isinstance(page, list):
                raise RuntimeError("Supabase purge response was not a row list.")
            rows.extend(row for row in page if isinstance(row, dict))
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    def patch_row(self, table: str, row_id: str, payload: dict[str, Any]) -> None:
        self._request(
            "PATCH",
            table,
            params={"id": f"eq.{quote(row_id, safe='')}"},
            headers={"Prefer": "return=minimal"},
            json=payload,
        )

    def delete_row(self, table: str, row_id: str) -> None:
        self._request(
            "DELETE",
            table,
            params={"id": f"eq.{quote(row_id, safe='')}"},
            headers={"Prefer": "return=minimal"},
        )


_PLACEHOLDER_RE = re.compile(
    r"^(?:\[[^\]]+\]|<[^>]+>|(?:your|replace|example|sample|dummy|demo|test)[-_ ]"
    r"?(?:api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|secret|value)?|\.\.\.)$",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(
    r"(?P<prefix>\bbearer\s+)(?P<value>[A-Za-z0-9._~+/=-]{20,})",
    re.IGNORECASE,
)
_KEYED_SECRET_RE = re.compile(
    r"(?P<prefix>\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"client[_ -]?secret|secret(?:[_ -]?key)?|password|authorization)\b\s*[:=]\s*[\"']?)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._~+/=-]{11,})",
    re.IGNORECASE,
)
_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:sk|ghp|glpat|xoxb|AKIA)[_-][A-Za-z0-9_-]{16,}\b",
    re.IGNORECASE,
)
_SECRET_FIELD_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"token|secret|password|authorization)",
    re.IGNORECASE,
)


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.fullmatch(value.strip()))


def redact_high_confidence_secret_values(value: str) -> tuple[str, int]:
    """Redact only strongly identified token values, never field names/placeholders."""

    matches = 0

    def replace_match(match: re.Match[str]) -> str:
        nonlocal matches
        candidate = match.group("value")
        if _is_placeholder(candidate):
            return match.group(0)
        matches += 1
        return f"{match.group('prefix')}[REDACTED]"

    redacted = _BEARER_RE.sub(replace_match, value)
    redacted = _KEYED_SECRET_RE.sub(replace_match, redacted)

    def replace_prefixed(match: re.Match[str]) -> str:
        nonlocal matches
        matches += 1
        return "[REDACTED]"

    redacted = _TOKEN_PREFIX_RE.sub(replace_prefixed, redacted)
    return redacted, matches


def _redact_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return redact_high_confidence_secret_values(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        matches = 0
        for key, item in value.items():
            if (
                isinstance(item, str)
                and _SECRET_FIELD_RE.search(str(key))
                and len(item.strip()) >= 12
                and not _is_placeholder(item)
            ):
                clean_item, item_matches = "[REDACTED]", 1
            else:
                clean_item, item_matches = _redact_value(item)
            redacted[key] = clean_item
            matches += item_matches
        return redacted, matches
    if isinstance(value, list):
        redacted_items = []
        matches = 0
        for item in value:
            clean_item, item_matches = _redact_value(item)
            redacted_items.append(clean_item)
            matches += item_matches
        return redacted_items, matches
    return value, 0


def _scan(client: PurgeClient) -> dict[str, int]:
    profiles = client.list_rows("mercury_connector_profiles", "id,status,metadata")
    profile_rows = sum(
        bool(PROFILE_SECRET_KEYS.intersection((row.get("metadata") or {}).keys()))
        for row in profiles
        if isinstance(row.get("metadata"), dict)
    )
    write_rows = len(client.list_rows("connector_write_requests", "id"))
    high_confidence_matches = 0
    for table, fields in SCAN_TARGETS.items():
        rows = client.list_rows(table, ",".join(("id", *fields)))
        for row in rows:
            for field in fields:
                _, matches = _redact_value(row.get(field))
                high_confidence_matches += matches
    return {
        "profile_rows": profile_rows,
        "connector_write_request_rows": write_rows,
        "high_confidence_secret_matches": high_confidence_matches,
    }


def run_purge(
    client: PurgeClient,
    *,
    apply: bool = False,
    confirm: str = "",
) -> dict[str, int | str]:
    """Inspect or clean Cloud state without ever returning row values."""

    if apply and confirm != CONFIRMATION:
        raise ValueError(f"--apply requires --confirm {CONFIRMATION}")

    report = _scan(client)
    result: dict[str, int | str] = {
        "mode": "apply" if apply else "dry-run",
        **report,
        "redacted_rows": 0,
    }
    if not apply:
        return result

    profiles = client.list_rows("mercury_connector_profiles", "id,metadata")
    for row in profiles:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not PROFILE_SECRET_KEYS.intersection(metadata):
            continue
        clean_metadata = {
            key: value for key, value in metadata.items() if key not in PROFILE_SECRET_KEYS
        }
        row_id = str(row.get("id") or "").strip()
        if row_id:
            client.patch_row(
                "mercury_connector_profiles",
                row_id,
                {"status": "requires_credentials", "metadata": clean_metadata},
            )

    write_rows = client.list_rows("connector_write_requests", "id")
    for row in write_rows:
        row_id = str(row.get("id") or "").strip()
        if row_id:
            client.delete_row("connector_write_requests", row_id)

    redacted_rows = 0
    for table, fields in SCAN_TARGETS.items():
        rows = client.list_rows(table, ",".join(("id", *fields)))
        for row in rows:
            payload: dict[str, Any] = {}
            matches = 0
            for field in fields:
                clean_value, field_matches = _redact_value(row.get(field))
                if field_matches:
                    payload[field] = clean_value
                    matches += field_matches
            row_id = str(row.get("id") or "").strip()
            if matches and row_id:
                client.patch_row(table, row_id, payload)
                redacted_rows += 1
    result["redacted_rows"] = redacted_rows
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="apply the cleanup")
    parser.add_argument("--confirm", default="", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_purge(
            SupabaseRestClient.from_environment(),
            apply=args.apply,
            confirm=args.confirm,
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
