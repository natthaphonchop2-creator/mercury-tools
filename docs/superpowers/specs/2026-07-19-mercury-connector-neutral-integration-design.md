# Mercury Connector-Neutral Integration Design

**Status:** Approved design
**Date:** 2026-07-19
**Repository:** `mercury-tools`
**Decision:** Official OAuth first, Mercury advanced connectors when needed

## 1. Decision Summary

Mercury will be positioned and implemented as an accounting and ERP connector
platform. It will not be presented as a FlowAccount-specific product.

The connection strategy is capability-driven:

1. Prefer a provider's official remote MCP and OAuth flow when one exists.
2. Use Mercury API Connector Drivers for reviewed REST/OpenAPI integrations.
3. Use a Mercury Local Bridge for LAN, database, file, or legacy ERP systems.
4. Normalize every provider behind accounting capabilities and route Skills by
   capability rather than vendor-specific endpoint names.

FlowAccount is one validated connector option. Its official MCP is currently a
read-only provider data plane. That provider limitation must be represented as
capability state, not as a permanent limitation of Mercury or every other ERP.

The public Mercury plugin remains the one-click product entry point. Connecting
an accounting system is a separate, explicit authorization step because a user
must grant OAuth access, provide an API credential securely, or install a local
bridge. Mercury must minimize that step but cannot remove provider consent.

## 2. Relationship to Earlier Designs

This design refines, rather than discards, the approved
`2026-07-11-mercury-unified-local-erp-mcp-design.md`.

The following parts remain:

- normalized Action Catalog;
- connector-specific drivers for behavior not expressible as catalog data;
- local credential isolation for API-key integrations;
- deterministic action policy, idempotency, redaction, and audit;
- Supabase-backed accounting knowledge and endpoint evidence; and
- generic ERP execution rather than one tool per endpoint.

The following decisions change:

- local OpenAPI execution is no longer the default for every provider;
- an official provider MCP is preferred when available;
- provider read-only status is not generalized into a Mercury-wide write ban;
- the core product and plugin copy are connector-neutral;
- tool annotations are assigned per behavior instead of using one blanket
  private/audited annotation for every tool;
- ordinary mutations require one clear approval, not multiple visible tool
  confirmation steps; and
- public installation and advanced local execution are separate deployment
  modes with an explicit handoff.

## 3. Product Positioning

### 3.1 Product statement

Mercury connects AI agents to accounting systems and ERP data through official
MCP integrations, reviewed APIs, and local bridges, then adds accounting
knowledge, reusable Skills, cross-system workflows, approvals, and auditability.

### 3.2 Connector-neutral language

Plugin metadata, README headings, default prompts, screenshots, and setup copy
must lead with accounting and ERP outcomes. They must not use one vendor as the
product headline.

The connector catalog presents providers as peers and shows factual state:

- `ready_read_only`
- `ready_read_write`
- `needs_authorization`
- `needs_credentials`
- `needs_local_bridge`
- `preview`
- `provider_capability_unavailable`
- `validation_failed`

Provider names may appear in the catalog, setup flow, evidence, and
provider-specific Skills. They do not define the Mercury brand or generic Skill
contract.

### 3.3 Initial catalog

The first connector-neutral catalog contains:

- FlowAccount through official MCP/OAuth where supported, with Mercury OpenAPI
  support retained as an advanced reviewed adapter;
- PEAK through its reviewed API Connector Driver;
- Express through a Local Bridge contract and discovery scaffold;
- Custom ERP through OpenAPI, Swagger, Postman, or reviewed endpoint docs;
- Generic remote MCP providers; and
- standard authentication drivers such as OAuth client credentials, bearer
  token, API-key header, HTTP Basic, and provider-specific signed requests.

Catalog presence does not claim production readiness. Readiness is derived from
environment-specific validation evidence.

## 4. Connection Modes

### 4.1 Native MCP and OAuth

Use this mode when the ERP provider publishes an official remote MCP.

The AI host owns the provider MCP connection and OAuth session. Mercury does not
receive, proxy, or store the provider access token. Mercury Skills orchestrate
the provider tools that are already available to the host.

This distinction is required by the MCP topology: one MCP server cannot call
another server through the host unless the host explicitly performs those tool
calls. Therefore, Mercury's core server supplies capability routing, Skill
plans, context, and non-secret connector profile state; the host agent invokes
the official provider MCP tools.

Native MCP setup flow:

1. List connector options and select a company system.
2. Detect whether the provider MCP is available to the host.
3. If absent, provide the provider's verified remote MCP connection target or
   host-native install handoff.
4. Let the host complete OAuth and company selection.
5. Call one provider-declared safe read tool.
6. Save only a sanitized Mercury connector profile and observed capabilities.
7. Mark the profile `ready_read_only` or `ready_read_write` from evidence.

The core Mercury plugin must not silently bundle one provider MCP because doing
so would make the product vendor-centered. Provider packs may be offered as
optional integrations later.

### 4.2 Mercury API Connector Driver

Use this mode when a provider exposes a reviewed API but no suitable native MCP.

The local Mercury runtime:

- imports and validates endpoint specifications;
- loads credentials from local secure state only when an action is executed;
- maps vendor actions to normalized capabilities;
- executes allowlisted requests through the selected driver;
- enforces environment, risk, approval, idempotency, and response policy; and
- records a redacted local audit event.

API credentials never pass through ordinary MCP arguments or chat. Until a host
provides a standard secure-secret prompt, setup uses one guided local command
with hidden input. A future OS credential-vault integration may replace the
repository-local secret file without changing connector contracts.

### 4.3 Mercury Local Bridge

Use this mode for an ERP reachable only through a LAN, local database, desktop
application, watched folder, or export/import workflow.

The bridge runs on the user's machine or company network. The hosted Mercury
service never receives LAN credentials or raw database access. Its first release
may expose read, discovery, and export capabilities before write-back is added.

### 4.4 Generic connector learning

Mercury may learn a new connector from:

- OpenAPI 3.x;
- Swagger 2;
- Postman Collection 2.1;
- a provider remote MCP tool catalog; or
- reviewed endpoint documentation and examples.

Learning produces a draft Action Catalog, not immediate production authority.
The user must trust the exact host, configure an authentication driver, and run
safe validation before actions become executable.

## 5. Capability Model

Skills depend on normalized capabilities, for example:

```text
company.read
contacts.list
documents.invoice.list
documents.invoice.read
documents.invoice.create
documents.expense.create
tax.vat.summary.read
journal.draft.create
journal.post
payments.create
documents.void
```

Each connector profile maintains capability state per environment:

```text
declared -> observed -> enabled
                  `-> provider_unavailable
                  `-> validation_failed
                  `-> policy_blocked
```

The router distinguishes the reason an action cannot run:

- `provider_unavailable`: the selected provider does not expose it;
- `not_authorized`: the OAuth grant or API credential lacks scope;
- `not_validated`: Mercury has no acceptable endpoint evidence;
- `policy_confirmation_required`: the action is available but needs approval;
- `environment_mismatch`: evidence or credentials belong to another environment;
- `local_bridge_required`: the action cannot run from the hosted service.

This replaces vague messages such as `blocked` or `requires setup` when a more
specific next action is known.

## 6. User Experience

### 6.1 Public one-click installation

The marketplace/OpenAI plugin installs the Mercury core and its hosted MCP
without Git clone, Python setup, API keys, Supabase credentials, or a Mercury
Owner Token.

The first prompt is outcome-oriented:

```text
Connect an accounting or ERP system
```

It lists all reviewed connector options with readiness badges. No provider is
preselected merely because Mercury has more test evidence for it.

### 6.2 Provider authorization

After a connector is selected:

- native MCP: host OAuth and company picker;
- reviewed API: one guided secure credential setup;
- local ERP: Local Bridge setup and safe discovery;
- custom API: specification import, host trust, auth selection, and safe probe.

Mercury must ask only for values that cannot be derived from the connector
manifest. Fixed grant types, scopes, token paths, and API base URLs belong in
reviewed connector presets.

### 6.3 GitHub fallback

The GitHub repository remains a transparent installation and development path.
It must include:

- valid Codex plugin metadata;
- a version-pinned `.mcp.json`;
- one primary install command;
- a no-clone hosted MCP configuration example;
- an advanced local connector section;
- an explicit connector readiness table; and
- no credential examples containing usable secrets.

The reference FlowAccountMCP repository is used only as a usability benchmark.
Mercury will not copy browser-session interception, internal web-app endpoints,
or persistent browser cookies as its standard authentication design.

## 7. Tool Surface

### 7.1 Connector discovery and setup

The public contract should converge on:

- `list_connectors()`
- `get_connector_setup(connector_id, mode=None)`
- `link_connector_profile(workspace_id, connector_id, connection_mode,
  environment, company_ref=None)`
- `validate_connector_connection(workspace_id, connector_id)`
- `connector_status(workspace_id, connector_id=None)`
- `connector_capabilities(workspace_id, connector_id)`
- `unlink_connector_profile(workspace_id, connector_id, confirm)`

`workspace_id` is required on workspace-scoped tools. Catalog browsing remains
separate and does not pretend an omitted workspace has an implicit meaning.

### 7.2 Knowledge and Skills

- `search_knowledge`
- `retrieve_context_pack`
- `get_document`
- `list_accounting_skills`
- `get_accounting_skill_schema`
- `run_accounting_skill`

Generic Skill inputs use a typed envelope and named parameter records. Every
Skill publishes its own machine-readable input schema before execution.

### 7.3 Workflows

Tools with mutually exclusive input sources are split:

- `inspect_flow_files(files, include_tags, exclude_tags)`
- `run_inline_flow(flow_yaml, environment, dry_run)`
- `run_flow_files(files, include_tags, exclude_tags, environment, dry_run)`
- `run_workspace_flow(workspace_id, flow_id, environment, dry_run)`
- `save_workspace_flow(workspace_id, name, flow_yaml, metadata)`

`files`, tags, environments, metadata, and Skill parameters have explicit JSON
schemas. No public tool accepts an unconstrained object/object-array union.

### 7.4 Advanced local ERP execution

- `search_erp_actions`
- `get_erp_action_schema`
- `run_erp_read`
- `prepare_erp_mutation`
- `execute_erp_create`
- `execute_erp_update`
- `execute_sensitive_erp_action`
- `get_erp_request_status`
- `import_erp_spec`
- `list_connector_drivers`
- `credential_status`

Internal preview, payload hashing, and policy checks remain mandatory. The user
sees one approval before an ordinary mutation rather than a visible
preview/confirm/execute tool ceremony. Sensitive actions receive an elevated
confirmation, but not two duplicative confirmations for the same immutable
payload.

The execution tools are split because MCP annotations are static. A generic
tool that may both create a draft and delete a record cannot accurately describe
its risk to a host.

## 8. Tool Schemas and Annotations

### 8.1 Input-schema rules

Every public tool must satisfy:

- environment fields use explicit enums;
- workspace-scoped identifiers are required;
- arrays have typed item schemas and bounded length;
- metadata uses a named Pydantic model with `extra="forbid"`;
- dynamic values use typed name/value records;
- source variants use separate tools rather than ambiguous unions where host
  review cannot display the distinction clearly;
- dates, action IDs, capabilities, and paths have patterns and examples; and
- secret-bearing fields do not exist in public tool schemas.

### 8.2 Annotation matrix

The hosted server must replace the blanket `_AUDITED_PRIVATE` annotation with
behavior-specific constants.

| Tool class | Read only | Destructive | Idempotent | Open world |
| --- | --- | --- | --- | --- |
| Knowledge search/read | true | false | n/a | false |
| Connector catalog/status | true | false | n/a | false |
| Safe external ERP read | true | false | n/a | true |
| Create workspace/profile | false | false | false | false |
| Save immutable flow version | false | false | true | false |
| Import explicit HTTPS spec | false | false | false | true |
| Create/draft ERP record | false | false | action-dependent | true |
| Update/replace ERP record | false | true | action-dependent | true |
| Delete/void/payment/post/finalize | false | true | false | true |
| Unlink Mercury connector profile | false | true | true | false |

Operational access logging does not convert a business read into a user-visible
business mutation for host approval UX. It is append-only observability, cannot
alter authorization or provider state, and remains outside the provider
transaction. The actual runtime controls, not annotations alone, enforce
authorization and safety.

Tool annotations must follow the MCP meanings:

- `readOnlyHint` describes whether user or business state changes;
- `destructiveHint` distinguishes additive/ordinary mutation from destructive
  or irreversible behavior;
- `idempotentHint` is set on mutations only when repeated calls add no further
  effect; and
- `openWorldHint` is true when a tool reaches a provider, arbitrary approved
  HTTPS source, email recipient, or other external entity.

Saving an immutable flow version is idempotent only when the version key is
content-addressed and an identical repeated save returns the existing version.

## 9. Safety Policy

### 9.1 Controls retained

- no usable secret in Git, Supabase, RAG, logs, Skills, chat, or public tool
  arguments;
- explicit connector, company, and environment binding;
- exact action catalog allowlisting; no arbitrary HTTP proxy;
- trusted-host, redirect, SSRF, path, and private-network controls;
- payload hashing and immutable approval binding;
- redacted local and cloud audit records;
- idempotency and duplicate checks;
- no blind retry after a dispatched mutation with unknown outcome;
- provider response/body-level error interpretation; and
- separation of sandbox, UAT, production, and local evidence.

### 9.2 Gates relaxed or removed

- remove the blanket statement that hosted Mercury can never participate in ERP
  integration;
- remove a permanent production-write ban from the platform-wide policy;
- remove duplicate confirmation steps for ordinary create/update actions;
- remove vendor-wide blocks when only one provider capability is unavailable;
- remove manual repo cloning and CLI setup from the public native-MCP path;
- remove a single annotation applied to all tools; and
- remove FlowAccount-specific tool names from generic accounting workflows.

### 9.3 Gates that remain capability-dependent

An action may execute only when all are true:

1. the selected provider and environment declare the capability;
2. the connection has the required scope or credential fields;
3. endpoint evidence meets the action's validation threshold;
4. the action and host are allowlisted;
5. the immutable payload passes schema and accounting preflight checks;
6. required approval is present; and
7. no duplicate or unresolved `outcome_unknown` request blocks dispatch.

Official FlowAccount MCP writes remain unavailable while the provider publishes
read-only tools. This is a provider capability fact, not a Mercury safety ban.
A reviewed Mercury API driver may expose a write only when that separate API
path is documented, validated, authorized, and clearly identified to the user.

## 10. Knowledge, Skills, and Cross-MCP Workflows

Mercury remains more than a connector by combining:

- cited Thai and international accounting knowledge;
- ERP endpoint dictionaries and validation evidence;
- reusable accountant-designed Skills;
- company and connector context;
- reconciliation and close workflows;
- approval and audit policy; and
- host-level orchestration with Sheets, Drive, Gmail, ecommerce, banking, and
  other MCP integrations selected by the user.

Cross-MCP orchestration is performed by the AI host. Mercury contributes the
ordered Skill plan, accounting rules, evidence requirements, normalized
capability selection, and result schema. It does not pretend to possess another
plugin's OAuth token or call another host tool from inside the Mercury server.

## 11. Data Model Changes

Connector manifests add:

- `display_name`
- `connection_modes`
- `official_mcp_url`
- `auth_modes`
- `supported_environments`
- `capability_source`
- `provider_capability_status`
- `validation_evidence`
- `setup_defaults`
- `local_bridge_requirement`
- `last_reviewed_at`

Workspace connector profiles store only:

- selected connector and connection mode;
- environment;
- opaque company reference and safe display name;
- external MCP server identity or local driver identity;
- observed capability states;
- validation timestamp and evidence references; and
- non-secret setup status.

No native-provider OAuth token is copied into Mercury storage. Local API secret
state remains outside the hosted product tables.

## 12. Migration Plan

1. Change product metadata and README copy to connector-neutral language.
2. Add the connection-mode and capability-state fields to connector manifests.
3. Correct hosted MCP annotations per tool behavior.
4. Finish explicit public input schemas and remove ambiguous generic unions.
5. Split flow source variants and write execution into create, update, and
   sensitive static risk classes.
6. Add native MCP setup guidance and sanitized external-profile linking.
7. Add provider capability routing to accounting Skills.
8. Retain the local API runtime as the advanced connector path.
9. Add PEAK, Express Local Bridge, Custom ERP, and Generic MCP catalog entries
   with honest readiness badges.
10. Update plugin packaging for one-click hosted installation and a separate
    advanced local connector handoff.
11. Keep compatibility aliases for one release, then remove superseded tools
    after client and Skill migration.

## 13. Testing

### 13.1 Schema and review tests

- generated input schemas contain no unconstrained object fields;
- environment and connector selectors expose documented enums;
- workspace-scoped tools require `workspace_id`;
- flow source tools have one unambiguous source shape;
- annotations match the behavior matrix;
- read tools do not appear as destructive or mutation tools;
- create tools are not overclassified as destructive;
- update/replace and sensitive tools are classified as destructive;
- external read/write tools set `openWorldHint=true`; and
- plugin review reports no `Unclear Arguments` findings for the revised tools.

### 13.2 Connector routing tests

- a native read-only MCP profile routes read Skills and rejects unavailable
  writes with `provider_capability_unavailable`;
- a reviewed API driver routes GET and eligible mutation actions;
- an unvalidated custom API cannot execute even after import;
- Local Bridge requirements produce a specific setup handoff;
- one Skill plan can resolve equivalent capabilities across two connectors; and
- a missing provider capability does not disable unrelated connector actions.

### 13.3 Safety tests

- secret scanning covers Git history and generated plugin artifacts;
- no credential appears in MCP input/output, audit, RAG, or telemetry;
- ordinary mutation receives exactly one user-facing approval;
- sensitive mutation receives one elevated approval with immutable payload
  summary;
- payload change invalidates approval;
- `outcome_unknown` blocks replay;
- redirects and untrusted hosts are rejected; and
- native MCP OAuth tokens remain owned by the host/provider integration.

### 13.4 Packaging tests

- public plugin install requires no clone or local language runtime;
- installed core exposes one Mercury MCP entry;
- connector catalog is provider-neutral and shows factual readiness;
- native provider setup hands off to host OAuth;
- advanced local setup remains available without changing public plugin state;
- clean-install Codex and OpenAI plugin smoke tests pass; and
- GitHub install documentation works from a clean machine.

## 14. Acceptance Criteria

1. Mercury is presented as an accounting and ERP connector platform.
2. No product headline or primary default prompt centers FlowAccount.
3. The connector catalog includes native MCP, API driver, Local Bridge, and
   Custom ERP options with honest readiness states.
4. The public plugin installs in one click and does not require Supabase or ERP
   secrets from the user.
5. Native provider MCP authorization uses host OAuth and provider company
   selection.
6. The same accounting Skill can route across multiple compatible providers.
7. Provider read-only status blocks only unavailable provider actions.
8. Reviewed API drivers can expose eligible GET and write capabilities without
   a Mercury-wide production-write ban.
9. Public tool schemas produce no unclear-argument review warnings.
10. Tool annotations accurately represent reads, ordinary writes, destructive
    writes, idempotency, and external interaction.
11. Ordinary create/update operations require one clear approval.
12. Delete, void, payment, posting, finalization, email, and sharing remain
    explicitly elevated actions.
13. No usable secret is committed or returned by an MCP tool.
14. Mercury retains RAG citations, accounting Skills, workflows, cross-MCP
    planning, and audit evidence beyond a simple provider connector.

## 15. Explicit Trade-offs

- One-click installs Mercury, not automatic access to every accounting company;
  provider authorization remains a deliberate second step.
- A native provider MCP is easiest to connect but Mercury cannot add operations
  that the provider does not publish.
- Host-level cross-MCP orchestration is safer than token proxying but requires
  the host agent to execute the ordered provider calls.
- Local API drivers provide broader capability but require local runtime and
  credential setup.
- Local Bridge expands legacy ERP coverage but introduces deployment and network
  support work.
- A normalized capability layer improves Skill portability but cannot erase
  vendor-specific accounting semantics; connector metadata and evidence remain
  visible in outputs.
- Correct tool annotations improve host UX but are hints, not authorization.
  Deterministic runtime policy remains the enforcement boundary.

## 16. Sources

- FlowAccount AI Connector help:
  <https://flowaccount.com/en/help-center/category/ai-connector-mcp>
- FlowAccount MCP tutorial video:
  <https://youtu.be/dITThQELYjs>
- FlowAccountMCP usability reference:
  <https://github.com/todsawat/FlowAccountMCP>
- MCP 2025-06-18 schema and ToolAnnotations:
  <https://modelcontextprotocol.io/specification/2025-06-18/schema>
- OpenAI Codex plugin build guide:
  <https://developers.openai.com/codex/plugins/build>
