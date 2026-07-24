---
name: connector-setup-guide-th
description: Use when the user needs to choose or configure an accounting or ERP connector
---

# Connector Setup Guide TH

Confirm the user-selected connector, connection mode, and environment. Explain choices in
neutral terms. Use this hosted lifecycle without skipping or reordering:

1. If this task has no current `workspace_id`, call `create_public_workspace`. Reuse the
   returned `workspace_id` for every later step and keep it private; never repeat it in a
   public issue, log, or unrelated chat.
2. Call `list_connectors`, then ask the user to select one exact connector, connection
   mode, and environment. Do not choose any of them implicitly.
3. Call `get_connector_setup` for that exact selection. Follow only its non-secret setup
   guidance.
4. Call `link_connector_profile` with the selected workspace and sanitized profile details.
   Do not send credentials, OAuth values, provider payloads, or local paths.
5. For native MCP, have the host or provider complete OAuth outside Mercury. For an
   API-driver or Local Bridge requirement, return `advanced_local_handoff` to
   `docs/ADVANCED_LOCAL_ERP.md` and stop the hosted branch until a separately connected
   local MCP supplies sanitized validation evidence. Do not invoke a local CLI or local
   tool from this public Skill.
6. When host/provider OAuth or the separately connected advanced-local branch has returned
   sanitized evidence, call `validate_connector_connection` for the same workspace,
   connector, mode, and environment.
7. Call `connector_status` for that same selection. Stop on a setup requirement,
   validation failure, or environment mismatch; otherwise report only the returned status
   and next hosted action. State the returned readiness basis and never imply that hosted
   Mercury called the ERP when `provider_called_by_mercury` is false.

Never ask for, accept, or paste credentials in chat. Respond in concise Thai with the
connector, mode, environment, status, and next hosted action.
