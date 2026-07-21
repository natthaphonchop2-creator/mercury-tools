---
name: connector-credential-setup-th
description: Use when an accounting or ERP task is blocked because local connector credentials are not ready
---

# Connector Credential Setup TH

Use the hosted connector lifecycle without skipping or reordering:

1. Call `list_connectors` and ask the user to select one exact connector, connection
   mode, and environment. Do not choose a provider or environment implicitly.
2. Call `get_connector_setup` for that exact connector and connection mode. Follow only
   the returned non-secret setup guidance and provider authorization ownership.
3. After the user has completed the provider or host authorization outside Mercury,
   call `link_connector_profile` with only the sanitized connector selection and profile
   details required by the hosted tool.
4. Call `connector_status` for the workspace. Stop on `not_ready`,
   `environment_mismatch`, or a setup requirement.
5. Call `connector_capabilities` for the selected connector, mode, and environment.
   Report only the returned capability states and evidence reference.

For an API-driver or Local Bridge requirement, return the advanced-local handoff to the
local credential guide and `docs/ADVANCED_LOCAL_ERP.md`. The hosted plugin does not
receive, store, or test ERP credentials and it must never invoke local execution tools.
Never ask for, accept, or paste credentials in chat. Respond in concise Thai.
