---
name: mercury-flow-runner
description: Use when the user asks to list, save, preview, or run Mercury Flows for accounting workflows
---

# Mercury Flow Runner

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
