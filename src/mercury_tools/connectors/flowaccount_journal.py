"""Focused FlowAccount HTTP adapter for private General Journal writes."""

from __future__ import annotations

from typing import Any

import httpx

from mercury_tools.safety.redaction import redact_json


class FlowAccountJournalError(RuntimeError):
    """Definitive connector failure that is safe to show after redaction."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(str(redact_json(message))[:300])
        self.code = code
        self.status_code = status_code


class FlowAccountOutcomeUnknown(FlowAccountJournalError):
    """The write may have reached FlowAccount, so it must not be retried."""


class FlowAccountJournalClient:
    """Acquire FlowAccount tokens and call only the journal endpoints v1 needs."""

    def __init__(
        self,
        *,
        api_base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        grant_type: str = "client_credentials",
        scope: str = "flowaccount-api",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.grant_type = grant_type
        self.scope = scope
        self.http = http_client or httpx.Client(timeout=60)

    @staticmethod
    def _json_payload(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _access_token(self) -> str:
        try:
            response = self.http.post(
                self.token_url,
                data={
                    "grant_type": self.grant_type,
                    "scope": self.scope,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise FlowAccountJournalError(
                "authentication_failed",
                "FlowAccount token request failed.",
            ) from exc

        payload = self._json_payload(response)
        token = str(payload.get("access_token") or "")
        if response.status_code >= 300 or not token:
            raise FlowAccountJournalError(
                "authentication_failed",
                "FlowAccount token request failed.",
                status_code=response.status_code,
            )
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        write: bool,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._access_token()
        try:
            response = self.http.request(
                method,
                f"{self.api_base_url}/{path.lstrip('/')}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=json,
            )
        except httpx.HTTPError as exc:
            if write:
                raise FlowAccountOutcomeUnknown(
                    "outcome_unknown",
                    "FlowAccount write did not return a response.",
                ) from exc
            raise FlowAccountJournalError(
                "connector_unavailable",
                "FlowAccount read did not return a response.",
            ) from exc

        payload = self._json_payload(response)
        if write and response.status_code >= 500:
            raise FlowAccountOutcomeUnknown(
                "outcome_unknown",
                "FlowAccount returned a server error after dispatch.",
                status_code=response.status_code,
            )
        if response.status_code >= 300 or payload.get("status") is False:
            message = str(payload.get("message") or "FlowAccount rejected the request.")
            raise FlowAccountJournalError(
                "rejected",
                message,
                status_code=response.status_code,
            )
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def list_chart_accounts(self) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/chart-of-accounts/accounts",
            write=False,
        )
        accounts = data.get("accounts")
        if not isinstance(accounts, list):
            raise FlowAccountJournalError(
                "invalid_response",
                "FlowAccount chart response is invalid.",
            )
        return [row for row in accounts if isinstance(row, dict)]

    def create_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/journal-entries/draft",
            write=True,
            json=payload,
        )

    def approve_draft(self, record_id: int) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/journal-entries/{int(record_id)}/approve",
            write=True,
        )
