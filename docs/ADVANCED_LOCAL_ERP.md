# Advanced Local ERP Execution

Advanced ERP reads and mutations run through a separately connected local Mercury
MCP. Start it from the reviewed repository:

```bash
mercury mcp serve-local
```

This server is not part of the one-click hosted plugin. It resolves repository
roots from the active MCP session and reads credentials only from repository-local
secure state. Do not provide provider credentials, OAuth tokens, API keys, or
business payloads to the hosted Mercury MCP or in chat.

## Mutation Sequence

1. Use `search_erp_actions` and `get_erp_action_schema` to select one reviewed
   action, then call `prepare_erp_mutation` with the action ID and required inputs.
2. The prepared response contains a sanitized immutable summary, `payload_hash`,
   `mutation_class`, `approval_level`, expiry, and the exact `next_tool`.
3. Obtain one explicit approval for that unchanged summary. Call only the returned
   class-specific tool with `request_id` and `payload_hash`:
   - `execute_erp_create` for `create`.
   - `execute_erp_update` for `update`.
   - `execute_sensitive_erp_action` for `sensitive`.
4. On expiry, hash mismatch, binding mismatch, or `outcome_unknown`, stop. Inspect
   `get_erp_request_status`; never replay a mutation. Create a new preparation only
   after resolving the cause.

The internal preview, payload binding, credential revision checks, preflight checks,
redaction, append-only audit records, and fail-closed restart behavior remain
mandatory. The selected execute tool records the single host-visible approval; there
is no public confirmation tool.

## FlowAccount Boundary

The official FlowAccount MCP is read-only. A separately reviewed FlowAccount API-driver
action may write only through this advanced local MCP after local credential
setup, action validation, immutable preparation, and the class-specific approval flow.
