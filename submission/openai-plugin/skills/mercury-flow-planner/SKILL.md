---
name: mercury-flow-planner
description: Use when the user asks to inspect, validate, save, preview, or run a Mercury Flow through the hosted plugin.
---

# Mercury Flow Planner

1. Use `flow_cheat_sheet` when syntax or command choice is unclear.
2. Use `check_flow_syntax` before every new or edited flow.
3. Use `inspect_flow_files` for a multi-file in-memory workspace.
4. Default `run_mercury_flow`, `run_flow`, and `run_flow_files` to dry run unless the user
   explicitly requests closed-workspace execution.
5. Create a public workspace only with user approval. Use `save_workspace_flow` only
   after showing title, purpose, and declared capabilities.
6. Use `list_workspace_flows` before selecting a saved flow and
   `run_workspace_flow` with `dry_run=true` first.

Hosted Mercury Flows cannot substitute for a production ERP POST. Never convert a flow
preview into an external financial action or claim that it changed an ERP record.
