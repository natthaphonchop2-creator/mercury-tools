"""Mercury V1 MCP tool registration and request-bound handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request

from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal
from mercury_tools.config import load_settings
from mercury_tools.mcp.v1_schemas import GetMercuryContextOutput
from mercury_tools.workspaces.service import WorkspaceService

GET_MERCURY_CONTEXT_TOOL = "get_mercury_context"
WorkspaceServiceFactory = Callable[[], WorkspaceService]

_IDEMPOTENT_MERCURY_STATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _workspace_service() -> WorkspaceService:
    return WorkspaceService.from_settings(load_settings())


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or token != token.strip()
        or any(character.isspace() for character in token)
    ):
        return None
    return token


async def get_mercury_context(
    context: Context,
    *,
    service_factory: WorkspaceServiceFactory = _workspace_service,
) -> GetMercuryContextOutput:
    """Bootstrap and return the caller's sanitized Mercury workspace context."""

    try:
        request = context.request_context.request
    except (AttributeError, ValueError):
        raise MercuryAuthError("mercury_auth_required") from None
    if not isinstance(request, Request):
        raise MercuryAuthError("mercury_auth_required")

    principal = getattr(request.state, "mercury_principal", None)
    if not isinstance(principal, MercuryPrincipal):
        raise MercuryAuthError("mercury_auth_required")

    access_token = _bearer_token(request.headers.get("authorization"))
    if access_token is None:
        raise MercuryAuthError("mercury_auth_required")

    service = service_factory()
    mercury_context = await asyncio.to_thread(
        service.bootstrap,
        principal,
        access_token,
    )
    return GetMercuryContextOutput.model_validate(
        mercury_context.model_dump(mode="python")
    )


def configure_v1_tools(
    server: FastMCP,
    *,
    enabled: bool,
    service_factory: WorkspaceServiceFactory = _workspace_service,
) -> None:
    registered = server._tool_manager.get_tool(GET_MERCURY_CONTEXT_TOOL)
    if registered is not None:
        if enabled:
            return
        server.remove_tool(GET_MERCURY_CONTEXT_TOOL)
        return
    if not enabled:
        return

    async def get_mercury_context_tool(
        context: Context,
    ) -> GetMercuryContextOutput:
        return await get_mercury_context(
            context,
            service_factory=service_factory,
        )

    get_mercury_context_tool.__name__ = GET_MERCURY_CONTEXT_TOOL
    server.add_tool(
        get_mercury_context_tool,
        name=GET_MERCURY_CONTEXT_TOOL,
        description=(
            "Idempotently bootstrap and return the authenticated user's "
            "sanitized Mercury workspace context."
        ),
        annotations=_IDEMPOTENT_MERCURY_STATE,
        structured_output=True,
    )


__all__ = [
    "GET_MERCURY_CONTEXT_TOOL",
    "WorkspaceServiceFactory",
    "configure_v1_tools",
    "get_mercury_context",
]
