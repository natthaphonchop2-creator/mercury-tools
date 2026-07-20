# Local Action Catalog

The local action catalog merges a cached global knowledge/catalog snapshot with
the active repository's imported actions. Action execution always runs in the
advanced-local 20-tool MCP process with repository-local credentials and audit
state. It is separate from the public hosted 24-tool Mercury MCP.

## Import And Trust

Use `import_erp_spec` in the local MCP to import a repository-contained
OpenAPI, Swagger, Postman, or endpoint document. The response returns action
identities and sanitized metadata; it does not publish the source, request data,
or credentials to Cloud.

For a custom ERP host, configure the connector first. Mercury shows the host
that will be trusted and requires its exact confirmation before persisting the
repository configuration:

```bash
uv run mercury connector configure custom-books \
  --env production \
  --driver bearer \
  --base-url https://api.example.com/v1 \
  --repo-root .
```

At the prompt, type `trust api.example.com` only after verifying the provider
endpoint. The executor restricts requests to configured trusted hosts, rejects
unsafe targets and redirects, and does not accept arbitrary per-request URLs.

## Selection Sequence

1. Call `search_erp_actions` with the requested capability or endpoint intent.
2. Call `get_erp_action_schema` for the selected action and inspect the input
   contract, mutation class, and approval level.
3. Use `run_erp_read` only for an effective Tier 0 GET action.
4. Use the preparation and class-specific execution sequence below for every
   mutation.

Importing a spec does not authorize a write. It only makes local actions
available for selection and review.

## Mutation Classes And One Immutable Approval

| Mutation class | Approval level | Required sequence |
| --- | --- | --- |
| Safe read | None | `run_erp_read` for a safe GET action. |
| Create | Standard | `prepare_erp_mutation`, one distinct explicit user approval, then `execute_erp_create` once. |
| Update | Standard | `prepare_erp_mutation`, one distinct explicit user approval, then `execute_erp_update` once. |
| Sensitive | Elevated | `prepare_erp_mutation`, one distinct explicit user approval, then `execute_sensitive_erp_action` once. |

The returned `request_id` and `payload_hash` bind the action version, target,
inputs, attachments, credential revision, and preflight action versions. Internal
credential and dependency revisions are never returned. Do not alter inputs,
substitute a hash, reuse a stale preview, self-confirm, or execute a request more
than once.

## Unknown Outcomes

Network uncertainty or a provider 5xx after dispatch produces
`outcome_unknown`. This state blocks replay of the same payload.

1. Call `get_erp_request_status` and preserve the request ID in the case record.
2. Reconcile through an approved safe provider status action when one is in the
   catalog, or reconcile manually in the provider using the request evidence.
3. Never retry or re-prepare the same mutation while its outcome is unknown.
4. Create a fresh preparation only after the previous provider outcome is definite.

The local audit ledger records redacted lifecycle metadata, not provider record
values or credential material.
