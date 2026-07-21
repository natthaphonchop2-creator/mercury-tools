---
name: company-health-context-th
description: Use when the user asks for a company health, revenue, VAT, cash-flow, or management-report framework with cited accounting context.
---

# Company Health Context TH

1. Confirm the company period, available source data, currency, and requested decision.
2. Call `get_accounting_skill_schema` for `company-health-check-th`, then call
   `run_accounting_skill` with the workspace and sanitized inputs. Follow the returned
   capability plan; do not replace it with connector-specific assumptions.
3. Call `retrieve_workspace_context_pack` for connector-routed citations, or
   `retrieve_context_pack` when no workspace-specific routing is needed.
4. A provider read in the returned plan is performed by the host/provider integration,
   not by Mercury's hosted Skill tool.
5. Use only sanitized figures supplied by the user or returned by a host-authorized
   read. State assumptions and reconcile totals before presenting conclusions.
6. Return overview, revenue, VAT, cash flow, risks, missing evidence, and accountant
   review points in concise Thai.

Never claim current company figures unless they were supplied in the conversation or
returned by an explicitly connected host data tool.
