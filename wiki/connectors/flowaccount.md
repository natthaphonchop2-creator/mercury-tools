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

FlowAccount is the first Mercury accounting connector. Mercury indexes its
complete endpoint dictionary so the host AI can understand GET, POST, PUT, and
DELETE operations. Public contest mode executes only setup probes and declared
read capabilities for context, lookup, reconciliation, and reporting.

Setup validation may use a low-impact company/profile request to prove the
credential set. That validation check is not the final operating mode.
Production-changing POST/PUT/DELETE actions remain blocked in public contest
mode. The dictionary keeps their schemas and safety classes for explanation and
post-contest development; it does not enable those operations.

## Presets

- Grant type: `client_credentials`
- Scope: `flowaccount-api`
- Production API gateway: `https://openapi.flowaccount.com/v1`
- Production token URL: `https://openapi.flowaccount.com/token`
- Sandbox API gateway: `https://openapi.flowaccount.com/test`
- Sandbox token URL: `https://openapi.flowaccount.com/test/token`
- Official docs: `https://developers.flowaccount.com/`
- SDK repository: `https://github.com/flowaccount/flowaccount-openapi-sdk`
- Support/OpenChat: `https://line.me/ti/g2/Ph-aVSDpdApaBmeN152QvR6-5bPFMZXAISefhQ?utm_source=invitation&utm_medium=link_copy&utm_campaign=default`

## Endpoint Dictionary

Use `mercury://wiki/connectors/flowaccount-endpoint-dictionary` for the full
190-endpoint dictionary, module summary, test class meaning, request fields,
query parameters, body fields, and safety classes.
