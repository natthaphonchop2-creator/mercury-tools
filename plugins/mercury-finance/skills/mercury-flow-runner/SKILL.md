---
name: mercury-flow-runner
description: Use when the user asks to list, save, preview, or run Mercury Flows for accounting workflows
---

# Mercury Flow Runner

## Catalog and route

1. Call `get_accounting_skill_schema` with `skill_id=mercury-flow-runner`; validate inputs
   and use only the returned result contract.
2. Call `connector_status` for the workspace, then call `run_accounting_skill` with the
   same Skill ID and validated inputs.
3. If the route returns `connector_selection_required`, ask the user to choose one exact
   `connector_id`, `connection_mode`, and `environment` tuple from `choices`, then rerun.
4. Stop on any unavailable or setup status. Execute exactly one route branch below. Do not
   continue into another route branch.

## Route branches

### `native_mcp`

Use only the returned `invoke_provider_capability` steps in `ordered_steps`, in order,
through the exact provider MCP tools and server named by `host_tool_requirements`. Run
optional steps only when they are returned with `required=false`.

### `api_driver`

Use only the returned `advanced_local_handoff` step in `ordered_steps` and the local
Mercury tools named by that step. Do not invoke a provider MCP or a bridge in this branch.

### `local_bridge_required`

Stop without running data-access commands, report the bridge/setup requirement, and wait for setup
to complete before rerouting. Do not fall through to either ready branch.

## Flow operation after a ready branch

- Use `list_workspace_flows` to inspect saved flows.
- Use `save_workspace_flow` only after the user approves its title, purpose, declared data
  sources, and read-only behavior.
- Use `run_workspace_flow` only for a read-only flow. Use dry run unless the user explicitly
  requests the reviewed read execution.

Preserve citations and evidence references, include accountant review points, and shape the
result with the returned `output_schema_name`. Never self-confirm or execute a write. Never retry
a write. Respond in concise Thai with the flow, data source, result status, exceptions, and next
safe action.

Mercury does not own provider, Google, ecommerce, marketplace, or bank OAuth tokens.
