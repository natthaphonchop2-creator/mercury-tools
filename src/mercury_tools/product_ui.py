"""Mercury Connect product UI."""

# ruff: noqa: E501

from __future__ import annotations

from html import escape

PAGE_NAMES = ("start", "connect", "workspace", "connectors", "knowledge", "skills", "flows", "audit")

NAV_ITEMS = (
    ("start", "Start", "Console map"),
    ("connect", "Connect", "MCP host access"),
    ("workspace", "Workspace", "Company context"),
    ("connectors", "Programs", "Accounting systems"),
    ("knowledge", "Wiki", "RAG context"),
    ("skills", "Skills", "Agent playbooks"),
    ("flows", "Flows", "Runbooks"),
    ("audit", "Audit", "Evidence trail"),
)

STYLE = """
    :root {
      color-scheme: dark;
      --bg: #111923;
      --shell: #0e151f;
      --panel: #182331;
      --panel-2: #101720;
      --line: #314455;
      --line-2: #405467;
      --text: #f5f8fb;
      --muted: #93a1ad;
      --teal: #42c6bb;
      --gold: #f5bf45;
      --ok: #54d47f;
      --danger: #ff7070;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(90deg, rgba(66, 198, 187, .06), transparent 28%, transparent 72%, rgba(245, 191, 69, .05)),
        linear-gradient(180deg, #172131 0%, transparent 220px);
    }
    main {
      position: relative;
      z-index: 1;
      width: min(1240px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 44px;
    }
    .shell {
      display: grid;
      gap: 14px;
      align-items: start;
    }
    header {
      display: grid;
      grid-template-columns: minmax(280px, 1fr) auto;
      gap: 18px;
      align-items: start;
      margin-bottom: 4px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    .mark {
      display: grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border: 1px solid rgba(66, 198, 187, .72);
      border-radius: 8px;
      color: var(--gold);
      background: #0f1721;
      font-size: 24px;
      font-weight: 900;
      flex: 0 0 auto;
    }
    .brand b {
      display: block;
      font-size: 18px;
      letter-spacing: .02em;
    }
    .brand span, .status span, .muted { color: var(--muted); }
    .brand span { overflow-wrap: anywhere; }
    .status {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
      max-width: 620px;
      min-width: 0;
    }
    .status span, .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: rgba(16, 23, 32, .72);
      color: #c6d0d9;
      font-size: 12px;
      font-weight: 800;
      max-width: 100%;
      overflow-wrap: anywhere;
    }
    .nav-frame {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(14, 21, 31, .94);
      overflow: hidden;
    }
    .nav-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(24, 35, 49, .72);
    }
    .nav-head b { color: var(--teal); }
    .nav-head span { color: var(--muted); font-size: 12px; text-align: right; }
    .nav-list {
      display: grid;
      grid-template-columns: repeat(8, minmax(0, 1fr));
      gap: 8px;
      padding: 8px;
    }
    .nav-list a {
      display: grid;
      gap: 1px;
      color: #d5dee6;
      text-decoration: none;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 9px 10px;
      min-height: 54px;
      align-content: center;
    }
    .nav-list a b { font-size: 13px; }
    .nav-list a span { color: var(--muted); font-size: 12px; }
    .nav-list a.active {
      color: #151306;
      background: var(--gold);
      border-color: rgba(245, 191, 69, .72);
    }
    .nav-list a.active span { color: rgba(21, 19, 6, .72); }
    .content {
      min-width: 0;
    }
    .page {
      display: grid;
      gap: 14px;
    }
    .page-head {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(24, 35, 49, .92);
      padding: 20px;
    }
    .page-head h1 {
      max-width: 760px;
      margin: 4px 0 8px;
      font-size: 28px;
      line-height: 1.12;
    }
    .page-head p {
      max-width: 820px;
      color: var(--muted);
      margin: 0;
    }
    .eyebrow {
      color: var(--teal);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .page-grid {
      display: grid;
      grid-template-columns: minmax(320px, 430px) minmax(0, 1fr);
      gap: 14px;
      align-items: start;
      min-width: 0;
    }
    .page-grid.reverse {
      grid-template-columns: minmax(0, 1fr) minmax(320px, 430px);
    }
    .start-layout {
      display: grid;
      grid-template-columns: minmax(0, .9fr) minmax(320px, 1.1fr);
      gap: 14px;
      align-items: start;
      min-width: 0;
    }
    .gateway-card {
      display: grid;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: rgba(16, 23, 32, .72);
      text-decoration: none;
      color: #e5edf3;
    }
    .gateway-card b {
      color: var(--teal);
      font-size: 15px;
    }
    .gateway-card span {
      color: var(--muted);
      font-size: 13px;
    }
    .boundary-line {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(24, 35, 49, .94);
      box-shadow: 0 18px 58px rgba(0, 0, 0, .18);
      min-width: 0;
      padding: 18px;
    }
    .stack { display: grid; gap: 12px; min-width: 0; }
    .surface { display: grid; gap: 14px; min-width: 0; }
    .console-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      min-width: 0;
    }
    .cards-2 {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px;
      background: rgba(16, 23, 32, .72);
      min-height: 86px;
      min-width: 0;
    }
    .card b {
      display: block;
      color: var(--teal);
      margin-bottom: 4px;
    }
    .flow {
      display: grid;
      gap: 9px;
      margin-top: 12px;
    }
    .flow div {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: rgba(16, 23, 32, .62);
      color: #cbd6df;
    }
    .route-list {
      display: grid;
      gap: 9px;
      margin-top: 12px;
    }
    .route-link {
      display: grid;
      gap: 2px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      color: #e5edf3;
      background: rgba(16, 23, 32, .66);
      text-decoration: none;
    }
    .route-link:hover,
    .route-link:focus {
      border-color: rgba(66, 198, 187, .78);
    }
    .route-link b {
      color: var(--teal);
    }
    .route-link span {
      color: var(--muted);
      font-size: 12px;
    }
    .steps {
      display: grid;
      gap: 10px;
      counter-reset: step;
    }
    .step {
      position: relative;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(16, 23, 32, .66);
      padding: 12px 12px 12px 46px;
      min-height: 64px;
    }
    .step::before {
      counter-increment: step;
      content: counter(step);
      position: absolute;
      left: 12px;
      top: 12px;
      width: 24px;
      height: 24px;
      display: grid;
      place-items: center;
      border-radius: 999px;
      background: var(--gold);
      color: #1b160a;
      font-weight: 900;
      font-size: 12px;
    }
    .step b {
      display: block;
      color: #e5edf3;
      margin-bottom: 2px;
    }
    .step span {
      color: var(--muted);
      font-size: 12px;
    }
    h1, h2, h3, p { margin: 0; }
    h2 { font-size: 16px; margin-bottom: 12px; }
    h3 { font-size: 14px; margin-bottom: 8px; color: #dbe5ec; }
    .lead { color: var(--muted); margin-bottom: 16px; }
    label {
      display: block;
      margin: 12px 0 6px;
      color: #b9c5cf;
      font-size: 12px;
      font-weight: 850;
      letter-spacing: .01em;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid #34485a;
      border-radius: 8px;
      background: #0f1721;
      color: var(--text);
      padding: 10px 11px;
      font: inherit;
      outline: none;
    }
    textarea { min-height: 170px; resize: vertical; }
    input:focus, select:focus, textarea:focus { border-color: var(--teal); }
    button {
      border: 0;
      border-radius: 8px;
      background: var(--gold);
      color: #1b160a;
      padding: 11px 13px;
      font: 850 13px/1 ui-sans-serif, system-ui;
      cursor: pointer;
    }
    button.full { width: 100%; margin-top: 16px; }
    button.secondary {
      color: var(--text);
      background: #223142;
      border: 1px solid #3a4c5d;
    }
    button.danger {
      color: #fff;
      background: #53303a;
      border: 1px solid #7a4650;
    }
    .row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .list {
      display: grid;
      gap: 8px;
    }
    .item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: rgba(16, 23, 32, .66);
    }
    .item strong { display: block; }
    .item small { color: var(--muted); }
    .two {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .codebar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin: 16px 0 8px;
    }
    pre {
      overflow: auto;
      white-space: pre-wrap;
      border: 1px solid #314455;
      border-radius: 8px;
      background: #0b1119;
      color: #d9e3eb;
      padding: 13px;
      min-height: 84px;
      max-height: 260px;
      min-width: 0;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .message {
      min-height: 22px;
      color: var(--muted);
      margin-top: 10px;
    }
    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }
    .hidden { display: none !important; }
    @media (max-width: 980px) {
      header { grid-template-columns: 1fr; }
      .status { justify-content: flex-start; }
      .nav-head { align-items: flex-start; flex-direction: column; }
      .nav-head span { text-align: left; }
      .nav-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .start-layout { grid-template-columns: 1fr; }
      .page-grid, .page-grid.reverse, .cards-2 { grid-template-columns: 1fr; }
      .console-grid { grid-template-columns: 1fr; }
      .boundary-line { grid-template-columns: 1fr; }
      .two { grid-template-columns: 1fr; }
    }
    @media (max-width: 620px) {
      main { width: min(100vw - 20px, 1240px); padding-top: 14px; }
      header { gap: 10px; }
      .brand { align-items: flex-start; }
      .brand span {
        display: block;
        max-width: calc(100vw - 94px);
        line-height: 1.35;
      }
      .status {
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        width: 100%;
        max-width: none;
        justify-content: stretch;
      }
      .status span {
        width: 100%;
        text-align: center;
        font-size: 11px;
      }
      .nav-list { grid-template-columns: 1fr; }
      .page-head h1 { font-size: 24px; }
    }
"""

PAGE_CONTENT: dict[str, str] = {
    "start": """
      <section class="page" data-page="start">
        <div class="page-head">
          <span class="eyebrow">Mercury Setup Console</span>
          <h1>Prepare the accounting tool layer for an AI host.</h1>
          <p>The AI host remains the product surface. Mercury is MCP-first: this console only prepares access, accounting programs, wiki context, skills, flows, and audit records.</p>
        </div>
        <div class="start-layout">
          <section class="panel">
            <div class="row"><h2>First-time setup path</h2><span class="pill">remote MCP</span></div>
            <div class="steps">
              <div class="step"><b>Connect AI host</b><span>Create a workspace token for Codex, Cursor, Claude Desktop, or another MCP client.</span></div>
              <div class="step"><b>Select accounting program</b><span>Choose FlowAccount, PEAK, Express, or a future connector for this company.</span></div>
              <div class="step"><b>Enable skills and flows</b><span>Turn accounting playbooks into callable MCP tools and repeatable runbooks.</span></div>
              <div class="step"><b>Keep evidence</b><span>Log setup and tool activity with sanitized audit records.</span></div>
            </div>
          </section>

          <section class="panel">
            <h2>Open one setup section</h2>
            <p class="lead">Each section is its own route. Users do not configure everything from one dashboard screen.</p>
            <div class="stack">
              <a class="gateway-card" href="/connect"><b>1. Connect host</b><span>Generate MCP setup instructions for the AI program that will run the conversation.</span></a>
              <a class="gateway-card" href="/connectors"><b>2. Accounting programs</b><span>Select the accounting system and save connector credentials server-side.</span></a>
              <a class="gateway-card" href="/skills"><b>3. Skills and flows</b><span>Enable playbooks and YAML runbooks that the host agent can call.</span></a>
              <a class="gateway-card" href="/audit"><b>4. Audit trail</b><span>Review sanitized setup and tool-call evidence.</span></a>
            </div>
          </section>

          <section class="panel" style="grid-column:1 / -1">
            <div class="row"><h2>Mercury product boundary</h2><span class="pill">not a chat web app</span></div>
            <div class="boundary-line">
              <div class="card"><b>AI host</b><span>Codex, Cursor, Claude, or a customer agent owns chat, model, and UX.</span></div>
              <div class="card"><b>Mercury MCP</b><span>Provides accounting tools, wiki context, skills, flows, and audit records.</span></div>
              <div class="card"><b>Accounting systems</b><span>FlowAccount, PEAK, Express, and future connectors stay behind controlled tools.</span></div>
            </div>
          </section>
        </div>
      </section>
    """,
    "connect": """
      <section class="page" data-page="connect">
        <div class="page-head">
          <span class="eyebrow">MCP Connect</span>
          <h1>Connect an AI host to Mercury.</h1>
          <p>Create a signed workspace token for Codex, Cursor, Claude Desktop, or another MCP client. Mercury supplies tools, context, and audit evidence; the host AI keeps the conversation.</p>
        </div>
        <div class="page-grid">
          <div class="stack">
            <section class="panel">
              <h2>Generate host connection</h2>
              <p class="lead">Use this when a new user, company, or AI host needs access to Mercury.</p>
              <form id="connect-form">
                <label for="invite_code">Invite code</label>
                <input id="invite_code" name="invite_code" type="password" autocomplete="one-time-code" required />
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
                <button class="full" type="submit">Generate MCP connection</button>
              </form>
              <div id="connect-message" class="message"></div>
            </section>

            <section class="panel">
              <h2>Restore workspace</h2>
              <p class="lead">Paste an existing Mercury client token to load this workspace on another browser or machine.</p>
              <form id="restore-form">
                <label for="restore_token">Mercury client token</label>
                <input id="restore_token" name="token" type="password" autocomplete="off" />
                <button class="full" type="submit">Load workspace</button>
              </form>
              <div id="restore-message" class="message"></div>
            </section>
          </div>

          <div class="surface">
            <section id="install-panel" class="panel hidden">
              <div class="codebar"><h2>Codex install</h2><button class="secondary" type="button" data-copy="codex-command">Copy</button></div>
              <pre id="codex-command"></pre>
              <div class="codebar"><h2>MCP client config</h2><button class="secondary" type="button" data-copy="mcp-config">Copy</button></div>
              <pre id="mcp-config"></pre>
              <button id="forget-token" class="danger" type="button">Forget local token</button>
            </section>

            <section class="panel">
              <h2>Mercury boundary</h2>
              <div class="flow">
                <div><b>Host AI</b><br><span class="muted">Codex, Cursor, Claude, or a customer agent owns the chat and model runtime.</span></div>
                <div><b>Mercury MCP</b><br><span class="muted">Mercury exposes accounting tools, RAG context, skills, flows, and audit records.</span></div>
                <div><b>Accounting systems</b><br><span class="muted">FlowAccount, PEAK, Express, and future systems are connector targets.</span></div>
              </div>
            </section>
          </div>
        </div>
      </section>
    """,
    "workspace": """
      <section class="page" data-page="workspace">
        <div class="page-head">
          <span class="eyebrow">Workspace</span>
          <h1>Set the company context used by the AI host.</h1>
          <p>A workspace is the product boundary for one company: members, selected AI host, connector profiles, skills, flows, and audit state.</p>
        </div>
        <div class="page-grid">
          <div class="surface">
            <section class="panel">
              <div class="row">
                <div>
                  <h2>Workspace status</h2>
                  <p class="lead" id="workspace-subtitle">Generate or restore a Mercury connection first.</p>
                </div>
                <button id="refresh-dashboard" class="secondary" type="button">Refresh</button>
              </div>
              <div class="console-grid">
                <div class="card"><b>Company</b><span id="company-card">not connected</span></div>
                <div class="card"><b>AI host</b><span id="host-card">not connected</span></div>
                <div class="card"><b>Status</b><span id="workspace-status">waiting</span></div>
              </div>
            </section>
            <section class="panel">
              <h2>Workspace model</h2>
              <div class="cards-2">
                <div class="card"><b>Scope</b><span>one company context per client token</span></div>
                <div class="card"><b>Runtime</b><span>host AI calls Mercury tools through MCP</span></div>
                <div class="card"><b>Storage</b><span>Supabase product state and audit metadata</span></div>
                <div class="card"><b>Secrets</b><span>never returned in console pages or MCP output</span></div>
              </div>
            </section>
          </div>
          <section class="panel">
            <div class="row"><h2>Team access</h2><span class="pill">workspace scoped</span></div>
            <form id="team-form">
              <label for="member_email">Member email</label>
              <input id="member_email" name="email" type="email" autocomplete="email" />
              <label for="member_role">Role</label>
              <select id="member_role" name="role">
                <option value="member">Member</option>
                <option value="admin">Admin</option>
                <option value="viewer">Viewer</option>
              </select>
              <button class="full" type="submit">Invite member</button>
              <div id="team-message" class="message"></div>
            </form>
            <h3 style="margin-top:16px">Members</h3>
            <div id="member-list" class="list"><div class="item"><small>No members loaded yet.</small></div></div>
          </section>
        </div>
      </section>
    """,
    "connectors": """
      <section class="page" data-page="connectors">
        <div class="page-head">
          <span class="eyebrow">Connector Setup</span>
          <h1>Choose the accounting program Mercury tools may use.</h1>
          <p>This page stores only the program profile, environment, company label, and encrypted credential metadata. The AI host calls connector tools through Mercury MCP.</p>
        </div>
        <div class="page-grid">
          <section class="panel">
            <div class="row"><h2>Accounting program</h2><span class="pill">program profile</span></div>
            <form id="connector-form">
              <label for="connector_id">Program</label>
              <select id="connector_id" name="connector_id">
                <option value="flowaccount">FlowAccount</option>
                <option value="peak">PEAK Accounting</option>
                <option value="express">Express Account</option>
              </select>
              <label for="environment">Environment</label>
              <select id="environment" name="environment">
                <option value="production">Production</option>
                <option value="sandbox">Sandbox</option>
                <option value="local">Local gateway</option>
              </select>
              <label for="connector_company_name">Company name in accounting program</label>
              <input id="connector_company_name" name="company_name" />
              <button class="full" type="submit">Save connector profile</button>
              <div id="connector-message" class="message"></div>
            </form>
          </section>

          <div class="surface">
            <section class="panel">
              <h2>Configured profiles</h2>
              <div id="connector-list" class="list"><div class="item"><small>No connector profile yet.</small></div></div>
            </section>
            <section class="panel">
              <div class="row"><h2>Encrypted credential vault</h2><span class="pill">server-side only</span></div>
              <form id="credential-form">
                <div class="two">
                  <div>
                    <label for="credential_client_id">Client id / API key</label>
                    <input id="credential_client_id" name="client_id" type="password" autocomplete="off" />
                  </div>
                  <div>
                    <label for="credential_client_secret">Client secret</label>
                    <input id="credential_client_secret" name="client_secret" type="password" autocomplete="off" />
                  </div>
                </div>
                <button class="full" type="submit">Save encrypted credentials</button>
                <div id="credential-message" class="message"></div>
              </form>
            </section>
          </div>
        </div>
      </section>
    """,
    "knowledge": """
      <section class="page" data-page="knowledge">
        <div class="page-head">
          <span class="eyebrow">Knowledge</span>
          <h1>Maintain the cited accounting wiki behind Mercury MCP.</h1>
          <p>Knowledge is not a chat page. It is the RAG source layer that MCP tools use to return context packs, citations, and prompt-ready evidence to the host AI.</p>
        </div>
        <div class="page-grid">
          <section class="panel">
            <h2>LLM Wiki store</h2>
            <div class="cards-2">
              <div class="card"><b>Search tool</b><span>search_knowledge</span></div>
              <div class="card"><b>Context tool</b><span>retrieve_context_pack</span></div>
              <div class="card"><b>Database</b><span>Supabase Postgres + pgvector</span></div>
              <div class="card"><b>Citations</b><span>source, URI, chunk id, effective date metadata</span></div>
            </div>
          </section>
          <section class="panel">
            <h2>Knowledge responsibilities</h2>
            <div class="flow">
              <div><b>Ingest</b><br><span class="muted">Markdown, connector docs, official references, and reviewed accounting notes become chunks.</span></div>
              <div><b>Retrieve</b><br><span class="muted">Host agents ask Mercury for context packs before answering accounting tasks.</span></div>
              <div><b>Audit</b><br><span class="muted">MCP calls are logged with sanitized input hashes and output summaries.</span></div>
            </div>
          </section>
        </div>
      </section>
    """,
    "skills": """
      <section class="page" data-page="skills">
        <div class="page-head">
          <span class="eyebrow">Skills</span>
          <h1>Choose the accounting playbooks your AI host may use.</h1>
          <p>Skills are instruction packs with workflow rules, required evidence, output schemas, and accountant review points. They are invoked through Mercury MCP tools.</p>
        </div>
        <div class="page-grid reverse">
          <section class="panel">
            <div class="row"><h2>Skill library</h2><span class="pill">workspace scoped</span></div>
            <div id="skills-list" class="list"><div class="item"><small>No skills loaded yet.</small></div></div>
          </section>

          <section class="panel">
            <h2>Upload a workspace skill</h2>
            <p class="lead">Upload Markdown instructions. Mercury stores it as a draft skill and indexes it into the RAG wiki with citations.</p>
            <form id="upload-form">
              <div class="two">
                <div>
                  <label for="skill_title">Skill title</label>
                  <input id="skill_title" name="title" placeholder="Monthly Shopee/TikTok report" />
                </div>
                <div>
                  <label for="skill_category">Category</label>
                  <input id="skill_category" name="category" placeholder="reporting" />
                </div>
              </div>
              <label for="skill_markdown">Skill Markdown</label>
              <textarea id="skill_markdown" name="markdown" placeholder="# Skill name&#10;&#10;Goal, inputs, workflow, output schema..."></textarea>
              <button class="full" type="submit">Upload and enable skill</button>
              <div id="upload-message" class="message"></div>
            </form>
          </section>
        </div>
      </section>
    """,
    "flows": """
      <section class="page" data-page="flows">
        <div class="page-head">
          <span class="eyebrow">Mercury Flows</span>
          <h1>Build repeatable accounting agent runbooks.</h1>
          <p>Flows are YAML runbooks for MCP hosts. They package connector state checks, RAG retrieval, skill execution, validation steps, and report handoffs without turning Mercury into the chat surface.</p>
        </div>
        <div class="page-grid reverse">
          <section class="panel">
            <div class="row">
              <div>
                <h2>Flow editor</h2>
                <p class="lead">Validate and dry-run before a host agent uses the workflow.</p>
              </div>
              <span class="pill">dry-run first</span>
            </div>
            <form id="flow-form">
              <label for="flow_title">Flow title</label>
              <input id="flow_title" name="title" value="Company Health Check" />
              <label for="flow_yaml">Flow YAML</label>
              <textarea id="flow_yaml" name="flow_yaml">name: Company Health Check
description: Read-only Mercury flow for a Thai accounting health-check handoff.
tags: [accounting, read-only, flowaccount]
env:
  jurisdiction: TH
  connector: flowaccount
onFlowStart:
  - connectorStatus:
      saveAs: connector
---
- retrieveContextPack:
    query: "company health check revenue VAT cash flow accounting Thailand"
    task: "company_health_check_th"
    filters:
      jurisdiction: "${jurisdiction}"
      connector: "${connector}"
      review_status: reviewed
    maxChunks: 8
    saveAs: context
- runSkill:
    skillId: company-health-check-th
    inputs:
      task: "Prepare a concise Thai company health-check answer."
      connector: "${connector}"
      context_query: "{{ context.query }}"
    evidenceMode: true
    saveAs: skill
- emitReport:
    title: "Company health-check context pack"
    sections:
      - "Use the retrieved citations and skill package to answer in Thai."
      - "Do not expose raw tax IDs, emails, bearer tokens, or API keys."</textarea>
              <div class="two" style="margin-top:12px">
                <button id="flow-validate" class="secondary" type="button">Validate</button>
                <button id="flow-run" class="secondary" type="button">Dry-run</button>
              </div>
              <button class="full" type="submit">Save flow to workspace</button>
              <div id="flow-message" class="message"></div>
            </form>
            <div class="codebar"><h2>Flow result</h2><span class="pill">sanitized</span></div>
            <pre id="flow-result">No flow action yet.</pre>
          </section>

          <div class="surface">
            <section class="panel">
              <div class="row"><h2>Saved flows</h2><span class="pill">workspace scoped</span></div>
              <div id="flow-list" class="list"><div class="item"><small>No workspace flows saved yet.</small></div></div>
            </section>
            <section class="panel">
              <h2>Flow boundary</h2>
              <div class="cards-2">
                <div class="card"><b>Purpose</b><span>repeatable AI-agent runbooks</span></div>
                <div class="card"><b>Execution</b><span>MCP tool calls and context handoffs</span></div>
                <div class="card"><b>Writes</b><span>blocked unless future approval flow allows them</span></div>
                <div class="card"><b>Owner</b><span>workspace, not local machine config</span></div>
              </div>
            </section>
          </div>
        </div>
      </section>
    """,
    "audit": """
      <section class="page" data-page="audit">
        <div class="page-head">
          <span class="eyebrow">Audit</span>
          <h1>Review usage signals without exposing raw secrets.</h1>
          <p>Mercury keeps audit events for MCP calls, setup changes, connector profiles, skill toggles, uploads, and flow activity.</p>
        </div>
        <div class="page-grid">
          <section class="panel">
            <h2>Usage and audit</h2>
            <div id="event-list" class="list"><div class="item"><small>No events yet.</small></div></div>
          </section>
          <section class="panel">
            <h2>Runtime boundary</h2>
            <div class="cards-2">
              <div class="card"><b>MCP endpoint</b><span id="audit-endpoint">loading</span></div>
              <div class="card"><b>Credential policy</b><span>raw secrets are not returned to clients</span></div>
              <div class="card"><b>Default mode</b><span>read-oriented tools and context packs</span></div>
              <div class="card"><b>Host model</b><span>chosen by Codex, Cursor, Claude, or the user's agent</span></div>
            </div>
          </section>
        </div>
      </section>
    """,
}

SCRIPT = """
    const state = {
      token: localStorage.getItem('mercury_client_token') || '',
      endpoint: '',
      flows: []
    };
    const noopNode = {
      textContent: '',
      innerHTML: '',
      value: '',
      className: '',
      style: {},
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener() {},
      reset() {}
    };
    const $ = (selector) => document.querySelector(selector) || noopNode;
    const message = (selector, text, kind = '') => {
      const node = $(selector);
      node.textContent = text;
      node.className = 'message' + (kind ? ' ' + kind : '');
    };
    const authHeaders = () => ({ 'Authorization': 'Bearer ' + state.token });

    function setActiveNavigation() {
      const page = document.body.getAttribute('data-page') || 'start';
      document.querySelectorAll('[data-nav]').forEach((node) => {
        node.classList.toggle('active', node.getAttribute('data-nav') === page);
      });
    }

    async function loadStatus() {
      const res = await fetch('/api/status');
      const data = await res.json();
      state.endpoint = data.mcp_endpoint;
      $('#status-supabase').textContent = data.supabase ? 'Supabase ready' : 'Supabase missing';
      $('#status-embedding').textContent = data.embedding_provider + ' embeddings';
      $('#status-auth').textContent = data.http_auth_configured ? 'MCP auth ready' : 'MCP auth missing';
      $('#status-endpoint').textContent = data.mcp_endpoint;
      $('#audit-endpoint').textContent = data.mcp_endpoint;
    }

    async function authFetch(path, options = {}) {
      if (!state.token) throw new Error('Generate a Mercury connection first.');
      const response = await fetch(path, {
        ...options,
        headers: {
          ...(options.headers || {}),
          ...authHeaders(),
        }
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || data.error || 'Request failed.');
      return data;
    }

    function renderInstall(payload) {
      $('#install-panel').classList.remove('hidden');
      $('#codex-command').textContent = payload.codex.command;
      $('#mcp-config').textContent = JSON.stringify(payload.cursor.config, null, 2);
    }

    function renderInstallFromToken(token) {
      if (!state.endpoint) return;
      $('#install-panel').classList.remove('hidden');
      $('#codex-command').textContent = "export MERCURY_MCP_TOKEN='" + token + "'\\n" +
        'codex mcp add mercury-tools --url ' + state.endpoint + ' --bearer-token-env-var MERCURY_MCP_TOKEN';
      $('#mcp-config').textContent = JSON.stringify({
        mcpServers: {
          'mercury-tools': {
            url: state.endpoint,
            headers: { Authorization: 'Bearer ${MERCURY_MCP_TOKEN}' }
          }
        }
      }, null, 2);
    }

    function renderDashboard(data) {
      const workspace = data.workspace || {};
      const member = data.member || {};
      $('#workspace-subtitle').textContent = member.email ? member.email : 'Workspace loaded from Mercury token.';
      $('#company-card').textContent = workspace.name || workspace.company || 'not connected';
      $('#host-card').textContent = member.host_app || workspace.host_app || 'unknown';
      $('#workspace-status').textContent = data.status || workspace.status || 'ok';

      const profiles = data.connector_profiles || [];
      $('#connector-list').innerHTML = profiles.length ? profiles.map((item) => `
        <div class="item">
          <strong>${item.display_name || item.connector_id} <span class="pill">${item.environment}</span></strong>
          <small>${item.company_name || 'company not set'} - ${item.status}</small>
        </div>
      `).join('') : '<div class="item"><small>No connector profile yet.</small></div>';

      const members = data.members || [];
      $('#member-list').innerHTML = members.length ? members.map((item) => {
        const label = item.email_hash ? 'member ' + item.email_hash : (item.email === '[REDACTED_EMAIL]' ? 'member email hidden' : (item.email || 'workspace member'));
        const domain = item.email_domain ? ' - ' + item.email_domain : '';
        return `
          <div class="item">
            <strong>${label}</strong>
            <small>${item.role || 'member'} - ${item.status || 'active'}${domain}</small>
          </div>
        `;
      }).join('') : '<div class="item"><small>No members loaded yet.</small></div>';

      const skills = data.skills || [];
      $('#skills-list').innerHTML = skills.length ? skills.map((item) => `
        <div class="item">
          <div class="row">
            <div>
              <strong>${item.title}</strong>
              <small>${item.category} - ${item.summary}</small>
            </div>
            <button class="secondary" type="button" data-skill="${item.skill_id}" data-enabled="${item.enabled ? 'false' : 'true'}">${item.enabled ? 'Disable' : 'Enable'}</button>
          </div>
        </div>
      `).join('') : '<div class="item"><small>No skills loaded yet.</small></div>';

      state.flows = data.flows || [];
      $('#flow-list').innerHTML = state.flows.length ? state.flows.map((item) => `
        <div class="item">
          <div class="row">
            <div>
              <strong>${item.title || item.name}</strong>
              <small>${item.flow_id} - ${item.command_count || 0} commands - ${item.status || 'draft'}</small>
            </div>
            <button class="secondary" type="button" data-flow-load="${item.flow_id}">Load</button>
          </div>
        </div>
      `).join('') : '<div class="item"><small>No workspace flows saved yet.</small></div>';

      const events = data.events || [];
      $('#event-list').innerHTML = events.length ? events.map((item) => `
        <div class="item">
          <strong>${item.event_type}</strong>
          <small>${item.created_at} - ${item.status}</small>
        </div>
      `).join('') : '<div class="item"><small>No events yet.</small></div>';
    }

    async function loadDashboard() {
      if (!state.token) return;
      try {
        const data = await authFetch('/api/dashboard');
        renderDashboard(data);
      } catch (error) {
        $('#workspace-status').textContent = 'needs connection';
        $('#workspace-subtitle').textContent = error.message;
      }
    }

    $('#connect-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      message('#connect-message', 'Generating connection...');
      const payload = Object.fromEntries(new FormData(event.target).entries());
      const response = await fetch('/api/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        message('#connect-message', data.message || 'Connection failed.', 'error');
        return;
      }
      state.token = data.token;
      localStorage.setItem('mercury_client_token', state.token);
      renderInstall(data);
      message('#connect-message', 'Connection generated for ' + data.workspace.company + '.', 'ok');
      await loadDashboard();
    });

    $('#connector-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        message('#connector-message', 'Saving connector profile...');
        const payload = Object.fromEntries(new FormData(event.target).entries());
        const data = await authFetch('/api/connectors/setup', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        message('#connector-message', 'Saved ' + data.profile.connector_id + ' profile.', 'ok');
        await loadDashboard();
      } catch (error) {
        message('#connector-message', error.message, 'error');
      }
    });

    $('#restore-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = Object.fromEntries(new FormData(event.target).entries());
      const token = (payload.token || '').trim();
      if (!token.startsWith('mc_')) {
        message('#restore-message', 'Mercury client token must start with mc_.', 'error');
        return;
      }
      state.token = token;
      localStorage.setItem('mercury_client_token', state.token);
      try {
        await loadDashboard();
        renderInstallFromToken(state.token);
        message('#restore-message', 'Workspace loaded from Mercury token.', 'ok');
        event.target.reset();
      } catch (error) {
        localStorage.removeItem('mercury_client_token');
        state.token = '';
        message('#restore-message', error.message, 'error');
      }
    });

    $('#credential-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        message('#credential-message', 'Encrypting credentials...');
        const payload = Object.fromEntries(new FormData(event.target).entries());
        const data = await authFetch('/api/connectors/credentials', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            connector_id: $('#connector_id').value,
            environment: $('#environment').value,
            credentials: payload
          })
        });
        message('#credential-message', 'Saved encrypted credentials for ' + data.credentials.connector_id + '.', 'ok');
        event.target.reset();
        await loadDashboard();
      } catch (error) {
        message('#credential-message', error.message, 'error');
      }
    });

    $('#team-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        message('#team-message', 'Inviting member...');
        const payload = Object.fromEntries(new FormData(event.target).entries());
        const data = await authFetch('/api/team/invite', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        message('#team-message', 'Invited workspace member.', 'ok');
        event.target.reset();
        await loadDashboard();
      } catch (error) {
        message('#team-message', error.message, 'error');
      }
    });

    $('#skills-list').addEventListener('click', async (event) => {
      const skillId = event.target.getAttribute('data-skill');
      if (!skillId) return;
      try {
        await authFetch('/api/skills/enable', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ skill_id: skillId, enabled: event.target.getAttribute('data-enabled') === 'true' })
        });
        await loadDashboard();
      } catch (error) {
        alert(error.message);
      }
    });

    $('#upload-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        message('#upload-message', 'Uploading skill...');
        const payload = Object.fromEntries(new FormData(event.target).entries());
        const data = await authFetch('/api/skills/upload', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        message('#upload-message', 'Uploaded ' + data.skill_id + ' and indexed ' + data.chunks + ' chunks.', 'ok');
        event.target.reset();
        await loadDashboard();
      } catch (error) {
        message('#upload-message', error.message, 'error');
      }
    });

    $('#flow-list').addEventListener('click', (event) => {
      const flowId = event.target.getAttribute('data-flow-load');
      if (!flowId) return;
      const flow = state.flows.find((item) => item.flow_id === flowId);
      if (!flow) return;
      $('#flow_title').value = flow.title || flow.name || '';
      $('#flow_yaml').value = flow.yaml || '';
      message('#flow-message', 'Loaded ' + flowId + '.', 'ok');
    });

    $('#flow-validate').addEventListener('click', async () => {
      try {
        message('#flow-message', 'Validating flow...');
        const data = await authFetch('/api/flows/validate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ flow_yaml: $('#flow_yaml').value })
        });
        $('#flow-result').textContent = JSON.stringify(data, null, 2);
        message('#flow-message', 'Flow syntax is valid.', 'ok');
      } catch (error) {
        $('#flow-result').textContent = error.message;
        message('#flow-message', error.message, 'error');
      }
    });

    $('#flow-run').addEventListener('click', async () => {
      try {
        message('#flow-message', 'Running dry-run...');
        const data = await authFetch('/api/flows/run', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ flow_yaml: $('#flow_yaml').value, dry_run: true })
        });
        $('#flow-result').textContent = JSON.stringify(data, null, 2);
        message('#flow-message', 'Dry-run plan is ready.', 'ok');
      } catch (error) {
        $('#flow-result').textContent = error.message;
        message('#flow-message', error.message, 'error');
      }
    });

    $('#flow-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        message('#flow-message', 'Saving flow...');
        const payload = Object.fromEntries(new FormData(event.target).entries());
        const data = await authFetch('/api/flows/save', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        $('#flow-result').textContent = JSON.stringify(data, null, 2);
        message('#flow-message', 'Saved ' + data.flow.flow_id + '.', 'ok');
        await loadDashboard();
      } catch (error) {
        $('#flow-result').textContent = error.message;
        message('#flow-message', error.message, 'error');
      }
    });

    $('#refresh-dashboard').addEventListener('click', loadDashboard);
    $('#forget-token').addEventListener('click', () => {
      localStorage.removeItem('mercury_client_token');
      state.token = '';
      location.reload();
    });
    document.addEventListener('click', async (event) => {
      const id = event.target.getAttribute('data-copy');
      if (!id) return;
      await navigator.clipboard.writeText(document.getElementById(id).textContent);
      event.target.textContent = 'Copied';
      setTimeout(() => event.target.textContent = 'Copy', 1200);
    });

    setActiveNavigation();
    loadStatus().then(() => {
      if (state.token) renderInstallFromToken(state.token);
    }).catch(() => {});
    loadDashboard().catch(() => {});
"""


def _nav(active_page: str) -> str:
    items = []
    for key, label, hint in NAV_ITEMS:
        active = " active" if key == active_page else ""
        href = "/" if key == "start" else f"/{key}"
        items.append(
            f'<a href="{href}" data-nav="{key}" class="{active.strip()}"><b>{escape(label)}</b><span>{escape(hint)}</span></a>'
        )
    return "\n".join(items)


def render_connect_html(active_page: str = "connect") -> str:
    """Render one focused Mercury setup-console page."""
    page = active_page if active_page in PAGE_NAMES else "start"
    title = "Mercury Connect" if page in {"start", "connect"} else f"Mercury {page.title()}"
    body = PAGE_CONTENT[page]

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>{STYLE}</style>
</head>
<body data-page="{escape(page)}">
  <main>
    <div class="shell">
      <header>
        <div class="brand">
          <div class="mark">Mx</div>
          <div><b>Mercury Connect</b><span>MCP setup console for accounting AI hosts</span></div>
        </div>
        <div class="status">
          <span id="status-supabase">Supabase</span>
          <span id="status-embedding">Embeddings</span>
          <span id="status-auth">MCP auth</span>
          <span id="status-endpoint">Endpoint</span>
        </div>
      </header>

      <section class="nav-frame" aria-label="Mercury setup-console navigation">
        <div class="nav-head">
          <b>Setup sections</b>
          <span>Separate pages for setup. Mercury stays MCP-first; the AI host remains the product surface.</span>
        </div>
        <nav class="nav-list">
          {_nav(page)}
        </nav>
      </section>

      <div class="content">
        {body}
      </div>
    </div>
  </main>
  <script>{SCRIPT}</script>
</body>
</html>"""
