---
name: invoice-review-th
description: Use when the user asks to review invoices, tax invoices, receipts, missing fields, or accounting evidence
---

# Invoice Review TH

Use `workspace_connector_status` with `client_token` first. If connector setup is incomplete, route to `connector-credential-setup-th`.

Use `retrieve_workspace_context_pack` for invoice, customer, vendor, VAT, and evidence context. Use `run_mercury_flow` for approved invoice review or exception-check flows.

ตอบภาษาไทยเป็นรายการตรวจ: เลขเอกสาร คู่ค้า วันที่ VAT ยอดรวม สถานะ payment และข้อผิดปกติที่ควรแก้ก่อนปิดงวด. Do not expose raw connector credentials.
