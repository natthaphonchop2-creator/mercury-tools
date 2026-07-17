---
name: erp-endpoint-catalog-th
description: Use when the user asks what an ERP connector or API endpoint supports, or how Mercury can learn a new ERP API.
---

# ERP Endpoint Catalog TH

1. Call `list_connectors` to identify the supported ERP connector.
2. Call `connector_capabilities` for the selected connector before claiming an action is
   available.
3. Call `search_knowledge` with the connector filter and the requested business action,
   method, or endpoint term.
4. Use `get_document` only when the user needs the full indexed source behind a result.
5. Report documented capability, environment, method/path when cited, source, and any
   unresolved schema or authentication requirement.

Do not infer endpoint support from another connector. Do not ask for ERP credentials in
the hosted plugin. For a new ERP, explain that official OpenAPI or endpoint documents
must be curated and ingested before Mercury can claim support.
