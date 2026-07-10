# Mercury Accounting Knowledge MVP Design

Date: 2026-07-10
Status: Approved direction, written for review
Repo: `mercury-tools`

## Decision

Mercury will add a competition-ready accounting knowledge core without trying
to ingest every Thai or international accounting publication. The MVP will
cover the standards and Thai tax topics needed by the existing company-health,
VAT, invoice-review, and management-report skills.

The knowledge base will store concise, source-backed guidance and citations.
It will not copy complete TFAC or IFRS standards. Every standard page will state
that professional judgment and current official publications take precedence.

The RAG layer will route accounting-standard, tax, workflow, and ERP endpoint
queries into separate document domains. It will return no result when relevance
is too low instead of filling a context pack with unrelated endpoint chunks.

## Knowledge Scope

### Thai Accounting And Financial Reporting Standards

Create reviewed, source-backed summaries for:

1. TFRS 15: revenue from contracts with customers.
2. TFRS 16: leases.
3. TFRS 9: financial instruments, including receivables and expected credit
   loss at an operational-summary level.
4. TAS 2: inventories and cost measurement.
5. TAS 7: statement of cash flows.
6. TAS 12: income taxes at a recognition and presentation-summary level.
7. TAS 16: property, plant, equipment, depreciation, and disposal.
8. TFRS for NPAEs: scope and a practical decision guide for Thai non-publicly
   accountable entities.

Each page will include:

- standard ID and Thai/English title
- practical accounting questions it answers
- recognition and measurement overview
- required source data and evidence
- common review points and limitations
- related Mercury skills
- official TFAC and, when useful, IFRS source links
- source verification date
- `professional_review_required: true`

### Thai Tax Knowledge

1. Expand the existing Thai VAT page to cover output tax, input tax, tax invoice
   checks, non-deductible input tax warnings, reconciliation, and filing-context
   limitations.
2. Add a Thai withholding-tax page covering common workflow checks, document
   evidence, reconciliation, and the need to verify the current rate and form
   against Revenue Department guidance.

Tax pages will cite Revenue Department sources. Mercury will not present a tax
summary as a filing decision or legal opinion.

### Out Of Scope

- complete copies of TFAC, IFRS, or Revenue Department publications
- every TAS, TFRS, interpretation, notification, or tax ruling
- autonomous accounting conclusions without source evidence
- filing tax returns or posting journals
- legal or audit opinions

## Wiki Structure

```text
wiki/
  standards/
    th/
      tfrs-15-revenue.md
      tfrs-16-leases.md
      tfrs-9-financial-instruments.md
      tas-2-inventories.md
      tas-7-cash-flows.md
      tas-12-income-taxes.md
      tas-16-property-plant-equipment.md
      tfrs-for-npaes-overview.md
  tax/
    th-input-vat-basics.md
    th-withholding-tax-basics.md
  connectors/
    flowaccount-endpoint-dictionary.md
    peak-endpoint-dictionary.md
```

Standard pages use `doc_type: accounting_standard`. Tax pages use
`doc_type: tax`. Endpoint dictionaries remain `doc_type: endpoint_dictionary`.
All MVP pages use `jurisdiction: TH`, `review_status: reviewed`, and explicit
source metadata. Here, reviewed means source-checked editorial content, not an
accountant's assurance opinion.

## Source Policy

Primary sources are required:

- TFAC standards and explanatory-manual catalog:
  `https://acpro-std.tfac.or.th/standard/22/`
- IFRS issued-standards catalog for international cross-reference:
  `https://www.ifrs.org/issued-standards/`
- Thai Revenue Department VAT, tax invoice, and withholding-tax publications:
  `https://www.rd.go.th/`

Mercury stores original summaries, short identifiers, and links. It does not
store full copyrighted standard text. A page must not be marked reviewed unless
its source URL and source verification date are present.

## Knowledge Routing

Add a deterministic domain router alongside connector routing.

Supported domains:

- `connector_endpoint`
- `accounting_standard`
- `tax`
- `workflow`
- `general`

Explicit MCP filters always take precedence. Otherwise the router uses stable
Thai and English terms such as `TFRS`, `TAS`, `IFRS`, `มาตรฐานการบัญชี`,
`การรับรู้รายได้`, `สินค้าคงเหลือ`, `สัญญาเช่า`, `VAT`, `ภาษีซื้อ`,
`ภาษีขาย`, `ภาษีหัก ณ ที่จ่าย`, `endpoint`, and explicit ERP names.

The MCP response will include the inferred domain and applied filters so the
host AI can explain how Mercury routed the request.

## Relevance Guard

Hybrid retrieval will apply a minimum relevance score of `0.20` after database
search. Results below the threshold will not be returned to the host LLM.

When no chunk passes:

```json
{
  "status": "no_relevant_knowledge",
  "results": [],
  "minimum_score": 0.2
}
```

The threshold remains an application constant in v1 and is covered by tests.
It can become configurable after embedding quality and evaluation data improve.

## Workspace Context Packs

A ready connector must remain part of workspace retrieval, but a connector
filter must not exclude general accounting standards.

For standard or tax queries, `retrieve_workspace_context_pack` performs two
bounded searches:

1. Connector scope: selected ERP, reviewed connector documentation.
2. Knowledge scope: inferred accounting-standard or tax domain, `TH`, reviewed.

The server deduplicates chunks, sorts them by score, respects `max_chunks`, and
returns `retrieval_scopes`. For ordinary connector questions only connector
scope is required.

This allows a VAT request to contain both the selected ERP endpoint context and
Thai VAT guidance without treating one as the other.

## Existing MCP Compatibility

No new user-facing MCP tool is required for this knowledge milestone. Existing
tools keep their schemas:

- `search_knowledge`
- `retrieve_context_pack`
- `retrieve_workspace_context_pack`
- `get_document`
- `run_accounting_skill`
- `run_mercury_flow`

Responses may add `status`, `inferred_domain`, `minimum_score`, and
`retrieval_scopes`. Existing connector inference and explicit filters remain
compatible.

## Error And Safety Behavior

- Low-relevance retrieval returns an empty result, not unrelated context.
- Missing knowledge is stated explicitly.
- Draft documents are excluded from workspace context packs.
- Every result includes source title, source URI, URL or path, and heading.
- Standards and tax answers include an accountant-review point.
- No connector credentials, tokens, tax IDs, emails, or transaction payloads
  are added to the Wiki.

## Testing

### Unit Tests

- infer accounting-standard, tax, endpoint, workflow, and general domains
- preserve explicit `doc_type` and connector filters
- remove results below the relevance threshold
- keep high-relevance results and citations
- parse every new Wiki page with expected metadata
- reject reviewed pages missing official source metadata

### MCP Tests

- a TFRS query applies `doc_type=accounting_standard`
- a VAT query applies `doc_type=tax`
- an explicit FlowAccount endpoint query remains connector-scoped
- unrelated low-score endpoint chunks do not appear in a standards response
- workspace VAT retrieval merges connector and tax scopes
- workspace standard retrieval does not apply the connector filter to the
  standard scope

### Acceptance Checks

- live `search_knowledge` for TFRS 15 returns the TFRS 15 page with citation
- live VAT search returns the Thai VAT page, not an endpoint dictionary only
- an unknown standard returns `no_relevant_knowledge`
- FlowAccount and PEAK endpoint searches continue returning their reviewed
  endpoint dictionaries
- full tests and Ruff pass
- Wiki ingest succeeds and the remote MCP reports the new routing behavior

## Delivery

Implementation will update Wiki content, routing, RAG service behavior, MCP
payloads, tests, and the Wiki index. After local verification it will push the
feature branch, run the Supabase Wiki-ingest workflow, wait for Render deployment,
and smoke-test the public MCP endpoint.
