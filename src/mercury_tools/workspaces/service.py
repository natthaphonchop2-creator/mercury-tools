"""Idempotent Mercury V1 workspace bootstrap service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.config import Settings
from mercury_tools.db.user_client import SupabaseUserClient
from mercury_tools.workspaces.models import (
    MercuryContext,
    WorkspaceMembership,
    WorkspaceRole,
)

_ROLE_LEVEL = {
    WorkspaceRole.VIEWER: 0,
    WorkspaceRole.MEMBER: 1,
    WorkspaceRole.ADMIN: 2,
    WorkspaceRole.OWNER: 3,
}


class UserWorkspaceClient(Protocol):
    def bootstrap_context(self) -> dict[str, Any]: ...


UserClientFactory = Callable[[str], UserWorkspaceClient]


class WorkspaceAccessError(PermissionError):
    """Closed workspace authorization failure."""

    def __init__(self, code: str) -> None:
        if code not in {
            "workspace_access_denied",
            "workspace_role_insufficient",
        }:
            raise ValueError("workspace_access_error_invalid")
        self.code = code
        super().__init__(code)


class WorkspaceService:
    def __init__(self, *, user_client_factory: UserClientFactory) -> None:
        self._user_client_factory = user_client_factory

    def __repr__(self) -> str:
        return "WorkspaceService()"

    @classmethod
    def from_settings(cls, settings: Settings) -> WorkspaceService:
        project_url = settings.supabase_url
        auth_issuer = settings.supabase_auth_issuer
        publishable_key = settings.supabase_publishable_key

        def create_client(access_token: str) -> SupabaseUserClient:
            return SupabaseUserClient(
                project_url=project_url,
                auth_issuer=auth_issuer,
                publishable_key=publishable_key,
                access_token=access_token,
            )

        return cls(user_client_factory=create_client)

    def bootstrap(
        self,
        principal: MercuryPrincipal,
        access_token: str,
    ) -> MercuryContext:
        self._validate_principal(principal)
        client = self._user_client_factory(access_token)
        try:
            return MercuryContext.model_validate(client.bootstrap_context())
        except ValidationError:
            raise RuntimeError("workspace_context_response_invalid") from None

    def require_workspace(
        self,
        principal: MercuryPrincipal,
        access_token: str,
        workspace_id: UUID,
        required_role: WorkspaceRole,
    ) -> WorkspaceMembership:
        if not isinstance(workspace_id, UUID):
            raise ValueError("workspace_id_invalid")
        if not isinstance(required_role, WorkspaceRole):
            raise ValueError("workspace_role_invalid")

        context = self.bootstrap(principal, access_token)
        membership = next(
            (
                item
                for item in context.memberships
                if item.workspace_id == workspace_id
            ),
            None,
        )
        if membership is None:
            raise WorkspaceAccessError("workspace_access_denied")
        if _ROLE_LEVEL[membership.role] < _ROLE_LEVEL[required_role]:
            raise WorkspaceAccessError("workspace_role_insufficient")
        return membership

    @staticmethod
    def _validate_principal(principal: MercuryPrincipal) -> None:
        if not isinstance(principal, MercuryPrincipal):
            raise ValueError("mercury_principal_invalid")
        # Touch only the validated UUID. Database identity remains auth.uid().
        UUID(str(principal.subject))


__all__ = [
    "UserClientFactory",
    "UserWorkspaceClient",
    "WorkspaceAccessError",
    "WorkspaceService",
]
