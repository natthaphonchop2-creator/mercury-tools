"""Closed public schemas for Mercury V1 MCP tools."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mercury_tools.workspaces.models import NextAllowedAction, WorkspaceRole


class V1PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GetMercuryContextInput(V1PublicModel):
    pass


class WorkspaceMembershipOutput(V1PublicModel):
    tenant_id: UUID
    tenant_display_name: str = Field(min_length=1, max_length=200)
    workspace_id: UUID
    workspace_display_name: str = Field(min_length=1, max_length=200)
    role: WorkspaceRole


class GetMercuryContextOutput(V1PublicModel):
    status: Literal["ok"]
    active_workspace_id: UUID
    memberships: list[WorkspaceMembershipOutput] = Field(
        min_length=1,
        max_length=100,
    )
    next_allowed_actions: list[NextAllowedAction] = Field(max_length=20)


__all__ = [
    "GetMercuryContextInput",
    "GetMercuryContextOutput",
    "V1PublicModel",
    "WorkspaceMembershipOutput",
]
