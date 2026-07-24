---
name: flowaccount-journal-posting-th
description: Use when the user asks to record, draft, post, or approve a FlowAccount journal entry
---

# FlowAccount Journal Posting TH

Use the hosted connector lifecycle and Skill routing before handling a journal request:

1. Call `get_connector_setup` for the user-selected FlowAccount mode and environment.
2. Call `connector_status` for the workspace and stop when the selected profile is not
   ready.
3. Call `connector_capabilities` for that exact selection. Do not treat a declared or
   unvalidated capability as permission to execute a write.
4. Use the returned accounting Skill route for evidence gathering only. Collect the
   journal date, unique reference, description, and at least two balanced debit/credit
   lines. Stop when an account is missing or ambiguous, and never infer a balancing line.

## Provider Execution

Prepare a balanced journal proposal and show the exact date, reference, description,
accounts, debit, credit, and evidence before any provider call. The host must request
explicit user confirmation before invoking a connected provider write capability.
Mercury does not receive provider credentials.

If the host has no authorized provider capability for the selected environment, return
`provider_connection_required` and stop. On connector, authorization, validation, or
accounting-context failure, return only sanitized remediation. Respond in concise Thai.
