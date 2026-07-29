"""Stable MCP surface identifiers used by release verification."""

from __future__ import annotations

HOSTED_MCP_URL = "https://mercury-tools-mcp.onrender.com/mcp"

LEGACY_HOSTED_TOOL_NAMES = frozenset(
    {
        "search_knowledge",
        "retrieve_context_pack",
        "retrieve_workspace_context_pack",
        "get_document",
        "create_public_workspace",
        "get_public_workspace",
        "list_connectors",
        "get_connector_setup",
        "link_connector_profile",
        "validate_connector_connection",
        "connector_capabilities",
        "unlink_connector_profile",
        "connector_status",
        "list_accounting_skills",
        "get_accounting_skill_schema",
        "run_accounting_skill",
        "flow_cheat_sheet",
        "check_flow_syntax",
        "inspect_flow_files",
        "run_inline_flow",
        "run_flow_files",
        "list_workspace_flows",
        "run_workspace_flow",
        "save_workspace_flow",
    }
)

# The legacy public-demo surface remains available only while V1 is disabled.
HOSTED_TOOL_NAMES = LEGACY_HOSTED_TOOL_NAMES

V1_HOSTED_TOOL_NAMES = frozenset(
    {
        "get_mercury_context",
        "list_accounting_providers",
        "start_provider_connection",
        "list_provider_connections",
        "connector_status",
        "list_provider_capabilities",
        "get_capability_schema",
        "disconnect_provider",
    }
)
