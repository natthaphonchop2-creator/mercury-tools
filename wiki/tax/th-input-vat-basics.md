---
title: Thai Input VAT Basics
doc_type: tax
jurisdiction: TH
review_status: draft
source_uri: mercury://wiki/tax/th-input-vat-basics
source_url: https://www.rd.go.th/
---

# Thai Input VAT Basics

ภาษีซื้อ คือ VAT ที่กิจการจ่ายจากใบกำกับภาษีซื้อหรือค่าใช้จ่ายที่เกี่ยวข้อง
กับกิจการ การสรุปภาษีซื้อควรตรวจจำนวนเอกสาร วันที่เอกสาร เลขที่ใบกำกับภาษี
ชื่อผู้ขาย ยอดก่อน VAT ยอด VAT และสิทธิในการนำไปหักภาษีขาย

ภาษีขาย คือ VAT จากเอกสารขาย เช่น ใบกำกับภาษีหรือใบเสร็จรับเงิน/ใบกำกับภาษี
การสรุป VAT รายเดือนควรแยกภาษีขาย ภาษีซื้อ ส่วนต่างที่ต้องชำระหรือขอคืน และ
รายการที่ควรให้ฝ่ายบัญชีตรวจทาน

Input VAT review should reconcile tax invoices, VAT amounts, supplier identity,
invoice dates, and eligibility for deduction. Mercury should treat VAT summaries
as estimates unless the underlying tax report has been formally reviewed.

For normal user-facing answers, show the month, number of documents, VAT estimate,
and a short source line. Keep technical evidence and endpoint details hidden unless
the user asks for audit evidence or debug output.
