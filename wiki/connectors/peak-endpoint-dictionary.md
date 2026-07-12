---
title: PEAK API Data Dictionary
doc_type: endpoint_dictionary
review_status: reviewed
jurisdiction: TH
connector: peak
source_uri: mercury://wiki/connectors/peak-endpoint-dictionary
source_url: https://developers.peakaccount.com/reference/peak-open-api
source_path: wiki/connectors/peak-endpoint-dictionary.md
metadata:
  generated_from: supplied PEAK API documentation and sanitized Postman collection
  production_api_gateway: https://api.peakaccount.com/api/v1
  uat_api_gateway: https://peakengineapidev.azurewebsites.net/api/v1
  auth_method: hmac_sha1_client_token
  required_credential_fields:
    - connect_id
    - connect_key
    - application_code
    - user_token
---

> Mercury endpoint knowledge page. This page stores endpoint metadata, capability
> routing, field hints, and safety classes only. It must not store ConnectId,
> ConnectKey, ApplicationCode, UserToken, ClientToken, API keys, tax IDs, emails,
> bearer tokens, customer data, or accounting transaction payloads.

> Mercury Cloud serves this catalog and related knowledge read-only. The Mercury
> local MCP may execute a cataloged GET or POST from the user's machine after
> local credential setup, schema validation, risk classification, and the
> required preview/confirmation steps. It never exposes an arbitrary-URL HTTP
> proxy.

# PEAK API Data Dictionary

Generated from the supplied PEAK Postman collection and PEAK Open API setup docs.
Use this page to map user requests to the correct PEAK endpoint, capability, and
input checklist before calling connector tools.

## Coverage Summary

| Metric | Value |
| --- | --- |
| Total endpoints | 64 |
| Methods | GET=20, POST=44 |
| Connector | `peak` |
| Production base URL | `https://api.peakaccount.com/api/v1` |
| UAT base URL | `https://peakengineapidev.azurewebsites.net/api/v1` |
| Auth setup | `POST /clienttoken`, then use ClientToken + UserToken headers |

## Routing Rule

When a user asks Mercury to work with PEAK:

1. Identify the accounting object: contact, product, service, quotation, invoice,
   receipt, expense, purchase order, billing note, credit note, daily journal,
   payment method, tag, file, or invitation.
2. Identify the action: get/list, create, edit/update, approve, paidpayment,
   void/voidpayment, create from source document, attach, tag, or invite.
3. Match the endpoint row below and require the matching capability in the flow.
4. For a POST mutation, create an immutable preview, show the sanitized request
   shape and risk, collect the required confirmation, then dispatch locally.

## Intent Keywords

| User intent keywords | Preferred capability | Endpoint family |
| --- | --- | --- |
| ตั้งค่า PEAK, เชื่อม PEAK, token, ClientToken, UserToken | `auth.client_token.create` | `/clienttoken`, `/user` |
| ลูกค้า, ผู้ติดต่อ, supplier, vendor, contact | `contacts.*` | `/contacts` |
| สินค้า, product, stock item | `products.*` | `/products` |
| บริการ, service, fee item | `services.*` | `/services` |
| ช่องทางรับเงิน, payment method, bank, cash | `payment_methods.*` | `/paymentmethods` |
| ใบเสนอราคา, quotation, quote | `documents.quotation.*` | `/quotations` |
| ใบแจ้งหนี้, invoice, tax invoice, approve invoice | `documents.invoice.*` | `/invoices` |
| รับชำระ invoice, paidpayment, payment receipt | `documents.invoice.payment.create` | `/invoices/paidpayment` |
| ยกเลิกรับชำระ invoice, void payment | `documents.invoice.payment.void` | `/invoices/voidpayment` |
| ใบเสร็จ, receipt, create receipt from invoice | `documents.receipt.*` | `/receipts` |
| ค่าใช้จ่าย, expense, paid expense | `documents.expense.*` | `/expenses` |
| ใบสั่งซื้อ, purchase order, PO | `documents.purchase_order.*` | `/purchaseorders` |
| ใบวางบิล, billing note | `documents.billing_note.*` | `/billingnotes` |
| ใบวางบิลรายจ่าย, billing note expense | `documents.billing_note.*` | `/billingnotesexpenses` |
| ใบลดหนี้, credit note | `documents.credit_note.*` | `/creditnotes` |
| ใบลดหนี้รายจ่าย, expense credit note | `documents.credit_note_expense.*` | `/creditnotesExpenses` |
| สมุดรายวัน, daily journal, journal entry | `daily_journal.*` | `/dailyjournals` |
| ผังบัญชี, account code, chart of accounts | `journal.account_code.read` | `/dailyjournals/accountcode` |
| tag, จัดหมวด, label | `tags.*` | `/tags` |
| invite, เพิ่มผู้ใช้ | `invitation.create` | `/invitation` |

## Safety Classes

| Safety class | Meaning |
| --- | --- |
| `setup_auth` | Authentication/setup probe. Does not create accounting documents. |
| `safe_read` | GET endpoint for lookup/list/preflight. |
| `master_write` | POST creates or updates master data; local preview and confirmation are required. |
| `document_write` | POST creates or updates accounting documents; local preview and confirmation are required. |
| `payment_write` | POST records payment or paidpayment; Tier 2 confirmation is required. |
| `status_write` | POST changes status, approval, void, or voidpayment; Tier 2 confirmation is required. |
| `journal_write` | POST creates journal entries; local preview and confirmation are required. |
| `utility_write` | POST creates tags, invitations, or supporting records; risk-based confirmation is required. |

## Common Field Hints

| Object | Required or common fields to ask for before POST |
| --- | --- |
| Contact | `name`, `type`, optional `taxNumber`, `branchCode`, address, phone, email, contact person, bank account fields when needed |
| Product | `name`, `purchaseValue`, `purchaseVattype`, `sellValue`, `sellVatType`, optional account code and opening balance fields |
| Service | `name`, `purchaseValue`, `purchaseVattype`, `sellValue`, `sellVatType`, optional purchase/sell account and description |
| Invoice/Quotation/Receipt/Expense/PO | `issuedDate`, `dueDate`, `contactId` or contact code, products lines, quantity, price, `vatType`, tags |
| Payment | transaction id, payment date, payment method id or payment method object, amount, optional withholding tax and note |
| Credit note | source transaction type/id, reason type/id, reason description, returned goods flag, line items, issued date |
| Daily journal | issued date, journal type id, description, journal entries with account code, debit, credit |

## Endpoint Index

| # | Module | Method | Path | Postman name | Capability | Safety |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Authentication | POST | `/clienttoken` | Create Client Token | `auth.client_token.create` | setup_auth |
| 2 | Contacts | GET | `/contacts?code=C00001` | Get Peak Contact | `contacts.get` | safe_read |
| 3 | Contacts | POST | `/contacts` | Post Peak Contact | `contacts.create` | master_write |
| 4 | Contacts | POST | `/contacts` | Post Peak Contact /w Bank | `contacts.create` | master_write |
| 5 | Contacts | POST | `/contacts/edit` | Edit Peak Contact | `contacts.update` | master_write |
| 6 | Daily Journal | GET | `/dailyjournals/` | Get Peak DailyJournal | `daily_journal.get` | safe_read |
| 7 | Daily Journal | POST | `/dailyjournals` | Post Peak DailyJournal | `daily_journal.create` | journal_write |
| 8 | Daily Journal | GET | `/dailyjournals/accountcode` | Get Peak AccountCode | `journal.account_code.read` | safe_read |
| 9 | Expenses | GET | `/expenses?code=EXP1200006` | Get Peak Expense | `documents.expense.get` | safe_read |
| 10 | Expenses | GET | `/expenses/list?limit=20&page=1&status=3` | Get Peak Expense List | `documents.expense.list` | safe_read |
| 11 | Expenses | POST | `/expenses` | Post Peak Expense | `documents.expense.create` | document_write |
| 12 | Expenses | POST | `/expenses` | Edit Peak Expense | `documents.expense.update` | document_write |
| 13 | Expenses | POST | `/expenses/allinone` | Post Peak Expense All In One | `documents.expense.create` | document_write |
| 14 | Expenses | POST | `/expenses/paidpayment` | Peak Expense Payment | `documents.expense.payment.create` | payment_write |
| 15 | Expenses | POST | `/expenses/createbypurchaseorder` | ByPurchaseOrder Peak Expense | `documents.expense.create` | document_write |
| 16 | Invoices | POST | `/invoices` | Post Peak Invoice | `documents.invoice.create` | document_write |
| 17 | Invoices | POST | `/invoices` | Post Peak Invoice /w Fee | `documents.invoice.create` | document_write |
| 18 | Invoices | POST | `/invoices/edit` | Edit Peak Invoice | `documents.invoice.update` | document_write |
| 19 | Invoices | GET | `/invoices?code=IV-201906010` | Get Peak Invoice | `documents.invoice.get` | safe_read |
| 20 | Invoices | GET | `/invoices/list?limit=20&page=1&status=3` | Get Peak Invoice List | `documents.invoice.list` | safe_read |
| 21 | Invoices | POST | `/invoices/voidpayment` | Void Peak Invoice Payment | `documents.invoice.payment.void` | status_write |
| 22 | Invoices | POST | `/invoices/approve` | Approve Peak Invoice | `documents.invoice.approve` | status_write |
| 23 | Invoices | POST | `/invoices/paidpayment` | Peak Invoice Payment | `documents.invoice.payment.create` | payment_write |
| 24 | Invoices | POST | `/invoices/paidpaymentallinone` | Peak Invoice Payment All In One | `documents.invoice.payment.create` | payment_write |
| 25 | Payment Methods | POST | `/paymentmethods` | Post Peak PaymentMethod | `payment_methods.create` | master_write |
| 26 | Payment Methods | GET | `/paymentmethods` | Get Peak PaymentMethod | `payment_methods.list` | safe_read |
| 27 | Products | POST | `/products` | Post Peak Product | `products.create` | master_write |
| 28 | Products | POST | `/products/edit` | Edit Peak Product | `products.update` | master_write |
| 29 | Products | GET | `/products?code=P0001` | Get Peak Product | `products.get` | safe_read |
| 30 | Products | POST | `/products` | Post Peak Product /w Account | `products.create` | master_write |
| 31 | Purchase Orders | GET | `/purchaseorders` | Get Peak Purchase Order | `documents.purchase_order.get` | safe_read |
| 32 | Purchase Orders | GET | `/purchaseorders/list?limit=20&page=1&status=0` | Get Peak Purchase Order List | `documents.purchase_order.list` | safe_read |
| 33 | Purchase Orders | POST | `/purchaseorders` | Post Peak Purchase Order | `documents.purchase_order.create` | document_write |
| 34 | Quotations | POST | `/quotations` | Post Peak Quotation | `documents.quotation.create` | document_write |
| 35 | Quotations | POST | `/quotations/allinone` | Post Peak Quotation All In One | `documents.quotation.create` | document_write |
| 36 | Quotations | POST | `/quotations/edit` | Edit Peak Quotation | `documents.quotation.update` | document_write |
| 37 | Quotations | POST | `/quotations/void` | Void Peak Quotation | `documents.quotation.void` | status_write |
| 38 | Quotations | GET | `/quotations` | Get Peak Quotation | `documents.quotation.get` | safe_read |
| 39 | Quotations | GET | `/quotations/list?limit=20&page=1&status=0` | Get Peak Quotation List | `documents.quotation.list` | safe_read |
| 40 | Receipts | POST | `/receipts` | Post Peak Receipt | `documents.receipt.create` | document_write |
| 41 | Receipts | POST | `/receipts/allinone` | Post Peak Receipt All In One | `documents.receipt.create` | document_write |
| 42 | Receipts | POST | `/receipts/edit` | Edit Peak Receipt | `documents.receipt.update` | document_write |
| 43 | Receipts | GET | `/receipts?code=RT-20190600014` | Get Peak Receipt | `documents.receipt.get` | safe_read |
| 44 | Receipts | GET | `/receipts/list?limit=20&page=1&status=0` | Get Peak Receipt List | `documents.receipt.list` | safe_read |
| 45 | Receipts | POST | `/receipts/createbyinvoice` | ByInvoice Peak Receipt | `documents.receipt.create_from_invoice` | document_write |
| 46 | Receipts | POST | `/receipts/void` | Void Peak Receipt | `documents.receipt.void` | status_write |
| 47 | Receipts | POST | `/receipts` | Post Peak Receipt With Cheque | `documents.receipt.create` | document_write |
| 48 | Receipts | POST | `/receipts` | Post Peak Receipt /w Fee | `documents.receipt.create` | document_write |
| 49 | Services | POST | `/services` | Post Peak Service | `services.create` | master_write |
| 50 | Services | POST | `/services` | Edit Peak Service | `services.update` | master_write |
| 51 | Services | GET | `/services` | Get Peak Service | `services.list` | safe_read |
| 52 | Services | POST | `/services` | Post Peak Service /w Account | `services.create` | master_write |
| 53 | Billing Note Expenses | POST | `/billingnotesexpenses` | Post Peak Billing Note Expense | `documents.billing_note.create` | document_write |
| 54 | Billing Note Expenses | GET | `/billingnotesexpenses` | Get Peak Billing Note Expense | `documents.billing_note.get` | safe_read |
| 55 | Invitation | POST | `/invitation` | Post Invitation | `invitation.create` | utility_write |
| 56 | Credit Notes | POST | `/creditnotes` | Post Peak Credit Note | `documents.credit_note.create` | document_write |
| 57 | Credit Notes | GET | `/creditnotes?code=CN-20180700015` | Get Peak Credit Note | `documents.credit_note.get` | safe_read |
| 58 | Credit Notes | POST | `/creditnotes` | Post Peak Credit Note Full Credit | `documents.credit_note.create` | document_write |
| 59 | Credit Note Expenses | POST | `/creditnotesExpenses` | Post Peak Credit Note Expense | `documents.credit_note_expense.create` | document_write |
| 60 | Credit Note Expenses | GET | `/creditnotesExpenses` | Get Peak Credit Note Expense | `documents.credit_note_expense.get` | safe_read |
| 61 | Billing Notes | POST | `/billingnotes` | Post Peak Billing Note | `documents.billing_note.create` | document_write |
| 62 | Billing Notes | GET | `/billingnotes` | Get Peak Billing Note | `documents.billing_note.get` | safe_read |
| 63 | Tags | POST | `/tags` | Post Peak Tag | `tags.create` | utility_write |
| 64 | Tags | POST | `/tags/remove` | Remove Peak Tag | `tags.remove` | utility_write |

## Connector Credential Fields

Mercury setup must collect these through a secure credential path only:

- `connect_id`
- `connect_key`
- `application_code`
- `user_token`

Do not ask users to paste the raw values into ordinary chat.

## Response Handling

PEAK may return HTTP 200 while the response body has a failed `resCode`. Mercury
must inspect body-level `resCode`, `resDesc`, and the expected response node for
each endpoint. Treat missing `resCode=200` as a failed operation and report only
sanitized status and error context.

<!-- MERCURY GENERATED ACTION CATALOG START -->

## Generated Mercury Action Catalog

This section is generated from the sanitized built-in catalog. Each block binds
endpoint knowledge to one immutable Mercury action identity.

### 1. Peak Invoice Payment All In One

action_id: act_0114957a3922dc0f46a99907
method: POST
path: /invoices/paidpaymentallinone
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_0114957a3922dc0f46a99907

### 2. Peak Expense Payment

action_id: act_0b47bbf8bcc51ceada745adc
method: POST
path: /expenses/paidpayment
capability: documents.expense.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_0b47bbf8bcc51ceada745adc

### 3. Post Peak Product /w Account

action_id: act_0dcc727c86ca16631816ed12
method: POST
path: /products
capability: products.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_0dcc727c86ca16631816ed12

### 4. Void Peak Receipt

action_id: act_260661133c4f8b1fbbeeadac
method: POST
path: /receipts/void
capability: documents.receipt.void
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_260661133c4f8b1fbbeeadac

### 5. Void Peak Invoice Payment

action_id: act_308b782f3c276b9a0dd818d3
method: POST
path: /invoices/voidpayment
capability: documents.invoice.payment.void
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_308b782f3c276b9a0dd818d3

### 6. Post Peak Credit Note

action_id: act_3863cfee28a4ce0ab4f65ab9
method: POST
path: /creditnotes
capability: documents.credit_note.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_3863cfee28a4ce0ab4f65ab9

### 7. Get Peak DailyJournal

action_id: act_3c4cc6c07a8ffa418232c909
method: GET
path: /dailyjournals/
capability: daily_journal.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_3c4cc6c07a8ffa418232c909

### 8. Post Peak Receipt All In One

action_id: act_3f63028cae55eb90783e7be5
method: POST
path: /receipts/allinone
capability: documents.receipt.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_3f63028cae55eb90783e7be5

### 9. Post Peak Receipt

action_id: act_402bb61694a153488b29ab33
method: POST
path: /receipts
capability: documents.receipt.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_402bb61694a153488b29ab33

### 10. Post Peak Quotation All In One

action_id: act_41394a4923b15a4c53cf644d
method: POST
path: /quotations/allinone
capability: documents.quotation.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_41394a4923b15a4c53cf644d

### 11. Post Peak Invoice /w Fee

action_id: act_46c9288523202918dd477367
method: POST
path: /invoices
capability: documents.invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_46c9288523202918dd477367

### 12. Post Peak Credit Note Full Credit

action_id: act_46db1343db1c07b58d37cf3e
method: POST
path: /creditnotes
capability: documents.credit_note.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_46db1343db1c07b58d37cf3e

### 13. Get Peak PaymentMethod

action_id: act_4ec1db144d0b79dbdc133236
method: GET
path: /paymentmethods
capability: payment_methods.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_4ec1db144d0b79dbdc133236

### 14. Post Peak Product

action_id: act_5ab58230233fb4cff85d36ff
method: POST
path: /products
capability: products.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_5ab58230233fb4cff85d36ff

### 15. Edit Peak Expense

action_id: act_5ccd2472984a6e8007989784
method: POST
path: /expenses
capability: documents.expense.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_5ccd2472984a6e8007989784

### 16. Peak Invoice Payment

action_id: act_5d022ddce93542bed3ba3c55
method: POST
path: /invoices/paidpayment
capability: documents.invoice.payment.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_5d022ddce93542bed3ba3c55

### 17. Edit Peak Contact

action_id: act_6aa9e1aeb5c70e874a3a2b19
method: POST
path: /contacts/edit
capability: contacts.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_6aa9e1aeb5c70e874a3a2b19

### 18. Get Peak Purchase Order List

action_id: act_6e49e877eb897677e2eb5b15
method: GET
path: /purchaseorders/list
capability: documents.purchase_order.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_6e49e877eb897677e2eb5b15

### 19. Post Peak DailyJournal

action_id: act_6eca4e9dd9b68da50839acc2
method: POST
path: /dailyjournals
capability: daily_journal.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_6eca4e9dd9b68da50839acc2

### 20. Get Peak Receipt

action_id: act_7284bd508c7d69b2062caf86
method: GET
path: /receipts
capability: documents.receipt.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_7284bd508c7d69b2062caf86

### 21. Get Peak Product

action_id: act_7771eabbe70dd3c4cb2db76f
method: GET
path: /products
capability: products.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_7771eabbe70dd3c4cb2db76f

### 22. Get Peak Quotation

action_id: act_86726c451fa0efd2550e9991
method: GET
path: /quotations
capability: documents.quotation.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_86726c451fa0efd2550e9991

### 23. Get Peak Billing Note

action_id: act_88d77abde2055b5bc7d1dd13
method: GET
path: /billingnotes
capability: documents.billing_note.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_88d77abde2055b5bc7d1dd13

### 24. Post Peak Expense All In One

action_id: act_8db2ad5402e03ff26f75d826
method: POST
path: /expenses/allinone
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_8db2ad5402e03ff26f75d826

### 25. Post Peak Tag

action_id: act_8fcd208ff82ccc6429492e75
method: POST
path: /tags
capability: tags.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_8fcd208ff82ccc6429492e75

### 26. Get Peak Expense List

action_id: act_926b428b4b14729473e0e0c0
method: GET
path: /expenses/list
capability: documents.expense.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_926b428b4b14729473e0e0c0

### 27. Post Peak Invoice

action_id: act_92c9d694f30bc103781a62ee
method: POST
path: /invoices
capability: documents.invoice.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_92c9d694f30bc103781a62ee

### 28. Post Peak Purchase Order

action_id: act_93a09cc8c33bbcb6f9ac3679
method: POST
path: /purchaseorders
capability: documents.purchase_order.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_93a09cc8c33bbcb6f9ac3679

### 29. Edit Peak Invoice

action_id: act_93fa566f5267d83e04faba9e
method: POST
path: /invoices/edit
capability: documents.invoice.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_93fa566f5267d83e04faba9e

### 30. Post Peak Expense

action_id: act_97010c84618ec71bd7944a7b
method: POST
path: /expenses
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_97010c84618ec71bd7944a7b

### 31. ByInvoice Peak Receipt

action_id: act_97c2fd64b9ffb9e25179c49d
method: POST
path: /receipts/createbyinvoice
capability: documents.receipt.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_97c2fd64b9ffb9e25179c49d

### 32. Void Peak Quotation

action_id: act_987d7da49b5c2152305f4fcd
method: POST
path: /quotations/void
capability: documents.quotation.void
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_987d7da49b5c2152305f4fcd

### 33. Get Peak Expense

action_id: act_9e8c8d5a1fe33e4a9c28cef1
method: GET
path: /expenses
capability: documents.expense.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_9e8c8d5a1fe33e4a9c28cef1

### 34. Edit Peak Service

action_id: act_a739859a3ff8941fa0a6c25c
method: POST
path: /services
capability: services.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_a739859a3ff8941fa0a6c25c

### 35. Post Peak Billing Note

action_id: act_a7f3097c2a8e59319c0dd716
method: POST
path: /billingnotes
capability: documents.billing_note.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_a7f3097c2a8e59319c0dd716

### 36. Get Peak Contact

action_id: act_a854ec32e2b7ac849f11deeb
method: GET
path: /contacts
capability: contacts.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_a854ec32e2b7ac849f11deeb

### 37. Post Peak Billing Note Expense

action_id: act_a86f5ab29488d2109529cfdb
method: POST
path: /billingnotesexpenses
capability: documents.billing_note_expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_a86f5ab29488d2109529cfdb

### 38. Post Peak Credit Note Expense

action_id: act_ab53b5325bebc62984f4516b
method: POST
path: /creditnotesExpenses
capability: documents.credit_note_expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_ab53b5325bebc62984f4516b

### 39. Post Invitation

action_id: act_ae4c4adac955fc15bf85865f
method: POST
path: /invitation
capability: invitation.create
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_ae4c4adac955fc15bf85865f

### 40. Get Peak Credit Note

action_id: act_b76683cad7af033640036e7c
method: GET
path: /creditnotes
capability: documents.credit_note.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_b76683cad7af033640036e7c

### 41. ByPurchaseOrder Peak Expense

action_id: act_baa0f8fc773de48ffe631499
method: POST
path: /expenses/createbypurchaseorder
capability: documents.expense.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_baa0f8fc773de48ffe631499

### 42. Get Peak Invoice

action_id: act_bd70b00e0df3d157d22fb1a1
method: GET
path: /invoices
capability: documents.invoice.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_bd70b00e0df3d157d22fb1a1

### 43. Get Peak Purchase Order

action_id: act_c022b664b9954b24789e0d5d
method: GET
path: /purchaseorders
capability: documents.purchase_order.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_c022b664b9954b24789e0d5d

### 44. Post Peak Receipt /w Fee

action_id: act_c0fb09d59ce450c3819a9521
method: POST
path: /receipts
capability: documents.receipt.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_c0fb09d59ce450c3819a9521

### 45. Edit Peak Receipt

action_id: act_c2feb6d508c7ad482c11a468
method: POST
path: /receipts/edit
capability: documents.receipt.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_c2feb6d508c7ad482c11a468

### 46. Post Peak Receipt With Cheque

action_id: act_c4af9d26172dd61c9a9e4432
method: POST
path: /receipts
capability: documents.receipt.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_c4af9d26172dd61c9a9e4432

### 47. Remove Peak Tag

action_id: act_c5b0baafb0d76261e443457c
method: POST
path: /tags/remove
capability: tags.delete
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_c5b0baafb0d76261e443457c

### 48. Post Peak Service

action_id: act_cbe47f843982380a2f6e3bf5
method: POST
path: /services
capability: services.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_cbe47f843982380a2f6e3bf5

### 49. Get Peak Invoice List

action_id: act_ccc3343c893ddeb9a4d4a207
method: GET
path: /invoices/list
capability: documents.invoice.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_ccc3343c893ddeb9a4d4a207

### 50. Edit Peak Product

action_id: act_cfea2f8629ca1587e64c87cd
method: POST
path: /products/edit
capability: products.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_cfea2f8629ca1587e64c87cd

### 51. Post Peak Contact

action_id: act_d1034a254d9cc65822cc83c9
method: POST
path: /contacts
capability: contacts.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_d1034a254d9cc65822cc83c9

### 52. Post Peak Contact /w Bank

action_id: act_d14adea3e772e3f406ba790e
method: POST
path: /contacts
capability: contacts.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_d14adea3e772e3f406ba790e

### 53. Get Peak Billing Note Expense

action_id: act_d1aa008c401d40255ca0738e
method: GET
path: /billingnotesexpenses
capability: documents.billing_note_expense.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_d1aa008c401d40255ca0738e

### 54. Get Peak Service

action_id: act_d1d6ef93ff84247b17baa719
method: GET
path: /services
capability: services.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_d1d6ef93ff84247b17baa719

### 55. Post Peak PaymentMethod

action_id: act_d34838c6775859ea15830d5d
method: POST
path: /paymentmethods
capability: payment_methods.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_d34838c6775859ea15830d5d

### 56. Get Peak Receipt List

action_id: act_dbb5a5862c26dec57ef1a220
method: GET
path: /receipts/list
capability: documents.receipt.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_dbb5a5862c26dec57ef1a220

### 57. Get Peak AccountCode

action_id: act_e370123e4fd83afb6490cdf9
method: GET
path: /dailyjournals/accountcode
capability: journal.account_code.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_e370123e4fd83afb6490cdf9

### 58. Post Peak Quotation

action_id: act_e4744c138983b7fd8fd9b402
method: POST
path: /quotations
capability: documents.quotation.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_e4744c138983b7fd8fd9b402

### 59. Get Peak Credit Note Expense

action_id: act_e5ebcd779f494d89d8e53d2e
method: GET
path: /creditnotesExpenses
capability: documents.credit_note_expense.get
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_e5ebcd779f494d89d8e53d2e

### 60. Edit Peak Quotation

action_id: act_e705bc1495702ad0914e3494
method: POST
path: /quotations/edit
capability: documents.quotation.update
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_e705bc1495702ad0914e3494

### 61. Post Peak Service /w Account

action_id: act_eaadd03ba498084a17d266fa
method: POST
path: /services
capability: services.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_eaadd03ba498084a17d266fa

### 62. Get Peak Quotation List

action_id: act_ed12fe4f9e2c080243869ffa
method: GET
path: /quotations/list
capability: documents.quotation.list
risk_tier: 0
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_ed12fe4f9e2c080243869ffa

### 63. Approve Peak Invoice

action_id: act_ef839e82267a176b9fc738b8
method: POST
path: /invoices/approve
capability: documents.invoice.approve
risk_tier: 2
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_ef839e82267a176b9fc738b8

### 64. Create Client Token

action_id: act_f087bdd426544f61eb91387a
method: POST
path: /clienttoken
capability: auth.client_token.create
risk_tier: 1
confidence: example_derived
source_citation: mercury://catalog/global/peak/source#act_f087bdd426544f61eb91387a

<!-- MERCURY GENERATED ACTION CATALOG END -->
