---
title: PEAK Open API Connector
doc_type: connector
review_status: reviewed
jurisdiction: TH
connector: peak
source_uri: mercury://wiki/connectors/peak-open-api
source_url: https://developers.peakaccount.com/reference/peak-open-api
---

# PEAK Open API Connector

## Purpose

PEAK Open API connects an external operating system, order system, marketplace workflow, or ERP-adjacent workflow to PEAK Accounting. Mercury treats PEAK as an endpoint-capable accounting connector: GET endpoints provide context and reconciliation data, while POST endpoints run approved document, master-data, payment, and journal workflows.

## Required Setup Material

PEAK setup normally arrives from two sources:

- Encrypted PEAK ZIP or credential text containing `ConnectId` and `ConnectKey`.
- PEAK email/instructions containing `ApplicationCode` and the UserToken flow.

Mercury setup should collect these fields through a secure credential path:

- `connect_id`
- `connect_key`
- `application_code`
- `user_token`

The host agent must not ask for these values in normal chat.

## Environments

- UAT: `https://peakengineapidev.azurewebsites.net/api/v1`
- Production: `https://api.peakaccount.com/api/v1`

PEAK UAT may have operating hours. If `POST /clienttoken` returns a PEAK body-level internal error outside those hours, retry during the stated UAT window before assuming the credential set is wrong.

## Authentication

PEAK uses a short-lived ClientToken plus a UserToken:

1. Generate UTC timestamp in `yyyyMMddHHmmss`.
2. Generate HMAC-SHA1 signature of the timestamp using `ConnectId`.
3. Call `POST /clienttoken` with body `PeakClientToken.connectId` and `PeakClientToken.password`.
4. Read `PeakClientToken.token`.
5. Use headers `Client-Token`, `User-Token`, `Time-Stamp`, `Time-Signature`, and `Content-Type` for subsequent calls.

PEAK can return HTTP 200 with a failed body-level `resCode`. Mercury must inspect `resCode` and `resDesc`, not only the HTTP status.

## Setup Validation

Mercury should validate PEAK credentials with low-impact setup checks before any accounting action:

- `POST /clienttoken`
- `GET /user`
- optional `GET /paymentmethods`
- optional list endpoints such as contacts, products, services, invoices, expenses, or receipts

This validation proves the credential set and account context. It is not the product's final operating mode.

## Endpoint Operating Model

- GET endpoints can run after connector setup for lookup, report context, reconciliation, and preflight checks.
- POST endpoints can run when a Mercury flow declares the exact capability, validates required inputs, shows a preview, and receives user approval.
- Production mutations require explicit confirmation and an audit event. UAT/sandbox mutations still require declared capability and preview.
- Create, edit, paidpayment, approve, void, invitation, file attachment, and journal posting are supported workflow targets, not default chat actions.

## Capability Map

Initial capability map:

- `auth.client_token.create`
- `user.info.read`
- `contacts.get`
- `contacts.list`
- `contacts.create`
- `contacts.update`
- `products.get`
- `products.list`
- `products.create`
- `products.update`
- `services.get`
- `services.list`
- `services.create`
- `services.update`
- `payment_methods.list`
- `payment_methods.create`
- `documents.quotation.get`
- `documents.quotation.list`
- `documents.quotation.create`
- `documents.quotation.update`
- `documents.quotation.void`
- `documents.invoice.get`
- `documents.invoice.list`
- `documents.invoice.create`
- `documents.invoice.update`
- `documents.invoice.approve`
- `documents.invoice.payment.create`
- `documents.invoice.payment.void`
- `documents.receipt.get`
- `documents.receipt.list`
- `documents.receipt.create`
- `documents.receipt.update`
- `documents.receipt.void`
- `documents.receipt.create_from_invoice`
- `documents.expense.get`
- `documents.expense.list`
- `documents.expense.create`
- `documents.expense.update`
- `documents.expense.payment.create`
- `documents.purchase_order.get`
- `documents.purchase_order.list`
- `documents.purchase_order.create`
- `documents.billing_note.get`
- `documents.billing_note.create`
- `documents.credit_note.get`
- `documents.credit_note.create`
- `documents.credit_note_expense.get`
- `documents.credit_note_expense.create`
- `daily_journal.get`
- `daily_journal.create`
- `journal.account_code.read`
- `tags.create`
- `tags.remove`
- `files.attach`
- `invitation.create`

## Accountant Review Points

- Confirm the PEAK company/account context after UserToken validation.
- Confirm transaction counting rules before high-volume document creation.
- Confirm whether batch consolidation is appropriate before posting documents.
- Confirm partial-document limitations before creating invoices from quotations or other source documents.
- Confirm Credit Note flows because PEAK may require referenced source documents.
