---
name: flowaccount-journal-posting-th
description: Use when the user asks to record, draft, post, or approve a FlowAccount journal entry
---

# FlowAccount Journal Posting TH

Use hosted connector lifecycle and Skill routing to determine whether the requested
FlowAccount journal capability is available. Collect the journal date, unique reference,
description, and at least two balanced debit/credit lines. Stop when an account is missing
or ambiguous, and never infer a balancing line.

## API-Driver Write Handoff

For a reviewed API-driver journal mutation, return `advanced_local_handoff` rather than
calling a local ERP action from this public Skill. Direct the user to
`docs/ADVANCED_LOCAL_ERP.md` and require a separately connected local Mercury MCP before
continuing. The hosted plugin does not own ERP credentials or execute local mutations.

The advanced-local handoff must use this input shape, without sending it to the hosted
plugin:

```yaml
action_id: reviewed-action-id
inputs:
  json_object: '{"body":{"reference":"EXAMPLE-001"}}'
```

`json_object` is the only `inputs` property. It contains one UTF-8 JSON object and must
not contain credentials.

The advanced local guide defines the immutable preparation, one approval, class-specific
execution, expiry, redaction, audit, and no-replay requirements. On connector,
authorization, validation, or accounting-context failure, stop and return only sanitized
remediation. Respond in concise Thai.
