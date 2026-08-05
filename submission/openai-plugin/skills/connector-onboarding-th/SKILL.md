---
name: connector-onboarding-th
description: Use when the user wants to connect an accounting provider or check connection readiness without sharing credentials.
---

# Connector Onboarding TH

First use opens secure Mercury sign-in. Follow this lifecycle exactly:

`get_mercury_context` -> `list_accounting_providers` -> `start_provider_connection` -> secure authorization_url/setup_url -> `list_provider_connections` -> `connector_status` -> `list_provider_capabilities`

1. Let the user select a provider and environment from the available list.
2. `start_provider_connection` returns a secure authorization_url or setup_url. Send the
   user through that handoff; never request credentials in chat.
3. Never pass an API key, client secret, bearer token, tax id, email, or another
   personal identifier. Provider credentials are encrypted server-side; no ERP credentials
   enter chat, model, RAG, log, or audit output.
4. After secure authorization, identify the intended connection with
   `list_provider_connections`, then use `connector_status` and
   `list_provider_capabilities` to establish its readiness and qualified capability.
5. If the connection is not ready or lacks the required qualified capability, report the
   exact gap and do not claim that provider action is available.

Connection setup does not authorize arbitrary accounting actions. The user retains
approval over provider authorization and any later financial action.
