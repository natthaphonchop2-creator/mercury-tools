---
title: FlowAccount Connector
doc_type: connector
jurisdiction: TH
connector: flowaccount
review_status: draft
source_uri: mercury://wiki/connectors/flowaccount
source_url: https://developers.flowaccount.com/
---

# FlowAccount Connector

FlowAccount ใช้เป็น connector สำหรับอ่านข้อมูลบริษัท ลูกค้า/ผู้ติดต่อ สินค้า
ใบแจ้งหนี้ ใบกำกับภาษี รายได้ ภาษีขาย ภาษีซื้อ และบริบทเอกสารบัญชี เพื่อให้
host AI เช่น Codex หรือ Cursor นำข้อมูลไปตอบพร้อม citation ได้

FlowAccount is the first Mercury accounting connector. v1 focuses on read-only
company context, contacts, products, invoice review, VAT summaries, and sandbox
document drafting.

Production mutation calls are blocked in v1. Sandbox writes require explicit
confirmation and should be recorded in an audit ledger.
