---
name: flowaccount-connector-setup-th
description: Use when a FlowAccount task needs connector setup or connection troubleshooting
---

# FlowAccount Connector Setup TH

Use the hosted lifecycle for the user-selected FlowAccount mode and environment:

1. If this task has no current `workspace_id`, call `create_public_workspace`. Reuse the
   returned `workspace_id` for every later step and keep it private; never repeat it in a
   public issue, log, or unrelated chat.
2. Call `list_connectors` and confirm the exact FlowAccount connection mode and
   environment selected by the user.
3. Call `get_connector_setup` for FlowAccount and the selected mode. Provider
   authorization belongs to the MCP host or FlowAccount, never to Mercury chat.
4. Call `link_connector_profile` only after the user completes provider or host
   authorization outside Mercury. Store only the sanitized profile selection.
5. After the host performs the documented safe probe, call
   `validate_connector_connection` with only its sanitized evidence for the same
   FlowAccount mode and environment.
6. Call `connector_status` for the workspace and stop when it reports setup or
   readiness is incomplete.
7. Call `connector_capabilities` for the exact mode and environment before describing
   any available action. Treat `provider_unavailable` and `not_validated` as distinct
   outcomes. State the returned readiness basis and never imply that hosted Mercury
   called FlowAccount when `provider_called_by_mercury` is false.

For reads or writes, invoke only capabilities exposed by an already connected and
authorized provider in the host. If the provider capability is unavailable, report
`provider_connection_required` and stop. Never ask for, accept, or paste credentials in
chat. Never change environments implicitly. Respond in concise Thai.
