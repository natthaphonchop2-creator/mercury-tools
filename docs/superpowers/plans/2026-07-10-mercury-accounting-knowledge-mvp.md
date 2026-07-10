# Mercury Accounting Knowledge MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the source-backed Thai accounting knowledge required by Mercury's existing skills and stop RAG from returning unrelated ERP endpoint chunks for standards and tax questions.

**Architecture:** Keep Markdown plus Supabase as the knowledge source of truth. Add deterministic knowledge-domain routing before search, filter low-relevance results in `RagService`, and merge bounded connector plus accounting scopes for workspace context packs. Preserve current MCP tool schemas and add only response metadata.

**Tech Stack:** Python 3.11, FastMCP/MCP 1.26, Supabase Postgres/pgvector, Markdown/YAML front matter, pytest, Ruff, GitHub Actions, Render.

## Global Constraints

- Do not copy complete TFAC, IFRS, or Revenue Department publications.
- Use official source URLs and `source_verified_at: 2026-07-10` on every reviewed accounting-standard or tax page.
- `review_status: reviewed` means source-checked editorial content, not accountant assurance.
- Set `professional_review_required: true` on accounting-standard and tax pages.
- Keep existing MCP input schemas backward compatible.
- Keep production-changing ERP actions blocked.
- Never store connector credentials, tokens, tax IDs, emails, or transaction payloads in the Wiki.
- Use a hybrid-search relevance threshold of `0.20`.

---

## File Structure

**Create**

- `tests/test_knowledge_routing.py`: domain-routing and relevance-guard tests.
- `tests/test_accounting_knowledge_wiki.py`: regulated Wiki metadata and corpus tests.
- `wiki/standards/th/tfrs-15-revenue.md`: revenue recognition summary.
- `wiki/standards/th/tfrs-16-leases.md`: lease accounting summary.
- `wiki/standards/th/tfrs-9-financial-instruments.md`: receivables and ECL summary.
- `wiki/standards/th/tas-2-inventories.md`: inventory cost and NRV summary.
- `wiki/standards/th/tas-7-cash-flows.md`: cash-flow classification summary.
- `wiki/standards/th/tas-12-income-taxes.md`: current and deferred tax summary.
- `wiki/standards/th/tas-16-property-plant-equipment.md`: PPE lifecycle summary.
- `wiki/standards/th/tfrs-for-npaes-overview.md`: NPAE scope decision guide.
- `wiki/tax/th-withholding-tax-basics.md`: Thai WHT workflow summary.

**Modify**

- `src/mercury_tools/rag/routing.py`: infer and apply knowledge domains.
- `src/mercury_tools/rag/service.py`: enforce minimum relevance.
- `src/mercury_tools/rag/chunking.py`: validate reviewed regulated-document metadata.
- `src/mercury_tools/mcp/server.py`: route global search and merge workspace scopes.
- `src/mercury_tools/cli.py`: use the same domain routing as MCP.
- `tests/test_search_filters.py`: preserve connector-routing compatibility.
- `tests/test_mcp_rag_routing.py`: verify MCP response routing metadata.
- `tests/test_connector_mcp_tools.py`: verify workspace mixed retrieval.
- `wiki/tax/th-input-vat-basics.md`: replace draft with reviewed VAT guidance.
- `wiki/index.md`: expose all standard and tax pages.
- `README.md`: describe the accounting-standard and endpoint knowledge boundary.

---

### Task 1: Deterministic Knowledge-Domain Routing

**Files:**
- Create: `tests/test_knowledge_routing.py`
- Modify: `src/mercury_tools/rag/routing.py`
- Modify: `src/mercury_tools/mcp/server.py:850-907`
- Modify: `src/mercury_tools/cli.py:90-125`
- Test: `tests/test_search_filters.py`
- Test: `tests/test_mcp_rag_routing.py`

**Interfaces:**
- Consumes: existing `infer_connector_id(query)` and explicit MCP filter dictionaries.
- Produces: `KnowledgeDomain`, `infer_knowledge_domain(query)`, and `apply_knowledge_routing(query, filters) -> tuple[dict[str, Any], str | None, str | None]` where the tuple is applied filters, inferred connector, inferred domain.

- [ ] **Step 1: Write failing routing tests**

```python
from mercury_tools.rag.routing import apply_knowledge_routing, infer_knowledge_domain


def test_infers_accounting_standard_domain() -> None:
    assert infer_knowledge_domain("TFRS 15 การรับรู้รายได้") == "accounting_standard"


def test_infers_tax_domain() -> None:
    assert infer_knowledge_domain("สรุปภาษีซื้อ VAT เดือนนี้") == "tax"


def test_infers_connector_endpoint_domain() -> None:
    assert infer_knowledge_domain("FlowAccount invoice list endpoint") == "connector_endpoint"


def test_standard_query_does_not_apply_inferred_connector_to_general_standard() -> None:
    filters, connector, domain = apply_knowledge_routing(
        "FlowAccount ใช้ TFRS 15 รับรู้รายได้อย่างไร", None
    )
    assert filters == {"doc_type": "accounting_standard"}
    assert connector == "flowaccount"
    assert domain == "accounting_standard"


def test_explicit_filters_win() -> None:
    filters, connector, domain = apply_knowledge_routing(
        "VAT FlowAccount", {"connector": "flowaccount", "doc_type": "tax"}
    )
    assert filters == {"connector": "flowaccount", "doc_type": "tax"}
    assert connector is None
    assert domain is None
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_knowledge_routing.py -v`

Expected: import failures for `apply_knowledge_routing` and `infer_knowledge_domain`.

- [ ] **Step 3: Implement minimal deterministic routing**

```python
KnowledgeDomain = Literal[
    "connector_endpoint", "accounting_standard", "tax", "workflow", "general"
]

DOMAIN_DOC_TYPES = {
    "connector_endpoint": "endpoint_dictionary",
    "accounting_standard": "accounting_standard",
    "tax": "tax",
    "workflow": "workflow",
}


def apply_knowledge_routing(query, filters):
    applied = dict(filters or {})
    connector = None if applied.get("connector") else infer_connector_id(query)
    domain = None if applied.get("doc_type") else infer_knowledge_domain(query)
    if domain in DOMAIN_DOC_TYPES:
        applied["doc_type"] = DOMAIN_DOC_TYPES[domain]
    if connector and domain not in {"accounting_standard", "tax"}:
        applied["connector"] = connector
    return applied, connector, domain
```

Use ordered pattern groups so standard identifiers win over generic terms,
tax terms win over workflow terms, and explicit endpoint language selects
`connector_endpoint`.

- [ ] **Step 4: Route MCP and CLI through the new function**

Add `inferred_domain` to `search_knowledge` and `retrieve_context_pack` payloads.
Keep `inferred_connector` and `applied_filters` unchanged for compatibility.

- [ ] **Step 5: Run routing and MCP tests**

Run: `uv run pytest tests/test_knowledge_routing.py tests/test_search_filters.py tests/test_mcp_rag_routing.py -v`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/mercury_tools/rag/routing.py src/mercury_tools/mcp/server.py src/mercury_tools/cli.py tests/test_knowledge_routing.py tests/test_search_filters.py tests/test_mcp_rag_routing.py
git commit -m "Route Mercury accounting knowledge by domain"
```

---

### Task 2: Relevance Guard And Empty-Knowledge Status

**Files:**
- Modify: `src/mercury_tools/rag/service.py`
- Modify: `src/mercury_tools/mcp/server.py:850-907`
- Test: `tests/test_knowledge_routing.py`
- Test: `tests/test_mcp_rag_routing.py`

**Interfaces:**
- Consumes: `SearchResult.score` from Supabase or the in-memory store.
- Produces: `MIN_RELEVANCE_SCORE = 0.20`; `RagService.search(..., minimum_score: float = MIN_RELEVANCE_SCORE)`; MCP status `ok` or `no_relevant_knowledge`.

- [ ] **Step 1: Write failing relevance tests**

```python
def test_rag_service_drops_results_below_minimum_score() -> None:
    store = FakeStore(scores=[0.19, 0.05])
    service = RagService(store=store, embedder=FakeEmbedder())
    assert service.search("unknown standard") == []


def test_rag_service_keeps_results_at_threshold() -> None:
    store = FakeStore(scores=[0.31, 0.20])
    service = RagService(store=store, embedder=FakeEmbedder())
    assert [row.score for row in service.search("TFRS 15")] == [0.31, 0.20]
```

Add an MCP assertion that empty results return:

```python
assert payload["status"] == "no_relevant_knowledge"
assert payload["minimum_score"] == 0.2
assert payload["results"] == []
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_knowledge_routing.py tests/test_mcp_rag_routing.py -v`

Expected: low-score rows are still returned and MCP status fields are missing.

- [ ] **Step 3: Implement score filtering**

```python
MIN_RELEVANCE_SCORE = 0.20


def search(..., minimum_score: float = MIN_RELEVANCE_SCORE):
    rows = self.store.search_knowledge(...)
    return [row for row in rows if row.score >= minimum_score]
```

Add `status` and `minimum_score` to global MCP search/context-pack responses.
Do not expose a user-controlled threshold in v1.

- [ ] **Step 4: Run targeted tests**

Run: `uv run pytest tests/test_knowledge_routing.py tests/test_mcp_rag_routing.py tests/test_cli_search.py -v`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/mercury_tools/rag/service.py src/mercury_tools/mcp/server.py tests/test_knowledge_routing.py tests/test_mcp_rag_routing.py tests/test_cli_search.py
git commit -m "Reject low relevance Mercury knowledge"
```

---

### Task 3: Source-Backed Accounting And Tax Corpus

**Files:**
- Create: `tests/test_accounting_knowledge_wiki.py`
- Create: the eight files under `wiki/standards/th/` listed in File Structure.
- Create: `wiki/tax/th-withholding-tax-basics.md`
- Modify: `wiki/tax/th-input-vat-basics.md`
- Modify: `wiki/index.md`
- Modify: `src/mercury_tools/rag/chunking.py`

**Interfaces:**
- Consumes: `document_from_markdown(path, root=wiki_root)`.
- Produces: reviewed documents with `doc_type`, `jurisdiction`, `source_url`, `source_verified_at`, `professional_review_required`, citation headings, and stable `mercury://wiki/...` URIs.

- [ ] **Step 1: Write failing corpus tests**

```python
EXPECTED = {
    "tfrs-15-revenue.md": "TFRS 15",
    "tfrs-16-leases.md": "TFRS 16",
    "tfrs-9-financial-instruments.md": "TFRS 9",
    "tas-2-inventories.md": "TAS 2",
    "tas-7-cash-flows.md": "TAS 7",
    "tas-12-income-taxes.md": "TAS 12",
    "tas-16-property-plant-equipment.md": "TAS 16",
    "tfrs-for-npaes-overview.md": "TFRS for NPAEs",
}


def test_accounting_standard_pages_are_source_checked() -> None:
    for filename, standard_id in EXPECTED.items():
        document = document_from_markdown(STANDARDS / filename, root=WIKI)
        assert document.doc_type == "accounting_standard"
        assert document.jurisdiction == "TH"
        assert document.review_status == "reviewed"
        assert document.source_url.startswith("https://")
        assert document.metadata["standard_id"] == standard_id
        assert document.metadata["source_verified_at"] == "2026-07-10"
        assert document.metadata["professional_review_required"] is True
        assert chunk_document(document)
```

Add equivalent VAT/WHT assertions and verify every title appears in
`wiki/index.md`.

- [ ] **Step 2: Write failing regulated-metadata validation test**

```python
def test_reviewed_regulated_page_requires_source_verification(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text("""---
title: Bad standard
doc_type: accounting_standard
review_status: reviewed
jurisdiction: TH
source_url: https://example.com
---
# Bad
""", encoding="utf-8")
    with pytest.raises(ValueError, match="source_verified_at"):
        document_from_markdown(path, root=tmp_path)
```

- [ ] **Step 3: Run tests and confirm RED**

Run: `uv run pytest tests/test_accounting_knowledge_wiki.py -v`

Expected: missing files and missing validation behavior fail.

- [ ] **Step 4: Validate reviewed regulated metadata**

In `document_from_markdown`, apply the requirement only when:

```python
regulated = doc_type in {"accounting_standard", "tax"}
if regulated and review_status == "reviewed":
    if not metadata.get("source_url"):
        raise ValueError("reviewed regulated document requires source_url")
    if not metadata.get("source_verified_at"):
        raise ValueError("reviewed regulated document requires source_verified_at")
```

This avoids changing legacy index and connector-document semantics.

- [ ] **Step 5: Create the eight standards summaries**

Use this exact front-matter shape with topic-specific IDs, titles, URIs, and
source URLs:

```yaml
---
title: TFRS 15 Revenue From Contracts With Customers
doc_type: accounting_standard
jurisdiction: TH
review_status: reviewed
standard_id: TFRS 15
source_uri: mercury://wiki/standards/th/tfrs-15-revenue
source_url: https://acpro-std.tfac.or.th/standard/22/
source_verified_at: 2026-07-10
professional_review_required: true
metadata:
  international_reference: IFRS 15
  official_catalog: https://acpro-std.tfac.or.th/standard/22/
---
```

Every body must contain `Purpose`, `Core Accounting Model`, `Required Data And
Evidence`, `Mercury Review Checks`, `Limitations`, and `Official References`.
Write original summaries rather than quoting the standards.

- [ ] **Step 6: Upgrade VAT and create WHT knowledge**

Both tax pages use Revenue Department URLs and contain `Operational Summary`,
`Evidence Checklist`, `Reconciliation Checks`, `Escalate To Accountant`, and
`Official References`. The VAT page covers output/input tax, tax-invoice
evidence, non-deductible warnings, and filing limitations. The WHT page avoids
hard-coding rates where payer/payee/income classification determines the rate.

- [ ] **Step 7: Update the Wiki index and run tests**

Run: `uv run pytest tests/test_accounting_knowledge_wiki.py tests/test_chunking.py tests/test_wiki_content.py tests/test_peak_wiki_content.py -v`

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/mercury_tools/rag/chunking.py tests/test_accounting_knowledge_wiki.py wiki/index.md wiki/standards wiki/tax
git commit -m "Add Mercury accounting standards knowledge"
```

---

### Task 4: Mixed Connector And Accounting Workspace Context

**Files:**
- Modify: `src/mercury_tools/mcp/server.py:911-970`
- Test: `tests/test_connector_mcp_tools.py:924-1075`

**Interfaces:**
- Consumes: active workspace connector profile, `infer_knowledge_domain`, and `RagService.context_pack`.
- Produces: workspace context payload with `retrieval_scopes`, deduplicated context rows, `status`, and `minimum_score`.

- [ ] **Step 1: Write failing mixed-context tests**

```python
def test_workspace_vat_context_merges_connector_and_tax_scopes(monkeypatch) -> None:
    payload = retrieve_workspace_context_pack(
        workspace_id="workspace-token",
        query="สรุป VAT ภาษีซื้อ ภาษีขาย",
        task="vat_summary_th",
        max_chunks=8,
    )
    assert payload["status"] == "ok"
    assert payload["retrieval_scopes"] == ["connector:flowaccount", "tax:TH"]
    assert {row["metadata"]["doc_type"] for row in payload["context"]} == {
        "endpoint_dictionary",
        "tax",
    }
```

Add a TFRS test asserting that the standard-scope call has no connector filter,
and a deduplication test asserting repeated `chunk_id` values appear once.

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest tests/test_connector_mcp_tools.py -k 'workspace_context' -v`

Expected: current implementation performs one connector-only search.

- [ ] **Step 3: Implement bounded two-scope retrieval**

For inferred `tax` or `accounting_standard`:

```python
connector_limit = max(1, max_chunks // 2)
knowledge_limit = max(1, max_chunks - connector_limit)
connector_filters = {"connector": connector_id, "review_status": "reviewed"}
knowledge_filters = {
    "jurisdiction": "TH",
    "doc_type": inferred_domain,
    "review_status": "reviewed",
}
```

Map `accounting_standard` and `tax` directly to their doc types. Merge by
`chunk_id`, sort descending by score, and truncate to `max_chunks`. For other
domains keep one connector-scoped search.

- [ ] **Step 4: Add structured no-result behavior**

Return `no_relevant_knowledge` only when both scopes are empty. Keep
`requires_setup` behavior unchanged when no connector profile is ready.

- [ ] **Step 5: Run targeted tests**

Run: `uv run pytest tests/test_connector_mcp_tools.py tests/test_mcp_rag_routing.py -v`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/mercury_tools/mcp/server.py tests/test_connector_mcp_tools.py tests/test_mcp_rag_routing.py
git commit -m "Merge ERP and accounting context packs"
```

---

### Task 5: Documentation, Full Verification, Ingest, And Deployment

**Files:**
- Modify: `README.md`
- Modify: `docs/JUDGE_QUICKSTART.md` only if the live behavior or verification evidence changes.

**Interfaces:**
- Consumes: completed routing, corpus, relevance guard, and workspace context behavior.
- Produces: verified local package, ingested Supabase Wiki, deployed public MCP, and reproducible evidence.

- [ ] **Step 1: Document the knowledge boundary**

Add a concise README section stating:

```text
Mercury separates ERP endpoint dictionaries from accounting-standard and Thai
tax summaries. The host LLM receives source citations; Mercury does not replace
professional accounting judgment and does not reproduce full standards.
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest tests/test_knowledge_routing.py tests/test_accounting_knowledge_wiki.py tests/test_mcp_rag_routing.py tests/test_connector_mcp_tools.py -v
```

Expected: all focused tests pass.

- [ ] **Step 3: Run full quality gates**

Run:

```bash
uv run ruff check .
uv run pytest
python3 /Users/natthaphon/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/mercury-finance
```

Expected: Ruff passes, the test suite has zero failures, and plugin validation passes.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md docs/JUDGE_QUICKSTART.md
git commit -m "Document Mercury knowledge routing"
```

Skip the commit when `docs/JUDGE_QUICKSTART.md` does not need a change and the
README update was already included in another focused commit.

- [ ] **Step 5: Push the feature branch**

Run: `git push origin mercury-public-mcp-contest`

Expected: GitHub accepts all local commits.

- [ ] **Step 6: Ingest the Wiki into Supabase**

Trigger the repository Wiki-ingest workflow with GitHub CLI, wait for completion,
and require a successful conclusion. Use the existing workflow name discovered
with `gh workflow list`; do not invent a workflow filename.

- [ ] **Step 7: Wait for Render and verify health**

Run:

```bash
uv run mercury-tools remote verify --url https://mercury-tools-mcp.onrender.com --json
```

Expected: `ready: true`, health HTTP 200, Supabase true, and MCP reachable.

- [ ] **Step 8: Smoke-test live knowledge routing**

Through the remote MCP, verify:

- `TFRS 15 การรับรู้รายได้` returns `doc_type=accounting_standard`.
- `ภาษีซื้อ VAT ใบกำกับภาษี` returns `doc_type=tax`.
- an intentionally unknown standard returns `no_relevant_knowledge`.
- `FlowAccount invoice list endpoint` still returns the FlowAccount endpoint dictionary.
- `PEAK invoice list endpoint` still returns the PEAK endpoint dictionary.

- [ ] **Step 9: Record final evidence**

Update `docs/JUDGE_QUICKSTART.md` with the deployed commit, deployment evidence,
test count, and live smoke results without credentials or provider payloads.

- [ ] **Step 10: Final commit and push**

```bash
git add docs/JUDGE_QUICKSTART.md
git commit -m "Record accounting knowledge deployment"
git push origin mercury-public-mcp-contest
```

Expected: clean worktree, open PR checks passing, Supabase ingestion successful,
and the public MCP returning the new corpus.
