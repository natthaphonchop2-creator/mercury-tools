"""Service-role persistence for encrypted private connector writes."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

from mercury_tools.config import Settings, require_supabase
from mercury_tools.db.product import now_utc, vault_key
from mercury_tools.safety.redaction import redact_json

BLOCKING_STATUSES = ("executing", "draft_created", "approved", "outcome_unknown")
FAILURE_STATUSES = {"failed", "outcome_unknown"}


def _iso(value: datetime) -> str:
    return value.isoformat()


def _encrypt_payload(
    settings: Settings,
    *,
    workspace_key_value: str,
    payload: dict[str, Any],
) -> str:
    plaintext = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return Fernet(vault_key(settings, workspace_key_value)).encrypt(plaintext).decode(
        "ascii"
    )


def _decrypt_payload(
    settings: Settings,
    *,
    workspace_key_value: str,
    ciphertext: str,
) -> dict[str, Any]:
    try:
        plaintext = Fernet(vault_key(settings, workspace_key_value)).decrypt(
            ciphertext.encode("ascii")
        )
        payload = json.loads(plaintext)
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Journal write payload cannot be decrypted.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Journal write payload is invalid.")
    return payload


class SupabaseJournalWriteStore:
    """Persist write previews and enforce one-way state transitions."""

    def __init__(self, settings: Settings):
        require_supabase(settings)
        if not settings.connect_signing_secret:
            raise RuntimeError(
                "MERCURY_CREDENTIAL_VAULT_SECRET is required for journal writes."
            )
        self.settings = settings
        self.base_url = f"{settings.supabase_url}/rest/v1"
        self.headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        extra_headers = kwargs.pop("headers", {})
        headers = {**self.headers, **extra_headers}
        response = httpx.request(method, url, headers=headers, timeout=60, **kwargs)
        if response.status_code >= 300:
            raise RuntimeError(
                f"Supabase journal request failed: HTTP {response.status_code} "
                f"{str(redact_json(response.text))[:300]}"
            )
        if not response.text:
            return None
        return response.json()

    def create_preview(
        self,
        *,
        workspace_uuid: str,
        connector_profile_id: str,
        workspace_key: str,
        environment: str,
        input_hash: str,
        payload: dict[str, Any],
        expires_at: datetime,
    ) -> dict[str, Any]:
        request_key = f"mjp_{secrets.token_urlsafe(18)}"
        row = {
            "request_key": request_key,
            "workspace_id": workspace_uuid,
            "connector_profile_id": connector_profile_id,
            "connector_id": "flowaccount",
            "environment": environment,
            "operation": "journal.create",
            "input_hash": input_hash,
            "encrypted_payload": _encrypt_payload(
                self.settings,
                workspace_key_value=workspace_key,
                payload=payload,
            ),
            "payload_version": 1,
            "status": "previewed",
            "expires_at": _iso(expires_at),
            "response_summary": {},
        }
        rows = self._request(
            "POST",
            "connector_write_requests",
            headers={**self.headers, "Prefer": "return=representation"},
            json=[row],
        )
        if not rows:
            raise RuntimeError("Supabase did not return the journal preview row.")
        return rows[0]

    def load_request(
        self,
        *,
        request_key: str,
        workspace_uuid: str,
        workspace_key: str,
    ) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "connector_write_requests",
            params={
                "request_key": f"eq.{request_key}",
                "workspace_id": f"eq.{workspace_uuid}",
                "select": "*",
                "limit": "1",
            },
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["payload"] = _decrypt_payload(
            self.settings,
            workspace_key_value=workspace_key,
            ciphertext=str(row.get("encrypted_payload") or ""),
        )
        row.pop("encrypted_payload", None)
        return row

    def find_blocking_duplicate(
        self,
        *,
        workspace_uuid: str,
        connector_profile_id: str,
        input_hash: str,
        exclude_request_key: str,
    ) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "connector_write_requests",
            params={
                "workspace_id": f"eq.{workspace_uuid}",
                "connector_profile_id": f"eq.{connector_profile_id}",
                "operation": "eq.journal.create",
                "input_hash": f"eq.{input_hash}",
                "request_key": f"neq.{exclude_request_key}",
                "status": f"in.({','.join(BLOCKING_STATUSES)})",
                "select": (
                    "request_key,status,flowaccount_record_id,document_serial,created_at"
                ),
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def _transition(
        self,
        *,
        params: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        rows = self._request(
            "PATCH",
            "connector_write_requests",
            params=params,
            headers={**self.headers, "Prefer": "return=representation"},
            json={**payload, "updated_at": now_utc()},
        )
        return rows[0] if rows else None

    def claim_preview(
        self,
        *,
        request_key: str,
        workspace_uuid: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        return self._transition(
            params={
                "request_key": f"eq.{request_key}",
                "workspace_id": f"eq.{workspace_uuid}",
                "status": "eq.previewed",
                "expires_at": f"gt.{_iso(now)}",
            },
            payload={"status": "executing", "executed_at": _iso(now)},
        )

    def record_draft(
        self,
        *,
        request_key: str,
        workspace_uuid: str,
        record_id: int,
        document_serial: str | None,
        response_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._transition(
            params={
                "request_key": f"eq.{request_key}",
                "workspace_id": f"eq.{workspace_uuid}",
                "status": "eq.executing",
            },
            payload={
                "status": "draft_created",
                "flowaccount_record_id": int(record_id),
                "document_serial": document_serial,
                "response_summary": redact_json(response_summary),
            },
        )

    def record_failure(
        self,
        *,
        request_key: str,
        workspace_uuid: str,
        status: str,
        response_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        if status not in FAILURE_STATUSES:
            raise ValueError("Journal failure status must be failed or outcome_unknown.")
        return self._transition(
            params={
                "request_key": f"eq.{request_key}",
                "workspace_id": f"eq.{workspace_uuid}",
                "status": "eq.executing",
            },
            payload={
                "status": status,
                "response_summary": redact_json(response_summary),
            },
        )

    def load_draft_by_record_id(
        self,
        *,
        workspace_uuid: str,
        record_id: int,
    ) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "connector_write_requests",
            params={
                "workspace_id": f"eq.{workspace_uuid}",
                "flowaccount_record_id": f"eq.{int(record_id)}",
                "status": "eq.draft_created",
                "select": (
                    "request_key,connector_profile_id,environment,input_hash,status,"
                    "flowaccount_record_id,document_serial"
                ),
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def claim_draft_for_approval(
        self,
        *,
        request_key: str,
        workspace_uuid: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        return self._transition(
            params={
                "request_key": f"eq.{request_key}",
                "workspace_id": f"eq.{workspace_uuid}",
                "status": "eq.draft_created",
            },
            payload={"status": "executing"},
        )

    def record_approved(
        self,
        *,
        request_key: str,
        workspace_uuid: str,
        approved_at: datetime,
        response_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._transition(
            params={
                "request_key": f"eq.{request_key}",
                "workspace_id": f"eq.{workspace_uuid}",
                "status": "eq.executing",
            },
            payload={
                "status": "approved",
                "approved_at": _iso(approved_at),
                "response_summary": redact_json(response_summary),
            },
        )
