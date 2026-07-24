---
name: flowaccount-connector-setup-th
description: Use when a FlowAccount task needs local connector setup or connection troubleshooting
---

# FlowAccount Connector Setup TH

Use the hosted lifecycle for the user-selected FlowAccount mode and environment:

1. If this task has no current `workspace_id`, call `create_public_workspace`. Reuse the
   returned `workspace_id` for every later step and keep it private; never repeat it in a
   public issue, log, or unrelated chat.
2. Call `list_connectors` and confirm the exact FlowAccount connection mode and
   environment selected by the user.
3. Call `get_connector_setup` for FlowAccount and the selected mode. For native MCP,
   direct provider authorization to the host; for API-driver setup, return only the
   advanced-local handoff documented by the response.
4. Call `link_connector_profile` only after the user completes provider or host
   authorization outside Mercury. Store only the sanitized profile selection.
5. After the host or separately connected local runtime performs the documented safe
   probe, call `validate_connector_connection` with only its sanitized evidence for the
   same FlowAccount mode and environment.
6. Call `connector_status` for the workspace and stop when it reports setup or
   readiness is incomplete.
7. Call `connector_capabilities` for the exact mode and environment before describing
   any available action. Treat `provider_unavailable` and `not_validated` as distinct
   outcomes. State the returned readiness basis and never imply that hosted Mercury
   called FlowAccount when `provider_called_by_mercury` is false.

An API-driver write requires a separately connected local Mercury MCP. Return
`advanced_local_handoff` and link to the local credential guide and
`docs/ADVANCED_LOCAL_ERP.md`; do not invoke local execution tools from this public Skill.
Never ask for, accept, or paste credentials in chat. Never change environments implicitly.
Respond in concise Thai.
