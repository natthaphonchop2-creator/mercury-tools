---
name: mercury-flow-runner
description: Use when the user asks to list, save, preview, or run Mercury Flows for accounting workflows
---

# Mercury Flow Runner

Call `connector_status` with the current `workspace_id` before any flow that
needs ERP data. If setup is incomplete, route to
`connector-credential-setup-th` and stop.

Use `list_workspace_flows` to find saved flows and `save_workspace_flow` only
after the user approves the title, purpose, and declared capabilities. Use
`run_workspace_flow` for a saved flow and `run_mercury_flow` for inline or
built-in flows. Pass the same `workspace_id` to every workspace tool.

Default to dry run. Public contest mode permits read capabilities only and must
return `public_preview_read_only` for mutation capabilities.

ตอบภาษาไทยแบบ operator summary: flow, data source, preview status, result,
evidence count, exceptions, and next safe command. Never print credentials or
hidden runtime values.
