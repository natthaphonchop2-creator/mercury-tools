# ERP Action Catalog

Mercury stores normalized, connector-neutral action metadata for reviewed ERP
documentation. The catalog helps a host agent identify:

- connector and environment
- HTTP method and endpoint
- accounting capability
- request and response schema
- review and validation state
- evidence and citation source

## What the hosted MCP does

The hosted MCP can search the catalog, explain endpoint requirements, route an
accounting Skill, and return the ordered provider capabilities needed for a workflow.
It does not accept ERP credentials or turn an unvalidated catalog entry into an
authorized provider action.

When an ERP provider is already connected to the MCP host, Mercury returns
`host_tool_requirements` and ordered `invoke_connected_provider_capability` steps. The
host remains responsible for provider authorization, user approval, and the actual
provider call.

## Validation states

- `observed`: supported by sanitized execution evidence
- `provider_unavailable`: the provider does not expose the requested operation
- `not_authorized`: the connected account lacks permission
- `validation_failed`: a safe probe failed
- `environment_mismatch`: evidence came from a different environment
- `not_validated`: documented but not proven against a provider

Catalog presence and documentation coverage are not production-readiness claims.
