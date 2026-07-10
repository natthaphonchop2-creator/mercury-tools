# Mercury Private FlowAccount Journal MCP

Mercury's public MCP remains read-only. The optional private MCP exposes three
authenticated tools for one FlowAccount General Journal Voucher workflow:

- `preview_flowaccount_journal`
- `create_flowaccount_journal_draft`
- `approve_flowaccount_journal`

The private endpoint is:

```text
https://mercury-tools-mcp.onrender.com/private-mcp
```

It is mounted only when the server has `MERCURY_PRIVATE_MCP_TOKEN`. Never commit
the token or put its value in a skill, prompt, audit event, or Supabase row.

## Codex Connection

Set the token in the operator's local environment, then add the private MCP:

```bash
export MERCURY_PRIVATE_MCP_TOKEN="<company-private-token>"

codex mcp add mercury-finance-private \
  --url https://mercury-tools-mcp.onrender.com/private-mcp \
  --bearer-token-env-var MERCURY_PRIVATE_MCP_TOKEN
```

The GitHub marketplace also contains the **Mercury Finance Private** plugin.
Its MCP config references the environment variable name but contains no value.

## Required Flow

1. `preview_flowaccount_journal` reads the connected company's chart of
   accounts, resolves exact codes or an exact unique name, and validates the
   journal without writing.
2. Mercury shows the Dr/Cr table and waits for explicit confirmation.
3. `create_flowaccount_journal_draft` consumes the preview once and sends
   `POST /journal-entries/draft`.
4. Mercury shows the FlowAccount record ID and document serial, then waits for
   a new confirmation.
5. `approve_flowaccount_journal` sends
   `POST /journal-entries/{id}/approve` for that Mercury-created draft.

A draft remains in FlowAccount but does not affect financial statements until
it is approved. Draft creation and approval are never combined in one tool
call.

## Marketplace Shipping Example

```text
Dr. Shipping expense                                      4,236.00
    Cr. 11379.01 Online shop/E-Commerce - TikTok Shop     2,844.00
    Cr. 11379.04 Online shop/E-Commerce - Shopee          1,392.00
```

Before production preview, supply:

- final document date in `YYYY-MM-DD`;
- a unique reference;
- exact shipping-expense account code or an unambiguous account name;
- each debit and credit amount;
- a description suitable for the accounting record.

Mercury resolves FlowAccount's internal `chartOfAccountId` from
`GET /chart-of-accounts/accounts`. It never silently chooses a fuzzy match.

## Duplicate And Failure Rules

- A preview expires after 10 minutes.
- A preview can create at most one draft.
- The input hash includes workspace, connector profile, environment, date,
  reference, resolved account IDs, sides, and amounts.
- A successful, executing, or `outcome_unknown` hash blocks automatic replay.
- A timeout, disconnect, or server error after dispatch returns
  `outcome_unknown`; do not retry before inspecting FlowAccount.
- Payment, delete, void, attachment, email, share, arbitrary status reset, and
  PEAK writes remain unavailable.

## Server Configuration

Render requires these secret and non-secret settings:

```text
MERCURY_PRIVATE_MCP_PATH=/private-mcp
MERCURY_PRIVATE_MCP_TOKEN=<company-private-token>
MERCURY_CREDENTIAL_VAULT_SECRET=<existing-vault-secret>
SUPABASE_URL=<project-url>
SUPABASE_SERVICE_ROLE_KEY=<server-only-key>
```

Apply these migrations before enabling the private route:

```text
supabase/migrations/0005_flowaccount_private_journal_writes.sql
supabase/migrations/20260710_add_flowaccount_journal_skill.sql
```

`connector_write_requests` uses RLS. `anon` and `authenticated` receive no
table privileges; only `service_role` can access encrypted write state.

## Verification Boundary

CI mocks all FlowAccount write responses and never creates a live journal. A
live production write requires the operator to inspect the preview and confirm
the draft and approval as two separate actions.
