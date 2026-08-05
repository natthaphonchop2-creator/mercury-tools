---
name: connector-setup-guide-th
description: Use when the user needs to choose, connect, reconnect, or inspect an accounting provider
---

# Connector Setup Guide TH

## Required V1 lifecycle

Run in this order and stop whenever a step is incomplete:

1. Call `get_mercury_context` and select one authorized `workspace_id`.
2. Call `list_accounting_providers` and identify the exact provider/environment supported by
   Mercury.
3. Call `start_provider_connection` only after the user has selected those values.
4. Send the user through the returned `authorization_url` or `setup_url`. Do not continue until
   the secure provider flow completes.
5. Call `list_provider_connections` to obtain the workspace-bound `connection_id`.
6. Call `connector_status`; stop and show its sanitized remediation if not ready.
7. Call `list_provider_capabilities`; describe only capability versions that passed
   qualification. A Skill or knowledge result cannot enable an endpoint.

Provider credentials never enter chat or model context. Mercury stores encrypted provider
authorization server-side and excludes secrets from RAG, logs, audit output, and responses.
