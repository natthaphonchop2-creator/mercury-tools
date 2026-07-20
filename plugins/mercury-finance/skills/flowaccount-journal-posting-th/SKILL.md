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

1. Call `search_erp_actions` for the exact journal mutation. Stop if results are
   ambiguous.
2. Call `get_erp_action_schema` for the selected immutable action and build only the
   required input.
3. Call `preview_erp_write` and show the sanitized preview, environment, totals, and
   risk information.
4. For every journal mutation, not only approval, branch on the returned
   `approval_level` and `mutation_class`. The original request is never write
   confirmation.

### One Immutable Approval

- A missing or unknown approval contract is invalid for a journal mutation: stop
  without confirming or executing it.
- Whether the approval level is standard or elevated, stop and wait for one distinct
  explicit user approval. Keep the returned `request_id` and `payload_hash` bound to
  the unchanged preview, call `confirm_erp_write` exactly once with both values, then
  call `execute_erp_write` exactly once for that action. Never add another approval
  prompt or confirmation call based on the legacy catalog risk tier.

Approval is a separate action. Do not chain it to draft creation. Start a new `search_erp_actions`, call `get_erp_action_schema`, create a fresh `preview_erp_write`, and use its new `request_id` and payload hash. Apply the returned risk contract again.

If a stale or expired preview, payload hash mismatch, action-version or binding mismatch,
state mismatch, or changed inputs appears. Stop; discard the old request. Never reuse its
`request_id` or `payload_hash`: redo `search_erp_actions`, call
`get_erp_action_schema`, create a fresh `preview_erp_write`, and collect one new
approval.

If execution returns `outcome_unknown`, stop, call `get_erp_request_status` with the
same request, and never replay or retry a mutation. On duplicate, validation, connector,
or authentication failure, stop and report only sanitized remediation. Respond in concise
Thai.
