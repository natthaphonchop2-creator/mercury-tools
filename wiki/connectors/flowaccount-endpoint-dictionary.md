---
title: FlowAccount Endpoint Data Dictionary
doc_type: endpoint_dictionary
review_status: reviewed
jurisdiction: TH
connector: flowaccount
source_uri: mercury://wiki/connectors/flowaccount-endpoint-dictionary
source_url: https://developers.flowaccount.com/
source_path: wiki/connectors/flowaccount-endpoint-dictionary.md
metadata:
  official_docs: https://developers.flowaccount.com/
  sdk_repository: https://github.com/flowaccount/flowaccount-openapi-sdk
  support_openchat: https://line.me/ti/g2/Ph-aVSDpdApaBmeN152QvR6-5bPFMZXAISefhQ?utm_source=invitation&utm_medium=link_copy&utm_campaign=default
  production_api_gateway: https://openapi.flowaccount.com/v1
  sandbox_api_gateway: https://openapi.flowaccount.com/test
  production_token_url: https://openapi.flowaccount.com/v1/token
  sandbox_token_url: https://openapi.flowaccount.com/test/token
  grant_type: client_credentials
  scope: flowaccount-api
---

> Mercury endpoint knowledge page. This page intentionally stores endpoint metadata, presets, test classes, and source links only. It must not store client ids, client secrets, bearer tokens, cookies, tax IDs, or customer data.

## Mercury Operating Note

FlowAccount is an endpoint-capable accounting connector. Mercury Cloud serves this
catalog and related knowledge read-only. The Mercury local MCP may execute a
cataloged GET, POST, PUT, PATCH, or DELETE from the user's machine after local
credential setup, schema validation, risk classification, and the required
preview/confirmation steps. It never exposes an arbitrary-URL HTTP proxy. Setup
validation uses a low-impact company/profile check.

# FlowAccount Endpoint Data Dictionary

Generated: 2026-06-26 11:47:29 UTC

Source:

- Postman collection: OpenAPI - Collection
- Postman uid: `45983860-979836bd-755e-4f91-8851-b1ae7a231c66`
- Collection updated at: `2025-07-30T03:09:59.000Z`
- Official docs: https://developers.flowaccount.com/

หมายเหตุ: ไฟล์นี้สร้างจาก Postman collection ที่ sanitize แล้ว ไม่แสดง access token, client secret, cookie หรือข้อมูลส่วนตัวจาก response จริง

## Coverage Summary

| Metric | Value |
| --- | --- |
| Total endpoints | 190 |
| Methods | DELETE=13, GET=36, POST=119, PUT=22 |
| Test classes | destructive_delete=13, executed_auth=1, file_upload=10, mutating_update=22, mutating_write=89, outbound_email=9, requires_record_id=11, safe_read=25, share_link=10 |

## Module Summary

| Module | Meaning | Endpoints |
| --- | --- | --- |
| Authorization | การยืนยันตัวตนและขอ access token | 1 |
| Billing Notes (BL) | ใบวางบิล | 13 |
| Cash Invoice (CA) | ใบกำกับภาษี/ใบเสร็จรับเงินสด | 18 |
| Chart of Account (COA) | ผังบัญชี | 1 |
| Contacts | ผู้ติดต่อ ลูกค้า หรือผู้ขาย | 5 |
| Expense (EXP) | ค่าใช้จ่าย | 20 |
| Journal Entry | รายการสมุดรายวัน | 10 |
| MyCompany | ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า | 9 |
| Product Master | ข้อมูล master ของสินค้า | 5 |
| Products | สินค้าและบริการ | 8 |
| Purchase Order (PO) | ใบสั่งซื้อ | 13 |
| Quotation (QT) | ใบเสนอราคา | 10 |
| Receipt (RE) | ใบเสร็จรับเงิน | 14 |
| Receiving Inventory (RI) | เอกสารซื้อ/รับสินค้า | 18 |
| Tax Invoices (INV) | ใบกำกับภาษี | 18 |
| Tax Invoices (INV) / Receipt (RE) | ใบกำกับภาษี/ใบเสร็จรับเงิน | 18 |
| Withholding Tax (WHT) | หนังสือรับรองหัก ณ ที่จ่าย | 9 |

## Test Class Meaning

| Test class | Meaning |
| --- | --- |
| executed_auth | ขอ token ได้ทันที |
| safe_read | GET ที่ไม่มี path variable ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| requires_record_id | GET ที่ต้องมี record id จริงก่อน |
| mutating_write | POST ที่สร้าง/เปลี่ยนข้อมูลใน sandbox |
| mutating_update | PUT ที่แก้ไขข้อมูล ต้องมีข้อมูลทดสอบก่อน |
| destructive_delete | DELETE ต้องสร้างข้อมูลทดสอบแล้วค่อยลบ |
| file_upload | ต้องมี record id และไฟล์ตัวอย่าง |
| outbound_email | อาจส่งอีเมลออก ต้องยืนยันก่อนทดสอบ |
| share_link | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อน |

## Endpoint Index

| # | Module | Method | Path | Name | Purpose | Test class |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Authorization | POST | /token | Authentication | ขอ access token สำหรับเรียก FlowAccount API (การยืนยันตัวตนและขอ access token) | executed_auth |
| 2 | Quotation (QT) | GET | /quotations?currentPage=1&pageSize=20 | Get-All-QT | ดึงรายการข้อมูล (ใบเสนอราคา) | safe_read |
| 3 | Quotation (QT) | GET | /quotations/{{recordId}} | Get-QT-By-ID | ดึงข้อมูลตามรหัส (ใบเสนอราคา) | requires_record_id |
| 4 | Quotation (QT) | POST | /quotations | Create-QT-Simple | สร้างข้อมูลหรือเอกสารใหม่ (ใบเสนอราคา) | mutating_write |
| 5 | Quotation (QT) | POST | /quotations/inline | Create-QT-Inline | สร้างข้อมูลหรือเอกสารใหม่ (ใบเสนอราคา) | mutating_write |
| 6 | Quotation (QT) | PUT | /quotations/{{recordId}} | Update-QT-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสนอราคา) | mutating_update |
| 7 | Quotation (QT) | PUT | /quotations/{{recordId}} | Update-QT-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสนอราคา) | mutating_update |
| 8 | Quotation (QT) | DELETE | /quotations/{{recordId}} | Delete-QT-By-ID | ลบข้อมูลหรือเอกสาร (ใบเสนอราคา) | destructive_delete |
| 9 | Quotation (QT) | POST | /quotations/{{recordId}}/status/awaiting | Change-Status-QT | เปลี่ยนสถานะเอกสาร (ใบเสนอราคา) | mutating_write |
| 10 | Quotation (QT) | POST | /quotations/{{recordId}}/attachment | Upload-File-QT | อัปโหลดไฟล์แนบให้เอกสาร (ใบเสนอราคา) | file_upload |
| 11 | Quotation (QT) | POST | /quotations/sharedocument | Share-Document-QT | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบเสนอราคา) | share_link |
| 12 | Billing Notes (BL) | GET | /billing-notes?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-BL | ดึงรายการข้อมูล (ใบวางบิล) | safe_read |
| 13 | Billing Notes (BL) | GET | /billing-notes/{{recordId}} | Get-BL-By-ID | ดึงข้อมูลตามรหัส (ใบวางบิล) | requires_record_id |
| 14 | Billing Notes (BL) | POST | /billing-notes | Create-BL-Simple | สร้างข้อมูลหรือเอกสารใหม่ (ใบวางบิล) | mutating_write |
| 15 | Billing Notes (BL) | POST | /billing-notes/inline | Create-BL-Inline | สร้างข้อมูลหรือเอกสารใหม่ (ใบวางบิล) | mutating_write |
| 16 | Billing Notes (BL) | POST | /upgrade/billing-notes | Upgrade-BL-Simple | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบวางบิล) | mutating_write |
| 17 | Billing Notes (BL) | POST | /upgrade/billing-notes/inline | Upgrade-BL-Inline | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบวางบิล) | mutating_write |
| 18 | Billing Notes (BL) | PUT | /billing-notes/{{recordId}} | Update-BL-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (ใบวางบิล) | mutating_update |
| 19 | Billing Notes (BL) | PUT | /billing-notes/{{recordId}} | Update-BL-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (ใบวางบิล) | mutating_update |
| 20 | Billing Notes (BL) | DELETE | /billing-notes/{{recordId}} | Delete-BL-By-ID | ลบข้อมูลหรือเอกสาร (ใบวางบิล) | destructive_delete |
| 21 | Billing Notes (BL) | POST | /billing-notes/{{recordId}}/status/awaiting | Change-Status-BL | เปลี่ยนสถานะเอกสาร (ใบวางบิล) | mutating_write |
| 22 | Billing Notes (BL) | POST | /billing-notes/{{recordId}}/attachment | Upload-File-BL | อัปโหลดไฟล์แนบให้เอกสาร (ใบวางบิล) | file_upload |
| 23 | Billing Notes (BL) | POST | /billing-notes/email-document | Send-Email-BL | ส่งเอกสารทางอีเมล (ใบวางบิล) | outbound_email |
| 24 | Billing Notes (BL) | POST | /billing-notes/sharedocument | Share-Document-BL | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบวางบิล) | share_link |
| 25 | Tax Invoices (INV) | GET | /tax-invoices?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-INV | ดึงรายการข้อมูล (ใบกำกับภาษี) | safe_read |
| 26 | Tax Invoices (INV) | GET | /tax-invoices/{{recordId}} | Get-INV-By-ID | ดึงข้อมูลตามรหัส (ใบกำกับภาษี) | requires_record_id |
| 27 | Tax Invoices (INV) | POST | /tax-invoices | Create-INV-Simple | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) | mutating_write |
| 28 | Tax Invoices (INV) | POST | /tax-invoices/with-payment | Create-INV-Simple-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) | mutating_write |
| 29 | Tax Invoices (INV) | POST | /tax-invoices/inline | Create-INV-Inline | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) | mutating_write |
| 30 | Tax Invoices (INV) | POST | /tax-invoices/inline/with-payment | Create-INV-Inline-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) | mutating_write |
| 31 | Tax Invoices (INV) | POST | /upgrade/tax-invoices | Upgrade-INV-Simple | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี) | mutating_write |
| 32 | Tax Invoices (INV) | POST | /upgrade/tax-invoices/with-payment | Upgrade-INV-Simple-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี) | mutating_write |
| 33 | Tax Invoices (INV) | POST | /upgrade/tax-invoices/inline | Upgrade-INV-Inline | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี) | mutating_write |
| 34 | Tax Invoices (INV) | POST | /upgrade/tax-invoices/inline/with-payment | Upgrade-INV-Inline-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี) | mutating_write |
| 35 | Tax Invoices (INV) | PUT | /tax-invoices/{{recordId}} | Update-INV-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี) | mutating_update |
| 36 | Tax Invoices (INV) | PUT | /tax-invoices/{{recordId}} | Update-INV-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี) | mutating_update |
| 37 | Tax Invoices (INV) | DELETE | /tax-invoices/{{recordId}} | Delete-INV-By-ID | ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี) | destructive_delete |
| 38 | Tax Invoices (INV) | POST | /tax-invoices/{{recordId}}/status/awaiting | Change-Status-INV | เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี) | mutating_write |
| 39 | Tax Invoices (INV) | POST | /tax-invoices/{{recordId}}/payment | Change-Status-Paid-INV | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี) | mutating_write |
| 40 | Tax Invoices (INV) | POST | /tax-invoices/{{recordId}}/attachment | Upload-File-INV | อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี) | file_upload |
| 41 | Tax Invoices (INV) | POST | /tax-invoices/email-document | Send-Email-INV | ส่งเอกสารทางอีเมล (ใบกำกับภาษี) | outbound_email |
| 42 | Tax Invoices (INV) | POST | /tax-invoices/sharedocument | Share-Document-INV | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี) | share_link |
| 43 | Tax Invoices (INV) / Receipt (RE) | GET | /tax-invoices?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-INV/RE | ดึงรายการข้อมูล (ใบกำกับภาษี/ใบเสร็จรับเงิน) | safe_read |
| 44 | Tax Invoices (INV) / Receipt (RE) | GET | /tax-invoices/{{recordId}} | Get-INV/RE-By-ID | ดึงข้อมูลตามรหัส (ใบกำกับภาษี/ใบเสร็จรับเงิน) | requires_record_id |
| 45 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices | Create-INV/RE-Simple | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 46 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/with-payment | Create-INV/RE-Simple-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 47 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/inline | Create-INV/RE-Inline | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 48 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/inline/with-payment | Create-INV/RE-Inline-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 49 | Tax Invoices (INV) / Receipt (RE) | POST | /upgrade/tax-invoices | Upgrade-INV/RE-Simple | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 50 | Tax Invoices (INV) / Receipt (RE) | POST | /upgrade/tax-invoices/with-payment | Upgrade-INV/RE-Simple-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 51 | Tax Invoices (INV) / Receipt (RE) | POST | /upgrade/tax-invoices/inline | Upgrade-INV/RE-Inline | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 52 | Tax Invoices (INV) / Receipt (RE) | POST | /upgrade/tax-invoices/inline/with-payment | Upgrade-INV/RE-Inline-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 53 | Tax Invoices (INV) / Receipt (RE) | PUT | /tax-invoices/{{recordId}} | Update-INV/RE-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_update |
| 54 | Tax Invoices (INV) / Receipt (RE) | PUT | /tax-invoices/{{recordId}} | Update-INV/RE-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_update |
| 55 | Tax Invoices (INV) / Receipt (RE) | DELETE | /tax-invoices/{{recordId}} | Delete-INV/RE-By-ID | ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) | destructive_delete |
| 56 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/{{recordId}}/status/awaiting | Change-Status-INV/RE | เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 57 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/{{recordId}}/payment | Change-Status-Paid-INV/RE | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน) | mutating_write |
| 58 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/{{recordId}}/attachment | Upload-File-INV/RE | อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) | file_upload |
| 59 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/email-document | Send-Email-INV/RE | ส่งเอกสารทางอีเมล (ใบกำกับภาษี/ใบเสร็จรับเงิน) | outbound_email |
| 60 | Tax Invoices (INV) / Receipt (RE) | POST | /tax-invoices/sharedocument | Share-Document-INV/RE | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) | share_link |
| 61 | Receipt (RE) | GET | /receipts?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-RE | ดึงรายการข้อมูล (ใบเสร็จรับเงิน) | safe_read |
| 62 | Receipt (RE) | GET | /receipts/{{recordId}} | Get-RE-By-ID | ดึงข้อมูลตามรหัส (ใบเสร็จรับเงิน) | requires_record_id |
| 63 | Receipt (RE) | POST | /upgrade/receipts | Upgrade-RE-Simple | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบเสร็จรับเงิน) | mutating_write |
| 64 | Receipt (RE) | POST | /upgrade/receipts/with-payment | Upgrade-RE-Simple-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน) | mutating_write |
| 65 | Receipt (RE) | POST | /receipts/inline | Upgrade-RE-Inline | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบเสร็จรับเงิน) | mutating_write |
| 66 | Receipt (RE) | POST | /upgrade/receipts/inline/with-payment | Upgrade-RE-Inline-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน) | mutating_write |
| 67 | Receipt (RE) | PUT | /receipts/{{recordId}} | Update-RE-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสร็จรับเงิน) | mutating_update |
| 68 | Receipt (RE) | PUT | /receipts/{{recordId}} | Update-RE-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสร็จรับเงิน) | mutating_update |
| 69 | Receipt (RE) | DELETE | /receipts/{{recordId}} | Delete-RE-By-ID | ลบข้อมูลหรือเอกสาร (ใบเสร็จรับเงิน) | destructive_delete |
| 70 | Receipt (RE) | POST | /tax-invoices/{{recordId}}/status/awaiting | Change-Status-RE | เปลี่ยนสถานะเอกสาร (ใบเสร็จรับเงิน) | mutating_write |
| 71 | Receipt (RE) | POST | /receipts/{{recordId}}/payment | Change-Status-Paid-RE | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน) | mutating_write |
| 72 | Receipt (RE) | POST | /receipts/{{recordId}}/attachment | Upload-File-RE | อัปโหลดไฟล์แนบให้เอกสาร (ใบเสร็จรับเงิน) | file_upload |
| 73 | Receipt (RE) | POST | /receipts/email-document | Send-Email-RE | ส่งเอกสารทางอีเมล (ใบเสร็จรับเงิน) | outbound_email |
| 74 | Receipt (RE) | POST | /receipts/sharedocument | Share-Document-RE | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบเสร็จรับเงิน) | share_link |
| 75 | Cash Invoice (CA) | GET | /cash-invoices?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-CA | ดึงรายการข้อมูล (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | safe_read |
| 76 | Cash Invoice (CA) | GET | /cash-invoices/{{recordId}} | Get-CA-By-ID | ดึงข้อมูลตามรหัส (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | requires_record_id |
| 77 | Cash Invoice (CA) | POST | /cash-invoices | Create-CA-Simple | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 78 | Cash Invoice (CA) | POST | /cash-invoices/with-payment | Create-CA-Simple-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 79 | Cash Invoice (CA) | POST | /cash-invoices/inline | Create-CA-Inline | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 80 | Cash Invoice (CA) | POST | /cash-invoices/inline/with-payment | Create-CA-Inline-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 81 | Cash Invoice (CA) | POST | /upgrade/cash-invoices | Upgrade-CA-Simple | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 82 | Cash Invoice (CA) | POST | /upgrade/cash-invoices/with-payment | Upgrade-CA-Simple-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 83 | Cash Invoice (CA) | POST | /upgrade/cash-invoices/inline | Upgrade-CA-Inline | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 84 | Cash Invoice (CA) | POST | /upgrade/cash-invoices/inline/with-payment | Upgrade-CA-Inline-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 85 | Cash Invoice (CA) | PUT | /cash-invoices/{{recordId}} | Update-CA-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_update |
| 86 | Cash Invoice (CA) | PUT | /cash-invoices/{{recordId}} | Update-CA-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_update |
| 87 | Cash Invoice (CA) | DELETE | /cash-invoices/{{recordId}} | Delete-CA-By-ID | ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | destructive_delete |
| 88 | Cash Invoice (CA) | POST | /cash-invoices/{{recordId}}/status/awaiting | Change-Status-CA | เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 89 | Cash Invoice (CA) | POST | /cash-invoices/{{recordId}}/payment | Change-Status-Paid-CA | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | mutating_write |
| 90 | Cash Invoice (CA) | POST | /cash-invoices/{{recordId}}/attachment | Upload-File-CA | อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | file_upload |
| 91 | Cash Invoice (CA) | POST | /cash-invoices/email-document | Send-Email-CA | ส่งเอกสารทางอีเมล (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | outbound_email |
| 92 | Cash Invoice (CA) | POST | /cash-invoices/sharedocument | Share-Document-CA | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) | share_link |
| 93 | Purchase Order (PO) | GET | /purchases-orders?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-PO | ดึงรายการข้อมูล (ใบสั่งซื้อ) | safe_read |
| 94 | Purchase Order (PO) | GET | /purchases-orders/{{recordId}} | Get-PO-By-ID | ดึงข้อมูลตามรหัส (ใบสั่งซื้อ) | requires_record_id |
| 95 | Purchase Order (PO) | POST | /purchases-orders | Create-PO-Simple | สร้างข้อมูลหรือเอกสารใหม่ (ใบสั่งซื้อ) | mutating_write |
| 96 | Purchase Order (PO) | POST | /purchases-orders/inline | Create-PO-Inline | สร้างข้อมูลหรือเอกสารใหม่ (ใบสั่งซื้อ) | mutating_write |
| 97 | Purchase Order (PO) | POST | /upgrade/purchases-orders | Upgrade-PO-Simple | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบสั่งซื้อ) | mutating_write |
| 98 | Purchase Order (PO) | POST | /upgrade/purchases-orders/inline | Upgrade-PO-Inline | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบสั่งซื้อ) | mutating_write |
| 99 | Purchase Order (PO) | PUT | /purchases-orders/{{recordId}} | Update-PO-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (ใบสั่งซื้อ) | mutating_update |
| 100 | Purchase Order (PO) | PUT | /purchases-orders/{{recordId}} | Update-PO-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (ใบสั่งซื้อ) | mutating_update |
| 101 | Purchase Order (PO) | DELETE | /purchases-orders/{{recordId}} | Delete-PO-By-ID | ลบข้อมูลหรือเอกสาร (ใบสั่งซื้อ) | destructive_delete |
| 102 | Purchase Order (PO) | POST | /purchases-orders/{{recordId}}/status/awaiting | Change-Status-PO | เปลี่ยนสถานะเอกสาร (ใบสั่งซื้อ) | mutating_write |
| 103 | Purchase Order (PO) | POST | /purchases-orders/{{recordId}}/attachment | Upload-File-PO | อัปโหลดไฟล์แนบให้เอกสาร (ใบสั่งซื้อ) | file_upload |
| 104 | Purchase Order (PO) | POST | /purchases-orders/email-document | Send-Email-PO | ส่งเอกสารทางอีเมล (ใบสั่งซื้อ) | outbound_email |
| 105 | Purchase Order (PO) | POST | /purchases-orders/sharedocument | Share-Document-PO | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบสั่งซื้อ) | share_link |
| 106 | Receiving Inventory (RI) | GET | /purchases?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-RI | ดึงรายการข้อมูล (เอกสารซื้อ/รับสินค้า) | safe_read |
| 107 | Receiving Inventory (RI) | GET | /purchases/{{recordId}} | Get-RI-By-ID | ดึงข้อมูลตามรหัส (เอกสารซื้อ/รับสินค้า) | requires_record_id |
| 108 | Receiving Inventory (RI) | POST | /purchases | Create-RI-Simple | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 109 | Receiving Inventory (RI) | POST | /purchases/with-payment | Create-RI-Simple-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 110 | Receiving Inventory (RI) | POST | /purchases/inline | Create-RI-Inline | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 111 | Receiving Inventory (RI) | POST | /purchases/inline/with-payment | Create-RI-Inline-With-Payment | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 112 | Receiving Inventory (RI) | POST | /upgrade/purchases | Upgrade-RI-Simple | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 113 | Receiving Inventory (RI) | POST | /upgrade/purchases/with-payment | Upgrade-RI-Simple-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 114 | Receiving Inventory (RI) | POST | /upgrade/purchases/inline | Upgrade-RI-Inline | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 115 | Receiving Inventory (RI) | POST | /upgrade/purchases/inline/with-payment | Upgrade-RI-Inline-With-Payment | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 116 | Receiving Inventory (RI) | PUT | /purchases/{{recordId}} | Update-RI-Simple | แก้ไขข้อมูลหรือเอกสารเดิม (เอกสารซื้อ/รับสินค้า) | mutating_update |
| 117 | Receiving Inventory (RI) | PUT | /purchases/{{recordId}} | Update-RI-Inline | แก้ไขข้อมูลหรือเอกสารเดิม (เอกสารซื้อ/รับสินค้า) | mutating_update |
| 118 | Receiving Inventory (RI) | DELETE | /purchases/{{recordId}} | Delete-RI-By-ID | ลบข้อมูลหรือเอกสาร (เอกสารซื้อ/รับสินค้า) | destructive_delete |
| 119 | Receiving Inventory (RI) | POST | /purchases/{{recordId}}/status/awaiting | Change-Status-RI | เปลี่ยนสถานะเอกสาร (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 120 | Receiving Inventory (RI) | POST | /purchases/{{recordId}}/payment | Change-Status-Paid-RI | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า) | mutating_write |
| 121 | Receiving Inventory (RI) | POST | /purchases/{{recordId}}/attachment | Upload-File-RI | อัปโหลดไฟล์แนบให้เอกสาร (เอกสารซื้อ/รับสินค้า) | file_upload |
| 122 | Receiving Inventory (RI) | POST | /purchases/email-document | Send-Email-RI | ส่งเอกสารทางอีเมล (เอกสารซื้อ/รับสินค้า) | outbound_email |
| 123 | Receiving Inventory (RI) | POST | /purchases/sharedocument | Share-Document-RI | สร้างหรือส่งลิงก์แชร์เอกสาร (เอกสารซื้อ/รับสินค้า) | share_link |
| 124 | Expense (EXP) | GET | /expenses?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get-All-Expense | ดึงรายการข้อมูล (ค่าใช้จ่าย) | safe_read |
| 125 | Expense (EXP) | GET | /expenses/{{recordId}} | Get - Expense By Id | ดึงข้อมูลตามรหัส (ค่าใช้จ่าย) | requires_record_id |
| 126 | Expense (EXP) | GET | /expenses/categories/business | Get - Business Categories Expense | ดึงรายการข้อมูล (ค่าใช้จ่าย) | safe_read |
| 127 | Expense (EXP) | GET | /expenses/categories/accounting | Get - Accounting Categories Expense | ดึงรายการข้อมูล (ค่าใช้จ่าย) | safe_read |
| 128 | Expense (EXP) | POST | /expenses | Create - Exp Simple (Exclusive vat) | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 129 | Expense (EXP) | POST | /expenses | Create - Exp Simple (Inclusive vat) | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 130 | Expense (EXP) | POST | /expenses/inline | Create - Exp Inline discount percent | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 131 | Expense (EXP) | POST | /expenses/inline | Create - Exp Inline discount amount | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 132 | Expense (EXP) | POST | /expenses/inline | Create - Exp Inline vat (vat 7%) | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 133 | Expense (EXP) | POST | /expenses/inline | Create - Exp Inline vat (no vat) | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 134 | Expense (EXP) | POST | /expenses/with-payment | Create - Exp Simple with payment (Exclusive vat) | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 135 | Expense (EXP) | POST | /expenses | Create - Exp Simple with payment (Exclusive vat) Test | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 136 | Expense (EXP) | POST | /expenses/inline/with-payment | Create - Exp Inline vat with payment (vat 7%) | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) | mutating_write |
| 137 | Expense (EXP) | PUT | /expenses/{{recordId}} | Update - Exp Simple (Exclusive vat) by Id | ดึงข้อมูลตามรหัส (ค่าใช้จ่าย) | mutating_update |
| 138 | Expense (EXP) | DELETE | /expenses/{{recordId}} | Delete - Expense By Id | ลบข้อมูลหรือเอกสาร (ค่าใช้จ่าย) | destructive_delete |
| 139 | Expense (EXP) | POST | /expenses/{{recordId}}/status/void | Change Status - Expense | เปลี่ยนสถานะเอกสาร (ค่าใช้จ่าย) | mutating_write |
| 140 | Expense (EXP) | POST | /expenses/{{recordId}}/payment | Change Status Payment - Expense | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ค่าใช้จ่าย) | mutating_write |
| 141 | Expense (EXP) | POST | /expenses/{{recordId}}/attachment | Attachment File - Expense | อัปโหลดไฟล์แนบให้เอกสาร (ค่าใช้จ่าย) | file_upload |
| 142 | Expense (EXP) | POST | /expenses/sharedocument | Sharedocument - Expense | สร้างหรือส่งลิงก์แชร์เอกสาร (ค่าใช้จ่าย) | share_link |
| 143 | Expense (EXP) | POST | /expenses/email-document | Send Email - Expense | ส่งเอกสารทางอีเมล (ค่าใช้จ่าย) | outbound_email |
| 144 | Withholding Tax (WHT) | GET | /withholding-taxes?currentPage=1&pageSize=20&range=3&month=7&year=2021 | Get - WHT | ดึงรายการข้อมูล (หนังสือรับรองหัก ณ ที่จ่าย) | safe_read |
| 145 | Withholding Tax (WHT) | GET | /withholding-taxes/{{recordId}} | Get - WHT By Id | ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย) | requires_record_id |
| 146 | Withholding Tax (WHT) | POST | /withholding-taxes/{{recordId}}/status/void | Change Status - WHT By Id | ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย) | mutating_write |
| 147 | Withholding Tax (WHT) | POST | /withholding-taxes/sharedocument | Sharedocument - WHT | สร้างหรือส่งลิงก์แชร์เอกสาร (หนังสือรับรองหัก ณ ที่จ่าย) | share_link |
| 148 | Withholding Tax (WHT) | POST | /withholding-taxes/email-document | Send Email - WHT | ส่งเอกสารทางอีเมล (หนังสือรับรองหัก ณ ที่จ่าย) | outbound_email |
| 149 | Withholding Tax (WHT) | POST | /withholding-taxes/{{recordId}}/attachment | Attachment File - WHT | อัปโหลดไฟล์แนบให้เอกสาร (หนังสือรับรองหัก ณ ที่จ่าย) | file_upload |
| 150 | Withholding Tax (WHT) | POST | /withholding-taxes | Create - WHT | สร้างข้อมูลหรือเอกสารใหม่ (หนังสือรับรองหัก ณ ที่จ่าย) | mutating_write |
| 151 | Withholding Tax (WHT) | DELETE | /withholding-taxes/{{recordId}} | Delete - WHT By Id | ลบข้อมูลหรือเอกสาร (หนังสือรับรองหัก ณ ที่จ่าย) | destructive_delete |
| 152 | Withholding Tax (WHT) | PUT | /withholding-taxes/{{recordId}} | Update - WHT By Id | ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย) | mutating_update |
| 153 | Journal Entry | POST | /journal-entries/draft | Draft Journal Voucher (JV) | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) | mutating_write |
| 154 | Journal Entry | POST | /journal-entries/draft | Draft Purchase Voucher (UV) | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) | mutating_write |
| 155 | Journal Entry | POST | /journal-entries/draft | Draft Sales Voucher (SV) | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) | mutating_write |
| 156 | Journal Entry | POST | /journal-entries/draft | Draft Payment Voucher (PV) | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) | mutating_write |
| 157 | Journal Entry | POST | /journal-entries/draft | Draft Received Voucher (RV) | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) | mutating_write |
| 158 | Journal Entry | POST | /journal-entries/approve | Approved Journal Voucher (JV) | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) | mutating_write |
| 159 | Journal Entry | POST | /journal-entries/approve | Approved Purchase Voucher (UV) | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) | mutating_write |
| 160 | Journal Entry | POST | /journal-entries/approve | Approved Sales Voucher (SV) | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) | mutating_write |
| 161 | Journal Entry | POST | /journal-entries/approve | Approved Payment Voucher (PV) | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) | mutating_write |
| 162 | Journal Entry | POST | /journal-entries/approve | Approved Received Voucher (RV) | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) | mutating_write |
| 163 | Chart of Account (COA) | GET | /chart-of-accounts/accounts | Get List Chart Of Account | ดึงรายการข้อมูล (ผังบัญชี) | safe_read |
| 164 | Product Master | GET | /product-masters?filter=%5B%7B'columnName'%3A'productCode'%2C'columnValue'%3A'N0001'%7D%5D | Get All Product Masters | ดึงรายการข้อมูล (ข้อมูล master ของสินค้า) | safe_read |
| 165 | Product Master | POST | /product-masters | Create Product Master | สร้างข้อมูลหรือเอกสารใหม่ (ข้อมูล master ของสินค้า) | mutating_write |
| 166 | Product Master | GET | /product-masters/{{Product_Id}} | Get Product Master By Id | ดึงข้อมูลตามรหัส (ข้อมูล master ของสินค้า) | requires_record_id |
| 167 | Product Master | PUT | /product-masters/{{Product_Id}} | Update Product Master | แก้ไขข้อมูลหรือเอกสารเดิม (ข้อมูล master ของสินค้า) | mutating_update |
| 168 | Product Master | DELETE | /product-masters/{{Product_Id}} | Delete Product Master | ลบข้อมูลหรือเอกสาร (ข้อมูล master ของสินค้า) | destructive_delete |
| 169 | Products | GET | /products?filter=%5B%7B'columnName'%3A'name'%2C'columnValue'%3A'Service'%2C'columnPredicateOperator'%3A'And'%7D%5D | Get - All Products | ดึงรายการข้อมูล (สินค้าและบริการ) | safe_read |
| 170 | Products | GET | /products/12851240 | Get - Product By Id | ดึงข้อมูลตามรหัส (สินค้าและบริการ) | safe_read |
| 171 | Products | POST | /products | Create - Product Service | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) | mutating_write |
| 172 | Products | POST | /products | Create - Product Non Inventory | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) | mutating_write |
| 173 | Products | POST | /products | Create - Product Inventory | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) | mutating_write |
| 174 | Products | POST | /products | Create - Product Inventory has stock | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) | mutating_write |
| 175 | Products | PUT | /products/12851240 | Update - Product By Id | ดึงข้อมูลตามรหัส (สินค้าและบริการ) | mutating_update |
| 176 | Products | DELETE | /products/12851240 | Delete - Product By Id | ลบข้อมูลหรือเอกสาร (สินค้าและบริการ) | destructive_delete |
| 177 | Contacts | GET | /contacts | Get - All Contacts | ดึงรายการข้อมูล (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) | safe_read |
| 178 | Contacts | GET | /contacts/130093 | Get - Contact By Id | ดึงข้อมูลตามรหัส (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) | safe_read |
| 179 | Contacts | POST | /contacts | Create - Contact | สร้างข้อมูลหรือเอกสารใหม่ (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) | mutating_write |
| 180 | Contacts | PUT | /contacts/130093 | Update - Contact By Id | ดึงข้อมูลตามรหัส (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) | mutating_update |
| 181 | Contacts | DELETE | /contacts/130093 | Delete - Contact By Id | ลบข้อมูลหรือเอกสาร (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) | destructive_delete |
| 182 | MyCompany | GET | /company/info | GET - Company Infomation | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | safe_read |
| 183 | MyCompany | PUT | /company/info | Update - Company Infomation | แก้ไขข้อมูลหรือเอกสารเดิม (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | mutating_update |
| 184 | MyCompany | GET | /bank-accounts | GET - All Bank Account | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | safe_read |
| 185 | MyCompany | GET | /bank-channel/cheque | GET - All Cheque | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | safe_read |
| 186 | MyCompany | GET | /bank-channel/credit-card | GET - All Credit Card | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | safe_read |
| 187 | MyCompany | GET | /bank-channel/petty-cash | GET - All Petty Cash | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | safe_read |
| 188 | MyCompany | GET | /bank-channel/other-channels | GET - All Other Channel | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | safe_read |
| 189 | MyCompany | POST | /bank-channel/bank-accounts | Create - Bank Account | สร้างข้อมูลหรือเอกสารใหม่ (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | mutating_write |
| 190 | MyCompany | GET | /settings/documents-remark | Get - Documents Remark | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) | safe_read |

## Endpoint Details

### 1. POST /token

| Field | Value |
| --- | --- |
| Module | Authorization |
| Folder path | Authorization |
| Postman name | Authentication |
| Purpose | ขอ access token สำหรับเรียก FlowAccount API (การยืนยันตัวตนและขอ access token) |
| Meaning | ขอ access token สำหรับเรียก FlowAccount APIในหมวด การยืนยันตัวตนและขอ access token |
| Auth | Client credentials form body |
| Test class | executed_auth |
| Test note | ทดสอบได้ทันทีด้วย client credentials |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/x-www-form-urlencoded |  |
| Cookie | [redacted] |  |

Body mode: `urlencoded`; parse status: `parsed_urlencoded`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| client_id | text | {{client_id}} | field จาก request body ใน Postman collection |
| client_secret | text | [redacted] | field จาก request body ใน Postman collection |
| grant_type | text | {{grant_type}} | field จาก request body ใน Postman collection |
| scope | text | {{scope}} | field จาก request body ใน Postman collection |

### 2. GET /quotations?currentPage=1&pageSize=20

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Get-All-QT |
| Purpose | ดึงรายการข้อมูล (ใบเสนอราคา) |
| Meaning | ดึงรายการข้อมูลในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | True | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | True | เดือนที่ใช้ค้นหา |
| year | 2021 | True | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 3. GET /quotations/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Get-QT-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (ใบเสนอราคา) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 4. POST /quotations

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Create-QT-Simple |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบเสนอราคา) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 5. POST /quotations/inline

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Create-QT-Inline |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบเสนอราคา) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 6. PUT /quotations/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Update-QT-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสนอราคา) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 7. PUT /quotations/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Update-QT-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสนอราคา) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 8. DELETE /quotations/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Delete-QT-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (ใบเสนอราคา) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 9. POST /quotations/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Change-Status-QT |
| Purpose | เปลี่ยนสถานะเอกสาร (ใบเสนอราคา) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 10. POST /quotations/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Upload-File-QT |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ใบเสนอราคา) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 11. POST /quotations/sharedocument

| Field | Value |
| --- | --- |
| Module | Quotation (QT) |
| Folder path | Quotation (QT) |
| Postman name | Share-Document-QT |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบเสนอราคา) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด ใบเสนอราคา |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 30881712 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 12. GET /billing-notes?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Get-All-BL |
| Purpose | ดึงรายการข้อมูล (ใบวางบิล) |
| Meaning | ดึงรายการข้อมูลในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 13. GET /billing-notes/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Get-BL-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (ใบวางบิล) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 14. POST /billing-notes

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Create-BL-Simple |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบวางบิล) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 15. POST /billing-notes/inline

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Create-BL-Inline |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบวางบิล) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 16. POST /upgrade/billing-notes

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Upgrade-BL-Simple |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบวางบิล) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 17. POST /upgrade/billing-notes/inline

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Upgrade-BL-Inline |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบวางบิล) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 18. PUT /billing-notes/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Update-BL-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบวางบิล) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 19. PUT /billing-notes/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Update-BL-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบวางบิล) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 20. DELETE /billing-notes/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Delete-BL-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (ใบวางบิล) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 21. POST /billing-notes/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Change-Status-BL |
| Purpose | เปลี่ยนสถานะเอกสาร (ใบวางบิล) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 22. POST /billing-notes/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Upload-File-BL |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ใบวางบิล) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 23. POST /billing-notes/email-document

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Send-Email-BL |
| Purpose | ส่งเอกสารทางอีเมล (ใบวางบิล) |
| Meaning | ส่งเอกสารทางอีเมลในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 5512755 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | Send Email | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | Send Email from production | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| doCopy | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | SendEmailCoppies | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |

### 24. POST /billing-notes/sharedocument

| Field | Value |
| --- | --- |
| Module | Billing Notes (BL) |
| Folder path | Billing Notes (BL) |
| Postman name | Share-Document-BL |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบวางบิล) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด ใบวางบิล |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 81281 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 25. GET /tax-invoices?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Get-All-INV |
| Purpose | ดึงรายการข้อมูล (ใบกำกับภาษี) |
| Meaning | ดึงรายการข้อมูลในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 26. GET /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Get-INV-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (ใบกำกับภาษี) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 27. POST /tax-invoices

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Create-INV-Simple |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 28. POST /tax-invoices/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Create-INV-Simple-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 29. POST /tax-invoices/inline

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Create-INV-Inline |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 30. POST /tax-invoices/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Create-INV-Inline-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | number | 288.9 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 31. POST /upgrade/tax-invoices

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Upgrade-INV-Simple |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 32. POST /upgrade/tax-invoices/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Upgrade-INV-Simple-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 33. POST /upgrade/tax-invoices/inline

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Upgrade-INV-Inline |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 34. POST /upgrade/tax-invoices/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Upgrade-INV-Inline-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 35. PUT /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Update-INV-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 36. PUT /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Update-INV-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 37. DELETE /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Delete-INV-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 38. POST /tax-invoices/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Change-Status-INV |
| Purpose | เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 39. POST /tax-invoices/{{recordId}}/payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Change-Status-Paid-INV |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| paymentStructureType | string | PaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentId | integer | 5512185 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | number | 3.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 40. POST /tax-invoices/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Upload-File-INV |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 41. POST /tax-invoices/email-document

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Send-Email-INV |
| Purpose | ส่งเอกสารทางอีเมล (ใบกำกับภาษี) |
| Meaning | ส่งเอกสารทางอีเมลในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | Send Email | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | Send Email from production | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| doCopy | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | SendEmailCoppies | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |

### 42. POST /tax-invoices/sharedocument

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) |
| Folder path | Tax Invoices (INV) |
| Postman name | Share-Document-INV |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด ใบกำกับภาษี |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 43. GET /tax-invoices?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Get-All-INV/RE |
| Purpose | ดึงรายการข้อมูล (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | ดึงรายการข้อมูลในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 44. GET /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Get-INV/RE-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 45. POST /tax-invoices

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Create-INV/RE-Simple |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 46. POST /tax-invoices/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Create-INV/RE-Simple-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 47. POST /tax-invoices/inline

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Create-INV/RE-Inline |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 48. POST /tax-invoices/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Create-INV/RE-Inline-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | number | 288.9 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 49. POST /upgrade/tax-invoices

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Upgrade-INV/RE-Simple |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 50. POST /upgrade/tax-invoices/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Upgrade-INV/RE-Simple-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 51. POST /upgrade/tax-invoices/inline

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Upgrade-INV/RE-Inline |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 52. POST /upgrade/tax-invoices/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Upgrade-INV/RE-Inline-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | number | 288.9 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 53. PUT /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Update-INV/RE-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 54. PUT /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Update-INV/RE-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 55. DELETE /tax-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Delete-INV/RE-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 56. POST /tax-invoices/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Change-Status-INV/RE |
| Purpose | เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 57. POST /tax-invoices/{{recordId}}/payment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Change-Status-Paid-INV/RE |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| paymentStructureType | string | PaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentId | integer | 5512185 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | number | 3.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 58. POST /tax-invoices/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Upload-File-INV/RE |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 59. POST /tax-invoices/email-document

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Send-Email-INV/RE |
| Purpose | ส่งเอกสารทางอีเมล (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | ส่งเอกสารทางอีเมลในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | Send Email | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | Send Email from production | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| doCopy | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | SendEmailCoppies | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |

### 60. POST /tax-invoices/sharedocument

| Field | Value |
| --- | --- |
| Module | Tax Invoices (INV) / Receipt (RE) |
| Folder path | Tax Invoices (INV) / Receipt (RE) |
| Postman name | Share-Document-INV/RE |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 61. GET /receipts?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Get-All-RE |
| Purpose | ดึงรายการข้อมูล (ใบเสร็จรับเงิน) |
| Meaning | ดึงรายการข้อมูลในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 62. GET /receipts/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Get-RE-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (ใบเสร็จรับเงิน) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 63. POST /upgrade/receipts

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Upgrade-RE-Simple |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบเสร็จรับเงิน) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | INV2021070001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 64. POST /upgrade/receipts/with-payment

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Upgrade-RE-Simple-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | INV2021070001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 65. POST /receipts/inline

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Upgrade-RE-Inline |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบเสร็จรับเงิน) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | INV2021070001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 66. POST /upgrade/receipts/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Upgrade-RE-Inline-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | INV2021070001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 67. PUT /receipts/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Update-RE-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสร็จรับเงิน) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | INV2021070001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 68. PUT /receipts/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Update-RE-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสร็จรับเงิน) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | INV2021070001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 69. DELETE /receipts/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Delete-RE-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (ใบเสร็จรับเงิน) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 70. POST /tax-invoices/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Change-Status-RE |
| Purpose | เปลี่ยนสถานะเอกสาร (ใบเสร็จรับเงิน) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 71. POST /receipts/{{recordId}}/payment

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Change-Status-Paid-RE |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| paymentStructureType | string | PaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | number | 3.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 72. POST /receipts/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Upload-File-RE |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ใบเสร็จรับเงิน) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 73. POST /receipts/email-document

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Send-Email-RE |
| Purpose | ส่งเอกสารทางอีเมล (ใบเสร็จรับเงิน) |
| Meaning | ส่งเอกสารทางอีเมลในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | Send Email | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | Send Email from production | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| doCopy | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | SendEmailCoppies | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |

### 74. POST /receipts/sharedocument

| Field | Value |
| --- | --- |
| Module | Receipt (RE) |
| Folder path | Receipt (RE) |
| Postman name | Share-Document-RE |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบเสร็จรับเงิน) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด ใบเสร็จรับเงิน |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 75. GET /cash-invoices?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Get-All-CA |
| Purpose | ดึงรายการข้อมูล (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | ดึงรายการข้อมูลในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 76. GET /cash-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Get-CA-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 77. POST /cash-invoices

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Create-CA-Simple |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 78. POST /cash-invoices/with-payment

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Create-CA-Simple-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 79. POST /cash-invoices/inline

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Create-CA-Inline |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 80. POST /cash-invoices/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Create-CA-Inline-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | number | 288.9 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 81. POST /upgrade/cash-invoices

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Upgrade-CA-Simple |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 82. POST /upgrade/cash-invoices/with-payment

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Upgrade-CA-Simple-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 83. POST /upgrade/cash-invoices/inline

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Upgrade-CA-Inline |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 84. POST /upgrade/cash-invoices/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Upgrade-CA-Inline-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | number | 288.9 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 85. PUT /cash-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Update-CA-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 86. PUT /cash-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Update-CA-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 87. DELETE /cash-invoices/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Delete-CA-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 88. POST /cash-invoices/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Change-Status-CA |
| Purpose | เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 89. POST /cash-invoices/{{recordId}}/payment

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Change-Status-Paid-CA |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| paymentStructureType | string | PaymentReceivingCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | number | 3.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Receiving Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 90. POST /cash-invoices/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Upload-File-CA |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 91. POST /cash-invoices/email-document

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Send-Email-CA |
| Purpose | ส่งเอกสารทางอีเมล (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | ส่งเอกสารทางอีเมลในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | Send Email | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | Send Email from production | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| doCopy | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | SendEmailCoppies | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |

### 92. POST /cash-invoices/sharedocument

| Field | Value |
| --- | --- |
| Module | Cash Invoice (CA) |
| Folder path | Cash Invoice (CA) |
| Postman name | Share-Document-CA |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด ใบกำกับภาษี/ใบเสร็จรับเงินสด |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 93. GET /purchases-orders?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Get-All-PO |
| Purpose | ดึงรายการข้อมูล (ใบสั่งซื้อ) |
| Meaning | ดึงรายการข้อมูลในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 94. GET /purchases-orders/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Get-PO-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (ใบสั่งซื้อ) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 95. POST /purchases-orders

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Create-PO-Simple |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบสั่งซื้อ) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 96. POST /purchases-orders/inline

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Create-PO-Inline |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ใบสั่งซื้อ) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 97. POST /upgrade/purchases-orders

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Upgrade-PO-Simple |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบสั่งซื้อ) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 98. POST /upgrade/purchases-orders/inline

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Upgrade-PO-Inline |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบสั่งซื้อ) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 17.66 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 270 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 252.34 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 99. PUT /purchases-orders/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Update-PO-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบสั่งซื้อ) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 100. PUT /purchases-orders/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Update-PO-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ใบสั่งซื้อ) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | QT2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 101. DELETE /purchases-orders/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Delete-PO-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (ใบสั่งซื้อ) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 102. POST /purchases-orders/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Change-Status-PO |
| Purpose | เปลี่ยนสถานะเอกสาร (ใบสั่งซื้อ) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 103. POST /purchases-orders/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Upload-File-PO |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ใบสั่งซื้อ) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 104. POST /purchases-orders/email-document

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Send-Email-PO |
| Purpose | ส่งเอกสารทางอีเมล (ใบสั่งซื้อ) |
| Meaning | ส่งเอกสารทางอีเมลในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 5512755 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | Send Email | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | Send Email from production | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| doCopy | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | SendEmailCoppies | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |

### 105. POST /purchases-orders/sharedocument

| Field | Value |
| --- | --- |
| Module | Purchase Order (PO) |
| Folder path | Purchase Order (PO) |
| Postman name | Share-Document-PO |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ใบสั่งซื้อ) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด ใบสั่งซื้อ |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 81281 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 106. GET /purchases?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Get-All-RI |
| Purpose | ดึงรายการข้อมูล (เอกสารซื้อ/รับสินค้า) |
| Meaning | ดึงรายการข้อมูลในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 107. GET /purchases/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Get-RI-By-ID |
| Purpose | ดึงข้อมูลตามรหัส (เอกสารซื้อ/รับสินค้า) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 108. POST /purchases

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Create-RI-Simple |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 109. POST /purchases/with-payment

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Create-RI-Simple-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentPaidCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Paid Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 110. POST /purchases/inline

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Create-RI-Inline |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 111. POST /purchases/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Create-RI-Inline-With-Payment |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentPaidCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | number | 288.9 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Paid Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 112. POST /upgrade/purchases

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Upgrade-RI-Simple |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (เอกสารซื้อ/รับสินค้า) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | SimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | PO2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 113. POST /upgrade/purchases/with-payment

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Upgrade-RI-Simple-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-31 | วันที่ออกเอกสาร |
| creditType | integer | 3 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 1 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remarks | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | PO2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| documentPaymentStructureType | string | SimpleDocumentWithPaymentPaidCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 321 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Paid Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 114. POST /upgrade/purchases/inline

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Upgrade-RI-Inline |
| Purpose | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (เอกสารซื้อ/รับสินค้า) |
| Meaning | แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้าในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | PO2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | InlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| saleAndPurchaseChannel | integer | 0 | ช่องทางขายหรือซื้อ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 115. POST /upgrade/purchases/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Upgrade-RI-Inline-With-Payment |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Example Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Example Notes | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | PO2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| documentPaymentStructureType | string | InlineDocumentWithPaymentPaidCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | number | 288.9 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Paid Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 116. PUT /purchases/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Update-RI-Simple |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (เอกสารซื้อ/รับสินค้า) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 087-654-3210 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 300 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 21 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 321 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | integer | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark | หมายเหตุบนเอกสาร |
| internalNotes | string | Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateSimpleDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | PO2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 100 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |

### 117. PUT /purchases/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Update-RI-Inline |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (เอกสารซื้อ/รับสินค้า) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| recordId | integer | 0 | รหัสเอกสารใน FlowAccount |
| companyName | string | Good Afternoon Data | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Good Afternoon Data | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | excode | รหัสผู้ติดต่อ |
| contactName | string | ExampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactPerson | string | ExamplePerson | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2025-07-01 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 30 | จำนวนวันเครดิต |
| dueDate | string | 2025-07-31 | วันครบกำหนดชำระ |
| salesName | string | Example salesName | ชื่อพนักงานขาย |
| projectName | string | Example Project | ชื่อโครงการ |
| reference | string | Example Ref | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| useReceiptDeduction | boolean | False | เปิดใช้ยอดหักในใบเสร็จ |
| subTotal | integer | 300 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 30 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 270 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 18.9 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | number | 288.9 | ยอดรวมสุทธิ |
| documentShowWithholdingTax | boolean | [redacted] | กำหนดให้แสดงหัก ณ ที่จ่ายในเอกสาร |
| documentWithholdingTaxPercentage | integer | [redacted] | เปอร์เซ็นต์หัก ณ ที่จ่ายของเอกสาร |
| documentWithholdingTaxAmount | number | [redacted] | ยอดหัก ณ ที่จ่ายของเอกสาร |
| documentDeductionType | integer | 0 | ประเภทยอดหัก/หัก ณ ที่จ่ายในเอกสาร |
| documentDeductionAmount | integer | 0 | ยอดหักเพิ่มหรือยอดลดหนี้ในเอกสาร |
| remarks | string | Remark Document | หมายเหตุบนเอกสาร |
| internalNotes | string | Internal Note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |
| documentStructureType | string | UpdateInlineDocument | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |
| documentReference | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].recordId | integer | 7916283 | รหัสเอกสารใน FlowAccount |
| documentReference[].referenceDocumentSerial | string | PO2021070007 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentReference[].referenceDocumentType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 270 | ยอดที่นำไปคำนวณ VAT |
| items | array | 3 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| items[].type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| items[].name | string | Service | ชื่อรายการ |
| items[].description | string | Type service | รายละเอียดรายการ |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | บริการ | หน่วยนับ |
| items[].pricePerUnit | integer | 100 | ราคาต่อหน่วย |
| items[].total | integer | 90 | ยอดรวมของรายการ |
| items[].sellChartOfAccountCode | string | 41210 | รหัสผังบัญชีฝั่งขาย |
| items[].buyChartOfAccountCode | string |  | รหัสผังบัญชีฝั่งซื้อ |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |

### 118. DELETE /purchases/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Delete-RI-By-ID |
| Purpose | ลบข้อมูลหรือเอกสาร (เอกสารซื้อ/รับสินค้า) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 119. POST /purchases/{{recordId}}/status/awaiting

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Change-Status-RI |
| Purpose | เปลี่ยนสถานะเอกสาร (เอกสารซื้อ/รับสินค้า) |
| Meaning | เปลี่ยนสถานะเอกสารในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `none`; parse status: `none`

### 120. POST /purchases/{{recordId}}/payment

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Change-Status-Paid-RI |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า) |
| Meaning | บันทึกหรือเปลี่ยนสถานะการชำระเงินในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| paymentStructureType | string | PaymentPaidCash | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentId | integer | 5512185 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2025-07-31 | วันที่ชำระเงิน |
| collected | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDeductionAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | number | 3.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Paid Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 121. POST /purchases/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Upload-File-RI |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (เอกสารซื้อ/รับสินค้า) |
| Meaning | อัปโหลดไฟล์แนบให้เอกสารในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 122. POST /purchases/email-document

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Send-Email-RI |
| Purpose | ส่งเอกสารทางอีเมล (เอกสารซื้อ/รับสินค้า) |
| Meaning | ส่งเอกสารทางอีเมลในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | Send Email | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | Send Email from production | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| doCopy | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentStructureType | string | SendEmailCoppies | โครงสร้างเอกสาร เช่น SimpleDocument หรือ InlineDocument |

### 123. POST /purchases/sharedocument

| Field | Value |
| --- | --- |
| Module | Receiving Inventory (RI) |
| Folder path | Receiving Inventory (RI) |
| Postman name | Share-Document-RI |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (เอกสารซื้อ/รับสินค้า) |
| Meaning | สร้างหรือส่งลิงก์แชร์เอกสารในหมวด เอกสารซื้อ/รับสินค้า |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 19213241 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 124. GET /expenses?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Get-All-Expense |
| Purpose | ดึงรายการข้อมูล (ค่าใช้จ่าย) |
| Meaning | ดึงรายการข้อมูลในหมวด ค่าใช้จ่าย |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 125. GET /expenses/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Get - Expense By Id |
| Purpose | ดึงข้อมูลตามรหัส (ค่าใช้จ่าย) |
| Meaning | แสดงข้อมูลเอกสารค่าใช้จ่าย ตามเลขที่ id เอกสาร |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1inline~1with-payment/post |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 126. GET /expenses/categories/business

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Get - Business Categories Expense |
| Purpose | ดึงรายการข้อมูล (ค่าใช้จ่าย) |
| Meaning | เรียกดูข้อมูลหมวดหมู่เอกสารค่าใช้จ่าย (สำหรับนักธุรกิจ) |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1categories~1business/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 127. GET /expenses/categories/accounting

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Get - Accounting Categories Expense |
| Purpose | ดึงรายการข้อมูล (ค่าใช้จ่าย) |
| Meaning | เรียกดูข้อมูลหมวดหมู่เอกสารค่าใช้จ่าย (สำหรับนักบัญชี) |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1categories~1accounting/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 128. POST /expenses

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Simple (Exclusive vat) |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses/post |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Simple document exclusive vat | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].total | integer | 10000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 10 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 630 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9630 | ยอดรวมสุทธิ |
| remarks | string | Expense - Simple document exclusive vat | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Simple document exclusive vat | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 129. POST /expenses

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Simple (Inclusive vat) |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses/post |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Simple document inclusive vat | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].total | integer | 10000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 10 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 588.79 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9000 | ยอดรวมสุทธิ |
| remarks | string | Expense - Simple document inclusive vat | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Simple document inclusive vat | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 130. POST /expenses/inline

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Inline discount percent |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย ส่วนลดเปอร์เซ็นต์ แยกตามรายการสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Inline discount percentage inclusive vat | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].total | integer | 9000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 8411.21 | ยอดที่นำไปคำนวณ VAT |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 588.79 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9000 | ยอดรวมสุทธิ |
| remarks | string | Expense - Inline discount percentage inclusive vat | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Inline discount percentage inclusive vat | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 131. POST /expenses/inline

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Inline discount amount |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย ส่วนลดแบบจำนวน แยกตามรายการสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Inline discount amount inclusive vat | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| discountType | integer | 3 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].discountAmount | integer | 1000 | มูลค่าส่วนลด |
| items[].total | integer | 9000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 8411.21 | ยอดที่นำไปคำนวณ VAT |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 588.79 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9000 | ยอดรวมสุทธิ |
| remarks | string | Expense - Inline discount amount inclusive vat | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Inline discount amount inclusive vat | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 132. POST /expenses/inline

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Inline vat (vat 7%) |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย ภาษีและส่วนลด แยกตามรายการสินค้า แบบมีภาษี 7 % |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Inline vat by item vat type 7% | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | True | เปิดใช้ VAT ระดับรายการ |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| items[].total | integer | 9000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 8411.21 | ยอดที่นำไปคำนวณ VAT |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 588.79 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9000 | ยอดรวมสุทธิ |
| remarks | string | Expense - Inline vat by item vat type 7% | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Inline vat by item vat type 7% | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 133. POST /expenses/inline

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Inline vat (no vat) |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย ภาษีและส่วนลด แยกตามรายการสินค้า แบบไม่มีภาษี |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Inline vat by item vat type no vat. | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | True | เปิดใช้ VAT ระดับรายการ |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | -1 | อัตรา VAT |
| items[].total | integer | 9000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| exemptAmount | integer | 9000 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | integer | 0 | ยอดที่นำไปคำนวณ VAT |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 0 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9000 | ยอดรวมสุทธิ |
| remarks | string | Expense - Inline vat by item vat type no vat. | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Inline vat by item vat type no vat. | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 134. POST /expenses/with-payment

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Simple with payment (Exclusive vat) |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย พร้อมเปลี่ยนสถานะชำระเงินแล้ว |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Simple document with payment exclusive vat | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].total | integer | 10000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 10 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 630 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9630 | ยอดรวมสุทธิ |
| remarks | string | Expense - Simple document exclusive vat | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Simple document exclusive vat | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2020-11-11 | วันที่ชำระเงิน |
| collected | integer | 9630 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | create expense simple with payment cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 135. POST /expenses

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Simple with payment (Exclusive vat) Test |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย พร้อมเปลี่ยนสถานะชำระเงินแล้ว |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string |  | รหัสผู้ติดต่อ |
| contactName | string | Omise Co., Ltd. | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | Head Office | สาขาของผู้ติดต่อ |
| contactPerson | string |  | ชื่อบุคคลติดต่อ |
| contactEmail | string |  | อีเมลผู้ติดต่อ |
| contactNumber | string |  | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10240 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 3 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2021-06-15 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2021-06-15 | วันครบกำหนดชำระ |
| salesName | string | [REDACTED] | ชื่อพนักงานขาย |
| projectName | string |  | ชื่อโครงการ |
| reference | string | invoice: INV2021060007 | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | False | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | False | เปิดใช้ VAT ระดับรายการ |
| expenseCategoryView | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].total | integer | 10000 | ยอดรวมของรายการ |
| subTotal | number | 41.789999999999964 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 0 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 0 | มูลค่าส่วนลด |
| totalAfterDiscount | number | 41.789999999999964 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| grandTotal | number | 41.789999999999964 | ยอดรวมสุทธิ |
| remarks | string | Transaction fee for invoice: INV2021060007 | หมายเหตุบนเอกสาร |
| internalNotes | string |  | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |

### 136. POST /expenses/inline/with-payment

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Create - Exp Inline vat with payment (vat 7%) |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย) |
| Meaning | สร้างเอกสารค่าใช้จ่าย ภาษีและส่วนลด แยกตามรายการสินค้า พร้อมเปลี่ยนสถานะชำระเงินแล้ว |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Inline vat by item vat type 7% | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | True | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| discountType | integer | 1 | ประเภทส่วนลด |
| useInlineDiscount | boolean | True | เปิดใช้ส่วนลดระดับรายการ |
| useInlineVat | boolean | True | เปิดใช้ VAT ระดับรายการ |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].discountAmount | integer | 10 | มูลค่าส่วนลด |
| items[].vatRate | integer | 7 | อัตรา VAT |
| items[].total | integer | 9000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| exemptAmount | integer | 0 | ยอดยกเว้นภาษีมูลค่าเพิ่ม |
| vatableAmount | number | 8411.21 | ยอดที่นำไปคำนวณ VAT |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | number | 588.79 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9000 | ยอดรวมสุทธิ |
| remarks | string | Expense - Inline vat by item vat type 7% | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Inline vat by item vat type 7% | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2020-11-11 | วันที่ชำระเงิน |
| collected | integer | 9630 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | create expense simple with payment cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 137. PUT /expenses/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Update - Exp Simple (Exclusive vat) by Id |
| Purpose | ดึงข้อมูลตามรหัส (ค่าใช้จ่าย) |
| Meaning | อัพเดรต เอกสารค่าใช้จ่าย |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | บริษัท ตัวอย่าง จำกัด | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Example Co., Ltd. | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ Head Office | ชื่อสาขาบริษัท |
| companyBranchEn | string | สำนักงานใหญ่ 00000 | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactCode | string | C0001 | รหัสผู้ติดต่อ |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ 00000 | สาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactNumber | string | 099-999-9999 | เบอร์โทรผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactGroup | integer | 1 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| publishedOn | string | 2020-11-11 | วันที่ออกเอกสาร |
| creditType | integer | 1 | ประเภทเครดิต/การชำระเงิน |
| creditDays | integer | 0 | จำนวนวันเครดิต |
| dueDate | string | 2020-11-11 | วันครบกำหนดชำระ |
| projectName | string | Expense - Simple document exclusive vat | ชื่อโครงการ |
| reference | string | INV2020110001 | เลขที่อ้างอิง |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| salesName | string | Sale Name | ชื่อพนักงานขาย |
| items | array | 1 item(s) | รายการสินค้า/บริการในเอกสาร |
| items[].description | string | Marketing | รายละเอียดรายการ |
| items[].systemCode | integer | 1001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].categoryId | integer | 199493 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameForeign | string | Marketing & Advertising | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].nameLocal | string | การตลาดและโฆษณา | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditId | integer | 13775861 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCode | string | 21399 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditCategory | integer | 2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameForeign | string | 21399 / Other Payables | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].creditNameLocal | string | 21399 / เจ้าหนี้อืน | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitId | integer | 13776005 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCode | string | 53029 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitCategory | integer | 5 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameForeign | string | 53029 / Other advertising and marketing expenses | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].debitNameLocal | string | 53029 / ค่าใช้จ่ายด้านโฆษณาและการตลาดอื่นๆนๆดอื่นๆ | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| items[].quantity | integer | 1 | จำนวน |
| items[].unitName | string | Package | หน่วยนับ |
| items[].pricePerUnit | integer | 10000 | ราคาต่อหน่วย |
| items[].total | integer | 10000 | ยอดรวมของรายการ |
| subTotal | integer | 10000 | ยอดรวมก่อนส่วนลดและภาษี |
| discountPercentage | integer | 10 | เปอร์เซ็นต์ส่วนลด |
| discountAmount | integer | 1000 | มูลค่าส่วนลด |
| totalAfterDiscount | integer | 9000 | ยอดรวมหลังหักส่วนลด |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| vatAmount | integer | 630 | ยอดภาษีมูลค่าเพิ่ม |
| grandTotal | integer | 9630 | ยอดรวมสุทธิ |
| remarks | string | Expense - Simple document exclusive vat | หมายเหตุบนเอกสาร |
| internalNotes | string | Expense - Simple document exclusive vat | หมายเหตุภายใน |
| showSignatureOrStamp: true | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 138. DELETE /expenses/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Delete - Expense By Id |
| Purpose | ลบข้อมูลหรือเอกสาร (ค่าใช้จ่าย) |
| Meaning | ลบเอกสารค่าใช้จ่าย ตามเลขที่ id เอกสาร |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1{id}/delete |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 139. POST /expenses/{{recordId}}/status/void

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Change Status - Expense |
| Purpose | เปลี่ยนสถานะเอกสาร (ค่าใช้จ่าย) |
| Meaning | เปลี่ยนสถานะเอกสารค่าใช้จ่าย |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1{id}~1status~1{statusId}/post |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
|  |  | True | query parameter จาก Postman collection |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 140. POST /expenses/{{recordId}}/payment

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Change Status Payment - Expense |
| Purpose | บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ค่าใช้จ่าย) |
| Meaning | เปลี่ยนสถานะเอกสารค่าใช้จ่าย เป็นชำระเงินแล้ว |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 5482973 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentMethod | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentDate | string | 2020-01-01 | วันที่ชำระเงิน |
| collected | number | 249.6 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldPercentage | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withheldAmount | number | 7.2 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| paymentRemarks | string | Payment Cash | หมายเหตุการชำระเงิน |
| remainingCollectedType | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remainingCollected | integer | 0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 141. POST /expenses/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Attachment File - Expense |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (ค่าใช้จ่าย) |
| Meaning | แนบไฟล์ในเอกสารค่าใช้จ่าย |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1{id}~1attachment/post |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| attachment | file |  | field จาก request body ใน Postman collection |

### 142. POST /expenses/sharedocument

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Sharedocument - Expense |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (ค่าใช้จ่าย) |
| Meaning | สร้างลิงค์เอกสารค่าใช้จ่าย |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1sharedocument/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 1542393 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 143. POST /expenses/email-document

| Field | Value |
| --- | --- |
| Module | Expense (EXP) |
| Folder path | Expense (EXP) |
| Postman name | Send Email - Expense |
| Purpose | ส่งเอกสารทางอีเมล (ค่าใช้จ่าย) |
| Meaning | ส่งเอกสารค่าใช้จ่าย ให้ผู้รับทางอีเมล |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | https://developers.flowaccount.com/#tag/Expenses/paths/~1expenses~1email-document/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 1542393 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | wittholding tax document | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | send email from API | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 144. GET /withholding-taxes?currentPage=1&pageSize=20&range=3&month=7&year=2021

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Get - WHT |
| Purpose | ดึงรายการข้อมูล (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | ดึงรายการข้อมูลในหมวด หนังสือรับรองหัก ณ ที่จ่าย |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | False | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | False | จำนวนรายการต่อหน้า |
| startDate |  | True | วันเริ่มต้นของช่วงค้นหา |
| endDate |  | True | วันสิ้นสุดของช่วงค้นหา |
| searchString |  | True | ข้อความค้นหา เช่น ชื่อลูกค้า โครงการ หรือเลขเอกสาร |
| range | 3 | False | ช่วงเวลา: 0=ทั้งหมด, 1=เดือนนี้, 3=เดือนก่อน, 5=ช่วงวันที่, 7=ปีนี้, 9=ปีก่อน, 15=ปีบัญชี |
| month | 7 | False | เดือนที่ใช้ค้นหา |
| year | 2021 | False | ปีที่ใช้ค้นหา |

Body mode: `none`; parse status: `none`

### 145. GET /withholding-taxes/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Get - WHT By Id |
| Purpose | ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | แสดงข้อมูลเอกสารหัก ณ ที่จ่าย ตามเลขที่ id เอกสาร |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes~1{id}/get |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 146. POST /withholding-taxes/{{recordId}}/status/void

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Change Status - WHT By Id |
| Purpose | ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | เปลี่ยนสถานะเอกสารใบหัก ณ ที่จ่าย |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes~1{id}~1status~1{statusId}/post |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
|  |  | True | query parameter จาก Postman collection |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 147. POST /withholding-taxes/sharedocument

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Sharedocument - WHT |
| Purpose | สร้างหรือส่งลิงก์แชร์เอกสาร (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | สร้างลิงค์เอกสารใบหัก ณ ที่จ่าย |
| Auth | Bearer token |
| Test class | share_link |
| Test note | อาจสร้างลิงก์แชร์เอกสาร ต้องมี record id ก่อนทดสอบ |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes~1sharedocument/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 1542393 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 148. POST /withholding-taxes/email-document

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Send Email - WHT |
| Purpose | ส่งเอกสารทางอีเมล (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | ส่งเอกสารใบหัก ณ ที่จ่าย ให้ผู้รับทางอีเมล |
| Auth | Bearer token |
| Test class | outbound_email |
| Test note | มีโอกาสส่งอีเมลออกนอกระบบ ต้องยืนยันก่อนทดสอบ |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes~1email-document/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentId | integer | 1542393 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| fromemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| toemail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| cCMyself | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| ccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bccEmail | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| subject | string | wittholding tax document | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| message | string | send email from API | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| culture | string | th | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 149. POST /withholding-taxes/{{recordId}}/attachment

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Attachment File - WHT |
| Purpose | อัปโหลดไฟล์แนบให้เอกสาร (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | แนบไฟล์ในเอกสารใบหัก ณ ที่จ่าย |
| Auth | Bearer token |
| Test class | file_upload |
| Test note | ต้องมี record id และไฟล์ตัวอย่างก่อนทดสอบ |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes~1{id}~1attachment/post |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `formdata`; parse status: `parsed_formdata`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| attachment | file |  | field จาก request body ใน Postman collection |

### 150. POST /withholding-taxes

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Create - WHT |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | สร้างเอกสารใบหัก ณ ที่จ่าย |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| content-type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactName | string | Withholidng | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactNumber | string | [REDACTED] | เบอร์โทรผู้ติดต่อ |
| contactGroup | integer | 3 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| contactPerson | string | New New | ชื่อบุคคลติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | office heads | สาขาของผู้ติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| publishedOn | string | 2020-04-12 | วันที่ออกเอกสาร |
| entity | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| textOther | string | 2020 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].incomeType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxRate | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxAmount | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxAmountNoVat | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].total | integer | 104 | ยอดรวมของรายการ |
| withholdingTaxItems[].isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| withholdingTaxItems[].vatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].description | null |  | รายละเอียดรายการ |
| withholdingTaxItems[].value | string | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].withheld | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxType | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| total | integer | 100 | ยอดรวมของรายการ |
| totalTaxWithheld | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| taxPayment | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| taxPaymentOthers | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| providentFundNumber | string | 9999 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| providentFundAmount | string | 888 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| socialSecurityAmount | string | 777 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string | remark | หมายเหตุบนเอกสาร |
| internalNotes | string | note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |

### 151. DELETE /withholding-taxes/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Delete - WHT By Id |
| Purpose | ลบข้อมูลหรือเอกสาร (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | ลบเอกสารใบหัก ณ ที่จ่าย ตามเลขที่ id เอกสาร |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes~1{id}/delete |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 152. PUT /withholding-taxes/{{recordId}}

| Field | Value |
| --- | --- |
| Module | Withholding Tax (WHT) |
| Folder path | Withholding Tax (WHT) |
| Postman name | Update - WHT By Id |
| Purpose | ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย) |
| Meaning | อัพเดตข้อมูลเอกสารใบหัก ณ ที่จ่าย |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | https://developers.flowaccount.com/#tag/Withholding-Tax/paths/~1withholding-taxes~1{id}/put |

Path parameters:

| Name | Meaning |
| --- | --- |
| recordId | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| content-type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyName | string | ชื่อบริษัท | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Company Name English | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranch | string | สำนักงานใหญ่ | ชื่อสาขาบริษัท |
| companyBranchEn | string | Head Office | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |
| contactName | string | Withholidng | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactNumber | string | [REDACTED] | เบอร์โทรผู้ติดต่อ |
| contactGroup | integer | 3 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| contactPerson | string | New New | ชื่อบุคคลติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | office heads | สาขาของผู้ติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| publishedOn | string | 2020-04-12 | วันที่ออกเอกสาร |
| entity | integer | 7 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| textOther | string | 2020 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].incomeType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxRate | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxAmount | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxAmountNoVat | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].total | integer | 104 | ยอดรวมของรายการ |
| withholdingTaxItems[].isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| withholdingTaxItems[].vatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].description | null |  | รายละเอียดรายการ |
| withholdingTaxItems[].value | string | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].withheld | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| withholdingTaxItems[].taxType | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| isVatInclusive | boolean | False | ระบุว่าราคาสินค้ารวม VAT แล้วหรือไม่ |
| isVat | boolean | True | ระบุว่ามี VAT หรือไม่ |
| total | integer | 100 | ยอดรวมของรายการ |
| totalTaxWithheld | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| taxPayment | integer | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| taxPaymentOthers | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| providentFundNumber | string | 9999 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| providentFundAmount | string | 888 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| socialSecurityAmount | string | 777 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string | remark | หมายเหตุบนเอกสาร |
| internalNotes | string | note | หมายเหตุภายใน |
| showSignatureOrStamp | boolean | True | กำหนดให้แสดงลายเซ็นหรือตราประทับ |

### 153. POST /journal-entries/draft

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Draft Journals |
| Postman name | Draft Journal Voucher (JV) |
| Purpose | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) |
| Meaning | สร้างรายการสมุดรายวันแบบร่างในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentDate | string | 2024-02-28 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| description | string | example description 1 | รายละเอียดรายการ |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string | example remark | หมายเหตุบนเอกสาร |
| reference | string | DOC000001 | เลขที่อ้างอิง |
| bookOfAccounts | array | 3 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | string | 107 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 154. POST /journal-entries/draft

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Draft Journals |
| Postman name | Draft Purchase Voucher (UV) |
| Purpose | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) |
| Meaning | สร้างรายการสมุดรายวันแบบร่างในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentType | integer | 53 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentDate | string | 2024-02-28 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| description | string | example description 1 | รายละเอียดรายการ |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string | example remark | หมายเหตุบนเอกสาร |
| reference | string | DOC000001 | เลขที่อ้างอิง |
| bookOfAccounts | array | 3 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | string | 107 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 155. POST /journal-entries/draft

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Draft Journals |
| Postman name | Draft Sales Voucher (SV) |
| Purpose | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) |
| Meaning | สร้างรายการสมุดรายวันแบบร่างในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentType | integer | 55 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentDate | string | 2024-02-28 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| description | string | example description 1 | รายละเอียดรายการ |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string | example remark | หมายเหตุบนเอกสาร |
| reference | string | DOC000001 | เลขที่อ้างอิง |
| bookOfAccounts | array | 3 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | string | 107 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 156. POST /journal-entries/draft

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Draft Journals |
| Postman name | Draft Payment Voucher (PV) |
| Purpose | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) |
| Meaning | สร้างรายการสมุดรายวันแบบร่างในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentType | integer | 57 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentDate | string | 2024-02-28 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| description | string | example description 1 | รายละเอียดรายการ |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string | example remark | หมายเหตุบนเอกสาร |
| reference | string | DOC000001 | เลขที่อ้างอิง |
| bookOfAccounts | array | 3 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | string | 107 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 157. POST /journal-entries/draft

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Draft Journals |
| Postman name | Draft Received Voucher (RV) |
| Purpose | สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน) |
| Meaning | สร้างรายการสมุดรายวันแบบร่างในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| documentType | integer | 59 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentDate | string | 2024-02-28 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| description | string | example description 1 | รายละเอียดรายการ |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string | example remark | หมายเหตุบนเอกสาร |
| reference | string | DOC000001 | เลขที่อ้างอิง |
| bookOfAccounts | array | 3 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | string | 107 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 158. POST /journal-entries/approve

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Approved Journals |
| Postman name | Approved Journal Voucher (JV) |
| Purpose | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) |
| Meaning | สร้างหรืออนุมัติรายการสมุดรายวันในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| description | string | example description 1 | รายละเอียดรายการ |
| documentDate | string | 2024-01-31 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentType | integer | 51 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string |  | หมายเหตุบนเอกสาร |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| reference | string | example reference | เลขที่อ้างอิง |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| bookOfAccounts | array | 2 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 159. POST /journal-entries/approve

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Approved Journals |
| Postman name | Approved Purchase Voucher (UV) |
| Purpose | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) |
| Meaning | สร้างหรืออนุมัติรายการสมุดรายวันในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| description | string | example description 1 | รายละเอียดรายการ |
| documentDate | string | 2024-01-31 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentType | integer | 53 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string |  | หมายเหตุบนเอกสาร |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| reference | string | example reference | เลขที่อ้างอิง |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| bookOfAccounts | array | 2 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 160. POST /journal-entries/approve

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Approved Journals |
| Postman name | Approved Sales Voucher (SV) |
| Purpose | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) |
| Meaning | สร้างหรืออนุมัติรายการสมุดรายวันในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| description | string | example description 1 | รายละเอียดรายการ |
| documentDate | string | 2024-01-31 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentType | integer | 55 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string |  | หมายเหตุบนเอกสาร |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| reference | string | example reference | เลขที่อ้างอิง |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| bookOfAccounts | array | 2 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 161. POST /journal-entries/approve

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Approved Journals |
| Postman name | Approved Payment Voucher (PV) |
| Purpose | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) |
| Meaning | สร้างหรืออนุมัติรายการสมุดรายวันในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| description | string | example description 1 | รายละเอียดรายการ |
| documentDate | string | 2024-01-31 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentType | integer | 57 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string |  | หมายเหตุบนเอกสาร |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| reference | string | example reference | เลขที่อ้างอิง |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| bookOfAccounts | array | 2 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 162. POST /journal-entries/approve

| Field | Value |
| --- | --- |
| Module | Journal Entry |
| Folder path | Journal Entry / Approved Journals |
| Postman name | Approved Received Voucher (RV) |
| Purpose | สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน) |
| Meaning | สร้างหรืออนุมัติรายการสมุดรายวันในหมวด รายการสมุดรายวัน |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| description | string | example description 1 | รายละเอียดรายการ |
| documentDate | string | 2024-01-31 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| documentType | integer | 59 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| remarks | string |  | หมายเหตุบนเอกสาร |
| note | string | example note | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| reference | string | example reference | เลขที่อ้างอิง |
| contactId | string |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactName | string | exampleName | ชื่อผู้ติดต่อหรือนิติบุคคล |
| bookOfAccounts | array | 2 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].debitCredit | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].chartOfAccountId | integer | 351811341 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].value | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| bookOfAccounts[].description | string | example description 2 | รายละเอียดรายการ |

### 163. GET /chart-of-accounts/accounts

| Field | Value |
| --- | --- |
| Module | Chart of Account (COA) |
| Folder path | Chart of Account (COA) |
| Postman name | Get List Chart Of Account |
| Purpose | ดึงรายการข้อมูล (ผังบัญชี) |
| Meaning | ดึงรายการข้อมูลในหมวด ผังบัญชี |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Body mode: `none`; parse status: `none`

### 164. GET /product-masters?filter=%5B%7B'columnName'%3A'productCode'%2C'columnValue'%3A'N0001'%7D%5D

| Field | Value |
| --- | --- |
| Module | Product Master |
| Folder path | Product Master |
| Postman name | Get All Product Masters |
| Purpose | ดึงรายการข้อมูล (ข้อมูล master ของสินค้า) |
| Meaning | ดึงรายการข้อมูลในหมวด ข้อมูล master ของสินค้า |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | True | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | True | จำนวนรายการต่อหน้า |
| sortBy | [{'name':'productCode','sortOrder':'asc'}] | True | query parameter จาก Postman collection |
| sortBy | [{"name":"name","sortOrder":"desc"}] | True | query parameter จาก Postman collection |
| filter | [{'columnName':'categoryId','columnValue':'517725','columnPredicateOperator':'And'}] | True | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |
| filter | [{'columnName':'name','columnValue':'Product Master Service','columnPredicateOperator':'And'}] | True | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |
| filter | [{'columnName':'type','columnValue':'1','columnPredicateOperator':'And'}] | True | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |
| filter | %5B%7B'columnName'%3A'productCode'%2C'columnValue'%3A'N0001'%7D%5D | False | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Authorization | [redacted] |  |

Body mode: `none`; parse status: `none`

### 165. POST /product-masters

| Field | Value |
| --- | --- |
| Module | Product Master |
| Folder path | Product Master |
| Postman name | Create Product Master |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ข้อมูล master ของสินค้า) |
| Meaning | สร้างข้อมูลหรือเอกสารใหม่ในหมวด ข้อมูล master ของสินค้า |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | Postman collection only |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Authorization | [redacted] |  |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| code | string | S0001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| name | string | Product Master Service | ชื่อรายการ |
| categoryName | string | Description Product Master Service | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellSettings | object |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellSettings.vatType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellSettings.description | string | Sell vat type settings description | รายละเอียดรายการ |
| sellSettings.chartOfAccountId | integer | 3424143 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].unitName | string | ครั้ง | หน่วยนับ |
| productLists[].sellPrice | number | 10000.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].buyPrice | number | 10000.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].barcode | string | SERVICE | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].isMainProduct | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 166. GET /product-masters/{{Product_Id}}

| Field | Value |
| --- | --- |
| Module | Product Master |
| Folder path | Product Master |
| Postman name | Get Product Master By Id |
| Purpose | ดึงข้อมูลตามรหัส (ข้อมูล master ของสินค้า) |
| Meaning | ดึงข้อมูลตามรหัสในหมวด ข้อมูล master ของสินค้า |
| Auth | Bearer token |
| Test class | requires_record_id |
| Test note | ต้องมี record id จริงก่อนจึงทดสอบได้ |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| Product_Id | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Authorization | [redacted] |  |

Body mode: `none`; parse status: `none`

### 167. PUT /product-masters/{{Product_Id}}

| Field | Value |
| --- | --- |
| Module | Product Master |
| Folder path | Product Master |
| Postman name | Update Product Master |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ข้อมูล master ของสินค้า) |
| Meaning | แก้ไขข้อมูลหรือเอกสารเดิมในหมวด ข้อมูล master ของสินค้า |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| Product_Id | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Authorization | [redacted] |  |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| id | integer | 1001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| code | string | S0001 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| name | string | Product Master Service | ชื่อรายการ |
| categoryName | string | Description Product Master Service | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellSettings | object |  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellSettings.vatType | integer | 1 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellSettings.description | string | Sell vat type settings description | รายละเอียดรายการ |
| sellSettings.chartOfAccountId | integer | 12341234 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists | array | 1 item(s) | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].id | integer | 2001 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| productLists[].unitName | string | ครั้ง  | หน่วยนับ |
| productLists[].sellPrice | number | 10000.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].buyPrice | number | 10000.0 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].barcode | string | SERVICE | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| productLists[].isMainProduct | boolean | True | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 168. DELETE /product-masters/{{Product_Id}}

| Field | Value |
| --- | --- |
| Module | Product Master |
| Folder path | Product Master |
| Postman name | Delete Product Master |
| Purpose | ลบข้อมูลหรือเอกสาร (ข้อมูล master ของสินค้า) |
| Meaning | ลบข้อมูลหรือเอกสารในหมวด ข้อมูล master ของสินค้า |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | Postman collection only |

Path parameters:

| Name | Meaning |
| --- | --- |
| Product_Id | path variable ที่ต้องแทนด้วยค่าจริงก่อนเรียก API |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Authorization | [redacted] |  |

Body mode: `none`; parse status: `none`

### 169. GET /products?filter=%5B%7B'columnName'%3A'name'%2C'columnValue'%3A'Service'%2C'columnPredicateOperator'%3A'And'%7D%5D

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Get - All Products |
| Purpose | ดึงรายการข้อมูล (สินค้าและบริการ) |
| Meaning | แสดงข้อมูลสินค้าทั้งหมด |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products~1{id}/get |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | True | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | True | จำนวนรายการต่อหน้า |
| sortBy | [{'name':'productCode','sortOrder':'asc'}] | True | Sort By Product Code |
| sortBy | [{"name":"name","sortOrder":"desc"}] | True | Sort By Product Name |
| sortBy | [{"name":"unitPrice","sortOrder":"asc"}] | True | Sort By Unit Price |
| filter | [{'columnName':'categoryId','columnValue':'517725','columnPredicateOperator':'And'}] | True | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |
| filter | %5B%7B'columnName'%3A'name'%2C'columnValue'%3A'Service'%2C'columnPredicateOperator'%3A'And'%7D%5D | False | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |
| filter | [{'columnName':'barcode','columnValue':'A000001','columnPredicateOperator':'And'}] | True | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |
| filter | [{'columnName':'productCode','columnValue':'IN001'}] | True | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 170. GET /products/12851240

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Get - Product By Id |
| Purpose | ดึงข้อมูลตามรหัส (สินค้าและบริการ) |
| Meaning | แสดงข้อมูลสินค้า ตาม id รายการสินค้า |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products~1{id}/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `empty`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 171. POST /products

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Create - Product Service |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) |
| Meaning | สร้างสินค้า ประเภทบริการ |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| type | integer | 1 | ประเภทรายการสินค้า/บริการ |
| code | string | S0002 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| name | string | Service | ชื่อรายการ |
| sellDescription | string | Description Service | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellPrice | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| unitName | string | Service | หน่วยนับ |
| categoryName | string | Service | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| barcode | string | BarcodeService | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 172. POST /products

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Create - Product Non Inventory |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) |
| Meaning | สร้างสินค้า ประเภทไม่นับสต๊อก |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| type | integer | 3 | ประเภทรายการสินค้า/บริการ |
| code | string | N0002 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| name | string | Non Inventory | ชื่อรายการ |
| sellDescription | string | Description Non Inventory  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellPrice | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| unitName | string | Product | หน่วยนับ |
| categoryName | string | Product | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| barcode | string | Barcode Non Inventory | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyDescription | string | Description Non Inventory  | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyPrice | integer | 50 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 173. POST /products

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Create - Product Inventory |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) |
| Meaning | สร้างสินค้า ประเภทนับสต๊อก |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| type | integer | 5 | ประเภทรายการสินค้า/บริการ |
| code | string | IN003 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| name | string | Inventory no stock | ชื่อรายการ |
| sellDescription | string | Description Inventory no stock | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellPrice | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| unitName | string | Product | หน่วยนับ |
| categoryName | string | Product | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| barcode | string | BarcodeInventory | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyDescription | string | Description Inventory no stock | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyPrice | integer | 50 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 174. POST /products

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Create - Product Inventory has stock |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ) |
| Meaning | สร้างสินค้า ประเภทนับสต๊อก มียอดเริ่มต้น |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| type | integer | 5 | ประเภทรายการสินค้า/บริการ |
| code | string | IN004 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| name | string | Inventory has stock | ชื่อรายการ |
| sellDescription | string | Description Inventory has stock | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellPrice | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| unitName | string | Product | หน่วยนับ |
| categoryName | string | Product | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| barcode | string | BarcodeInventory | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyDescription | string | Description Inventory has stock | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyPrice | integer | 50 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| inventoryPublishedOn | string | 2020-01-01 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| inventoryQuantity | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| inventoryPrice | integer | 50 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 175. PUT /products/12851240

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Update - Product By Id |
| Purpose | ดึงข้อมูลตามรหัส (สินค้าและบริการ) |
| Meaning | อัพเดตสินค้า ตาม id รายการสินค้า |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products~1{id}/put |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| type | integer | 5 | ประเภทรายการสินค้า/บริการ |
| code | string | IN002 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| name | string | Inventory | ชื่อรายการ |
| sellDescription | string | Description Update Product Inventory has balance | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellPrice | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| sellVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| unitName | string | Product | หน่วยนับ |
| categoryName | string | Product | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| barcode | string | BarcodeInventory | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyDescription | string | Description Update Product Inventory has balance | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyPrice | integer | 50 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| buyVatType | integer | 3 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| inventoryPublishedOn | string | 2020-01-01 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| inventoryQuantity | integer | 100 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| inventoryPrice | integer | 50 | อ้างอิงจากตัวอย่าง request body ใน Postman collection |

### 176. DELETE /products/12851240

| Field | Value |
| --- | --- |
| Module | Products |
| Folder path | Products |
| Postman name | Delete - Product By Id |
| Purpose | ลบข้อมูลหรือเอกสาร (สินค้าและบริการ) |
| Meaning | ลบสินค้า ตามเลข id รายการสินค้า |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | https://developers.flowaccount.com/#tag/Products/paths/~1products~1{id}/delete |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `empty`

Body fields: ไม่มี field ใน collection หรือ parse ไม่ได้

### 177. GET /contacts

| Field | Value |
| --- | --- |
| Module | Contacts |
| Folder path | Contacts |
| Postman name | Get - All Contacts |
| Purpose | ดึงรายการข้อมูล (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) |
| Meaning | แสดงข้อมูลลูกค้าทั้งหมด |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Contacts/paths/~1contacts/get |

Query parameters:

| Name | Sample | Disabled in collection | Meaning |
| --- | --- | --- | --- |
| currentPage | 1 | True | หน้าปัจจุบันของผลลัพธ์ |
| pageSize | 20 | True | จำนวนรายการต่อหน้า |
| sortBy | [{'name':'contactType','sortOrder':'desc'}] | True | Sort by Contact Type |
| sortBy | [{'name':'namelocal','sortOrder':'desc'}] | True | Sort by Contact Name |
| sortBy | [{'name':'contactPerson','sortOrder':'desc'}] | True | Sort by Contact Person Name |
| sortBy | [{'name':'email','sortOrder':'desc'}] | True | Sort by Contact Name |
| sortBy | [{'name':'phone2','sortOrder':'desc'}] | True | Sort By Phone |
| filter | [{'columnName':'contactType','columnValue':'3','columnPredicateOperator':'And'}] | True | เงื่อนไข filter แบบ JSON ตามตัวอย่างใน collection |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 178. GET /contacts/130093

| Field | Value |
| --- | --- |
| Module | Contacts |
| Folder path | Contacts |
| Postman name | Get - Contact By Id |
| Purpose | ดึงข้อมูลตามรหัส (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) |
| Meaning | แสดงข้อมูลผู้ติดต่อ ตาม id รายชื่อผู้ติดต่อ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Contacts/paths/~1contacts~1{id}/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 179. POST /contacts

| Field | Value |
| --- | --- |
| Module | Contacts |
| Folder path | Contacts |
| Postman name | Create - Contact |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) |
| Meaning | สร้างรายชื่อผู้ติดต่อ ลูกค้า หรือ ผู้จำหน่าย |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Contacts/paths/~1contacts/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| contactName | string | บริษัท ตัวอย่าง จำกัด | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactType | integer | 3 | ประเภทผู้ติดต่อ |
| contactGroup | integer | 3 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| contactCode | string | C0003 | รหัสผู้ติดต่อ |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactZipCode | string | 10500 | รหัสไปรษณีย์ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranch | string | สำนักงานใหญ่ | สาขาของผู้ติดต่อ |
| contactBranchCode | string | 00000 | รหัสสาขาของผู้ติดต่อ |
| contactPerson | string | ชื่อผู้ติดต่อ | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactMobile | string | [redacted] | เบอร์มือถือผู้ติดต่อ |
| contactOffice | string | 02-888-8888 | เบอร์สำนักงานผู้ติดต่อ |
| contactFax | string | [redacted] | แฟกซ์ผู้ติดต่อ |
| contactWebsite | string | [redacted] | เว็บไซต์ผู้ติดต่อ |
| conatactShippingAddress | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactCreditDays | integer | 30 | จำนวนวันเครดิตของผู้ติดต่อ |
| contactBankId | integer | 2 | รหัสธนาคารของผู้ติดต่อ |
| contactBankAccountNumber | string | [redacted] | เลขที่บัญชีธนาคารของผู้ติดต่อ |
| contactBankBranch | string | สีลม | สาขาธนาคารของผู้ติดต่อ |
| contactBankAccountType | integer | 1 | ประเภทบัญชีธนาคารของผู้ติดต่อ |
| contactNote | string | สร้าง contact จาก api | บันทึกเพิ่มเติมของผู้ติดต่อ |

### 180. PUT /contacts/130093

| Field | Value |
| --- | --- |
| Module | Contacts |
| Folder path | Contacts |
| Postman name | Update - Contact By Id |
| Purpose | ดึงข้อมูลตามรหัส (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) |
| Meaning | อัพเดตข้อมูลผู้ติดต่อ ตาม id รายชื่อผู้ติดต่อ |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | https://developers.flowaccount.com/#tag/Contacts/paths/~1contacts~1{id}/put |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| id | integer | 130093 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| contactGroup | integer | 3 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| contactType | integer | 3 | ประเภทผู้ติดต่อ |
| contactCode | string | Code | รหัสผู้ติดต่อ |
| contactName | string | name contact 009 | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranchCode | string | code branch | รหัสสาขาของผู้ติดต่อ |
| contactBranch | string | name branch | สาขาของผู้ติดต่อ |
| contactPerson | string | person contact | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactMobile | string | [redacted] | เบอร์มือถือผู้ติดต่อ |
| contactBankId | integer | 2 | รหัสธนาคารของผู้ติดต่อ |
| contactBankAccountNumber | string | [redacted] | เลขที่บัญชีธนาคารของผู้ติดต่อ |
| contactBankBranch | string | name bank | สาขาธนาคารของผู้ติดต่อ |
| contactBankAccountType | integer | 1 | ประเภทบัญชีธนาคารของผู้ติดต่อ |
| contactCreditDays | string | 30 | จำนวนวันเครดิตของผู้ติดต่อ |
| conatactShippingAddress | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactWebsite | string | [redacted] | เว็บไซต์ผู้ติดต่อ |
| contactFax | string | [redacted] | แฟกซ์ผู้ติดต่อ |
| contactOffice | string | office | เบอร์สำนักงานผู้ติดต่อ |
| contactNote | string | Note | บันทึกเพิ่มเติมของผู้ติดต่อ |

### 181. DELETE /contacts/130093

| Field | Value |
| --- | --- |
| Module | Contacts |
| Folder path | Contacts |
| Postman name | Delete - Contact By Id |
| Purpose | ลบข้อมูลหรือเอกสาร (ผู้ติดต่อ ลูกค้า หรือผู้ขาย) |
| Meaning | ลบผู้ติดต่อ ตาม id รายชื่อผู้ติดต่อ |
| Auth | Bearer token |
| Test class | destructive_delete |
| Test note | เป็นการลบข้อมูล ต้องสร้างข้อมูลทดสอบและยืนยันก่อน |
| Source document | https://developers.flowaccount.com/#tag/Contacts/paths/~1contacts~1{id}/delete |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| id | integer | 130093 | รหัสรายการหรือรหัสอ้างอิงในระบบ |
| contactGroup | integer | 3 | กลุ่มผู้ติดต่อ เช่น ลูกค้า/ผู้ขาย |
| contactType | integer | 3 | ประเภทผู้ติดต่อ |
| contactCode | string | Code | รหัสผู้ติดต่อ |
| contactName | string | name contact 009 | ชื่อผู้ติดต่อหรือนิติบุคคล |
| contactAddress | string | [redacted] | ที่อยู่ผู้ติดต่อ |
| contactTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของผู้ติดต่อ |
| contactBranchCode | string | code branch | รหัสสาขาของผู้ติดต่อ |
| contactBranch | string | name branch | สาขาของผู้ติดต่อ |
| contactPerson | string | person contact | ชื่อบุคคลติดต่อ |
| contactEmail | string | [redacted] | อีเมลผู้ติดต่อ |
| contactMobile | string | [redacted] | เบอร์มือถือผู้ติดต่อ |
| contactBankId | integer | 2 | รหัสธนาคารของผู้ติดต่อ |
| contactBankAccountNumber | string | [redacted] | เลขที่บัญชีธนาคารของผู้ติดต่อ |
| contactBankBranch | string | name bank | สาขาธนาคารของผู้ติดต่อ |
| contactBankAccountType | integer | 1 | ประเภทบัญชีธนาคารของผู้ติดต่อ |
| contactCreditDays | string | 30 | จำนวนวันเครดิตของผู้ติดต่อ |
| conatactShippingAddress | string | [redacted] | อ้างอิงจากตัวอย่าง request body ใน Postman collection |
| contactWebsite | string | [redacted] | เว็บไซต์ผู้ติดต่อ |
| contactFax | string | [redacted] | แฟกซ์ผู้ติดต่อ |
| contactOffice | string | office | เบอร์สำนักงานผู้ติดต่อ |
| contactNote | string | Note | บันทึกเพิ่มเติมของผู้ติดต่อ |

### 182. GET /company/info

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / My Company |
| Postman name | GET - Company Infomation |
| Purpose | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | แสดงข้อมูลบริษัทของคุณ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Business-Infomation/paths/~1company~1info/get |

Body mode: `none`; parse status: `none`

### 183. PUT /company/info

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / My Company |
| Postman name | Update - Company Infomation |
| Purpose | แก้ไขข้อมูลหรือเอกสารเดิม (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | อัพเดตข้อมูลบริษัทคุณ |
| Auth | Bearer token |
| Test class | mutating_update |
| Test note | เป็นการแก้ไขข้อมูล ต้องมี record id ของข้อมูลทดสอบก่อน |
| Source document | https://developers.flowaccount.com/#tag/Business-Infomation/paths/~1company~1info/put |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| companyType | integer | 10 | ประเภทกิจการ/บริษัท |
| companyName | string | บริษัท ตัวอย่าง จำกัด | ชื่อบริษัทภาษาไทย |
| companyNameEn | string | Example Co., Ltd. | ชื่อบริษัทภาษาอังกฤษ |
| companyAddress | string | [redacted] | ที่อยู่บริษัทภาษาไทย |
| companyAddressEn | string | [redacted] | ที่อยู่บริษัทภาษาอังกฤษ |
| companyZipCode | string | 10500 | รหัสไปรษณีย์บริษัท |
| companyTaxId | string | [redacted] | เลขประจำตัวผู้เสียภาษีของบริษัท |
| companyBranchCode | string | สำนักงานใหญ่ | รหัสสาขาบริษัท |
| companyBranch | string | Head Office | ชื่อสาขาบริษัท |
| companyBranchEn | string | 00000 | ชื่อสาขาบริษัทภาษาอังกฤษ |
| companyPhone | string | [redacted] | เบอร์โทรศัพท์บริษัท |
| companyMobile | string | [redacted] | เบอร์มือถือบริษัท |
| companyFax | string | [redacted] | เบอร์แฟกซ์บริษัท |
| companyWebsite | string | [redacted] | เว็บไซต์บริษัท |

### 184. GET /bank-accounts

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / Bank Channel |
| Postman name | GET - All Bank Account |
| Purpose | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | แสดงข้อมูลบัญชีธนาคารทั้งหมดในบริษัทของคุณ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Bank-Account/paths/~1bank-accounts/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 185. GET /bank-channel/cheque

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / Bank Channel |
| Postman name | GET - All Cheque |
| Purpose | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | แสดงข้อมูลบัญชีธนาคารทั้งหมดในบริษัทของคุณ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Bank-Account/paths/~1bank-accounts/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 186. GET /bank-channel/credit-card

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / Bank Channel |
| Postman name | GET - All Credit Card |
| Purpose | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | แสดงข้อมูลบัญชีธนาคารทั้งหมดในบริษัทของคุณ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Bank-Account/paths/~1bank-accounts/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 187. GET /bank-channel/petty-cash

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / Bank Channel |
| Postman name | GET - All Petty Cash |
| Purpose | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | แสดงข้อมูลบัญชีธนาคารทั้งหมดในบริษัทของคุณ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Bank-Account/paths/~1bank-accounts/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 188. GET /bank-channel/other-channels

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / Bank Channel |
| Postman name | GET - All Other Channel |
| Purpose | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | แสดงข้อมูลบัญชีธนาคารทั้งหมดในบริษัทของคุณ |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | https://developers.flowaccount.com/#tag/Bank-Account/paths/~1bank-accounts/get |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `none`; parse status: `none`

### 189. POST /bank-channel/bank-accounts

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / Bank Channel |
| Postman name | Create - Bank Account |
| Purpose | สร้างข้อมูลหรือเอกสารใหม่ (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | สร้างบัญชีธนาคาร สำหรับรับเงิน หรือ จ่ายเงิน ของบริษัทคุณ |
| Auth | Bearer token |
| Test class | mutating_write |
| Test note | เป็นการสร้าง/เปลี่ยนสถานะ/บันทึกข้อมูลใน sandbox |
| Source document | https://developers.flowaccount.com/#tag/Bank-Account/paths/~1bank-accounts/post |

Headers:

| Name | Sample | Description |
| --- | --- | --- |
| Content-Type | application/json |  |

Body mode: `raw`; parse status: `parsed_json`

Body fields:

| Field | Type | Sample | Meaning |
| --- | --- | --- | --- |
| bankAccountNumber | integer | [redacted] | เลขที่บัญชีธนาคาร |
| bankAccountName | string | บัญชีรับเงินบริษัท | ชื่อบัญชีธนาคาร |
| bankAccountType | integer | 1 | ประเภทบัญชีธนาคาร |
| bankBranch | string | เซ็นทรัลพระราม 2 | สาขาธนาคาร |
| bankId | integer | 1 | รหัสธนาคาร |

### 190. GET /settings/documents-remark

| Field | Value |
| --- | --- |
| Module | MyCompany |
| Folder path | MyCompany / Setting |
| Postman name | Get - Documents Remark |
| Purpose | ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า) |
| Meaning | ดึงรายการข้อมูลในหมวด ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า |
| Auth | Bearer token |
| Test class | safe_read |
| Test note | ทดสอบแบบ ไม่เปลี่ยนข้อมูล ได้ |
| Source document | Postman collection only |

Body mode: `none`; parse status: `none`

<!-- MERCURY GENERATED ACTION CATALOG START -->

## Generated Mercury Action Catalog

This section is generated from the sanitized built-in catalog. Each block binds
endpoint knowledge to one immutable Mercury action identity.

### 1. สร้างหรือส่งลิงก์แชร์เอกสาร (ใบเสร็จรับเงิน)

action_id: act_015b49bd41e4f80716d78ee4
method: POST
path: /receipts/sharedocument
capability: documents.receipt.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_015b49bd41e4f80716d78ee4

### 2. ลบข้อมูลหรือเอกสาร (ข้อมูล master ของสินค้า)

action_id: act_01b9797215c4f456f766a527
method: DELETE
path: /product-masters/{Product_Id}
capability: product_masters.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_01b9797215c4f456f766a527

### 3. ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_04c896400ea719532d300b17
method: GET
path: /bank-channel/credit-card
capability: bank_channels.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_04c896400ea719532d300b17

### 4. ดึงข้อมูลตามรหัส (เอกสารซื้อ/รับสินค้า)

action_id: act_05a6524a4c3ab0dcbeb90d40
method: GET
path: /purchases/{recordId}
capability: documents.purchase.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_05a6524a4c3ab0dcbeb90d40

### 5. แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี)

action_id: act_06e6b6f917c7007dcf6ff00f
method: PUT
path: /tax-invoices/{recordId}
capability: documents.invoice.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_06e6b6f917c7007dcf6ff00f

### 6. ดึงรายการข้อมูล (ใบวางบิล)

action_id: act_0b5ee93b2b65ccc79bc97f29
method: GET
path: /billing-notes
capability: documents.billing_note.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_0b5ee93b2b65ccc79bc97f29

### 7. แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสร็จรับเงิน)

action_id: act_0f01452a8f83a1b00219dbf8
method: PUT
path: /receipts/{recordId}
capability: documents.receipt.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_0f01452a8f83a1b00219dbf8

### 8. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_0f7d40067b256994236b1690
method: POST
path: /upgrade/cash-invoices/inline/with-payment
capability: documents.cash_invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_0f7d40067b256994236b1690

### 9. สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี)

action_id: act_0ffccd94c319126b3c1636ad
method: POST
path: /tax-invoices/sharedocument
capability: documents.invoice.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_0ffccd94c319126b3c1636ad

### 10. ดึงข้อมูลตามรหัส (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_107001c7e4eef81d38c499a1
method: GET
path: /tax-invoices/{recordId}
capability: documents.invoice.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_107001c7e4eef81d38c499a1

### 11. สร้างหรือส่งลิงก์แชร์เอกสาร (ค่าใช้จ่าย)

action_id: act_11f9469e693acc4d35526baf
method: POST
path: /expenses/sharedocument
capability: documents.expense.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_11f9469e693acc4d35526baf

### 12. ส่งเอกสารทางอีเมล (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_13c621b3be44c61edc8cb66e
method: POST
path: /cash-invoices/email-document
capability: documents.cash_invoice.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_13c621b3be44c61edc8cb66e

### 13. อัปโหลดไฟล์แนบให้เอกสาร (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_148aea8f383e8baac99e79b5
method: POST
path: /withholding-taxes/{recordId}/attachment
capability: documents.withholding_tax.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_148aea8f383e8baac99e79b5

### 14. สร้างข้อมูลหรือเอกสารใหม่ (ใบสั่งซื้อ)

action_id: act_153341d91049ad9657ea03be
method: POST
path: /purchases-orders/inline
capability: documents.purchase_order.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_153341d91049ad9657ea03be

### 15. ส่งเอกสารทางอีเมล (ค่าใช้จ่าย)

action_id: act_1734ca25c8aa5eba5449844b
method: POST
path: /expenses/email-document
capability: documents.expense.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_1734ca25c8aa5eba5449844b

### 16. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_1898616881be7e910ceacd46
method: POST
path: /tax-invoices/{recordId}/payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_1898616881be7e910ceacd46

### 17. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ค่าใช้จ่าย)

action_id: act_18e6662dc27063b55c7e7320
method: POST
path: /expenses/{recordId}/payment
capability: documents.expense.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_18e6662dc27063b55c7e7320

### 18. สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน)

action_id: act_1ab364842f3d0a038bcf7721
method: POST
path: /journal-entries/draft
capability: journal_entry.draft.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_1ab364842f3d0a038bcf7721

### 19. สร้างหรือส่งลิงก์แชร์เอกสาร (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_1b0823ff36149ce5c7af14a9
method: POST
path: /withholding-taxes/sharedocument
capability: documents.withholding_tax.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_1b0823ff36149ce5c7af14a9

### 20. แก้ไขข้อมูลหรือเอกสารเดิม (เอกสารซื้อ/รับสินค้า)

action_id: act_1fe22131cea7c84d9b1ee274
method: PUT
path: /purchases/{recordId}
capability: documents.purchase.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_1fe22131cea7c84d9b1ee274

### 21. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_21999f45d49a67a83e142c79
method: POST
path: /cash-invoices/inline
capability: documents.cash_invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_21999f45d49a67a83e142c79

### 22. สร้างหรือส่งลิงก์แชร์เอกสาร (ใบสั่งซื้อ)

action_id: act_21aad46b733c111783ff2f85
method: POST
path: /purchases-orders/sharedocument
capability: documents.purchase_order.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_21aad46b733c111783ff2f85

### 23. ส่งเอกสารทางอีเมล (ใบเสร็จรับเงิน)

action_id: act_22b6fb1673cf111b48b43c3a
method: POST
path: /receipts/email-document
capability: documents.receipt.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_22b6fb1673cf111b48b43c3a

### 24. ส่งเอกสารทางอีเมล (ใบวางบิล)

action_id: act_23f7e8939106da14bee6f401
method: POST
path: /billing-notes/email-document
capability: documents.billing_note.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_23f7e8939106da14bee6f401

### 25. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_2453abc64b4c8ad0bb8ccbad
method: POST
path: /expenses/inline/with-payment
capability: documents.expense.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_2453abc64b4c8ad0bb8ccbad

### 26. อัปโหลดไฟล์แนบให้เอกสาร (ค่าใช้จ่าย)

action_id: act_266b085b6f6b4140ce813c41
method: POST
path: /expenses/{recordId}/attachment
capability: documents.expense.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_266b085b6f6b4140ce813c41

### 27. เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_278319416de515baf42a99cb
method: POST
path: /tax-invoices/{recordId}/status/awaiting
capability: documents.invoice.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_278319416de515baf42a99cb

### 28. ดึงรายการข้อมูล (ผู้ติดต่อ ลูกค้า หรือผู้ขาย)

action_id: act_28a40ff500382918e7dc1ccb
method: GET
path: /contacts
capability: contacts.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_28a40ff500382918e7dc1ccb

### 29. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบสั่งซื้อ)

action_id: act_29d6903d6c23575f00934cf4
method: POST
path: /upgrade/purchases-orders
capability: documents.purchase_order.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_29d6903d6c23575f00934cf4

### 30. ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_2bdcfa22a54f608995a59721
method: POST
path: /withholding-taxes/{recordId}/status/void
capability: documents.withholding_tax.void
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_2bdcfa22a54f608995a59721

### 31. ขอ access token สำหรับเรียก FlowAccount API (การยืนยันตัวตนและขอ access token)

action_id: act_2fe33e97a18cbd6ee7cc0ac6
method: POST
path: /token
capability: auth.token.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_2fe33e97a18cbd6ee7cc0ac6

### 32. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี)

action_id: act_30c6bbf6133527126dce571b
method: POST
path: /tax-invoices/inline
capability: documents.invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_30c6bbf6133527126dce571b

### 33. แก้ไขข้อมูลหรือเอกสารเดิม (ข้อมูล master ของสินค้า)

action_id: act_339bea9f9e8d713818dedabf
method: PUT
path: /product-masters/{Product_Id}
capability: product_masters.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_339bea9f9e8d713818dedabf

### 34. อัปโหลดไฟล์แนบให้เอกสาร (ใบเสนอราคา)

action_id: act_35e9ff24728bbce9fb4f696f
method: POST
path: /quotations/{recordId}/attachment
capability: documents.quotation.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_35e9ff24728bbce9fb4f696f

### 35. ดึงข้อมูลตามรหัส (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_360aa086ea183d2c817bad9c
method: GET
path: /cash-invoices/{recordId}
capability: documents.cash_invoice.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_360aa086ea183d2c817bad9c

### 36. สร้างหรือส่งลิงก์แชร์เอกสาร (ใบวางบิล)

action_id: act_37367e9d686ae814b9b807df
method: POST
path: /billing-notes/sharedocument
capability: documents.billing_note.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_37367e9d686ae814b9b807df

### 37. อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_3742fc2fd1183e671f62f9bc
method: POST
path: /cash-invoices/{recordId}/attachment
capability: documents.cash_invoice.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_3742fc2fd1183e671f62f9bc

### 38. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี)

action_id: act_37a98ae379e691ce16f9886f
method: POST
path: /upgrade/tax-invoices/inline
capability: documents.invoice.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_37a98ae379e691ce16f9886f

### 39. แก้ไขข้อมูลหรือเอกสารเดิม (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_3962b47d771bc65b1d2411b7
method: PUT
path: /company/info
capability: company.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_3962b47d771bc65b1d2411b7

### 40. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_3bec2fe4f9b51ef5e820f382
method: POST
path: /upgrade/tax-invoices/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_3bec2fe4f9b51ef5e820f382

### 41. แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_3ee3b88d9c6321d5c2f8effd
method: PUT
path: /cash-invoices/{recordId}
capability: documents.cash_invoice.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_3ee3b88d9c6321d5c2f8effd

### 42. สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า)

action_id: act_447152b5811c38ecbebdf288
method: POST
path: /purchases/inline/with-payment
capability: documents.purchase.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_447152b5811c38ecbebdf288

### 43. ลบข้อมูลหรือเอกสาร (ใบเสนอราคา)

action_id: act_48ca679eaaa6ca7849a7dfd2
method: DELETE
path: /quotations/{recordId}
capability: documents.quotation.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_48ca679eaaa6ca7849a7dfd2

### 44. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบวางบิล)

action_id: act_49e392a500c231d4b608c630
method: POST
path: /upgrade/billing-notes
capability: documents.billing_note.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_49e392a500c231d4b608c630

### 45. สร้างข้อมูลหรือเอกสารใหม่ (ข้อมูล master ของสินค้า)

action_id: act_4af84354f00d8f9139b03c60
method: POST
path: /product-masters
capability: product_masters.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4af84354f00d8f9139b03c60

### 46. ดึงรายการข้อมูล (ใบเสร็จรับเงิน)

action_id: act_4b2ef85f09fdecb1d68a1767
method: GET
path: /receipts
capability: documents.receipt.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4b2ef85f09fdecb1d68a1767

### 47. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า)

action_id: act_4c94e28af476d3eed503fea6
method: POST
path: /purchases/{recordId}/payment
capability: documents.purchase.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4c94e28af476d3eed503fea6

### 48. สร้างหรือส่งลิงก์แชร์เอกสาร (เอกสารซื้อ/รับสินค้า)

action_id: act_4cd0c9c5fc28da8197ddf465
method: POST
path: /purchases/sharedocument
capability: documents.purchase.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4cd0c9c5fc28da8197ddf465

### 49. เปลี่ยนสถานะเอกสาร (ใบเสนอราคา)

action_id: act_4cf9e5802d57699dc0bf5140
method: POST
path: /quotations/{recordId}/status/awaiting
capability: documents.quotation.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4cf9e5802d57699dc0bf5140

### 50. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบวางบิล)

action_id: act_4da8a3fa8bb11003b2ca8a86
method: POST
path: /upgrade/billing-notes/inline
capability: documents.billing_note.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4da8a3fa8bb11003b2ca8a86

### 51. ดึงข้อมูลตามรหัส (ผู้ติดต่อ ลูกค้า หรือผู้ขาย)

action_id: act_4dd2db7e2bf8c3854764784c
method: GET
path: /contacts/{recordId}
capability: contacts.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4dd2db7e2bf8c3854764784c

### 52. ดึงรายการข้อมูล (ข้อมูล master ของสินค้า)

action_id: act_4e0873e60b60925fa10dd30f
method: GET
path: /product-masters
capability: product_masters.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_4e0873e60b60925fa10dd30f

### 53. ส่งเอกสารทางอีเมล (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_54de3cc569058db7879257c7
method: POST
path: /tax-invoices/email-document
capability: documents.invoice.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_54de3cc569058db7879257c7

### 54. ดึงรายการข้อมูล (ค่าใช้จ่าย)

action_id: act_56a89d42aecdf5c62f39f90c
method: GET
path: /expenses/categories/business
capability: documents.expense.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_56a89d42aecdf5c62f39f90c

### 55. สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ)

action_id: act_58cf6b5240d8d291d8411191
method: POST
path: /products
capability: products.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_58cf6b5240d8d291d8411191

### 56. อัปโหลดไฟล์แนบให้เอกสาร (เอกสารซื้อ/รับสินค้า)

action_id: act_5aab20fc3929e5baf0fe4e8b
method: POST
path: /purchases/{recordId}/attachment
capability: documents.purchase.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_5aab20fc3929e5baf0fe4e8b

### 57. สร้างข้อมูลหรือเอกสารใหม่ (ใบเสนอราคา)

action_id: act_5cb79cabb534698645c98529
method: POST
path: /quotations
capability: documents.quotation.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_5cb79cabb534698645c98529

### 58. สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน)

action_id: act_5d14ee0467a696707d4bc137
method: POST
path: /journal-entries/draft
capability: journal_entry.draft.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_5d14ee0467a696707d4bc137

### 59. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_5fb570b595f6dacd4217597b
method: POST
path: /cash-invoices
capability: documents.cash_invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_5fb570b595f6dacd4217597b

### 60. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี)

action_id: act_5feb2003ba48fb1081a5f45b
method: POST
path: /tax-invoices/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_5feb2003ba48fb1081a5f45b

### 61. ดึงรายการข้อมูล (ผังบัญชี)

action_id: act_64f6b1e4bd64aeb6f3de599c
method: GET
path: /chart-of-accounts/accounts
capability: journal.account_code.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_64f6b1e4bd64aeb6f3de599c

### 62. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (เอกสารซื้อ/รับสินค้า)

action_id: act_659e652a48409a44ac7f2448
method: POST
path: /upgrade/purchases
capability: documents.purchase.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_659e652a48409a44ac7f2448

### 63. เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี)

action_id: act_66861bf3c29f5f01bc725380
method: POST
path: /tax-invoices/{recordId}/status/awaiting
capability: documents.invoice.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_66861bf3c29f5f01bc725380

### 64. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน)

action_id: act_679a05d4fd31b8ca138615ea
method: POST
path: /upgrade/receipts/inline/with-payment
capability: documents.receipt.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_679a05d4fd31b8ca138615ea

### 65. ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_67c7e5bb3f5690b9bea36e5b
method: DELETE
path: /tax-invoices/{recordId}
capability: documents.invoice.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_67c7e5bb3f5690b9bea36e5b

### 66. สร้างข้อมูลหรือเอกสารใหม่ (ใบเสนอราคา)

action_id: act_696fe4b7416bcf60a0b8d2f5
method: POST
path: /quotations/inline
capability: documents.quotation.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_696fe4b7416bcf60a0b8d2f5

### 67. เปลี่ยนสถานะเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_6d0e0949b70d620079ed0e0a
method: POST
path: /cash-invoices/{recordId}/status/awaiting
capability: documents.cash_invoice.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_6d0e0949b70d620079ed0e0a

### 68. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_6fcaff07eec45b1481d04096
method: POST
path: /expenses/inline
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_6fcaff07eec45b1481d04096

### 69. ดึงรายการข้อมูล (ค่าใช้จ่าย)

action_id: act_72a24aa717ed06b4726f45a3
method: GET
path: /expenses/categories/accounting
capability: documents.expense.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_72a24aa717ed06b4726f45a3

### 70. สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_739443fc00796284a9dd76c9
method: POST
path: /cash-invoices/sharedocument
capability: documents.cash_invoice.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_739443fc00796284a9dd76c9

### 71. สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน)

action_id: act_755943502cd91b6a000cf556
method: POST
path: /journal-entries/approve
capability: journal_entry.approve
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_755943502cd91b6a000cf556

### 72. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี)

action_id: act_75f499d190e6fcb2701cc7f0
method: POST
path: /upgrade/tax-invoices
capability: documents.invoice.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_75f499d190e6fcb2701cc7f0

### 73. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า)

action_id: act_76ac380262b8f2ff8e4fbcdf
method: POST
path: /upgrade/purchases/inline/with-payment
capability: documents.purchase.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_76ac380262b8f2ff8e4fbcdf

### 74. เปลี่ยนสถานะเอกสาร (ใบสั่งซื้อ)

action_id: act_77262bf618b76e990b1a8094
method: POST
path: /purchases-orders/{recordId}/status/awaiting
capability: documents.purchase_order.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_77262bf618b76e990b1a8094

### 75. ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี)

action_id: act_772db41b0770b20007178a73
method: DELETE
path: /tax-invoices/{recordId}
capability: documents.invoice.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_772db41b0770b20007178a73

### 76. ดึงข้อมูลตามรหัส (สินค้าและบริการ)

action_id: act_789ac9768a77fb793ef62fbb
method: PUT
path: /products/{recordId}
capability: products.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_789ac9768a77fb793ef62fbb

### 77. แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_79f7d470dcf1e83a8590a52d
method: PUT
path: /tax-invoices/{recordId}
capability: documents.invoice.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_79f7d470dcf1e83a8590a52d

### 78. สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน)

action_id: act_79f899dad888e42e9d918b79
method: POST
path: /journal-entries/approve
capability: journal_entry.approve
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_79f899dad888e42e9d918b79

### 79. ดึงข้อมูลตามรหัส (ใบสั่งซื้อ)

action_id: act_7b2f02106ec029760ec08b67
method: GET
path: /purchases-orders/{recordId}
capability: documents.purchase_order.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_7b2f02106ec029760ec08b67

### 80. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_7c5e6edb4356c598e22ac81c
method: POST
path: /upgrade/cash-invoices/with-payment
capability: documents.cash_invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_7c5e6edb4356c598e22ac81c

### 81. ดึงรายการข้อมูล (เอกสารซื้อ/รับสินค้า)

action_id: act_7d0d020871eb89100358565f
method: GET
path: /purchases
capability: documents.purchase.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_7d0d020871eb89100358565f

### 82. แก้ไขข้อมูลหรือเอกสารเดิม (ใบวางบิล)

action_id: act_7f362a06d4157d27289be634
method: PUT
path: /billing-notes/{recordId}
capability: documents.billing_note.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_7f362a06d4157d27289be634

### 83. ส่งเอกสารทางอีเมล (ใบสั่งซื้อ)

action_id: act_7f80eb9c93db001bb2802a95
method: POST
path: /purchases-orders/email-document
capability: documents.purchase_order.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_7f80eb9c93db001bb2802a95

### 84. สร้างข้อมูลหรือเอกสารใหม่ (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_800c0161a6e01b41dc3a3158
method: POST
path: /withholding-taxes
capability: documents.withholding_tax.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_800c0161a6e01b41dc3a3158

### 85. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบเสร็จรับเงิน)

action_id: act_803c7059fd06d8e58f951e74
method: POST
path: /receipts/inline
capability: documents.receipt.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_803c7059fd06d8e58f951e74

### 86. สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน)

action_id: act_809067749a3dd20c2716ec86
method: POST
path: /journal-entries/draft
capability: journal_entry.draft.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_809067749a3dd20c2716ec86

### 87. อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี)

action_id: act_85e8148d7dc18e44da37cbd5
method: POST
path: /tax-invoices/{recordId}/attachment
capability: documents.invoice.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_85e8148d7dc18e44da37cbd5

### 88. ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_862e4ae305ce4f54acecec16
method: GET
path: /settings/documents-remark
capability: settings.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_862e4ae305ce4f54acecec16

### 89. สร้างข้อมูลหรือเอกสารใหม่ (ผู้ติดต่อ ลูกค้า หรือผู้ขาย)

action_id: act_87af9c03744a59e2a5f0c0fa
method: POST
path: /contacts
capability: contacts.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_87af9c03744a59e2a5f0c0fa

### 90. ลบข้อมูลหรือเอกสาร (สินค้าและบริการ)

action_id: act_8889da8ff6ce7f83f9114a00
method: DELETE
path: /products/{recordId}
capability: products.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_8889da8ff6ce7f83f9114a00

### 91. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_88fc96d59a472720363ee1c0
method: POST
path: /expenses/inline
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_88fc96d59a472720363ee1c0

### 92. ลบข้อมูลหรือเอกสาร (ใบเสร็จรับเงิน)

action_id: act_898b7e883e9a9796f89931e1
method: DELETE
path: /receipts/{recordId}
capability: documents.receipt.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_898b7e883e9a9796f89931e1

### 93. ดึงรายการข้อมูล (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_8b352bc607cbab6ca7820bc1
method: GET
path: /withholding-taxes
capability: documents.withholding_tax.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_8b352bc607cbab6ca7820bc1

### 94. เปลี่ยนสถานะเอกสาร (ใบวางบิล)

action_id: act_8dcdab472f92ba01819c52c3
method: POST
path: /billing-notes/{recordId}/status/awaiting
capability: documents.billing_note.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_8dcdab472f92ba01819c52c3

### 95. แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสนอราคา)

action_id: act_90819deb8f40a0a0420e0cd1
method: PUT
path: /quotations/{recordId}
capability: documents.quotation.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_90819deb8f40a0a0420e0cd1

### 96. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_909eea6771bda9710b82e7ae
method: POST
path: /expenses
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_909eea6771bda9710b82e7ae

### 97. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_91928855a9fc68619e7bb1ba
method: POST
path: /expenses/with-payment
capability: documents.expense.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_91928855a9fc68619e7bb1ba

### 98. ดึงข้อมูลตามรหัส (ผู้ติดต่อ ลูกค้า หรือผู้ขาย)

action_id: act_93258d0540643d4a271ca150
method: PUT
path: /contacts/{recordId}
capability: contacts.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_93258d0540643d4a271ca150

### 99. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน)

action_id: act_9400a6360e77c027c8da945b
method: POST
path: /receipts/{recordId}/payment
capability: documents.receipt.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_9400a6360e77c027c8da945b

### 100. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_94756a0f05ca50ebf5d7e577
method: POST
path: /expenses
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_94756a0f05ca50ebf5d7e577

### 101. ดึงรายการข้อมูล (ค่าใช้จ่าย)

action_id: act_96ed1617b6e4931cc4b585b2
method: GET
path: /expenses
capability: documents.expense.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_96ed1617b6e4931cc4b585b2

### 102. ดึงข้อมูลตามรหัส (ใบวางบิล)

action_id: act_97f9b66f25a476c32c038670
method: GET
path: /billing-notes/{recordId}
capability: documents.billing_note.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_97f9b66f25a476c32c038670

### 103. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (เอกสารซื้อ/รับสินค้า)

action_id: act_980c4e062dcd5cf653f7f859
method: POST
path: /upgrade/purchases/inline
capability: documents.purchase.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_980c4e062dcd5cf653f7f859

### 104. แก้ไขข้อมูลหรือเอกสารเดิม (เอกสารซื้อ/รับสินค้า)

action_id: act_9815a1384394dbbf7ae7c4e5
method: PUT
path: /purchases/{recordId}
capability: documents.purchase.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_9815a1384394dbbf7ae7c4e5

### 105. ดึงรายการข้อมูล (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_9a77991a6742a48906bbeca5
method: GET
path: /tax-invoices
capability: documents.invoice.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_9a77991a6742a48906bbeca5

### 106. แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสร็จรับเงิน)

action_id: act_9e6e9389e7688e247659eb91
method: PUT
path: /receipts/{recordId}
capability: documents.receipt.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_9e6e9389e7688e247659eb91

### 107. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี)

action_id: act_9f8278763c15f961c1eb9431
method: POST
path: /tax-invoices/inline/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_9f8278763c15f961c1eb9431

### 108. ดึงรายการข้อมูล (ใบกำกับภาษี)

action_id: act_a1103ce9f5dc42038e8174f8
method: GET
path: /tax-invoices
capability: documents.invoice.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a1103ce9f5dc42038e8174f8

### 109. ลบข้อมูลหรือเอกสาร (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_a15ba785fd21dafff07b99f2
method: DELETE
path: /withholding-taxes/{recordId}
capability: documents.withholding_tax.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a15ba785fd21dafff07b99f2

### 110. ดึงรายการข้อมูล (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_a20e76f20f4edc99d3c1988e
method: GET
path: /cash-invoices
capability: documents.cash_invoice.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a20e76f20f4edc99d3c1988e

### 111. สร้างหรือส่งลิงก์แชร์เอกสาร (ใบเสนอราคา)

action_id: act_a20eb8188f2588b921cb5a05
method: POST
path: /quotations/sharedocument
capability: documents.quotation.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a20eb8188f2588b921cb5a05

### 112. สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า)

action_id: act_a5deefd0147902d972503fac
method: POST
path: /purchases/with-payment
capability: documents.purchase.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a5deefd0147902d972503fac

### 113. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_a6cc8fd33bad6141ea45b08c
method: POST
path: /expenses/inline
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a6cc8fd33bad6141ea45b08c

### 114. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_a7a2a38441109e8cbf25f119
method: POST
path: /expenses
capability: documents.expense.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a7a2a38441109e8cbf25f119

### 115. ส่งเอกสารทางอีเมล (ใบกำกับภาษี)

action_id: act_a81d3c5245eae5ecf91cdd91
method: POST
path: /tax-invoices/email-document
capability: documents.invoice.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a81d3c5245eae5ecf91cdd91

### 116. สร้างข้อมูลหรือเอกสารใหม่ (ใบวางบิล)

action_id: act_a88353626405447f55289aab
method: POST
path: /billing-notes
capability: documents.billing_note.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a88353626405447f55289aab

### 117. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบเสร็จรับเงิน)

action_id: act_a8985c23c077ccac7592e62d
method: POST
path: /upgrade/receipts
capability: documents.receipt.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_a8985c23c077ccac7592e62d

### 118. สร้างข้อมูลหรือเอกสารใหม่ (ค่าใช้จ่าย)

action_id: act_acc6d5395d997360e88bb3e9
method: POST
path: /expenses/inline
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_acc6d5395d997360e88bb3e9

### 119. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี)

action_id: act_aeb20bbfc6ba8720bd0002af
method: POST
path: /upgrade/tax-invoices/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_aeb20bbfc6ba8720bd0002af

### 120. ดึงข้อมูลตามรหัส (ข้อมูล master ของสินค้า)

action_id: act_b059a4a38f0d165931402880
method: GET
path: /product-masters/{Product_Id}
capability: product_masters.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_b059a4a38f0d165931402880

### 121. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบสั่งซื้อ)

action_id: act_b0f795243061719592712f4c
method: POST
path: /upgrade/purchases-orders/inline
capability: documents.purchase_order.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_b0f795243061719592712f4c

### 122. สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ)

action_id: act_b1b39cad5da545edf01f8cd1
method: POST
path: /products
capability: products.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_b1b39cad5da545edf01f8cd1

### 123. อัปโหลดไฟล์แนบให้เอกสาร (ใบเสร็จรับเงิน)

action_id: act_b49aafeec268d9ec3fa04bb4
method: POST
path: /receipts/{recordId}/attachment
capability: documents.receipt.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_b49aafeec268d9ec3fa04bb4

### 124. อัปโหลดไฟล์แนบให้เอกสาร (ใบสั่งซื้อ)

action_id: act_b7546172c247dfe58f337952
method: POST
path: /purchases-orders/{recordId}/attachment
capability: documents.purchase_order.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_b7546172c247dfe58f337952

### 125. ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_b7d0ac30426331071666ef23
method: GET
path: /bank-channel/cheque
capability: bank_channels.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_b7d0ac30426331071666ef23

### 126. ดึงรายการข้อมูล (สินค้าและบริการ)

action_id: act_b9831f909a47a4364dba8d90
method: GET
path: /products
capability: products.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_b9831f909a47a4364dba8d90

### 127. ดึงข้อมูลตามรหัส (ค่าใช้จ่าย)

action_id: act_baee388f163a21f7489d440e
method: PUT
path: /expenses/{recordId}
capability: documents.expense.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_baee388f163a21f7489d440e

### 128. สร้างหรือส่งลิงก์แชร์เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_bbc7bb91d5e01505a88a674d
method: POST
path: /tax-invoices/sharedocument
capability: documents.invoice.share.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_bbc7bb91d5e01505a88a674d

### 129. สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า)

action_id: act_bc526a74e05377c18e6a4080
method: POST
path: /purchases
capability: documents.purchase.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_bc526a74e05377c18e6a4080

### 130. ลบข้อมูลหรือเอกสาร (ผู้ติดต่อ ลูกค้า หรือผู้ขาย)

action_id: act_bcf4c522b585a300d68130bd
method: DELETE
path: /contacts/{recordId}
capability: contacts.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_bcf4c522b585a300d68130bd

### 131. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_bd48b3ea2d48324cfb28a836
method: POST
path: /tax-invoices/inline/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_bd48b3ea2d48324cfb28a836

### 132. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (เอกสารซื้อ/รับสินค้า)

action_id: act_be4cf948c0c47c19a940ba97
method: POST
path: /upgrade/purchases/with-payment
capability: documents.purchase.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_be4cf948c0c47c19a940ba97

### 133. ดึงข้อมูลตามรหัส (ใบเสร็จรับเงิน)

action_id: act_be7440a613493602d93dde22
method: GET
path: /receipts/{recordId}
capability: documents.receipt.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_be7440a613493602d93dde22

### 134. ลบข้อมูลหรือเอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_bf1b36b47b8c434529e4ba8e
method: DELETE
path: /cash-invoices/{recordId}
capability: documents.cash_invoice.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_bf1b36b47b8c434529e4ba8e

### 135. แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_bf779c5430820d4a952cde59
method: PUT
path: /tax-invoices/{recordId}
capability: documents.invoice.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_bf779c5430820d4a952cde59

### 136. ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_c02a57cc5b5944d6d34f8d46
method: GET
path: /withholding-taxes/{recordId}
capability: documents.withholding_tax.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_c02a57cc5b5944d6d34f8d46

### 137. ดึงข้อมูลตามรหัส (ใบเสนอราคา)

action_id: act_c05f415fa8dd16d4a58761b5
method: GET
path: /quotations/{recordId}
capability: documents.quotation.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_c05f415fa8dd16d4a58761b5

### 138. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_c4691c7e52b89d9a8331d12b
method: POST
path: /tax-invoices/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_c4691c7e52b89d9a8331d12b

### 139. ดึงข้อมูลตามรหัส (สินค้าและบริการ)

action_id: act_c708318695455c9437da9f30
method: GET
path: /products/{recordId}
capability: products.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_c708318695455c9437da9f30

### 140. ลบข้อมูลหรือเอกสาร (เอกสารซื้อ/รับสินค้า)

action_id: act_c9dd5867a0807bbe7568b35c
method: DELETE
path: /purchases/{recordId}
capability: documents.purchase.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_c9dd5867a0807bbe7568b35c

### 141. แก้ไขข้อมูลหรือเอกสารเดิม (ใบวางบิล)

action_id: act_ca38ff27dc47596a6c20ff4b
method: PUT
path: /billing-notes/{recordId}
capability: documents.billing_note.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ca38ff27dc47596a6c20ff4b

### 142. ดึงข้อมูลตามรหัส (ค่าใช้จ่าย)

action_id: act_cac85416c88be9921b98da14
method: GET
path: /expenses/{recordId}
capability: documents.expense.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_cac85416c88be9921b98da14

### 143. ดึงรายการข้อมูล (ใบสั่งซื้อ)

action_id: act_cb9260b7b04a3b3483e15839
method: GET
path: /purchases-orders
capability: documents.purchase_order.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_cb9260b7b04a3b3483e15839

### 144. ลบข้อมูลหรือเอกสาร (ค่าใช้จ่าย)

action_id: act_cdf34b91bbe3b2a9995aa5f4
method: DELETE
path: /expenses/{recordId}
capability: documents.expense.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_cdf34b91bbe3b2a9995aa5f4

### 145. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_ce32407fa3e5af0f781f4b33
method: POST
path: /cash-invoices/{recordId}/payment
capability: documents.cash_invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ce32407fa3e5af0f781f4b33

### 146. สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน)

action_id: act_cf28f8d72dcf30724deae3f7
method: POST
path: /journal-entries/approve
capability: journal_entry.approve
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_cf28f8d72dcf30724deae3f7

### 147. ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_cfda9281facf4a5e94129392
method: GET
path: /company/info
capability: company.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_cfda9281facf4a5e94129392

### 148. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_d031d6b1371f6ed30503bd58
method: POST
path: /upgrade/tax-invoices
capability: documents.invoice.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d031d6b1371f6ed30503bd58

### 149. สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ)

action_id: act_d0505f5ffa949ab72ead7209
method: POST
path: /products
capability: products.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d0505f5ffa949ab72ead7209

### 150. สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน)

action_id: act_d1342389a0140d14a16ddb16
method: POST
path: /journal-entries/draft
capability: journal_entry.draft.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d1342389a0140d14a16ddb16

### 151. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_d266a8878b4d4575c01f400b
method: POST
path: /tax-invoices/inline
capability: documents.invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d266a8878b4d4575c01f400b

### 152. แก้ไขข้อมูลหรือเอกสารเดิม (ใบเสนอราคา)

action_id: act_d312aec02e05a77a1723f41a
method: PUT
path: /quotations/{recordId}
capability: documents.quotation.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d312aec02e05a77a1723f41a

### 153. ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_d49a0da15df4b6927f8be883
method: GET
path: /bank-channel/petty-cash
capability: bank_channels.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d49a0da15df4b6927f8be883

### 154. ส่งเอกสารทางอีเมล (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_d5216009b761b705ce679cfc
method: POST
path: /withholding-taxes/email-document
capability: documents.withholding_tax.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d5216009b761b705ce679cfc

### 155. สร้างข้อมูลหรือเอกสารใหม่ (สินค้าและบริการ)

action_id: act_d524205f39ce1d9ae2e50375
method: POST
path: /products
capability: products.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d524205f39ce1d9ae2e50375

### 156. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_d56ec4d55d7e7dc18b8fb1e0
method: POST
path: /upgrade/tax-invoices/inline/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d56ec4d55d7e7dc18b8fb1e0

### 157. สร้างข้อมูลหรือเอกสารใหม่ (เอกสารซื้อ/รับสินค้า)

action_id: act_d67d1320191616086ba42249
method: POST
path: /purchases/inline
capability: documents.purchase.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d67d1320191616086ba42249

### 158. แก้ไขข้อมูลหรือเอกสารเดิม (ใบสั่งซื้อ)

action_id: act_d7e966b8f28c4c9232d571da
method: PUT
path: /purchases-orders/{recordId}
capability: documents.purchase_order.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d7e966b8f28c4c9232d571da

### 159. ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_d83a97638940c28788602ba4
method: GET
path: /bank-accounts
capability: bank_accounts.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d83a97638940c28788602ba4

### 160. ดึงรายการข้อมูล (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_d939a9a9e72dc7c504d37625
method: GET
path: /bank-channel/other-channels
capability: bank_channels.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d939a9a9e72dc7c504d37625

### 161. แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี)

action_id: act_d9664c6e7d8815466012d007
method: PUT
path: /tax-invoices/{recordId}
capability: documents.invoice.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d9664c6e7d8815466012d007

### 162. สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน)

action_id: act_d9ab5afbee86cb46bbe87e38
method: POST
path: /journal-entries/approve
capability: journal_entry.approve
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_d9ab5afbee86cb46bbe87e38

### 163. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_da3925d52ff19d094ceef9b1
method: POST
path: /cash-invoices/inline/with-payment
capability: documents.cash_invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_da3925d52ff19d094ceef9b1

### 164. อัปโหลดไฟล์แนบให้เอกสาร (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_dc376e1d2668772965d102a1
method: POST
path: /tax-invoices/{recordId}/attachment
capability: documents.invoice.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_dc376e1d2668772965d102a1

### 165. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบเสร็จรับเงิน)

action_id: act_e005e75730d990ca135b57ab
method: POST
path: /upgrade/receipts/with-payment
capability: documents.receipt.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e005e75730d990ca135b57ab

### 166. สร้างข้อมูลหรือเอกสารใหม่ (ข้อมูลบริษัท ช่องทางการเงิน และการตั้งค่า)

action_id: act_e127ee2c326ef15ef8274b08
method: POST
path: /bank-channel/bank-accounts
capability: bank_channels.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e127ee2c326ef15ef8274b08

### 167. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_e382770c25c8c540b931db1f
method: POST
path: /tax-invoices
capability: documents.invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e382770c25c8c540b931db1f

### 168. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี)

action_id: act_e45d77e5944f4c124e771e7b
method: POST
path: /upgrade/tax-invoices/inline/with-payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e45d77e5944f4c124e771e7b

### 169. ลบข้อมูลหรือเอกสาร (ใบสั่งซื้อ)

action_id: act_e61a1fc8a6edef5256388d9c
method: DELETE
path: /purchases-orders/{recordId}
capability: documents.purchase_order.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e61a1fc8a6edef5256388d9c

### 170. สร้างข้อมูลหรือเอกสารใหม่ (ใบวางบิล)

action_id: act_e7495bfc924eb687d0ef08fb
method: POST
path: /billing-notes/inline
capability: documents.billing_note.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e7495bfc924eb687d0ef08fb

### 171. อัปโหลดไฟล์แนบให้เอกสาร (ใบวางบิล)

action_id: act_e8271aa8aaac485daa54e3e5
method: POST
path: /billing-notes/{recordId}/attachment
capability: documents.billing_note.attachment.upload
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e8271aa8aaac485daa54e3e5

### 172. สร้างรายการสมุดรายวันแบบร่าง (รายการสมุดรายวัน)

action_id: act_e977e2f65c9ab896d1dcfbc2
method: POST
path: /journal-entries/draft
capability: journal_entry.draft.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_e977e2f65c9ab896d1dcfbc2

### 173. สร้างหรืออนุมัติรายการสมุดรายวัน (รายการสมุดรายวัน)

action_id: act_ea49778952980c7b524ca85c
method: POST
path: /journal-entries/approve
capability: journal_entry.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ea49778952980c7b524ca85c

### 174. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_ec0bd9f4e788aa9aecd6829a
method: POST
path: /cash-invoices/with-payment
capability: documents.cash_invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ec0bd9f4e788aa9aecd6829a

### 175. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_ecce89b5aaeb4de731ccc842
method: POST
path: /upgrade/cash-invoices
capability: documents.cash_invoice.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ecce89b5aaeb4de731ccc842

### 176. บันทึกหรือเปลี่ยนสถานะการชำระเงิน (ใบกำกับภาษี)

action_id: act_ef2d305cc249fb18aab01c1d
method: POST
path: /tax-invoices/{recordId}/payment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ef2d305cc249fb18aab01c1d

### 177. เปลี่ยนสถานะเอกสาร (เอกสารซื้อ/รับสินค้า)

action_id: act_ef57c99d1a55d57a1e5687a9
method: POST
path: /purchases/{recordId}/status/awaiting
capability: documents.purchase.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ef57c99d1a55d57a1e5687a9

### 178. เปลี่ยนสถานะเอกสาร (ใบเสร็จรับเงิน)

action_id: act_efbe3f19d5f4746ad30ea0a7
method: POST
path: /tax-invoices/{recordId}/status/awaiting
capability: documents.invoice.status.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_efbe3f19d5f4746ad30ea0a7

### 179. สร้างข้อมูลหรือเอกสารใหม่ (ใบสั่งซื้อ)

action_id: act_f0be27a73d7e7b3bc7493643
method: POST
path: /purchases-orders
capability: documents.purchase_order.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f0be27a73d7e7b3bc7493643

### 180. ดึงรายการข้อมูล (ใบเสนอราคา)

action_id: act_f17b0f2f97104e569f1ffe30
method: GET
path: /quotations
capability: documents.quotation.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f17b0f2f97104e569f1ffe30

### 181. สร้างข้อมูลหรือเอกสารใหม่ (ใบกำกับภาษี)

action_id: act_f4276c49d652d470c92eec32
method: POST
path: /tax-invoices
capability: documents.invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f4276c49d652d470c92eec32

### 182. แก้ไขข้อมูลหรือเอกสารเดิม (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_f4731107fd349992739c124f
method: PUT
path: /cash-invoices/{recordId}
capability: documents.cash_invoice.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f4731107fd349992739c124f

### 183. ส่งเอกสารทางอีเมล (เอกสารซื้อ/รับสินค้า)

action_id: act_f4f0ede6b78f5a3b96a478de
method: POST
path: /purchases/email-document
capability: documents.purchase.email.send
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f4f0ede6b78f5a3b96a478de

### 184. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงินสด)

action_id: act_f6ec40d26e780e31a19bea45
method: POST
path: /upgrade/cash-invoices/inline
capability: documents.cash_invoice.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f6ec40d26e780e31a19bea45

### 185. ดึงข้อมูลตามรหัส (ใบกำกับภาษี)

action_id: act_f72d017ae88861d974cf48f2
method: GET
path: /tax-invoices/{recordId}
capability: documents.invoice.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f72d017ae88861d974cf48f2

### 186. ลบข้อมูลหรือเอกสาร (ใบวางบิล)

action_id: act_f92f5b67759ee55824733a47
method: DELETE
path: /billing-notes/{recordId}
capability: documents.billing_note.delete
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f92f5b67759ee55824733a47

### 187. แปลงหรือยกระดับเอกสารจากขั้นตอนก่อนหน้า (ใบกำกับภาษี/ใบเสร็จรับเงิน)

action_id: act_f9951bf6ba48dbb169e59c3b
method: POST
path: /upgrade/tax-invoices/inline
capability: documents.invoice.upgrade
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_f9951bf6ba48dbb169e59c3b

### 188. แก้ไขข้อมูลหรือเอกสารเดิม (ใบสั่งซื้อ)

action_id: act_fd033b1d6533ec1bdc4e9390
method: PUT
path: /purchases-orders/{recordId}
capability: documents.purchase_order.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_fd033b1d6533ec1bdc4e9390

### 189. เปลี่ยนสถานะเอกสาร (ค่าใช้จ่าย)

action_id: act_fda1a595fb442f399e60cec2
method: POST
path: /expenses/{recordId}/status/void
capability: documents.expense.void
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_fda1a595fb442f399e60cec2

### 190. ดึงข้อมูลตามรหัส (หนังสือรับรองหัก ณ ที่จ่าย)

action_id: act_ffec66b3803d3a8aa0eca84f
method: PUT
path: /withholding-taxes/{recordId}
capability: documents.withholding_tax.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/flowaccount/source#act_ffec66b3803d3a8aa0eca84f

<!-- MERCURY GENERATED ACTION CATALOG END -->
