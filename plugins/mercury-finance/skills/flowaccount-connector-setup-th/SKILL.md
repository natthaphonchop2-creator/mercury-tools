---
name: flowaccount-connector-setup-th
description: Use when a FlowAccount task needs a new, renewed, or verified Mercury connection
---

# FlowAccount Connector Setup TH

## Required V1 lifecycle

1. Call `get_mercury_context` and select one authorized `workspace_id`.
2. Call `list_accounting_providers` and use `flowaccount` with `sandbox` or `production` exactly
   as selected by the user.
3. Call `start_provider_connection` for FlowAccount.
4. Open its `authorization_url` so the user can sign in to FlowAccount and approve access. Do
   not continue until the OAuth handoff completes; never request an OAuth token in chat.
   Do not continue when the callback reports an error or an account mismatch.
5. Call `list_provider_connections` and select the matching FlowAccount connection.
6. Call `connector_status`; stop on expired, revoked, mismatched, or incomplete authorization.
7. Call `list_provider_capabilities`; report only exact capability versions that passed
   qualification for this environment.

Provider credentials never enter chat or model context. Mercury keeps encrypted OAuth material
server-side and returns only company-safe connection metadata, evidence, and sanitized audit.
