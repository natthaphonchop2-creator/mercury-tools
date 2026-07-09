---
name: vat-summary-th
description: Use when the user asks for Thai VAT output tax, input tax, filing context, or tax-period summaries
---

# VAT Summary TH

Call `connector_status` with the current `workspace_id` first. If setup is
incomplete, route to `connector-credential-setup-th` and stop.

Use `retrieve_workspace_context_pack` for the selected ERP's company, tax
period, chart of accounts, invoice, and evidence context. Use
`run_mercury_flow` only for a read-only VAT flow and keep the same
`workspace_id` throughout the task.

ตอบภาษาไทยแบบบัญชีอ่านง่าย แยกภาษีขาย ภาษีซื้อ ยอดสุทธิ ข้อยกเว้น และรายการ
ที่ต้องให้นักบัญชีตรวจทาน. Never show raw credentials.
