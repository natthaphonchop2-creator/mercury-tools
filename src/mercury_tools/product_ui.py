"""Mercury Connect product UI."""

# ruff: noqa: E501

CONNECT_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Mercury Connect</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #111923;
      --panel: #182331;
      --panel-2: #101720;
      --line: #2f4354;
      --line-2: #405467;
      --text: #f5f8fb;
      --muted: #93a1ad;
      --teal: #42c6bb;
      --gold: #f5bf45;
      --ok: #54d47f;
      --warn: #ffb65c;
      --danger: #ff7070;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 16% 0%, rgba(66, 198, 187, .12), transparent 28%),
        linear-gradient(180deg, #172131 0%, var(--bg) 64%);
      color: var(--text);
      font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 44px;
    }
    header {
      display: grid;
      grid-template-columns: minmax(280px, 1fr) auto;
      gap: 18px;
      align-items: start;
      margin-bottom: 14px;
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
      font-size: 25px;
      font-weight: 900;
      flex: 0 0 auto;
    }
    .brand b {
      display: block;
      font-size: 18px;
      letter-spacing: .02em;
    }
    .brand span, .status span, .muted { color: var(--muted); }
    .status {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
      max-width: 560px;
    }
    .status span, .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: rgba(16, 23, 32, .72);
      color: #c6d0d9;
      font-size: 12px;
      font-weight: 800;
    }
    .topbar {
      display: flex;
      align-items: center;
      gap: 8px;
      overflow-x: auto;
      padding: 8px;
      margin-bottom: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(16, 23, 32, .66);
    }
    .topbar a {
      color: #cbd6df;
      text-decoration: none;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 13px;
      font-weight: 850;
      white-space: nowrap;
    }
    .topbar a.active {
      color: #151306;
      background: var(--gold);
      border-color: rgba(245, 191, 69, .7);
    }
    .topbar .hint {
      margin-left: auto;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .page { display: none; }
    .page.active { display: block; }
    .page-head {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(24, 35, 49, .92);
      padding: 20px;
      margin-bottom: 14px;
    }
    .page-head h1 {
      max-width: 760px;
      margin: 4px 0 8px;
      font-size: 28px;
      line-height: 1.12;
    }
    .page-head p {
      max-width: 780px;
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
      grid-template-columns: minmax(320px, 410px) minmax(0, 1fr);
      gap: 14px;
      align-items: start;
      min-width: 0;
    }
    .page-grid.reverse {
      grid-template-columns: minmax(0, 1fr) minmax(320px, 410px);
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(24, 35, 49, .94);
      box-shadow: 0 20px 64px rgba(0, 0, 0, .2);
      min-width: 0;
    }
    .panel { padding: 18px; }
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
    textarea { min-height: 160px; resize: vertical; }
    input:focus, select:focus, textarea:focus { border-color: var(--teal); }
    button {
      border: 0;
      border-radius: 8px;
      background: linear-gradient(135deg, var(--gold), #ffad4c);
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
      max-height: 220px;
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
      .topbar .hint { display: none; }
      .page-grid, .page-grid.reverse, .cards-2 { grid-template-columns: 1fr; }
      .console-grid { grid-template-columns: 1fr; }
      .two { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="brand">
        <div class="mark">☿</div>
        <div><b>Mercury Connect</b><span>MCP control plane for accounting AI hosts</span></div>
      </div>
      <div class="status">
        <span id="status-supabase">Supabase</span>
        <span id="status-embedding">Embeddings</span>
        <span id="status-auth">MCP auth</span>
        <span id="status-endpoint">Endpoint</span>
      </div>
    </header>

    <nav class="topbar" aria-label="Mercury sections">
      <a href="/connect" data-nav="connect">Connect</a>
      <a href="/workspace" data-nav="workspace">Workspace</a>
      <a href="/connectors" data-nav="connectors">Connectors</a>
      <a href="/skills" data-nav="skills">Skills</a>
      <a href="/audit" data-nav="audit">Audit</a>
      <span class="hint">Mercury stays MCP-first. The AI host still does the conversation.</span>
    </nav>

    <section class="page" data-page="connect">
      <div class="page-head">
        <span class="eyebrow">Mercury Control Plane</span>
        <h1>Connect an AI host to Mercury.</h1>
        <p>Create a signed workspace token, then use Mercury as a remote MCP server from Codex, Cursor, Claude Desktop, or another MCP client.</p>
      </div>
      <div class="page-grid">
        <div class="stack">
          <section class="panel">
            <h2>Generate connection</h2>
            <p class="lead">This creates access for a host AI. Mercury is not replacing the chat surface.</p>
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
            <div class="codebar"><h2>Remote MCP config</h2><button class="secondary" type="button" data-copy="mcp-config">Copy</button></div>
            <pre id="mcp-config"></pre>
            <button id="forget-token" class="danger" type="button">Forget local token</button>
          </section>

          <section class="panel">
            <h2>What this console controls</h2>
            <div class="flow">
              <div><b>1. AI host</b><br><span class="muted">Codex, Cursor, or Claude keeps the chat and model runtime.</span></div>
              <div><b>2. Mercury MCP</b><br><span class="muted">Mercury provides accounting skills, RAG context, connector metadata, and audit trails.</span></div>
              <div><b>3. Accounting systems</b><br><span class="muted">FlowAccount, PEAK, and Express are connector targets, not separate web-app modules.</span></div>
            </div>
          </section>
        </div>
      </div>
    </section>

    <section class="page" data-page="workspace">
      <div class="page-head">
        <span class="eyebrow">Workspace</span>
        <h1>Manage the Mercury workspace used by the AI host.</h1>
        <p>Keep workspace identity, member access, and host context separate from connector credentials.</p>
      </div>
      <div class="page-grid">
        <div class="surface">
          <section class="panel">
            <div class="row">
              <div>
                <h2>Workspace status</h2>
                <p class="lead" id="workspace-subtitle">Generate a connection to create or load a workspace.</p>
              </div>
              <button id="refresh-dashboard" class="secondary" type="button">Refresh</button>
            </div>
            <div class="console-grid">
              <div class="card"><b>Company</b><span id="company-card">not connected</span></div>
              <div class="card"><b>AI host</b><span id="host-card">not connected</span></div>
              <div class="card"><b>Status</b><span id="workspace-status">waiting</span></div>
            </div>
          </section>
        </div>
        <section class="panel">
          <div class="row"><h2>Team workspace</h2><span class="pill">invite preview</span></div>
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

    <section class="page" data-page="connectors">
      <div class="page-head">
        <span class="eyebrow">Connector Setup</span>
        <h1>Set the accounting program that Mercury can reference.</h1>
        <p>Connector setup stores profile and encrypted credential metadata for MCP tools. The host AI still asks permission before using sensitive capabilities.</p>
      </div>
      <div class="page-grid">
        <section class="panel">
          <div class="row"><h2>Accounting connector</h2><span class="pill">program profile</span></div>
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

    <section class="page" data-page="skills">
      <div class="page-head">
        <span class="eyebrow">Skills</span>
        <h1>Choose the accounting workflows your AI host may use.</h1>
        <p>Skills are instruction packs and RAG-indexed context. They are invoked by the host agent through Mercury MCP tools.</p>
      </div>
      <div class="page-grid reverse">
        <section class="panel">
          <div class="row"><h2>Skill marketplace</h2><span class="pill">workspace scoped</span></div>
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

    <section class="page" data-page="audit">
      <div class="page-head">
        <span class="eyebrow">Audit</span>
        <h1>Review usage signals without exposing raw secrets.</h1>
        <p>Mercury keeps audit events for MCP calls, setup changes, connector profiles, skill toggles, and uploads.</p>
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
  </main>
  <script>
    const state = {
      token: localStorage.getItem('mercury_client_token') || '',
      endpoint: ''
    };
    const $ = (selector) => document.querySelector(selector);
    const message = (selector, text, kind = '') => {
      const node = $(selector);
      node.textContent = text;
      node.className = 'message' + (kind ? ' ' + kind : '');
    };
    const authHeaders = () => ({ 'Authorization': 'Bearer ' + state.token });
    const pageNames = ['connect', 'workspace', 'connectors', 'skills', 'audit'];

    function pageFromPath() {
      const page = location.pathname.replace(/^\\//, '') || 'connect';
      return pageNames.includes(page) ? page : 'connect';
    }

    function setPage(page) {
      const nextPage = pageNames.includes(page) ? page : 'connect';
      document.querySelectorAll('[data-page]').forEach((node) => {
        node.classList.toggle('active', node.getAttribute('data-page') === nextPage);
      });
      document.querySelectorAll('[data-nav]').forEach((node) => {
        node.classList.toggle('active', node.getAttribute('data-nav') === nextPage);
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
          <small>${item.company_name || 'company not set'} · ${item.status}</small>
        </div>
      `).join('') : '<div class="item"><small>No connector profile yet.</small></div>';

      const members = data.members || [];
      $('#member-list').innerHTML = members.length ? members.map((item) => {
        const label = item.email_hash ? 'member ' + item.email_hash : (item.email === '[REDACTED_EMAIL]' ? 'member email hidden' : (item.email || 'workspace member'));
        const domain = item.email_domain ? ' · ' + item.email_domain : '';
        return `
          <div class="item">
            <strong>${label}</strong>
            <small>${item.role || 'member'} · ${item.status || 'active'}${domain}</small>
          </div>
        `;
      }).join('') : '<div class="item"><small>No members loaded yet.</small></div>';

      const skills = data.skills || [];
      $('#skills-list').innerHTML = skills.length ? skills.map((item) => `
        <div class="item">
          <div class="row">
            <div>
              <strong>${item.title}</strong>
              <small>${item.category} · ${item.summary}</small>
            </div>
            <button class="secondary" type="button" data-skill="${item.skill_id}" data-enabled="${item.enabled ? 'false' : 'true'}">${item.enabled ? 'Disable' : 'Enable'}</button>
          </div>
        </div>
      `).join('') : '<div class="item"><small>No skills loaded yet.</small></div>';

      const events = data.events || [];
      $('#event-list').innerHTML = events.length ? events.map((item) => `
        <div class="item">
          <strong>${item.event_type}</strong>
          <small>${item.created_at} · ${item.status}</small>
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

    document.querySelectorAll('[data-nav]').forEach((node) => {
      node.addEventListener('click', (event) => {
        event.preventDefault();
        const page = node.getAttribute('data-nav');
        history.pushState({}, '', page === 'connect' ? '/connect' : '/' + page);
        setPage(page);
      });
    });

    window.addEventListener('popstate', () => setPage(pageFromPath()));

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

    setPage(pageFromPath());
    loadStatus().then(() => {
      if (state.token) renderInstallFromToken(state.token);
    }).catch(() => {});
    loadDashboard().catch(() => {});
  </script>
</body>
</html>"""
