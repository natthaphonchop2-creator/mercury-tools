---
name: flowaccount-journal-posting-th
description: Use when the user asks to prepare or create a supported FlowAccount accounting document or journal draft
---

# FlowAccount Document Create TH

## Qualification gate

1. Call `get_mercury_context` and use the selected workspace.
2. Call `connector_status`, then `list_provider_capabilities` for the exact FlowAccount
   connection. Continue only when the requested create capability and version are enabled by
   qualification.
3. Call `get_capability_schema` for that exact capability version. Validate every document line,
   amount, tax treatment, debit/credit balance, date, and reference.
4. Call `run_accounting_skill` with `skill_id=flowaccount-journal-posting-th` and
   `skill_version=0.1.0` to produce the accounting review plan. A Skill never grants authority.

## Mutation lifecycle

Use only this sequence: `prepare_document_create` -> `render_document_preview` ->
`confirm_document_create`.

- Preparation creates an immutable preview and must not contact the provider.
- Show the Thai preview and accountant review points before asking for explicit user confirmation.
- Confirm only with the exact preview ID, state version, and confirmation literal.
- Dispatch once. Never retry an unknown provider outcome automatically; verify or reconcile it.
- Return the provider result and sanitized audit reference without exposing credentials.

Provider credentials never enter chat or model context. The mandatory contract is immutable
preview, explicit confirmation, dispatch once, verify or reconcile, and sanitized audit.
