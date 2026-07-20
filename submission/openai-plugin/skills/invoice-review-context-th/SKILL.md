---
name: invoice-review-context-th
description: Use when the user asks for an invoice, receipt, tax-invoice, or accounting-evidence review using supplied data and cited rules.
---

# Invoice Review Context TH

1. Ask for a sanitized document or the exact fields to review. Do not request secrets or
   unnecessary personal data.
2. Call `get_accounting_skill_schema` for `invoice-review-th`, then call
   `run_accounting_skill` with the workspace and sanitized document fields.
3. Call `retrieve_context_pack` for cited document, jurisdiction, VAT, and evidence
   requirements. Use `retrieve_workspace_context_pack` only when connector routing is
   needed.
4. Compare supplied fields to cited requirements without inventing missing values.
5. Separate document defects, accounting treatment questions, and missing supporting
   evidence.
6. Respond in concise Thai with status, exceptions, requested corrections, and
   accountant review points.

The public hosted MCP does not fetch a private invoice from an ERP. A live read remains a
host/provider or approved local handoff described by the returned Skill plan.
