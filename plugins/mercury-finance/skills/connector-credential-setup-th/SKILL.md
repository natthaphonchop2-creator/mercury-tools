---
name: connector-credential-setup-th
description: Use when an ERP task cannot continue because provider authorization is missing, expired, or not ready
---

# Connector Credential Setup TH

## Required V1 lifecycle

Run exactly one step at a time and do not skip ahead:

1. Call `get_mercury_context`; ask the user to select a returned `workspace_id` when ambiguous.
2. Call `list_accounting_providers`; ask for the exact provider and environment only when they
   were not already specified.
3. Call `start_provider_connection` with that workspace, provider, and environment.
4. Open the returned `authorization_url` or `setup_url` in the secure browser handoff. Do not
   ask the user to paste credentials into chat. Do not continue until the provider handoff
   reports completion.
5. Call `list_provider_connections` and select only the new matching connection.
6. Call `connector_status`; do not continue unless authorization is ready.
7. Call `list_provider_capabilities` and report only exact capability versions whose
   qualification and availability permit use.

Provider credentials never enter chat or model context. Mercury encrypts provider authorization
server-side; tools, Skills, RAG, logs, and audit output receive only sanitized metadata.
