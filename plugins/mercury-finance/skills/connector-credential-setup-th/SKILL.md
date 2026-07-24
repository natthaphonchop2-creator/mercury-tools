---
name: connector-credential-setup-th
description: Use when an accounting or ERP task is blocked because provider authorization is not ready
---

# Connector Credential Setup TH

Use the hosted connector lifecycle without skipping or reordering:

1. If this task has no current `workspace_id`, call `create_public_workspace`. Reuse the
   returned `workspace_id` for every later step and keep it private; never repeat it in a
   public issue, log, or unrelated chat.
2. Call `list_connectors` and ask the user to select one exact connector, connection
   mode, and environment. Do not choose a provider or environment implicitly.
3. Call `get_connector_setup` for that exact connector and connection mode. Follow only
   the returned non-secret setup guidance and provider authorization ownership.
4. After the user has completed the provider or host authorization outside Mercury,
   call `link_connector_profile` with only the sanitized connector selection and profile
   details required by the hosted tool.
5. After the MCP host or ERP provider performs the documented safe probe, call
   `validate_connector_connection` with only its sanitized evidence for the same
   connector, mode, and environment.
6. Call `connector_status` for the workspace. Stop on `not_ready`,
   `environment_mismatch`, or a setup requirement.
7. Call `connector_capabilities` for the selected connector, mode, and environment.
   Report only the returned capability states, evidence reference, and readiness basis.
   Never imply that hosted Mercury called the ERP when
   `provider_called_by_mercury` is false.

The hosted plugin does not receive, store, or test ERP credentials. When the selected
provider cannot be authorized by the host, report `provider_connection_required` and
stop. Never ask for, accept, or paste credentials in chat. Respond in concise Thai.
