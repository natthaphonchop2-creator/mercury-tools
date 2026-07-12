---
name: vat-summary-th
description: Use when the user asks for Thai VAT output tax, input tax, filing context, or tax-period summaries
---

# VAT Summary TH

1. Call `credential_status` for the active repository, connector, and environment. Stop
   and route to local connector setup unless status is connected.
2. Call `retrieve_context_pack` for the company, tax period, VAT policy, and filing
   context. Preserve its citations for tax and accounting claims.
3. Call `search_erp_actions` with `risk_tier=0` for each required safe VAT or document
   read. Stop on ambiguity.
4. Call `get_erp_action_schema` for the exact selected action. Inspect the returned schema
   and prepare only its inputs.
5. Call `run_erp_read`; repeat the search, schema, and read steps only when another VAT
   source requires a separate action.

ตอบภาษาไทยแบบกระชับ: ภาษีขาย ภาษีซื้อ ยอดสุทธิ ข้อยกเว้น และรายการที่ต้องให้
นักบัญชีตรวจทาน. Do not include evidence counts, audit paths, or verbose evidence unless the user explicitly requests audit detail.
