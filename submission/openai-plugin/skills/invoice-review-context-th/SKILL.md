---
name: invoice-review-context-th
description: Use when the user asks for an invoice, receipt, tax-invoice, or accounting-evidence review using supplied data and cited rules.
---

# Invoice Review Context TH

1. Ask for a sanitized document or the exact fields to review. Do not request secrets or
   unnecessary personal data.
2. If provider data is required, call `list_provider_capabilities` for the selected
   connection and use `run_accounting_skill` only when the exact read capability is
   qualified.
3. Call `retrieve_context_pack` for cited document, jurisdiction, VAT, and evidence
   requirements.
4. Compare supplied fields to cited requirements without inventing missing values.
5. Separate document defects, accounting treatment questions, and missing supporting
   evidence.
6. Respond in concise Thai with status, exceptions, requested corrections, and
   accountant review points.

A live provider read uses only its qualified capability. Document creation is separate:
it requires an immutable preview and explicit confirmation before any provider action.
