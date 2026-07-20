---
name: erp-endpoint-catalog-th
description: Use when the user asks what an ERP connector or API endpoint supports, or how Mercury can learn a new ERP API.
---

# ERP Endpoint Catalog TH

1. Call `list_connectors` to identify the supported ERP connector.
2. Call `get_connector_setup` to distinguish native MCP, reviewed API-driver, and Local
   Bridge availability without requesting a credential.
3. For a linked workspace profile, call `connector_capabilities` before claiming an
   action is available in a specific mode and environment.
4. Call `search_knowledge` with the connector and requested capability, method, or
   endpoint term. Use `get_document` only for the full indexed source.
5. Report documented capability, environment, method/path when cited, source, and any
   unresolved schema, authorization, or validation requirement.

Do not infer endpoint support from another connector. Do not ask for ERP credentials in
the hosted plugin. A new or advanced reviewed driver is a local handoff; Mercury must
curate official endpoint documentation before claiming support or planning a write.
