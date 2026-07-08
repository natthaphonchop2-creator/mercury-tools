"""Mercury Tools MCP server."""

# ruff: noqa: E501

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from mercury_tools.config import load_settings
from mercury_tools.db.product import SupabaseProductStore, slugify
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.mercury_runtime import connector_status as read_connector_status
from mercury_tools.mercury_runtime import skill_markdown
from mercury_tools.product import (
    build_connection_payload,
    create_client_token,
    is_authorized_bearer,
    validate_connect_request,
    verify_client_token,
)
from mercury_tools.product_ui import CONNECT_HTML as PRODUCT_CONNECT_HTML
from mercury_tools.prompts import get_prompt
from mercury_tools.rag.chunking import chunk_document, sha256_text
from mercury_tools.rag.embeddings import create_embedding_provider
from mercury_tools.rag.models import KnowledgeDocument, SearchFilters
from mercury_tools.rag.service import RagService
from mercury_tools.safety.redaction import redact_json

mcp = FastMCP("Mercury Tools")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, protected_path: str):
        super().__init__(app)
        self.protected_path = protected_path.rstrip("/") or "/"

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or not request.url.path.startswith(self.protected_path):
            return await call_next(request)

        if not is_authorized_bearer(load_settings(), request.headers.get("authorization")):
            return JSONResponse(
                {"error": "unauthorized", "message": "Valid bearer token is required."},
                status_code=401,
            )
        return await call_next(request)


def _service() -> RagService:
    settings = load_settings()
    return RagService(
        store=SupabaseRagStore(settings),
        embedder=create_embedding_provider(settings),
    )


def _audit(tool_name: str, input_payload: dict[str, Any], output_summary: dict[str, Any]) -> None:
    try:
        settings = load_settings()
        SupabaseRagStore(settings).record_audit_event(
            {
                "tool_name": tool_name,
                "input": input_payload,
                "output_summary": output_summary,
                "status": "ok",
                "metadata": {"runtime": "mcp"},
            }
        )
    except Exception:
        pass


def _filters(filters: dict[str, Any] | None) -> SearchFilters:
    filters = filters or {}
    return SearchFilters(
        jurisdiction=filters.get("jurisdiction"),
        connector=filters.get("connector"),
        doc_type=filters.get("doc_type"),
        review_status=filters.get("review_status"),
        effective_date=filters.get("effective_date"),
    )


def _client_token_payload(request: Request) -> dict[str, Any]:
    settings = load_settings()
    authorization = request.headers.get("authorization") or ""
    if not authorization.startswith("Bearer "):
        raise PermissionError("Mercury client token is required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith("mc_"):
        raise PermissionError("Mercury client token is required for product APIs.")
    return verify_client_token(settings, token)


def _product_store(settings=None) -> SupabaseProductStore:
    return SupabaseProductStore(settings or load_settings())


def _fallback_dashboard(token_payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "status": "degraded",
        "reason": reason,
        "workspace": {
            "name": token_payload.get("company"),
            "host_app": token_payload.get("host_app"),
        },
        "member": {"email": token_payload.get("sub")},
        "connectors": [],
        "connector_profiles": [],
        "skills": [],
        "events": [],
    }


def _json_error(error: str, message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse({"error": error, "message": message}, status_code=status_code)


@mcp.tool()
def search_knowledge(
    query: str,
    filters: dict[str, Any] | None = None,
    top_k: int = 8,
    mode: str = "hybrid",
) -> dict[str, Any]:
    """Search Mercury accounting knowledge and return citation-bearing chunks."""
    results = _service().search(query, filters=_filters(filters), top_k=top_k, mode=mode)
    payload = {
        "query": query,
        "results": [
            {
                "chunk_id": result.chunk_id,
                "document_uri": result.document_uri,
                "score": result.score,
                "text": result.text,
                "citation": result.citation,
                "source_title": result.source_title,
                "source_uri": result.source_uri,
                "source_url": result.source_url,
                "source_path": result.source_path,
            }
            for result in results
        ],
    }
    payload = redact_json(payload)
    _audit(
        "search_knowledge",
        {"query": query, "filters": filters, "top_k": top_k},
        {"count": len(results)},
    )
    return payload


@mcp.tool()
def retrieve_context_pack(
    query: str,
    task: str | None = None,
    filters: dict[str, Any] | None = None,
    max_chunks: int = 12,
) -> dict[str, Any]:
    """Return a context pack with citations for the host agent to answer with."""
    pack = _service().context_pack(
        query,
        task=task,
        filters=_filters(filters),
        max_chunks=max_chunks,
    )
    payload = redact_json(pack.as_dict())
    _audit("retrieve_context_pack", {"query": query, "task": task}, {"count": len(pack.results)})
    return payload


@mcp.tool()
def get_document(document_id: str) -> dict[str, Any]:
    """Fetch one indexed knowledge document by UUID or document URI."""
    document = SupabaseRagStore(load_settings()).get_document(document_id)
    payload = redact_json({"status": "ok" if document else "not_found", "document": document})
    _audit("get_document", {"document_id": document_id}, {"found": bool(document)})
    return payload


@mcp.tool()
def connector_status() -> dict[str, Any]:
    """Read sanitized Mercury connector status from Mercury runtime metadata."""
    payload = read_connector_status()
    _audit("connector_status", {}, {"status": payload.get("status")})
    return payload


@mcp.tool()
def run_accounting_skill(
    skill_id: str,
    inputs: dict[str, Any],
    evidence_mode: bool = False,
) -> dict[str, Any]:
    """Return a read-only accounting skill execution package for the host agent."""
    markdown = skill_markdown(skill_id)
    payload = redact_json(
        {
            "status": "ok" if markdown else "not_found",
            "skill_id": skill_id,
            "inputs": inputs,
            "evidence_mode": evidence_mode,
            "skill_markdown": markdown,
            "note": "v1 returns a read-only skill package; production writes are blocked.",
        }
    )
    _audit(
        "run_accounting_skill",
        {"skill_id": skill_id, "inputs": inputs},
        {"status": payload["status"]},
    )
    return payload


@mcp.resource("mercury://wiki/index")
def wiki_index() -> str:
    """Return the Mercury LLM Wiki index prompt resource."""
    return (
        "Use search_knowledge or retrieve_context_pack to fetch indexed wiki content "
        "with citations."
    )


@mcp.resource("mercury://wiki/doc/{document_id}")
def wiki_document(document_id: str) -> str:
    """Return one knowledge document body."""
    document = SupabaseRagStore(load_settings()).get_document(document_id)
    if not document:
        return "Document not found."
    return str(document.get("body") or "")


@mcp.resource("mercury://skills/{skill_id}")
def skill_resource(skill_id: str) -> str:
    """Return bundled Mercury skill markdown if available locally."""
    return skill_markdown(skill_id) or "Skill not found."


@mcp.resource("mercury://connectors")
def connectors_resource() -> str:
    """Return sanitized connector status."""
    return str(connector_status())


@mcp.resource("mercury://audit/{event_id}")
def audit_resource(event_id: str) -> str:
    """Return one sanitized MCP audit event from Supabase."""
    event = SupabaseRagStore(load_settings()).get_audit_event(event_id)
    if not event:
        return "Audit event not found."
    return str(redact_json(event))


@mcp.prompt()
def company_health_check_th() -> str:
    return get_prompt("company_health_check_th")


@mcp.prompt()
def vat_summary_th() -> str:
    return get_prompt("vat_summary_th")


@mcp.prompt()
def invoice_review_th() -> str:
    return get_prompt("invoice_review_th")


@mcp.prompt()
def management_report_th() -> str:
    return get_prompt("management_report_th")


@mcp.prompt()
def connector_setup_guide_th() -> str:
    return get_prompt("connector_setup_guide_th")


CONNECT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Mercury Connect</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #121a26;
      --panel: #192331;
      --panel-2: #101823;
      --line: #2e4051;
      --text: #f4f7fb;
      --muted: #91a0ae;
      --teal: #42c6bb;
      --gold: #f5bf45;
      --ok: #54d47f;
      --danger: #ff7070;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 78% 16%, rgba(66, 198, 187, .12), transparent 28%),
        linear-gradient(180deg, #172131 0%, var(--bg) 58%);
      color: var(--text);
      font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 800;
      letter-spacing: .02em;
    }
    .mark {
      display: grid;
      place-items: center;
      width: 38px;
      height: 38px;
      border: 1px solid rgba(66, 198, 187, .6);
      border-radius: 8px;
      color: var(--gold);
      background: rgba(16, 24, 35, .8);
      font-size: 24px;
    }
    .status {
      display: flex;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .status span {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: rgba(16, 24, 35, .66);
    }
    .grid {
      display: grid;
      grid-template-columns: 420px 1fr;
      gap: 18px;
      align-items: stretch;
    }
    section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(25, 35, 49, .92);
      box-shadow: 0 24px 80px rgba(0, 0, 0, .22);
    }
    .setup, .output { padding: 22px; }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 30px; line-height: 1.1; margin-bottom: 10px; }
    h2 { font-size: 17px; margin-bottom: 14px; }
    .lead { color: var(--muted); margin-bottom: 22px; }
    label {
      display: block;
      margin: 14px 0 6px;
      color: #b8c4cf;
      font-size: 13px;
      font-weight: 700;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid #334657;
      border-radius: 8px;
      background: #0f1721;
      color: var(--text);
      padding: 11px 12px;
      font: inherit;
      outline: none;
    }
    input:focus, select:focus, textarea:focus { border-color: var(--teal); }
    button {
      width: 100%;
      border: 0;
      border-radius: 8px;
      background: linear-gradient(135deg, var(--gold), #ffad4c);
      color: #1b160a;
      padding: 12px 14px;
      margin-top: 18px;
      font: 800 14px/1 ui-sans-serif, system-ui;
      cursor: pointer;
    }
    button.secondary {
      width: auto;
      margin: 0;
      padding: 9px 11px;
      color: var(--text);
      background: #223142;
      border: 1px solid #3a4c5d;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 18px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px;
      background: rgba(16, 24, 35, .72);
    }
    .card b { display: block; color: var(--teal); }
    .card small { color: var(--muted); }
    .result { display: none; }
    .result.active { display: block; }
    .codebar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin: 18px 0 8px;
    }
    pre {
      overflow: auto;
      white-space: pre-wrap;
      border: 1px solid #314455;
      border-radius: 8px;
      background: #0b1119;
      color: #d9e3eb;
      padding: 14px;
      min-height: 96px;
    }
    .message {
      min-height: 24px;
      color: var(--muted);
      margin-top: 10px;
    }
    .message.error { color: var(--danger); }
    .note {
      margin-top: 16px;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 900px) {
      header { align-items: flex-start; flex-direction: column; gap: 14px; }
      .grid { grid-template-columns: 1fr; }
      .cards { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="brand"><div class="mark">☿</div><div>Mercury Connect<br><span style="color:var(--muted);font-weight:600">Accounting AI MCP setup</span></div></div>
      <div class="status"><span id="status-supabase">Supabase</span><span id="status-embedding">Host AI mode</span><span id="status-auth">MCP auth</span></div>
    </header>
    <div class="grid">
      <section class="setup">
        <h1>Connect Mercury to your AI workspace.</h1>
        <p class="lead">Generate a client token and copy-ready MCP config for Codex, Cursor, Claude, or another MCP host.</p>
        <form id="connect-form">
          <label for="invite_code">Invite code</label>
          <input id="invite_code" name="invite_code" autocomplete="one-time-code" required />
          <label for="email">Work email</label>
          <input id="email" name="email" type="email" autocomplete="email" required />
          <label for="company">Company</label>
          <input id="company" name="company" autocomplete="organization" required />
          <label for="host_app">AI host</label>
          <select id="host_app" name="host_app">
            <option value="codex">Codex</option>
            <option value="cursor">Cursor</option>
            <option value="claude">Claude Desktop</option>
            <option value="generic">Generic MCP client</option>
          </select>
          <button type="submit">Generate connection</button>
        </form>
        <div id="message" class="message"></div>
      </section>
      <section class="output">
        <div class="cards">
          <div class="card"><b>MCP endpoint</b><small id="endpoint-label">checking...</small></div>
          <div class="card"><b>Knowledge store</b><small>Supabase RAG + citations</small></div>
          <div class="card"><b>Host AI</b><small>Codex / Cursor / Claude use Mercury tools</small></div>
          <div class="card"><b>Accounting scope</b><small>FlowAccount, PEAK, Express roadmap</small></div>
        </div>
        <div id="empty">
          <h2>Ready for user onboarding</h2>
          <p class="lead">Mercury issues a per-user signed token here. Users do not need the server bearer token file.</p>
        </div>
        <div id="result" class="result">
          <div class="codebar"><h2>Codex install</h2><button class="secondary" type="button" data-copy="codex-command">Copy</button></div>
          <pre id="codex-command"></pre>
          <div class="codebar"><h2>Remote MCP config</h2><button class="secondary" type="button" data-copy="mcp-config">Copy</button></div>
          <pre id="mcp-config"></pre>
          <p class="note">Keep this client token private. Regenerate it if a tester leaves the workspace.</p>
        </div>
      </section>
    </div>
  </main>
  <script>
    const form = document.querySelector('#connect-form');
    const message = document.querySelector('#message');
    const result = document.querySelector('#result');
    const empty = document.querySelector('#empty');
    const codex = document.querySelector('#codex-command');
    const config = document.querySelector('#mcp-config');

    async function loadStatus() {
      const res = await fetch('/api/status');
      const data = await res.json();
      document.querySelector('#endpoint-label').textContent = data.mcp_endpoint;
      document.querySelector('#status-supabase').textContent = data.supabase ? 'Supabase ready' : 'Supabase not ready';
      document.querySelector('#status-embedding').textContent = data.embedding_provider + ' embeddings';
      document.querySelector('#status-auth').textContent = data.http_auth_configured ? 'Auth ready' : 'Auth missing';
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      message.textContent = 'Generating connection...';
      message.className = 'message';
      const payload = Object.fromEntries(new FormData(form).entries());
      const response = await fetch('/api/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        message.textContent = data.message || 'Connection failed.';
        message.className = 'message error';
        return;
      }
      codex.textContent = data.codex.command;
      config.textContent = JSON.stringify(data.cursor.config, null, 2);
      result.classList.add('active');
      empty.style.display = 'none';
      message.textContent = 'Connection generated for ' + data.workspace.company + '.';
    });

    document.addEventListener('click', async (event) => {
      const id = event.target.getAttribute('data-copy');
      if (!id) return;
      await navigator.clipboard.writeText(document.getElementById(id).textContent);
      event.target.textContent = 'Copied';
      setTimeout(() => event.target.textContent = 'Copy', 1200);
    });

    loadStatus().catch(() => {});
  </script>
</body>
</html>"""


async def root(_: Request) -> Response:
    return HTMLResponse(PRODUCT_CONNECT_HTML)


async def status(_: Request) -> Response:
    settings = load_settings()
    return JSONResponse(
        {
            "name": "Mercury Tools MCP",
            "status": "ok",
            "supabase": settings.supabase_configured,
            "openai": settings.openai_configured,
            "embedding_provider": settings.embedding_provider,
            "embedding_configured": settings.embedding_configured,
            "transport": "streamable-http",
            "mcp_path": settings.mcp_path,
            "mcp_endpoint": settings.mcp_endpoint,
            "health": "/healthz",
            "connect": "/api/connect",
            "dashboard": "/api/dashboard",
            "connector_setup": "/api/connectors/setup",
            "skill_enable": "/api/skills/enable",
            "skill_upload": "/api/skills/upload",
            "http_auth_configured": settings.http_auth_configured,
            "invite_required": bool(settings.connect_invite_code),
        }
    )


async def connect(request: Request) -> Response:
    settings = load_settings()
    try:
        data = await request.json()
        connect_request = validate_connect_request(settings, data)
        token = create_client_token(settings, connect_request)
        token_payload = verify_client_token(settings, token)
        payload = build_connection_payload(
            public_base_url=settings.public_base_url or str(request.base_url).rstrip("/"),
            mcp_path=settings.mcp_path,
            token=token,
            email=connect_request.email,
            company=connect_request.company,
            host_app=connect_request.host_app,
        )
        payload["dashboard"] = {"api": "/api/dashboard", "url": str(request.base_url).rstrip("/")}
        if settings.supabase_configured:
            try:
                persisted = _product_store(settings).upsert_connection(connect_request, token_payload)
                payload["persistence"] = {
                    "status": "ok",
                    "workspace_id": persisted["workspace"]["id"],
                    "member_id": persisted["member"]["id"],
                }
            except RuntimeError as exc:
                payload["persistence"] = {
                    "status": "degraded",
                    "reason": str(exc),
                }
        else:
            payload["persistence"] = {
                "status": "degraded",
                "reason": "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are not configured.",
            }
        return JSONResponse(payload)
    except PermissionError as exc:
        return JSONResponse({"error": "forbidden", "message": str(exc)}, status_code=403)
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"error": "bad_request", "message": str(exc)}, status_code=400)


async def dashboard(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return JSONResponse(
                _fallback_dashboard(token_payload, reason="Supabase is not configured.")
            )
        store = _product_store(settings)
        store.seed_skill_catalog()
        return JSONResponse(redact_json(store.dashboard(token_payload)))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return JSONResponse(_fallback_dashboard(token_payload, reason=str(exc)))


async def setup_connector(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to save connector profiles.",
                status_code=503,
            )
        data = await request.json()
        profile = _product_store(settings).set_connector_profile(
            token_payload=token_payload,
            connector_id=str(data.get("connector_id") or "").strip().lower(),
            environment=str(data.get("environment") or "").strip().lower(),
            company_name=str(data.get("company_name") or "").strip(),
            metadata={"source": "connect-ui"},
        )
        return JSONResponse(redact_json({"status": "ok", "profile": profile}))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def enable_skill(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to manage workspace skills.",
                status_code=503,
            )
        data = await request.json()
        row = _product_store(settings).set_skill_enabled(
            token_payload=token_payload,
            skill_id=str(data.get("skill_id") or "").strip(),
            enabled=bool(data.get("enabled")),
        )
        return JSONResponse(redact_json({"status": "ok", "skill": row}))
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def upload_skill(request: Request) -> Response:
    settings = load_settings()
    try:
        token_payload = _client_token_payload(request)
        if not settings.supabase_configured:
            return _json_error(
                "service_unavailable",
                "Supabase is required to upload workspace skills.",
                status_code=503,
            )
        data = await request.json()
        title = str(data.get("title") or "").strip()
        markdown = str(data.get("markdown") or "").strip()
        if not title:
            raise ValueError("Skill title is required.")
        if len(markdown) < 20:
            raise ValueError("Skill Markdown is too short.")

        context = _product_store(settings).workspace_for_token(token_payload)
        if not context:
            raise ValueError("Workspace is not registered for this client token.")
        skill_hash = sha256_text(f"{title}\n{markdown}")[:8]
        skill_id = f"workspace-{slugify(title, fallback='skill')}-{skill_hash}"
        document_uri = f"mercury://workspace/{context['workspace']['id']}/skills/{skill_id}"
        document = KnowledgeDocument(
            document_uri=document_uri,
            title=title,
            body=markdown,
            sha256=sha256_text(markdown),
            source_uri=document_uri,
            source_title=title,
            jurisdiction=str(data.get("jurisdiction") or "TH"),
            connector=str(data.get("connector") or "") or None,
            doc_type="skill",
            review_status="draft",
            metadata={
                "workspace_id": context["workspace"]["id"],
                "skill_id": skill_id,
                "source": "connect-ui-upload",
            },
        )
        chunks = chunk_document(document)
        embeddings = create_embedding_provider(settings).embed_texts([chunk.text for chunk in chunks])
        SupabaseRagStore(settings).upsert_document_with_chunks(document, chunks, embeddings)
        upload = _product_store(settings).record_uploaded_skill(
            token_payload=token_payload,
            skill_id=skill_id,
            title=title,
            markdown=markdown,
            metadata={
                "category": str(data.get("category") or "custom"),
                "document_uri": document_uri,
                "chunks": len(chunks),
            },
        )
        return JSONResponse(
            redact_json(
                {
                    "status": "ok",
                    "skill_id": skill_id,
                    "document_uri": document_uri,
                    "chunks": len(chunks),
                    "upload": upload,
                }
            )
        )
    except PermissionError as exc:
        return _json_error("unauthorized", str(exc), status_code=401)
    except ValueError as exc:
        return _json_error("bad_request", str(exc), status_code=400)
    except RuntimeError as exc:
        return _json_error("service_unavailable", str(exc), status_code=503)


async def healthz(_: Request) -> Response:
    settings = load_settings()
    return JSONResponse(
        {
            "status": "ok",
            "supabase": settings.supabase_configured,
            "openai": settings.openai_configured,
            "embedding_provider": settings.embedding_provider,
            "embedding_configured": settings.embedding_configured,
            "mcp_path": settings.mcp_path,
            "http_auth_required": settings.http_require_auth,
            "http_auth_configured": settings.http_auth_configured,
        }
    )


def create_http_app(*, require_auth: bool | None = None):
    settings = load_settings()
    mcp.settings.streamable_http_path = settings.mcp_path
    if settings.public_base_url:
        public_url = urlparse(settings.public_base_url)
        allowed_host = public_url.netloc
        allowed_origin = f"{public_url.scheme}://{public_url.netloc}"
        if allowed_host and allowed_host not in mcp.settings.transport_security.allowed_hosts:
            mcp.settings.transport_security.allowed_hosts.append(allowed_host)
        if allowed_origin and allowed_origin not in mcp.settings.transport_security.allowed_origins:
            mcp.settings.transport_security.allowed_origins.append(allowed_origin)
    app = mcp.streamable_http_app()
    app.add_route("/", root, methods=["GET"])
    app.add_route("/api/status", status, methods=["GET"])
    app.add_route("/api/connect", connect, methods=["POST"])
    app.add_route("/api/dashboard", dashboard, methods=["GET"])
    app.add_route("/api/connectors/setup", setup_connector, methods=["POST"])
    app.add_route("/api/skills/enable", enable_skill, methods=["POST"])
    app.add_route("/api/skills/upload", upload_skill, methods=["POST"])
    app.add_route("/healthz", healthz, methods=["GET"])

    should_require_auth = settings.http_require_auth if require_auth is None else require_auth
    if should_require_auth:
        if not settings.http_auth_configured:
            raise RuntimeError(
                "MERCURY_TOOLS_HTTP_BEARER_TOKEN or MERCURY_CONNECT_SIGNING_SECRET is required when HTTP auth is enabled."
            )
        app.add_middleware(
            BearerAuthMiddleware,
            protected_path=settings.mcp_path,
        )
    return app


def serve(
    *,
    transport: str = "streamable-http",
    host: str = "0.0.0.0",
    port: int = 8000,
    require_auth: bool | None = None,
) -> None:
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport in {"http", "streamable-http"}:
        import uvicorn

        uvicorn.run(create_http_app(require_auth=require_auth), host=host, port=port)
        return
    raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    serve()
