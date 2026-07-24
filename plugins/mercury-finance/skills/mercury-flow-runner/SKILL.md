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
4. Stop on any unavailable or setup status. Continue only when the route returns
   `status=ready`.

## Connected provider execution

Use only the returned `invoke_connected_provider_capability` steps, in order. The host
must invoke the exact separately connected ERP/provider capability described by
`host_tool_requirements`; Mercury never receives the provider credential. Run optional
steps only when they are returned with `required=false`.

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
