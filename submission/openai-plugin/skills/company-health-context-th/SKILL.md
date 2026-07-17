---
name: company-health-context-th
description: Use when the user asks for a company health, revenue, VAT, cash-flow, or management-report framework with cited accounting context.
---

# Company Health Context TH

1. Confirm the company period, available source data, currency, and requested decision.
2. Call `retrieve_context_pack` for the reporting and accounting context.
3. If a Mercury workspace is supplied, call `retrieve_workspace_context_pack` to route
   knowledge to its selected connector. This does not retrieve live ERP transactions.
4. Use only user-provided sanitized numbers for calculations. State assumptions and
   reconcile totals before presenting conclusions.
5. Return overview, revenue, VAT, cash flow, risks, missing evidence, and accountant
   review points in concise Thai.

Never claim current company figures unless they were supplied in the conversation or
returned by an explicitly connected data tool outside the public Mercury MCP.
