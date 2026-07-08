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
      background: linear-gradient(180deg, #172131 0%, var(--bg) 64%);
      color: var(--text);
      font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(1240px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 44px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 18px;
      margin-bottom: 18px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 280px;
    }
    .mark {
      display: grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border: 1px solid rgba(66, 198, 187, .7);
      border-radius: 8px;
      color: var(--gold);
      background: #0f1721;
      font-size: 25px;
      font-weight: 900;
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
    }
    .status span, .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: rgba(16, 23, 32, .7);
      color: #c6d0d9;
      font-size: 12px;
      font-weight: 700;
    }
    .layout {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 16px;
      align-items: start;
      min-width: 0;
    }
    section, .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(24, 35, 49, .94);
      box-shadow: 0 20px 64px rgba(0, 0, 0, .2);
      min-width: 0;
    }
    .panel { padding: 18px; }
    .stack { display: grid; gap: 12px; min-width: 0; }
    .console { display: grid; gap: 16px; min-width: 0; }
    .console-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      min-width: 0;
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
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 28px; line-height: 1.1; margin-bottom: 10px; }
    h2 { font-size: 16px; margin-bottom: 12px; }
    h3 { font-size: 14px; margin-bottom: 8px; color: #dbe5ec; }
    .lead { color: var(--muted); margin-bottom: 16px; }
    label {
      display: block;
      margin: 12px 0 6px;
      color: #b9c5cf;
      font-size: 12px;
      font-weight: 800;
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
    textarea { min-height: 132px; resize: vertical; }
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
      header { flex-direction: column; }
      .status { justify-content: flex-start; }
      .layout { grid-template-columns: 1fr; }
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
        <div><b>Mercury Connect</b><span>Accounting AI workspace, MCP, skills, and connectors</span></div>
      </div>
      <div class="status">
        <span id="status-supabase">Supabase</span>
        <span id="status-embedding">Embeddings</span>
        <span id="status-auth">MCP auth</span>
        <span id="status-endpoint">Endpoint</span>
      </div>
    </header>

    <div class="layout">
      <div class="stack">
        <section class="panel">
          <h1>Connect an AI host.</h1>
          <p class="lead">Create a Mercury workspace token, then use the generated MCP config in Codex, Cursor, Claude, or another MCP client.</p>
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
            <button class="full" type="submit">Generate connection</button>
          </form>
          <div id="connect-message" class="message"></div>
        </section>

        <section id="install-panel" class="panel hidden">
          <div class="codebar"><h2>Codex install</h2><button class="secondary" type="button" data-copy="codex-command">Copy</button></div>
          <pre id="codex-command"></pre>
          <div class="codebar"><h2>Remote MCP config</h2><button class="secondary" type="button" data-copy="mcp-config">Copy</button></div>
          <pre id="mcp-config"></pre>
          <button id="forget-token" class="danger" type="button">Forget local token</button>
        </section>
      </div>

      <div class="console">
        <section class="panel">
          <div class="row">
            <div>
              <h2>Workspace</h2>
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

        <section class="panel">
          <div class="row"><h2>Accounting connectors</h2><span class="pill">secrets stay outside Supabase</span></div>
          <div class="two">
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
            <div>
              <h3>Configured profiles</h3>
              <div id="connector-list" class="list"><div class="item"><small>No connector profile yet.</small></div></div>
            </div>
          </div>
        </section>

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

        <section class="panel">
          <h2>Usage and audit</h2>
          <div id="event-list" class="list"><div class="item"><small>No events yet.</small></div></div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const state = { token: localStorage.getItem('mercury_client_token') || '' };
    const $ = (selector) => document.querySelector(selector);
    const message = (selector, text, kind = '') => {
      const node = $(selector);
      node.textContent = text;
      node.className = 'message' + (kind ? ' ' + kind : '');
    };
    const authHeaders = () => ({ 'Authorization': 'Bearer ' + state.token });

    async function loadStatus() {
      const res = await fetch('/api/status');
      const data = await res.json();
      $('#status-supabase').textContent = data.supabase ? 'Supabase ready' : 'Supabase missing';
      $('#status-embedding').textContent = data.embedding_provider + ' embeddings';
      $('#status-auth').textContent = data.http_auth_configured ? 'MCP auth ready' : 'MCP auth missing';
      $('#status-endpoint').textContent = data.mcp_endpoint;
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

    loadStatus().catch(() => {});
    loadDashboard().catch(() => {});
  </script>
</body>
</html>"""
