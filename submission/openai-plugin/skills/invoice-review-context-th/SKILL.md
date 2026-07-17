---
name: invoice-review-context-th
description: Use when the user asks for an invoice, receipt, tax-invoice, or accounting-evidence review using supplied data and cited rules.
---

# Invoice Review Context TH

1. Ask for a sanitized document or the exact fields to review. Do not request secrets or
   unnecessary personal data.
2. Call `retrieve_context_pack` for the document type, jurisdiction, VAT, and evidence
   requirements.
3. Compare supplied fields to cited requirements without inventing missing values.
4. Separate document defects, accounting treatment questions, and missing supporting
   evidence.
5. Respond in concise Thai with status, exceptions, requested corrections, and
   accountant review points.

The public hosted MCP does not fetch a private invoice from an ERP. Use another
user-authorized data connector only when the host provides one.
