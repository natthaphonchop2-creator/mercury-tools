---
name: mercury-flow-runner
description: Use when the user asks to list, save, preview, or run Mercury Flows for accounting workflows
---

# Mercury Flow Runner

## Catalog contract

Call `get_accounting_skill_schema` with `skill_id=mercury-flow-runner`, then call
`run_accounting_skill` with the same Skill ID and validated inputs. Inspect `connector_status`
for the workspace first. Ask the user to select only when the route returns
`connector_selection_required`. Follow returned `ordered_steps`: let the host invoke connected
provider tools for `native_mcp`, use the advanced local handoff for `api_driver`, and stop for
setup on `local_bridge`.

Do not duplicate capability or provider mappings in this Skill. Preserve returned citations
and evidence references, include accountant review points, and shape the final result using
the returned `output_schema_name`. Mercury does not own provider, Google, ecommerce,
marketplace, or bank OAuth tokens; the host invokes those connected tools.

Call `credential_status` before a flow that needs ERP data. Stop and route to local
connector setup unless status is connected.

- Use `list_workspace_flows` to inspect saved flows.
- Use `save_workspace_flow` only after the user approves its title, purpose, and declared
  capabilities.
- Use `run_workspace_flow` only for flows that contain read actions or `preview_erp_write`.
  Default to dry run when the user has not requested a preview.

Never self-confirm or execute a write. Never retry a write, including inside a retry
block. A flow must return a write preview to the user and stop. Respond in concise Thai
with the flow, data source, preview or result status, exceptions, and next safe action.
