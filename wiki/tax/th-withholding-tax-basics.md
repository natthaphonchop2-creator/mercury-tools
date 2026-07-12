---
title: Thai Withholding Tax Basics
doc_type: tax
jurisdiction: TH
review_status: reviewed
source_uri: mercury://wiki/tax/th-withholding-tax-basics
source_url: https://www.rd.go.th/27862.html
source_path: wiki/tax/th-withholding-tax-basics.md
source_verified_at: "2026-07-10"
professional_review_required: true
metadata:
  authority: Thai Revenue Department
  subject: withholding tax workflow and evidence
  service_guides: https://www.rd.go.th/63641.html
---

# Thai Withholding Tax Basics

## Operational Summary

ภาษีเงินได้หัก ณ ที่จ่ายเป็นการจัดเก็บภาษีล่วงหน้า โดยผู้จ่ายเงินได้อาจมีหน้าที่
หักจากยอดที่จ่ายและนำส่งกรมสรรพากรตามหลักเกณฑ์ของประเภทผู้จ่าย ผู้รับเงินได้
ประเภทเงินได้ สถานะทางภาษี และเงื่อนไขธุรกรรม

Mercury ไม่ hard-code อัตราหรือแบบยื่นจากชื่อ expense เพียงอย่างเดียว ต้องจัด
ประเภทข้อเท็จจริงและตรวจตารางหรือคำสั่งกรมสรรพากรฉบับปัจจุบันก่อนคำนวณ

## Evidence Checklist

- ใบแจ้งหนี้ สัญญา ขอบเขตบริการ และหลักฐานวันที่จ่ายจริง
- ข้อมูลผู้จ่าย ผู้รับเงินได้ ประเภทนิติบุคคล/บุคคล และถิ่นที่อยู่ทางภาษี
- ฐานเงินได้ ค่าบริการ ค่าใช้จ่ายผ่านบัญชี VAT และรายการหักอื่น
- หนังสือรับรองภาษีหัก ณ ที่จ่ายและเลขอ้างอิงการนำส่ง
- แบบยื่น รายละเอียดรายผู้รับ และหลักฐานชำระภาษี
- บัญชีเจ้าหนี้ ค่าใช้จ่าย ภาษีหัก ณ ที่จ่ายค้างจ่าย และรายการจ่ายธนาคาร

## Reconciliation Checks

- เชื่อม invoice กับ payment เพื่อใช้วันที่เกิดหน้าที่ตามข้อเท็จจริง
- กระทบยอดยอดหักตามหนังสือรับรองกับบัญชีค้างจ่ายและแบบนำส่ง
- ตรวจเอกสารซ้ำ เอกสารขาด เลขผู้เสียภาษีผิด และผู้รับเงินได้ผิดประเภท
- แยกยอดก่อน VAT ค่าธรรมเนียม และรายการเบิกแทนตามหลักฐาน
- ติดตามรายการค้างนำส่งและรายการที่จ่ายแล้วแต่ยังไม่ออกหนังสือรับรอง

## Escalate To Accountant

ให้นักบัญชีหรือผู้เชี่ยวชาญภาษีตรวจอัตรา แบบยื่น สนธิสัญญาภาษีซ้อน ผู้รับเงินได้
ต่างประเทศ ค่าลิขสิทธิ์ ดอกเบี้ย เงินปันผล ค่าเช่า และธุรกรรมที่จัดประเภทไม่ชัด
ก่อนบันทึกหรือยื่นจริง

## Limitations

หน้านี้เป็น workflow checklist ไม่ใช่ตารางอัตราภาษี อัตราและเงื่อนไขต้องตรวจ
จากกรมสรรพากรตามวันที่จ่ายและข้อเท็จจริงของคู่สัญญา Mercury ไม่ยื่นแบบหรือออก
หนังสือรับรองแทนผู้มีหน้าที่

## Official References

- Revenue Department withholding-tax portal: https://www.rd.go.th/27862.html
- Revenue Department WHT service guides: https://www.rd.go.th/63641.html
