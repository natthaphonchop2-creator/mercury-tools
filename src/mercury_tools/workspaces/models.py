"""Tenant-bound Mercury V1 workspace models."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NextAllowedAction = Literal[
    "list_accounting_providers",
    "start_provider_connection",
]


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class WorkspaceMembership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: UUID
    tenant_display_name: str = Field(min_length=1, max_length=200)
    workspace_id: UUID
    workspace_display_name: str = Field(min_length=1, max_length=200)
    role: WorkspaceRole


class MercuryContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]
    active_workspace_id: UUID
    memberships: list[WorkspaceMembership] = Field(min_length=1, max_length=100)
    next_allowed_actions: list[NextAllowedAction] = Field(max_length=20)

    @model_validator(mode="after")
    def active_workspace_is_visible(self) -> MercuryContext:
        if self.active_workspace_id not in {
            membership.workspace_id for membership in self.memberships
        }:
            raise ValueError("active_workspace_not_visible")
        return self


__all__ = [
    "MercuryContext",
    "NextAllowedAction",
    "WorkspaceMembership",
    "WorkspaceRole",
]
