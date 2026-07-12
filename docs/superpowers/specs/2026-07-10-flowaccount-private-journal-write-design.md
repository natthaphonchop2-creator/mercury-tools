# FlowAccount Private Journal Write Design

**Date:** 2026-07-10  
**Status:** Approved for planning  
**Scope:** Private FlowAccount journal writes for one company workspace

## Problem

Mercury currently exposes a public read-only MCP. It can connect to FlowAccount,
retrieve accounting context, and plan accounting work, but it cannot record an
approved user instruction in FlowAccount.

The first required write workflow is a summarized marketplace shipping entry.
For example, one General Journal Voucher should record:

```text
Dr. Shipping expense                                      4,236.00
    Cr. 11379.01 Online shop/E-Commerce - TikTok Shop     2,844.00
    Cr. 11379.04 Online shop/E-Commerce - Shopee          1,392.00
```

Mercury must resolve company-specific chart-of-account IDs, verify that the
journal balances, show a preview, and wait for explicit confirmation before it
writes anything.

## Goals

- Keep the existing public `/mcp` endpoint read-only.
- Add a separately authenticated private MCP endpoint for company writes.
- Support one multi-line FlowAccount General Journal Voucher per request.
- Resolve account codes or unambiguous account names through FlowAccount's
  Chart of Accounts API.
- Create an awaiting draft before any approval action.
- Require a second explicit confirmation to approve a previously created draft.
- Prevent accidental duplicate submissions and unsafe automatic retries.
- Store auditable execution metadata without storing raw access tokens or client
  secrets in logs.

## Non-goals

- Generic POST access to arbitrary FlowAccount paths.
- PEAK or other ERP writes.
- Payment, delete, void, attachment, email, share, or status-reset actions.
- Automatic journal approval in the same operation that creates the draft.
- Autonomous account selection when a name is ambiguous.
- Public multi-tenant production writes or a general OAuth product flow.

## Source Contract

The implementation follows FlowAccount's current official Journal Entry API:

- Chart of Accounts: `GET /chart-of-accounts/accounts`
- Create draft: `POST /journal-entries/draft`
- Approve an existing draft: `POST /journal-entries/{id}/approve`
- General Journal Voucher: `documentType = 51`
- Debit line: `debitCredit = 1`
- Credit line: `debitCredit = 3`

Official references:

- <https://developers.flowaccount.com/tutorial/journal-entry-api/>
- <https://developers.flowaccount.com/tutorial/journal-entry-api/create-draft/>
- <https://developers.flowaccount.com/tutorial/journal-entry-api/get-chart-of-accounts>
- <https://developers.flowaccount.com/api-reference/>

The repository's generated endpoint dictionary remains supporting connector
evidence, but the official API documentation is authoritative when they differ.

## Architecture

### Public MCP

The existing `https://mercury-tools-mcp.onrender.com/mcp` endpoint and
`mercury-finance` contest plugin remain unchanged and read-only. Public tools
must not list or dispatch private journal-write operations.

### Private MCP

The Render service mounts a second FastMCP server at `/private-mcp`. This server
contains only private write-oriented tools and is protected by an HTTP Bearer
token held in the Render environment as `MERCURY_PRIVATE_MCP_TOKEN`.

The token is never committed to Git, stored in Supabase, returned by MCP tools,
or copied into audit records. A private Codex MCP configuration passes it using
`bearer_token_env_var`. Missing or invalid authorization is rejected before a
tool is invoked.

### Connector Adapter

A focused `FlowAccountJournalClient` owns:

- OAuth client-credentials token acquisition;
- chart-of-account retrieval and normalization;
- draft-journal creation;
- draft-status approval;
- response classification and redaction.

MCP tools call this adapter through a service layer. They do not construct URLs
or HTTP requests directly.

## Tool Contract

### `preview_flowaccount_journal`

Inputs:

- `workspace_id`
- `document_date` in `YYYY-MM-DD`
- `reference`, required for production
- `description`
- optional `note` and `remarks`
- `lines`, each containing:
  - `side`: `debit` or `credit`
  - `account_code` or `account_name`
  - `amount` as a decimal value
  - optional line `description`

Behavior:

1. Resolve the workspace's ready FlowAccount connector and environment.
2. Fetch the company Chart of Accounts.
3. Match an exact normalized account code first.
4. If no code is supplied, accept a unique normalized account-name match.
5. Return `account_resolution_required` when no account matches.
6. Return `ambiguous_account` with safe candidate labels when multiple names
   match; do not choose one automatically.
7. Normalize money to two decimal places and require positive line values.
8. Require at least one debit and one credit line.
9. Require total debit to equal total credit exactly after normalization.
10. Build the FlowAccount payload with `documentType=51`, but do not submit it.
11. Store an encrypted preview request with an input hash and 10-minute expiry.
12. Return a sanitized preview, `preview_id`, expiration, and confirmation text.

Example preview result:

```json
{
  "status": "awaiting_confirmation",
  "preview_id": "mjp_...",
  "environment": "production",
  "document_type": "JV",
  "total_debit": "4236.00",
  "total_credit": "4236.00",
  "lines": [
    {"side": "debit", "account": "Shipping expense", "amount": "4236.00"},
    {"side": "credit", "account": "11379.01 TikTok Shop", "amount": "2844.00"},
    {"side": "credit", "account": "11379.04 Shopee", "amount": "1392.00"}
  ]
}
```

### `create_flowaccount_journal_draft`

Inputs:

- `workspace_id`
- `preview_id`
- `confirm=true`

Behavior:

1. Require private MCP authentication.
2. Verify that the preview belongs to the workspace, is unexpired, unconsumed,
   and still targets the same connector profile and environment.
3. Reject a previously successful input hash as `duplicate_blocked`.
4. Acquire a fresh FlowAccount access token server-side.
5. Submit the stored payload to `POST /journal-entries/draft` exactly once.
6. Mark the preview consumed only after a classified response is recorded.
7. Return the sanitized FlowAccount `recordId`, `documentSerial`, status,
   debit, credit, and a second approval preview.

### `approve_flowaccount_journal`

Inputs:

- `workspace_id`
- `record_id`
- `confirm=true`

Behavior:

1. Require private MCP authentication and a draft created by Mercury in the
   same workspace.
2. Require a separate explicit user confirmation after draft creation.
3. Call `POST /journal-entries/{recordId}/approve` once.
4. Record the transition result and return only a sanitized status summary.
5. Never combine draft creation and approval in one MCP call.

## Account Resolution

FlowAccount journal payloads require internal integer `chartOfAccountId` values,
not only human-facing codes such as `11379.01`. Mercury therefore reads the
company chart first and keeps resolution scoped to the connected company.

Resolution order:

1. exact normalized account code;
2. exact normalized local name;
3. exact normalized foreign name;
4. stop and request user selection.

Fuzzy matching may be shown as suggestions but must never silently select an
account for a production journal.

## Persistence

Add `connector_write_requests` in Supabase with:

- opaque request ID;
- workspace and connector-profile references;
- environment and operation;
- encrypted normalized payload;
- SHA-256 input hash;
- `previewed`, `executing`, `draft_created`, `approved`, `failed`,
  `outcome_unknown`, `expired`, or `cancelled` status;
- FlowAccount record ID and document serial when known;
- created, expiry, executed, and approved timestamps;
- sanitized response summary.

RLS denies anonymous and authenticated direct access. Only the server's service
role can read or mutate write requests. Raw connector credentials remain in the
existing encrypted credential store.

## Duplicate Protection

The input hash binds:

- workspace and connector profile;
- environment;
- document date and reference;
- document type;
- resolved account IDs, sides, amounts, and line descriptions.

A successful or outcome-unknown request with the same hash blocks another
automatic submission. The user must inspect FlowAccount and explicitly create a
new request with a distinct reference if a replacement is required.

## Error Handling

- Validation errors do not create a write request.
- OAuth or pre-dispatch failures are safe to retry after a new preview.
- A definitive FlowAccount 4xx is returned as `rejected` with a sanitized error.
- A timeout, disconnect, or 5xx after dispatch returns `outcome_unknown`.
- `outcome_unknown` is never retried automatically because FlowAccount may have
  accepted the journal before the response was lost.
- No raw request payload, bearer token, client ID, client secret, tax ID, email,
  or complete contact record is written to application logs.

## Skill Flow

Add a compact `flowaccount-journal-posting-th` skill. The skill must:

1. confirm the private connector is ready;
2. collect missing date, reference, description, and journal lines;
3. call the preview tool;
4. show the balanced Dr/Cr summary;
5. stop and wait for explicit user confirmation;
6. create the draft;
7. show the FlowAccount draft reference;
8. ask separately whether to approve;
9. call approval only after a new explicit confirmation.

The skill must never treat the user's initial accounting instruction as approval
for both draft creation and final posting.

## Testing

Unit tests cover:

- exact account-code and account-name resolution;
- ambiguous and missing account behavior;
- decimal normalization and balanced-journal validation;
- request mapping to `documentType=51`, debit `1`, and credit `3`;
- private bearer authentication;
- preview expiry and workspace binding;
- one-time consumption and duplicate blocking;
- redaction of credentials and sensitive fields;
- approval requires a Mercury-created draft.

Adapter tests use mocked HTTP responses to verify token, chart, draft, and
approval calls. CI never creates a live journal.

Live acceptance proceeds in two manual stages:

1. sandbox: create and approve a disposable balanced JV;
2. production: create the user's supplied journal only after the user confirms
   the final date, reference, resolved accounts, and amounts.

## Acceptance Criteria

- Public MCP remains read-only and passes its existing contract tests.
- Requests without the private bearer token cannot list or call write tools.
- Mercury resolves the three example lines and previews one balanced JV for
  `4,236.00` total debit and credit.
- Draft creation results in one FlowAccount record and cannot be repeated with
  the same consumed preview or input hash.
- Approval is a separate confirmed operation on the same draft record.
- Every write attempt produces a sanitized audit event.
