---
name: flowaccount-journal-posting-th
description: Use when the user asks to record, draft, post, or approve a FlowAccount journal entry
---

# FlowAccount Journal Posting TH

Before calling any tool, collect and validate the required accounting context: active
repository, FlowAccount environment and company, document date, unique reference,
description, and at least two journal lines with side, positive amount, and an exact
account code or name. Apply the validation contract in
`src/mercury_tools/journals/models.py`. Stop if an account is missing or ambiguous. Show
the journal table and verify that total debit equals total credit. Never infer a balancing
line or treat the original request as write confirmation.

## Required Sequence

1. Call `search_erp_actions` for the exact journal action. Stop if results are ambiguous.
2. Call `get_erp_action_schema` for the selected immutable action and build only the
   required input.
3. Call `preview_erp_write` and show the sanitized preview, environment, totals, and
   risk tier.
4. Stop and wait for explicit confirmation. The original request is not confirmation.
5. Keep the returned `request_id` and `payload_hash` bound to the unchanged preview,
   then call `confirm_erp_write` with both values.
6. Call `execute_erp_write` exactly once after the request is ready to execute.

A Tier 2 approval is a separate action. Do not chain it to draft creation. Search and
load the approval schema, create a fresh `preview_erp_write`, then obtain two separate explicit confirmations.
After each explicit confirmation, call `confirm_erp_write` with the same bound request
and hash. Stop again when final confirmation is still required. Execute that approval
exactly once only after both confirmations are recorded.

If execution returns `outcome_unknown`, stop, call `get_erp_request_status` with the
same request, and never replay or retry a mutation. On duplicate, validation, connector, or
authentication failure, stop and report only sanitized remediation. Respond in concise
Thai.
