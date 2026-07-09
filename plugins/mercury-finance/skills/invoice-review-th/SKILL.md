---
name: invoice-review-th
description: Use when the user asks to review invoices, tax invoices, receipts, missing fields, or accounting evidence
---

# Invoice Review TH

Call `connector_status` with the current `workspace_id` first. If setup is
incomplete, route to `connector-credential-setup-th` and stop.

Use `retrieve_workspace_context_pack` for connector-specific invoice, customer,
vendor, VAT, and evidence context. Use `run_mercury_flow` only for a read-only
review flow and keep the same `workspace_id` throughout the task.

ตอบภาษาไทยเป็นรายการตรวจ: เลขเอกสาร คู่ค้า วันที่ VAT ยอดรวม สถานะ payment
และข้อผิดปกติที่ควรแก้ก่อนปิดงวด. Never expose connector credentials.
