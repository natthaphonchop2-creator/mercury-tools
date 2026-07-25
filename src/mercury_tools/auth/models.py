"""Secret-safe Mercury OAuth principal models."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AUTH_ERROR_CODES = frozenset(
    {
        "mercury_auth_required",
        "mercury_token_invalid",
        "mercury_scope_insufficient",
    }
)


class MercuryAuthError(RuntimeError):
    """A closed OAuth failure that contains a public error code only."""

    def __init__(self, code: str) -> None:
        if code not in AUTH_ERROR_CODES:
            raise ValueError("mercury_auth_error_code_invalid")
        self.code = code
        super().__init__(code)


class MercuryPrincipal(BaseModel):
    """Validated Supabase OAuth identity for one Mercury request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: UUID
    client_id: str = Field(min_length=1, max_length=512)
    scopes: frozenset[str]
    token_id: str | None = Field(default=None, min_length=1, max_length=512)


class PrincipalResolver(Protocol):
    async def resolve(self, bearer_token: str) -> MercuryPrincipal: ...


__all__ = [
    "AUTH_ERROR_CODES",
    "MercuryAuthError",
    "MercuryPrincipal",
    "PrincipalResolver",
]
