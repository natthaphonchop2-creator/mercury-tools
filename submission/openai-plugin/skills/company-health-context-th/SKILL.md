---
name: company-health-context-th
description: Use when the user asks for a company health, revenue, VAT, cash-flow, or management-report framework with cited accounting context.
---

# Company Health Context TH

1. Confirm the company period, available source data, currency, and requested decision.
2. Call `list_provider_capabilities` for the selected connection before using provider
   data. Do not infer a capability from another provider or environment.
3. Call `run_accounting_skill` with the selected connection and sanitized inputs only
   when its exact required read capability is qualified.
4. Call `retrieve_context_pack` for cited accounting and tax context.
5. Use only sanitized figures supplied by the user or returned through the qualified
   read. State assumptions and reconcile totals before presenting conclusions.
6. Return overview, revenue, VAT, cash flow, risks, missing evidence, and accountant
   review points in concise Thai.

Never claim current company figures unless they were supplied in the conversation or
returned by an explicitly qualified provider read.
