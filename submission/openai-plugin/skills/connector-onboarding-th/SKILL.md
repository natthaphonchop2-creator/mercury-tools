---
name: connector-onboarding-th
description: Use when the user wants to select a public Mercury ERP profile or check connector readiness without sharing credentials.
---

# Connector Onboarding TH

Follow this gate in order:

1. Call `list_connectors` and ask the user to choose connector and environment.
2. Call `connector_capabilities` and explain the public hosted capability boundary.
3. Call `create_public_workspace` only after the user agrees to create a non-secret
   workspace profile.
4. Call `start_connector_setup` with connector, environment, and optional company display
   name. Never pass an API key, client secret, bearer token, tax id, or email.
5. Call `connector_status` and report the sanitized profile plus the next available step.

The hosted MCP does not validate or store ERP credentials and cannot directly execute a
production ERP mutation. Do not tell the user that an ERP account is connected merely
because a public profile exists.
