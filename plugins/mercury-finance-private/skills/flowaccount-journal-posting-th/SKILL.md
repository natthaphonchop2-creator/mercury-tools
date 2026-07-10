---
name: flowaccount-journal-posting-th
description: Use when a user asks to record, post, draft, or approve a FlowAccount journal entry for a connected company.
---

# FlowAccount Journal Posting TH

Use `workspace_id` to keep every action inside the selected company connector.
Respond in Thai unless the user requests another language.

## Required Sequence

1. Call `connector_status` for `workspace_id`. Continue only when exactly one
   FlowAccount profile is ready. Never switch environment implicitly.
2. Collect missing `document_date`, unique `reference`, description, and lines.
   Each line needs `side`, amount, and exact account code or account name.
3. Call `preview_flowaccount_journal`.
4. If account resolution is missing or ambiguous, show safe candidates and stop.
   Never choose a fuzzy account match for the user.
5. Show one Dr/Cr table, total debit, total credit, environment, date, and
   reference. Confirm that debit equals credit.
6. After preview, wait for explicit confirmation. Do not treat the original
   accounting request as permission to write.
7. Call `create_flowaccount_journal_draft` with the returned `preview_id` and
   `confirm=true` only after that confirmation.
8. Show `record_id`, `document_serial`, and draft status. Explain that a draft
   does not affect financial statements until approved.
9. Then wait for a new explicit confirmation. Never combine draft creation and
   approval in one turn.
10. Call `approve_flowaccount_journal` with that `record_id` and `confirm=true`
    only after the second confirmation.

## Stop Conditions

- On `confirmation_required`, ask once and stop.
- On `duplicate_blocked`, show the existing reference and stop.
- On `outcome_unknown`, never retry. Ask the user to inspect FlowAccount first.
- On connector or authentication error, do not request secrets in chat. Route
  the user to the Mercury connector setup flow.
- Never call payment, delete, void, email, share, attachment, or arbitrary
  status endpoints.
