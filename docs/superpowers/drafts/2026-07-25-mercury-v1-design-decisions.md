# Mercury V1 Design Decision Log

Status: Historical decision log with current Design 8 authority
Last updated: 2026-08-01
Target final spec:
`docs/superpowers/specs/2026-08-01-mercury-v1-aws-primary-agentcore-design.md`

This file preserves decisions in approval order. Older sections are historical
when they conflict with Approved Design 8. The AWS-primary specification above
is implementation authority.

## Locked Product Scope

- Mercury V1 supports FlowAccount and PEAK Accounting.
- V1 exposes every provider-supported read and mutation capability that Mercury
  has discovered, schema-validated, and qualified.
- FlowAccount is the first end-to-end qualification target. PEAK follows the
  same connector contract after the FlowAccount path passes.
- Create, update, patch, delete, void, payment, approval, send, post, and other
  provider actions are in scope when their exact versions pass qualification.
- Every mutation follows:
  `Prepare -> Validate -> Preview -> Explicit confirmation -> Dispatch -> Reconcile -> Audit`.
- Batch operations may use one confirmation for one immutable preview that
  lists every document in the batch.
- Thai previews follow the `praneet-front` typography rules. They use a formal
  accounting-document presentation, Thai-safe line height and word breaking,
  and tabular numerals.

## Approved Design 1: Mercury Authorization Gateway

Design 1 was revised after comparing a host-composed multi-MCP plugin with a
Mercury-managed gateway. The approved design is the gateway, which supports
both OAuth and provider-issued credentials. Do not revert the specification to
the earlier host-composed design without a new user decision.

### Connection Model

- The host connects to Mercury MCP as the single product-facing MCP server.
- Mercury authenticates the host user with a Mercury-scoped access token.
- Mercury idempotently creates one personal default workspace on the user's
  first authenticated request. `get_mercury_context` returns the explicit
  active `workspace_id` required by later tools.
- Mercury acts as a downstream MCP client for the selected ERP provider.
- FlowAccount uses its provider-owned OAuth login, consent, and company
  selection flow.
- PEAK's public MCP contract uses a provider-issued User Token, Connect ID, and
  Connect Key rather than OAuth. The user creates these values in PEAK and
  submits them through a one-time secure Mercury setup page. They never pass
  through chat or the model. Secure URL-mode elicitation is a future client of
  the same setup contract, not a second V1 credential path.
- Mercury uses provider authorization material only when calling the matching
  provider MCP server.
- Provider OAuth tokens and provider-issued credentials are never passed
  through to the host, model, prompt, widget, RAG store, logs, or audit output.
- Express Account has no confirmed public MCP/OAuth contract in this design.
  It remains a future customer-operated Local Bridge connector.

### Responsibilities

Mercury owns:

- user and workspace connection state
- provider account linking
- provider tool discovery and normalized capability catalog
- accounting Skills and RAG retrieval
- request validation and schema mapping
- Thai HTML preview generation
- explicit approval and immutable preview binding
- downstream provider execution
- idempotency and ambiguous-result reconciliation
- sanitized audit records

The provider owns:

- user login and consent
- company or merchant selection
- ERP authorization and permissions
- source-of-truth accounting records
- the final read or create operation

### Data Flow

1. The host authenticates to Mercury MCP.
2. Mercury starts the selected provider authorization flow.
3. For FlowAccount, the user signs in, consents, and selects a company on the
   provider-owned page. For PEAK, the user creates provider credentials in PEAK
   and submits them through Mercury's secure, out-of-band setup flow.
4. Mercury stores the encrypted downstream connection.
5. Mercury discovers and validates the provider's read and document-create tools.
6. Mercury retrieves accounting guidance from its RAG store when required.
7. Mercury reads provider data or prepares an immutable create preview.
8. After explicit confirmation, Mercury calls the provider create tool.
9. Mercury returns the provider result and writes a sanitized audit event.

## Approved Design 2: Encrypted Provider Credential Vault

- Provider authorization records are stored separately from RAG documents,
  knowledge chunks, prompts, and audit events.
- FlowAccount access and refresh tokens plus PEAK User Token, Connect ID, and
  Connect Key values are encrypted with authenticated encryption such as
  AES-256-GCM.
- The encryption master key is supplied through the Render secret store. It is
  never committed to Git or stored alongside encrypted rows in Supabase.
- Each encrypted connection is bound to the Mercury user, workspace, provider,
  provider company or merchant, environment, granted scopes, and key version.
- Refresh tokens are stored encrypted. Short-lived access tokens should be
  cached in memory where practical and persisted encrypted only when required
  for continuity. Static provider credentials remain encrypted at rest.
- Refresh where supported, credential validation, revocation, disconnect,
  expiry handling, and encryption-key rotation are required lifecycle
  operations.
- Token values must not appear in application logs, exception messages, audit
  events, analytics, RAG content, model-visible tool results, or HTML previews.
- Database authorization and application checks must prevent one tenant from
  reading, refreshing, revoking, or using another tenant's connection.

## Approved Design 3: Mercury-Owned MCP Tool Contract

Mercury remains the only product-facing MCP server. It does not expose a
provider MCP server directly to the host. Mercury has two protocol roles:

- MCP server for ChatGPT, Codex, and other supported hosts
- downstream MCP client for FlowAccount and PEAK

The provider MCP servers are internal connector drivers. The host connects only
to Mercury, sees only Mercury tool names and schemas, and never receives a
provider token or raw provider tool session.

### Stable Core Tools

Mercury owns a stable core tool surface for connection, knowledge, workflow,
preview, execution state, and disconnection:

- `get_mercury_context`
- `list_accounting_providers`
- `start_provider_connection`
- `list_provider_connections`
- `connector_status`
- `list_provider_capabilities`
- `get_capability_schema`
- `search_knowledge`
- `retrieve_context_pack`
- `run_accounting_skill`
- `prepare_document_create`
- `render_document_preview`
- `confirm_document_create`
- `get_operation_status`
- `disconnect_provider`

Every core tool must have an explicit JSON Schema. Provider and environment
selectors use enums where the catalog is known. No public tool may accept an
untyped `inputs` object with an undocumented shape.

`get_mercury_context` accepts an empty object, performs idempotent default
workspace bootstrap, and returns only sanitized membership and active-workspace
state. `search_knowledge` and every workspace-bound tool require the returned
`workspace_id`.

`run_accounting_skill` may perform qualified provider reads and create Mercury
preview state, but it never dispatches a provider mutation. Every provider
create is dispatched only by `confirm_document_create`.

### Catalog-Generated Typed Action Tools

Mercury also exposes exact-schema action tools generated from the reviewed
provider capability catalog. Example names include:

- `mercury_flowaccount_invoice_list`
- `mercury_flowaccount_invoice_create_prepare`
- `mercury_peak_invoice_list`
- `mercury_peak_invoice_create_prepare`

These are Mercury tools, not raw provider tools. Each wrapper:

- has a stable Mercury-owned name
- publishes the exact validated input schema required for that action
- binds one provider, action, and environment contract
- validates and normalizes inputs before any downstream call
- invokes the provider MCP internally
- converts provider output into a stable Mercury response envelope
- records sanitized evidence without exposing provider tokens

Create wrappers prepare an immutable request and HTML preview. They do not call
the downstream create operation. `confirm_document_create` executes the exact
stored request identified by `preview_id`, preventing post-preview argument
changes.

### Discovery and Enablement

1. Mercury performs downstream `tools/list` discovery after provider linking.
2. Discovered tools are classified as read, document create, or excluded V1
   mutation.
3. Mercury compares the provider schema with its endpoint and capability
   catalog.
4. A new or changed action enters `discovered_unreviewed`.
5. Schema validation and safe non-production qualification promote the action
   to `nonproduction_qualified`.
6. A production capability becomes `enabled` only after an owner-authorized
   production canary passes for that exact version and environment.
7. Update, patch, delete, void, payment, approval, and other non-create
   mutations remain hidden in V1 even if the provider advertises them.
8. The catalog is cached and Tool Search loads relevant action tools on demand
   so the full provider catalog does not consume every model turn.

### Provider Driver and V1 Seed Contract

- V1 downstream drivers use Streamable HTTP MCP through a versioned,
  secretless `catalog/global/<provider>/driver.json`.
- Driver manifests declare exact environment resource URIs, auth adapters,
  timeout classes, and downstream-to-normalized capability mappings.
- FlowAccount uses downstream MCP OAuth discovery and PKCE with the exact
  Mercury callback. PEAK credentials are injected by a server-side auth adapter
  and never appear in public tool inputs.
- Sessions are isolated by tenant, user, connection, environment, and operation.
- The deterministic seed capabilities for both providers are
  `provider_profile.get`, `documents.invoice.list`,
  `documents.invoice.get`, and `documents.invoice.create`.
- Exact qualified versions are persisted under
  `catalog/global/<provider>/qualifications/`. Other discovered document-create
  actions remain unreviewed until individually qualified.

## Approved Design 4: Knowledge and Skill Routing

Mercury separates execution authority from retrieved knowledge. RAG content can
inform accounting reasoning, citations, and workflow guidance, but it cannot
enable a provider capability or choose an unqualified endpoint.

### Four Data Planes

1. The structured Capability Catalog stores provider tool schemas, endpoints,
   normalized capabilities, environments, version hashes, classification, and
   qualification state. Only actions with an `enabled` catalog state may run.
2. Mercury Knowledge RAG stores accounting standards, Thai tax references,
   workflow guidance, and human-readable provider documentation. Every
   published item carries source and citation fields plus jurisdiction,
   connector, document type, effective date, and review status.
3. The Skill Registry stores versioned workflow policy. Each Skill declares an
   exact input and output schema, required and optional capabilities, allowed
   and blocked action classes, evidence requirements, accountant review points,
   and knowledge retrieval routes.
4. Runtime evidence and audit records remain separate from RAG. Provider
   payloads, company data, and operation results are never re-ingested as
   knowledge automatically.

Git is the canonical source for first-party Skill definitions and instruction
content. Supabase stores the published catalog projection used by Hosted MCP.
Workspace-uploaded Skills remain draft until their schema and policy are
validated and explicitly published.

Knowledge tools require an authenticated `workspace_id`. Globally reviewed
first-party content is shared across authorized workspaces; workspace-owned
content is visible only to members after publication. Draft or another tenant's
content is never returned by normal search.

### Routing Order

1. Resolve the requested Skill and its exact published version.
2. Resolve the Mercury user, workspace, provider connection, company, and
   environment.
3. Verify every required capability against the structured Capability Catalog.
4. Select provider actions by exact catalog lookup, never by semantic search.
5. Retrieve cited accounting context with explicit Skill filters. Published
   retrieval defaults to `review_status=reviewed`; explicit filters take
   precedence over deterministic inference.
6. Invoke required read actions through Mercury's downstream provider driver.
7. Return facts, citations, missing evidence, accountant review points, and the
   next allowed action.
8. Route every document-create request into the immutable preview and explicit
   confirmation flow.

If exact knowledge or evidence is unavailable, Mercury returns
`insufficient_evidence`. It must not invent an endpoint, provider field,
accounting requirement, or source.

### Retrieval and Model Boundary

- Endpoint and schema routing is structured and keyword-exact first.
- PostgreSQL full-text search is the mandatory V1 retrieval path.
- Real semantic embeddings are an optional internal backend enhancement.
- Deterministic hash embeddings are test-only and must not be represented as
  semantic production retrieval.
- Semantic ranking can improve recall but can never authorize or select an ERP
  mutation.
- The host LLM remains the reasoning model. Mercury does not require users to
  supply an LLM API key.
- Other host-connected MCPs may provide evidence through typed inputs, but
  Mercury does not receive or reuse their OAuth tokens.

## Approved Design 5: Operation Safety and Ambiguous Outcomes

This design enables real document creation without allowing a timeout,
duplicate confirmation, or uncertain provider response to create duplicate
accounting records.

### Hosted Operation State

Every create request is persisted as a tenant-bound Mercury operation. Its
normal lifecycle is:

`prepared -> awaiting_confirmation -> dispatching -> succeeded`

Other terminal or recovery states are:

- `failed_pre_dispatch`: the request was not sent and can be retried safely
- `provider_rejected`: the provider returned a deterministic rejection; inputs
  must be corrected and previewed again
- `outcome_unknown`: dispatch may have occurred but Mercury cannot prove the
  provider outcome
- `needs_manual_review`: automated reconciliation cannot establish one exact
  outcome
- `expired`: the immutable preview expired before execution
- `cancelled`: the user cancelled before dispatch

Operation rows are bound to the Mercury tenant, user, workspace, provider
connection, company, environment, action version, immutable payload hash, and
confirmation record.

### Retry and Idempotency

- Read actions may use bounded retry with backoff.
- An expired OAuth token may be refreshed once before dispatch.
- A create failure proven to occur before dispatch may retry the same operation.
- Mercury never automatically retries a create after possible dispatch unless
  the provider contract gives a proven idempotency guarantee.
- Repeated confirmation of the same preview returns the existing operation and
  never creates a second provider request.
- Mercury supplies its operation ID as the provider idempotency key when the
  qualified provider action supports one.
- A workspace, connection, and payload-hash lock prevents concurrent duplicate
  execution.

### Reconciliation

For timeout, provider 5xx, connection loss after possible dispatch, or a
malformed provider response, Mercury marks the operation `outcome_unknown` and
blocks replay.

Mercury then uses the qualified provider status or lookup action with the
document ID, external reference, or another preview-bound identifier:

- one exact match resolves the operation to `succeeded`
- no match keeps the operation `outcome_unknown`
- multiple possible matches move it to `needs_manual_review`

Mercury does not treat absence of evidence as proof of failure.
`get_operation_status` returns the current state, a sanitized reason, and the
next permitted action.

### Batch Creation

- One confirmation may bind one immutable preview containing all batch items.
- Each document receives its own child operation and idempotency identity.
- Mercury uses a qualified native batch action when the provider has one;
  otherwise it dispatches child operations sequentially.
- A deterministic rejection or unknown outcome stops documents that have not
  yet been dispatched.
- The result lists each item as succeeded, rejected, not dispatched, unknown,
  or requiring manual review.
- Mercury does not claim rollback for provider documents already created.

## Approved Design 6: Thai HTML Preview and Widget Contract

Mercury uses a decoupled data and render flow so document preparation remains
usable by every MCP host while supported hosts can present an embedded Thai
HTML preview.

### Tool and Resource Contract

- `prepare_document_create` validates the requested create action, persists an
  immutable preview, and returns `preview_id`, `state_version`, expiry, and
  concise structured preview data.
- `render_document_preview(workspace_id, preview_id)` is a read-only
  presentation tool. It loads the stored preview and attaches the versioned resource
  `ui://widget/mercury-document-preview-v1.html`.
- The resource uses the MCP Apps UI MIME type
  `text/html;profile=mcp-app`.
- `confirm_document_create(workspace_id, preview_id, state_version,
  confirmation)` remains the only tool that may dispatch the stored create
  request.
- Hosts without widget support receive a complete text and structured-data
  fallback and can use the same confirmation tool.

The widget resource uses `_meta.ui.resourceUri` as the primary MCP Apps field
and may include `openai/outputTemplate` only as a compatibility alias.

### Preview Contents

The preview presents:

- document state, provider, company, and exact catalog-supported environment
- document type, counterparty, issue date, due date, and currency
- item quantities, unit prices, discounts, taxes, withholding tax, and totals
- validation failures, warnings, and accountant review points
- batch document count, aggregate totals, and per-document inspection
- an explicit provider- and environment-specific create confirmation action

Thai document typography follows the project design guidance: a Thai-capable
looped body face such as Sarabun or IBM Plex Sans Thai Looped, a restrained
Thai-capable heading face, no Thai letter spacing, readable body line height,
and tabular numerals for financial values. The layout is responsive,
keyboard-accessible, and printable in an A4-oriented form.

### Server Authority and Tamper Resistance

- The authoritative create payload stays on the Mercury server.
- The widget receives only sanitized fields required for human review.
- The widget sends `preview_id` and explicit confirmation, never a replacement
  provider payload. The non-widget fallback also returns `workspace_id`,
  `preview_id`, and `state_version`.
- Editing creates and validates a new immutable preview; it does not mutate the
  approved preview.
- Before dispatch, Mercury rechecks tenant, user, workspace, provider
  connection, company, environment, capability version, connection revision,
  expiry, and payload hash.
- Provider credentials, bearer tokens, and API keys never enter HTML, model
  context, client telemetry, or browser logs.
- The widget calls Mercury tools through the MCP Apps bridge and never calls a
  provider API directly.

### Host and Security Behavior

- Widget business state is server-owned; widget-local state is presentation
  state only.
- The component CSP allows only the exact Mercury resource and connection
  domains required by the widget.
- Mercury records one explicit confirmation. A host may still apply its own
  independent approval policy, but Mercury does not add a second internal
  confirmation prompt.
- Rendering or rerendering a preview never prepares or creates another
  document.

## Approved Design 7: Capability-Graduated Testing and Rollout

Mercury releases and enables provider behavior per qualified capability instead
of waiting for every discovered provider endpoint or enabling a provider as one
indivisible unit.

### Qualification and Test Layers

1. Unit and contract tests require explicit JSON schemas, normalized and
   sanitized provider responses, tenant-bound preview hashes, and deterministic
   capability states. RAG never authorizes an ERP action.
2. Hosted integration tests apply Supabase migrations, exercise encrypted OAuth
   token lifecycle behavior, and verify Render health, readiness, MCP
   initialization, tool discovery, and safe tool calls.
3. Provider certification covers FlowAccount OAuth consent and company
   selection plus PEAK secure credential setup and merchant binding. It
   requires enabled `provider_profile.get`, `documents.invoice.list`,
   `documents.invoice.get`, and one complete `documents.invoice.create` flow for
   each provider. PEAK uses a provider UAT tenant when available or a dedicated
   owner-authorized test merchant.
4. Failure tests cover expired or revoked tokens, wrong-company binding, schema
   drift, duplicate and concurrent confirmation, provider rejection, timeout
   after possible dispatch, provider 5xx, partial batch completion, and unknown
   outcome reconciliation without replay.
5. Plugin and widget tests cover one-click installation, Thai desktop and mobile
   rendering, A4 printing, keyboard access, text fallback, and tool schemas that
   produce no unclear-argument warnings.

The repository's plugin manifest, Hosted MCP config, OpenAI submission bundle,
and public OAuth-client deployment record must all reference the same canonical
resource and version. The bundle contains no static bearer token or OAuth client
secret.

### Capability Graduation

- Discovered actions begin as `discovered_unreviewed`.
- Schema validation, safety classification, provider-appropriate
  non-production evidence, response-shape checks, and release review are
  recorded against the exact provider, action, version, and environment.
- Only an exact version promoted to `enabled` appears as executable.
- A production version reaches `enabled` only after its owner-authorized canary
  passes; non-production evidence never authorizes production execution.
- A provider may have enabled reads and creates alongside other unavailable or
  unreviewed actions.
- Additional document types can graduate through the catalog without requiring
  a new Mercury application release when the executable adapter is already
  compatible with the qualified schema.
- Schema drift or operational evidence may demote or disable one capability
  without disabling the entire provider or Mercury service.

### V1 Release Gate

The `v1.0.0` public release requires:

- one-click Mercury plugin installation against the Hosted MCP
- FlowAccount OAuth and exact company selection
- PEAK secure provider authorization and exact merchant binding
- both providers have enabled `provider_profile.get`,
  `documents.invoice.list`, `documents.invoice.get`, and
  `documents.invoice.create` seed capabilities
- each provider completes `documents.invoice.create` through preview,
  confirmation, provider create, and audit
- no user-supplied LLM key and no provider credential in Git, model output,
  widget payload, logs, RAG, or audit
- duplicate confirmation proven not to duplicate provider documents
- ambiguous outcomes blocked from automatic replay
- text fallback for hosts without widget support
- production canary execution performed only by an authorized account owner
  after provider-appropriate non-production certification

### Rollout and Recovery

The release sequence is CI and secret scan, Render preview deployment,
FlowAccount sandbox certification, PEAK UAT or dedicated test-merchant
certification, owner-authorized production canary, and then the `v1.0.0` tag
and public plugin release.

Mercury uses the existing repository CI, Hosted MCP smoke test, Render
deployment, and secret scan. It does not introduce a separate release-control
repository. Recovery consists of disabling the exact catalog capability,
revoking or disconnecting an affected provider connection, and rolling Render
back to the previous healthy deployment. V1 database migrations use
backward-compatible expansion and avoid destructive release-time migrations.

## Approved Design 8: MCP-First SaaS with AgentCore Backend

Owner direction approved on 2026-08-01:

- Mercury is a customer-facing SaaS product; Amazon Bedrock AgentCore remains
  backend infrastructure and is never the customer experience.
- Mercury remains the only product-facing MCP server. Provider MCP servers and
  AgentCore Gateway are internal connector infrastructure.
- V1 launches MCP-first. Customers use their existing Claude, Codex, ChatGPT or
  compatible host, so Mercury does not require a customer-supplied LLM API key
  and does not initially bear general chat-model token costs.
- Mercury includes a small Web Console for sign-in, workspaces, provider setup,
  capability status, preview approval, sanitized audit, membership, plan and
  billing administration.
- The Web Console is not a standalone accounting application and does not add a
  Mercury chat interface.
- Mercury-owned chat, model inference and an AgentCore Harness agent loop are
  not planned or approved. Adding any of them requires a separate owner
  decision and must not be inferred as a later phase of this design.

### Managed Infrastructure Boundary

The approved architecture uses AgentCore Runtime, Gateway, Identity,
Policy and Observability to replace generic hosting, gateway, credential,
authorization and telemetry plumbing. It preserves the already-tested Mercury
domain core for Capability Catalog authority, provider normalization, cited
RAG, Skills, Thai preview, explicit confirmation, idempotency, reconciliation
and accounting-aware audit.

AWS is the sole target backend. Render and Supabase are frozen as test-only
reference systems until the one-time canonical URL cutover; there is no
dual-write, data synchronization, or open-ended hybrid production period. Test
tenants, credentials, operations, and audit rows are not migrated into AWS.
AgentCore Memory is not accounting knowledge or operation authority. AWS Agent
Registry may index Skill and MCP metadata, but Git remains canonical for
first-party Skills and the Capability Catalog remains execution authority.

### FastMCP and Full ERP Operations

- FastMCP remains the MCP protocol layer inside AgentCore Runtime. The first AWS
  deployment retains the pinned FastMCP API bundled with the official MCP Python
  SDK. Standalone FastMCP is a gated upgrade after AgentCore smoke testing.
- Mercury supports all provider-published operation classes, including reads,
  Create, Update, Patch, Delete, Void, Payment, Approval, Send, and Post.
- Only an exact provider, operation, version, and environment that has passed
  qualification is executable. No generic arbitrary-URL tool is exposed.
- Every mutation uses immutable preview, role and entitlement checks, one
  explicit confirmation, idempotency, outcome reconciliation, and sanitized
  audit.

### AWS Data and Product Delivery

- Asia Pacific Singapore (`ap-southeast-1`) is the primary region.
- Separate non-production and production AWS accounts isolate VPC, Aurora,
  S3, KMS, identity, credentials, and audit.
- Aurora PostgreSQL is the product and operation system of record. S3 plus
  Bedrock Knowledge Bases provides cited knowledge retrieval.
- AgentCore Identity manages outbound OAuth/API-key credentials; Aurora stores
  opaque references and provider account binding only.
- The one-click plugin authenticates on install and receives a token bound to
  user, tenant, workspace, role, and entitlements.
- A minimal Web Console handles workspace, connector, approval, audit, member,
  and plan controls. It does not contain Mercury Chat.

### Commercial Direction

Initial packaging is value-based rather than MCP-call-based:

- Starter: one-company read and reporting
- Business: qualified document Create with preview and approval
- Accountant: multiple companies, users and full audit controls
- Enterprise: SSO, organization-specific policy and SLA

The exact price, quota and payment provider remain separate commercial
decisions. Entitlements must bind the authenticated user, tenant, workspace,
provider connection and capability before execution.

## Design Status

Designs 1-8 are approved. The canonical revised written specification is
`docs/superpowers/specs/2026-08-01-mercury-v1-aws-primary-agentcore-design.md`
and is pending owner review before an implementation plan may be written.
