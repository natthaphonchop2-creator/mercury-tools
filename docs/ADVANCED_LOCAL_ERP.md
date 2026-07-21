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
   action, then call `prepare_erp_mutation` with the action ID and the explicit
   input envelope below.
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

## Input Envelope

`run_erp_read` and `prepare_erp_mutation` accept an `inputs` object with exactly one
required property: `json_object`. Its value is a UTF-8 JSON object containing the ERP
request inputs. Do not include credentials. The JSON must be a single object with no
duplicate keys, be at most 65,536 characters and bytes, and remain within the local
depth and key limits.

Use this exact call shape, replacing only the reviewed action ID and JSON values:

```json
{
  "action_id": "reviewed-action-id",
  "inputs": {
    "json_object": "{\"body\":{\"reference\":\"EXAMPLE-001\"}}"
  }
}
```

The same `inputs.json_object` envelope is required for a read and a mutation
preparation. Invalid input returns `erp_inputs_invalid` without reflecting the
submitted payload.

## FlowAccount Boundary

The official FlowAccount MCP is read-only. A separately reviewed FlowAccount API-driver
action may write only through this advanced local MCP after local credential
setup, action validation, immutable preparation, and the class-specific approval flow.
