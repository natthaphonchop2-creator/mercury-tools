# Connector Catalog

This catalog documents reviewed connector metadata, not a promise that a provider is
currently connected or production-ready. Readiness belongs to one exact connector, mode,
environment, and evidence record. No connector is selected by default.

| Connector | Mode | Readiness | Environments | Authorization owner | Capability status | Last reviewed |
| --- | --- | --- | --- | --- | --- | --- |
| FlowAccount | Native MCP | `available` | `production` | Provider and MCP host | Documented reads are `declared`; documented invoice create is `provider_unavailable` | 2026-07-19 |
| FlowAccount | API driver | `reviewed` | `production`, `sandbox` | User and connected provider host | Reviewed catalog actions are `not_validated` until safe environment evidence exists | 2026-07-19 |
| PEAK Accounting | API driver | `reviewed` | `production`, `uat`, `sandbox` | User and connected provider host | Reviewed catalog actions are `not_validated` until safe environment evidence exists | 2026-07-19 |
| Express Account | Local Bridge | `needs_validation` | `local`, `gateway` | Customer-operated Local Bridge | No provider actions are declared; routed work remains `local_bridge_required` | 2026-07-19 |
| Custom ERP | API driver | `draft` | `production`, `sandbox`, `gateway` | User and reviewed local API-driver configuration | Imported actions remain `not_validated` until host trust and safe validation evidence exist | 2026-07-19 |
| Generic MCP | Native MCP | `user_supplied` | `user_supplied` | User and MCP host | Discovered tools are `declared` only after the host provides a sanitized discovery result | 2026-07-19 |

## Connection modes

- **Native MCP**: the MCP host owns the provider session. Mercury returns connector
  requirements and ordered host-tool handoffs; it does not copy provider OAuth tokens.
- **API driver**: Mercury returns reviewed endpoint and capability metadata. The user's
  connected provider host owns authorization and executes the provider call; credentials
  never enter Mercury tool arguments or workspace records.
- **Local Bridge**: a customer-operated provider/bridge app is required for LAN, desktop,
  export, or other local-system access. It is connected separately to the host, not run as
  a second Mercury MCP. The hosted service never receives LAN credentials or raw database
  access.

## Capability states

- `declared`: provider or host documentation identifies the capability, but no workspace
  evidence has confirmed it.
- `not_validated`: a reviewed catalog action exists but the selected environment lacks
  safe validation evidence.
- `provider_unavailable`: the selected provider mode does not expose that action.
- `local_bridge_required`: the selected work cannot run until the separate bridge is
  installed and validated.

Use `list_connectors`, `get_connector_setup`, `connector_status`, and
`connector_capabilities` for current workspace evidence. This document remains a factual
catalog snapshot, not a live connection status endpoint.
