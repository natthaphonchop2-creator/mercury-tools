---
name: vat-summary-th
description: Use when the user asks for Thai VAT output tax, input tax, filing context, or tax-period summaries
---

# VAT Summary TH

Use `workspace_connector_status` with `client_token` first. If connector setup is incomplete, route to `connector-credential-setup-th`.

Use `retrieve_workspace_context_pack` for company, tax period, chart of accounts, invoice, and evidence context. Use `run_mercury_flow` only for an approved VAT summary flow.

ตอบภาษาไทยแบบบัญชีอ่านง่าย แยกภาษีขาย ภาษีซื้อ ยอดสุทธิ ข้อยกเว้น และรายการที่ต้องให้ผู้ทำบัญชีตรวจทาน. Never show raw credentials.
