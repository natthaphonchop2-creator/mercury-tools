---
name: erp-endpoint-catalog-th
description: Use when the user asks what a connected ERP provider supports or needs reviewed capability details.
---

# ERP Endpoint Catalog TH

1. Call `list_accounting_providers` when the user has not selected a provider.
2. For a selected connection, call `list_provider_capabilities` before claiming that an
   operation is available in its provider and environment.
3. Call `get_capability_schema` only for an exact qualified capability and version.
4. Call `search_knowledge` for connector documentation, capability details, method, or
   endpoint terminology; preserve the cited source for material claims.
5. Report the qualified capability, version, documented scope, and any unresolved
   authorization or schema requirement.

Do not infer support from another provider. Do not ask for ERP credentials in the hosted
plugin. A document create needs the exact qualified capability, an immutable preview,
and explicit confirmation; do not present it as an arbitrary write.
