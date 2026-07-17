from __future__ import annotations

from copy import deepcopy

import pytest

from mercury_tools.config import Settings
from mercury_tools.db.product import SupabaseProductStore


class TokenAccessStore(SupabaseProductStore):
    def __init__(
        self,
        *,
        token_overrides: dict | None = None,
        workspace_status: str = "active",
        member_status: str = "active",
    ) -> None:
        super().__init__(
            Settings(
                supabase_url="https://example.supabase.co",
                supabase_service_role_key="service-role",
                openai_api_key="",
            )
        )
        self.token = {
            "id": "token-1",
            "status": "active",
            "workspace_id": "workspace-1",
            "member_id": "member-1",
            "host_app": "generic",
            "scopes": ["public:workspace"],
            "expires_at": "2099-01-01T00:00:00+00:00",
            "revoked_at": None,
            **(token_overrides or {}),
        }
        self.workspace_status = workspace_status
        self.member_status = member_status

    def _request(self, method: str, path: str, **kwargs):
        assert method == "GET"
        if path == "mercury_client_tokens":
            return [deepcopy(self.token)]
        if path == "mercury_workspaces":
            return [
                {
                    "id": "workspace-1",
                    "workspace_key": "workspace-key",
                    "name": "Mercury Workspace",
                    "plan": "invite-preview",
                    "status": self.workspace_status,
                    "metadata": {},
                    "created_at": "2026-07-17T00:00:00+00:00",
                    "updated_at": "2026-07-17T00:00:00+00:00",
                }
            ]
        if path == "mercury_workspace_members":
            return [
                {
                    "id": "member-1",
                    "email": "public@example.invalid",
                    "role": "owner",
                    "host_app": "generic",
                    "status": self.member_status,
                    "created_at": "2026-07-17T00:00:00+00:00",
                    "last_seen_at": None,
                }
            ]
        raise AssertionError(f"unexpected path: {path}")


def test_active_public_workspace_token_resolves_context() -> None:
    context = TokenAccessStore().workspace_for_token({"jti": "workspace-token"})

    assert context is not None
    assert context["workspace"]["id"] == "workspace-1"
    assert context["member"]["id"] == "member-1"


@pytest.mark.parametrize(
    "token_overrides",
    [
        {"status": "revoked"},
        {"revoked_at": "2026-07-17T00:00:00+00:00"},
        {"expires_at": "2000-01-01T00:00:00+00:00"},
        {"expires_at": "invalid"},
    ],
)
def test_inactive_public_workspace_token_is_rejected(token_overrides: dict) -> None:
    context = TokenAccessStore(token_overrides=token_overrides).workspace_for_token(
        {"jti": "workspace-token"}
    )

    assert context is None


@pytest.mark.parametrize(
    ("workspace_status", "member_status"),
    [("disabled", "active"), ("active", "disabled")],
)
def test_inactive_workspace_or_member_is_rejected(
    workspace_status: str,
    member_status: str,
) -> None:
    context = TokenAccessStore(
        workspace_status=workspace_status,
        member_status=member_status,
    ).workspace_for_token({"jti": "workspace-token"})

    assert context is None
