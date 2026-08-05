---
name: peak-connector-setup-th
description: Use when a PEAK task needs a new, renewed, or verified Mercury connection
---

# PEAK Connector Setup TH

## Required V1 lifecycle

1. Call `get_mercury_context` and select one authorized `workspace_id`.
2. Call `list_accounting_providers` and use `peak` with `uat` or `production` exactly as the
   user selected.
3. Call `start_provider_connection` for PEAK.
4. Open the returned `setup_url`; PEAK credentials are entered only in Mercury's secure setup
   page. Do not continue until validation succeeds and never ask for Connect ID/Key in chat.
5. Call `list_provider_connections` and select the matching PEAK connection.
6. Call `connector_status`; stop on incomplete, invalid, expired, or mismatched setup.
7. Call `list_provider_capabilities`; report only exact capability versions that passed
   qualification for this environment.

Provider credentials never enter chat or model context. Mercury encrypts PEAK authorization
server-side and excludes it from responses, Skills, RAG, logs, and audit output.
