---
title: Mercury LLM Wiki Schema
doc_type: schema
review_status: reviewed
---

# Mercury LLM Wiki Schema

## Domain

Accounting agent knowledge for Thai-first finance and accounting workflows:
standards, Thai VAT/tax, connector playbooks, audit evidence, and management reporting.

## Frontmatter

Every wiki page should include:

```yaml
---
title: Page title
doc_type: accounting-standard | tax | connector | endpoint_dictionary | workflow | schema | index
jurisdiction: TH | international
connector: flowaccount | peak | express | null
review_status: draft | reviewed
source_uri: mercury://wiki/...
source_url: https://...
effective_date: YYYY-MM-DD
---
```

## Rules

- Prefer official sources and accountant-reviewed material.
- Keep connector credentials out of wiki pages.
- Use citations and source URLs/paths whenever possible.
- Write pages as reusable context for MCP host agents, not as final chat answers.
