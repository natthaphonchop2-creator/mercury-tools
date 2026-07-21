---
name: flowaccount-connector-setup-th
description: Use when a FlowAccount task needs local connector setup or connection troubleshooting
---

# FlowAccount Connector Setup TH

Use the hosted lifecycle for the user-selected FlowAccount mode and environment:

1. Call `list_connectors` and confirm the exact FlowAccount connection mode and
   environment selected by the user.
2. Call `get_connector_setup` for FlowAccount and the selected mode. For native MCP,
   direct provider authorization to the host; for API-driver setup, return only the
   advanced-local handoff documented by the response.
3. Call `link_connector_profile` only after the user completes provider or host
   authorization outside Mercury. Store only the sanitized profile selection.
4. Call `connector_status` for the workspace and stop when it reports setup or
   readiness is incomplete.
5. Call `connector_capabilities` for the exact mode and environment before describing
   any available action. Treat `provider_unavailable` and `not_validated` as distinct
   outcomes.

An API-driver write requires a separately connected local Mercury MCP. Return
`advanced_local_handoff` and link to the local credential guide and
`docs/ADVANCED_LOCAL_ERP.md`; do not invoke local execution tools from this public Skill.
Never ask for, accept, or paste credentials in chat. Never change environments implicitly.
Respond in concise Thai.
