---
name: peak-connector-setup-th
description: Use when a user wants to connect PEAK Accounting/Open API, validate required credentials, or learn what PEAK docs are needed before running accounting workflows
---

# PEAK Connector Setup TH

## Rule

Do not proceed to accounting workflows until PEAK credential setup is complete and the safe setup validation has passed.

Do not ask the user to paste ConnectId, ConnectKey, ApplicationCode, UserToken, ClientToken, zip passwords, bearer tokens, or accounting secrets into normal chat. Use Mercury Connect or the host app's secure MCP credential path.

## What PEAK Requires

- Accounting program: `peak`.
- Environment: `uat` for PEAK UAT, or `production` for live PEAK.
- Server URL preset:
  - UAT: `https://peakengineapidev.azurewebsites.net/api/v1`.
  - Production: `https://api.peakaccount.com/api/v1`.
- Credential fields:
  - `connect_id`: PEAK ConnectId from the encrypted ZIP.
  - `connect_key`: PEAK ConnectKey from the encrypted ZIP.
  - `application_code`: code from PEAK used to unlock the ZIP and support the UserToken flow.
  - `user_token`: UUID-like token from PEAK's UserToken flow.
- Runtime value that Mercury fetches automatically: PEAK ClientToken from `POST /clienttoken`.
- PEAK is endpoint-capable. Mercury should support both GET and POST endpoints according to the connector manifest and the user's approved workflow.

## Required Docs

- Official PEAK Open API: `https://developers.peakaccount.com/reference/peak-open-api`.
- API limits/pricing docs from PEAK developer portal when estimating monthly usage.
- Local/customer docs, when supplied:
  - `PEAK_API Documentation.pdf`
  - `PEAK_API.postman_collection.json`
  - `PEAK_UAT_Environment.postman_environment.json`
  - `API Credential 2.txt` or the encrypted ZIP contents
  - `user Token.rtf`

## Steps

1. Call `list_connectors` and confirm `peak` is available.
2. Call `start_connector_setup` with `connector_id="peak"` and the selected environment.
3. Show the preset server URL, token path, timestamp/signature method, and PEAK docs.
4. Ask only for missing credential fields through a secure path.
5. Call `submit_connector_credentials`.
6. Call `validate_connector_connection`.
7. If validation returns `ready`, continue to the requested workflow.
8. If PEAK returns body-level `resCode` other than `200`, keep the user on setup and report only sanitized status, resCode, and support correlation id if present.

## Setup Validation

Use low-impact setup validation before any accounting action:

- `POST /clienttoken` to obtain a short-lived ClientToken.
- `GET /user` to confirm UserToken and account context.
- Optional GET checks after validation: `/paymentmethods`, `/contacts/list`, `/products`, `/services`, `/invoices/list`.

This validation is not the final operating mode. It only proves that the credential set can access PEAK.

## Endpoint Operating Mode

- GET endpoints can be used for lookup, reconciliation, and report context when the connector is ready.
- POST endpoints can be used when the selected Mercury flow explicitly requests the matching capability, validates required input fields, shows a preview, and gets user approval.
- Create/edit/payment/approve/void/invitation/journal endpoints are not forbidden by design; they are gated because they can change accounting records.
- For production POST, require an explicit user confirmation and an audit event with connector, endpoint capability, input hash, and sanitized result.
- For UAT/sandbox POST, still require the workflow to declare the endpoint capability and show what document or master data will be created.

## Capability Groups

- Master data: contacts, products, services, payment methods.
- Income documents: quotations, invoices, receipts, billing notes, credit notes.
- Expense documents: purchase orders, expenses, expense credit notes, billing note expenses.
- Accounting: daily journals and account codes.
- Utility: tags, file attachment, invitation.

## Output

ตอบภาษาไทยแบบกระชับ: โปรแกรม, environment, company/account context if available, required credential fields still missing, validation status, GET/POST capability groups, next safe tool. Never show raw credentials or tokens.
