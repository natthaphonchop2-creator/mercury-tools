---
name: vat-summary-th
description: Use when the user asks for a Thai VAT summary, output tax, input tax, or VAT review
---

# VAT Summary TH

## V1 route

1. Call `get_mercury_context` and select one authorized workspace.
2. Call `connector_status`, then `list_provider_capabilities` for the selected ERP connection.
   Continue only when the exact invoice-list capability version has passed qualification.
3. Call `run_accounting_skill` with `skill_id=vat-summary-th`, `skill_version=0.1.0`, month or
   period, query, workspace, and connection.

## Result

ตอบภาษาไทยแบบกระชับ: ภาษีขาย ภาษีซื้อ ยอดสุทธิ ข้อยกเว้น เอกสารที่ข้อมูลไม่ครบ และรายการ
ที่ต้องให้นักบัญชีตรวจทาน. Separate document totals from tax interpretation. Preserve official
knowledge citations and do not represent estimates as a filed VAT return. Do not show verbose
evidence unless the user asks for audit detail.

This Skill is read-only. Provider credentials never enter chat or model context; Mercury stores
encrypted provider authorization server-side and returns only sanitized evidence and audit data.
