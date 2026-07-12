---
title: Thai VAT And Tax Invoice Basics
doc_type: tax
jurisdiction: TH
review_status: reviewed
source_uri: mercury://wiki/tax/th-input-vat-basics
source_url: https://www.rd.go.th/fileadmin/user_upload/ebook/taxinvoice.pdf
source_path: wiki/tax/th-input-vat-basics.md
source_verified_at: "2026-07-10"
professional_review_required: true
metadata:
  authority: Thai Revenue Department
  subject: VAT, input tax, output tax, and tax invoices
  reference_index: https://www.rd.go.th/307.html
---

# Thai VAT And Tax Invoice Basics

## Operational Summary

ภาษีขายเกิดจากการขายสินค้าหรือให้บริการของผู้ประกอบการจดทะเบียน ส่วนภาษีซื้อ
เกิดจากการซื้อสินค้าหรือรับบริการที่เกี่ยวข้องกับกิจการและมีหลักฐานตามกฎหมาย
การสรุป VAT ต้องแยกยอดขาย ยอดซื้อ ภาษีขาย ภาษีซื้อ รายการยกเว้น รายการอัตรา
ศูนย์ และภาษีซื้อที่ยังไม่ควรนำมาหัก

ใบกำกับภาษีเป็นหลักฐานสำคัญ แต่การมีเอกสารใน ERP ไม่ได้ยืนยันโดยอัตโนมัติว่า
ภาษีซื้อใช้หักได้ ต้องตรวจผู้ออกเอกสาร รายการบังคับ ความเกี่ยวข้องกับกิจการ
ช่วงเวลาภาษี เอกสารแก้ไข และข้อจำกัดตามกฎหมายปัจจุบัน

## Evidence Checklist

- ใบกำกับภาษี ใบเพิ่มหนี้ ใบลดหนี้ และใบแทนที่เกี่ยวข้อง
- ชื่อ ที่อยู่ เลขประจำตัวผู้เสียภาษี และสาขาของคู่ค้าเท่าที่กฎหมายกำหนด
- เลขที่เอกสาร วันที่ มูลค่าก่อนภาษี และจำนวน VAT
- หลักฐานรับสินค้า รับบริการ ชำระเงิน และความเกี่ยวข้องกับกิจการ
- รายงานภาษีซื้อ รายงานภาษีขาย บัญชีแยกประเภท และแบบยื่นของงวด
- สถานะ void, cancelled, refunded และเอกสารที่บันทึกข้ามงวด

## Reconciliation Checks

- กระทบยอดภาษีขายกับเอกสารขายและบัญชี VAT output
- กระทบยอดภาษีซื้อกับเอกสารซื้อและบัญชี VAT input
- ตรวจเอกสารซ้ำ เลขที่ขาดช่วง วันที่ผิดงวด และ VAT ที่คำนวณไม่สัมพันธ์กับฐาน
- แยกภาษีซื้อที่ข้อมูลไม่ครบ ไม่เกี่ยวกับกิจการ หรืออาจเข้าลักษณะต้องห้าม
- ตรวจ credit note และ debit note ให้กลับรายการฐานภาษีและ VAT ในงวดที่ถูกต้อง
- อธิบายส่วนต่างระหว่าง ERP รายงานภาษี และยอดที่ยื่นจริง

## Escalate To Accountant

ให้นักบัญชีตรวจเมื่อมีภาษีซื้อต้องห้าม การเฉลี่ยภาษีซื้อ ธุรกรรมต่างประเทศ
การยื่นเพิ่มเติม เอกสารย้อนหลัง หนี้สูญ การคืนสินค้า หรือข้อสงสัยเรื่องจุดเกิด
ความรับผิดทางภาษี Mercury ต้องแสดงยอดเป็น estimate จนกว่ารายงานภาษีและเอกสาร
จะผ่านการทบทวน

## Limitations

อัตรา แบบยื่น กำหนดเวลา และรายละเอียดทางกฎหมายอาจเปลี่ยนได้ ต้องตรวจกรมสรรพากร
ฉบับปัจจุบันก่อนยื่น Mercury ไม่ยื่นแบบและไม่ตัดสินสิทธิภาษีแทนนักบัญชี

## Official References

- Revenue Department tax-invoice guide: https://www.rd.go.th/fileadmin/user_upload/ebook/taxinvoice.pdf
- Revenue Department VAT portal: https://www.rd.go.th/307.html
- Revenue Code VAT sections: https://www.rd.go.th/5205.html
