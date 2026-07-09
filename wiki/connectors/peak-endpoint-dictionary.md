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

> Public contest policy: Mercury may explain every endpoint below, but executes
> only setup authentication probes and read capabilities. All accounting
> mutations remain blocked before connector dispatch.

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
4. In public contest mode, classify POST mutations as blocked and return
   `public_preview_read_only`. Their field hints remain documentation only.

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
| `master_write` | POST creates or updates master data; blocked in public contest mode. |
| `document_write` | POST creates or updates accounting documents; blocked in public contest mode. |
| `payment_write` | POST records payment or paidpayment; blocked in public contest mode. |
| `status_write` | POST changes status, approval, void, or voidpayment; blocked in public contest mode. |
| `journal_write` | POST creates journal entries; blocked in public contest mode. |
| `utility_write` | POST creates tags, invitations, or supporting records; blocked in public contest mode. |

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
