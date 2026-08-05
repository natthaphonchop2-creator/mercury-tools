---
name: accounting-knowledge-research-th
description: Use when the user needs cited Thai or international accounting, tax, finance, or ERP knowledge.
---

# Accounting Knowledge Research TH

1. Confirm the jurisdiction, reporting period, and decision the user is making.
2. Call `retrieve_context_pack` for a complete cited package. Use `search_knowledge`
   when the request is narrow or another reviewed source is needed.
3. Preserve source title, source URI, and chunk id for every material claim.
4. Separate sourced facts, inferences, missing facts, and accountant review points.
5. If no relevant knowledge is returned, say so and request the missing source or scope.

Respond in concise Thai unless the user asks for another language. Do not expose audit
metadata by default, fill evidence gaps from assumptions, or treat retrieved context as
a substitute for professional review of company-specific facts.
