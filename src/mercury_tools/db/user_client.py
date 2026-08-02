"""Request-scoped Supabase PostgREST client for Mercury users."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from mercury_tools.config import Settings, v1_supabase_rest_url


def _response_row(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], dict)
    ):
        raise RuntimeError("supabase_user_response_invalid")
    return value[0]


@dataclass(repr=True)
class SupabaseUserClient:
    """PostgREST client that activates RLS with one end-user bearer."""

    project_url: str = field(repr=False)
    auth_issuer: str = field(repr=False)
    publishable_key: str = field(repr=False)
    access_token: str = field(repr=False)
    http_client: httpx.Client | None = field(default=None, repr=False)
    base_url: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = v1_supabase_rest_url(
            project_url=self.project_url,
            auth_issuer=self.auth_issuer,
        )
        if not self.publishable_key:
            raise ValueError("supabase_user_configuration_invalid")
        if (
            not self.access_token
            or self.access_token != self.access_token.strip()
            or any(character.isspace() for character in self.access_token)
        ):
            raise ValueError("supabase_user_access_token_invalid")

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        access_token: str,
        http_client: httpx.Client | None = None,
    ) -> SupabaseUserClient:
        return cls(
            project_url=settings.supabase_url,
            auth_issuer=settings.supabase_auth_issuer,
            publishable_key=settings.supabase_publishable_key,
            access_token=access_token,
            http_client=http_client,
        )

    def bootstrap_context(self) -> dict[str, Any]:
        value = self._request(
            "POST",
            "rpc/bootstrap_mercury_context",
            json={},
        )
        return _response_row(value)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "apikey": self.publishable_key,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        try:
            if self.http_client is None:
                response = httpx.request(
                    method,
                    url,
                    headers=headers,
                    timeout=20,
                    follow_redirects=False,
                    **kwargs,
                )
            else:
                response = self.http_client.request(
                    method,
                    url,
                    headers=headers,
                    timeout=20,
                    follow_redirects=False,
                    **kwargs,
                )
        except httpx.HTTPError:
            raise RuntimeError("supabase_user_request_failed") from None
        if response.status_code >= 300:
            raise RuntimeError("supabase_user_request_failed")
        try:
            return response.json()
        except (TypeError, ValueError):
            raise RuntimeError("supabase_user_response_invalid") from None


__all__ = ["SupabaseUserClient"]
