---
name: connector-onboarding-th
description: Use when the user wants to select a public Mercury ERP profile or check connector readiness without sharing credentials.
---

# Connector Onboarding TH

If the user has no Mercury workspace, request approval before calling
`create_public_workspace`. Then follow this lifecycle exactly:

`list_connectors` -> `get_connector_setup` -> `link_connector_profile` -> host/provider authorization -> `validate_connector_connection` -> `connector_status`

1. Ask the user to select one connector, connection mode, environment, and company.
2. Link only sanitized profile metadata. Never pass an API key, client secret, bearer
   token, tax id, email, or another personal identifier.
3. Let the host/provider own OAuth, API credentials, company selection, and every
   provider call. For an API driver or Local Bridge, hand off to the user's connected
   provider integration; no ERP credentials enter the hosted core or chat.
4. Accept only sanitized host-observed evidence when validating the profile.
5. If status reports multiple profiles or mode_required, ask for an explicit profile;
   never choose silently.

The hosted core stores sanitized profile and audit metadata but no ERP credentials.
The host may invoke a separately connected provider only after any required user
approval. A linked profile is not a validated connection.
