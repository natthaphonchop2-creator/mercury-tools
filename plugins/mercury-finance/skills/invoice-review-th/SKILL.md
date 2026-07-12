---
name: invoice-review-th
description: Use when the user asks to review invoices, tax invoices, receipts, missing fields, or accounting evidence
---

# Invoice Review TH

1. Call `credential_status` for the active repository, connector, and environment. Stop
   and route to local connector setup unless status is connected.
2. Call `retrieve_context_pack` for invoice, VAT, counterparty, and review policy
   context. Preserve its citations for accounting and compliance claims.
3. Call `search_erp_actions` for the required safe invoice or receipt read. Stop on
   ambiguity.
4. Call `get_erp_action_schema` for the exact selected action and prepare only its inputs.
5. Call `run_erp_read`; repeat the search, schema, and read steps only when related
   documents require separate actions.

ตอบภาษาไทยแบบกระชับ: เลขเอกสาร คู่ค้า วันที่ VAT ยอดรวม สถานะ และข้อผิดปกติที่
ควรแก้ก่อนปิดงวด. Do not include evidence counts, audit paths, or verbose evidence unless the user explicitly requests audit detail.
