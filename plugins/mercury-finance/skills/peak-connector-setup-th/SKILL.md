---
name: peak-connector-setup-th
description: Use when a PEAK task needs connector setup or connection troubleshooting
---

# PEAK Connector Setup TH

Use the hosted lifecycle for the user-selected PEAK environment:

1. If this task has no current `workspace_id`, call `create_public_workspace`. Reuse the
   returned `workspace_id` for every later step and keep it private; never repeat it in a
   public issue, log, or unrelated chat.
2. Call `list_connectors` and confirm PEAK, its API-driver mode, and one exact
   environment selected by the user.
3. Call `get_connector_setup` for that selection. Return only non-secret setup guidance;
   do not collect API-driver values in chat.
4. Call `link_connector_profile` only with the sanitized profile selection after the
   required local setup is complete outside Mercury.
5. After the host performs the documented safe probe, call
   `validate_connector_connection` with only its sanitized evidence for the same PEAK
   mode and environment.
6. Call `connector_status` for the workspace. Stop when the profile remains unready or
   the environment differs from the selected mode.
7. Call `connector_capabilities` for PEAK and report the returned attested readiness
   state before planning any action. State the returned readiness basis and never imply
   that hosted Mercury called PEAK when `provider_called_by_mercury` is false.

Invoke only capabilities exposed by an already connected and authorized PEAK provider in
the host. If the provider capability is unavailable, report
`provider_connection_required` and stop. Never ask for, accept, or paste credentials in
chat. Never change environments implicitly. Respond in concise Thai.
