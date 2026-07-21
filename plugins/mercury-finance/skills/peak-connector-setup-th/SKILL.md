---
name: peak-connector-setup-th
description: Use when a PEAK task needs local connector setup or connection troubleshooting
---

# PEAK Connector Setup TH

Use the hosted lifecycle for the user-selected PEAK environment:

1. Call `list_connectors` and confirm PEAK, its API-driver mode, and one exact
   environment selected by the user.
2. Call `get_connector_setup` for that selection. Return the non-secret setup result
   and the advanced-local handoff; do not collect any API-driver values in chat.
3. Call `link_connector_profile` only with the sanitized profile selection after the
   required local setup is complete outside Mercury.
4. Call `connector_status` for the workspace. Stop when the profile remains unready or
   the environment differs from the selected mode.
5. Call `connector_capabilities` for PEAK and report the returned evidence-backed
   readiness state before planning any action.

Reviewed API-driver writes require a separately connected local Mercury MCP. Return
`advanced_local_handoff` with the local credential guide and
`docs/ADVANCED_LOCAL_ERP.md`; never invoke local execution tools from this public Skill.
Never ask for, accept, or paste credentials in chat. Never change environments implicitly.
Respond in concise Thai.
