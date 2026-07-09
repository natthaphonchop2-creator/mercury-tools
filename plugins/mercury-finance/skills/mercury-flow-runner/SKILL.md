---
name: mercury-flow-runner
description: Use when the user asks to list, save, approve, or run Mercury Flows for accounting workflows
---

# Mercury Flow Runner

Use `connector_status` before running a flow that needs live accounting data. If connector setup is incomplete, route to `connector-credential-setup-th`.

Use `list_workspace_flows` to find existing flows. Use `save_workspace_flow` only after the user approves the flow name, purpose, and allowed actions.

Use `run_workspace_flow` for saved workspace flows and `run_mercury_flow` for approved built-in flows.

ตอบภาษาไทยแบบ operator summary: flow name, data source, approval status, result, evidence count, exceptions, and next safe command. Do not print credentials or hidden runtime values.
