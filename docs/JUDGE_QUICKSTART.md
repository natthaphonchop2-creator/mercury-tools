# Mercury Finance Judge Quickstart

Mercury Finance is a Codex plugin backed by a public Remote MCP. It adds ERP
connector discovery, cited accounting knowledge, guided setup skills, and
read-only Mercury Flows directly inside the judge's AI host. It is not a web
application.

## Install

Add the GitHub marketplace once:

```bash
codex plugin marketplace add natthaphonchop2-creator/mercury-tools \
  --ref main \
  --sparse .agents/plugins \
  --sparse plugins/mercury-finance
```

Open the Codex plugin list, install **Mercury Finance**, then start a fresh
task. The plugin already contains the hosted MCP server config:

```text
https://mercury-tools-mcp.onrender.com/mcp
```

No repository clone, bearer token, local Python runtime, or browser setup page
is required.

## First Use

Ask Mercury to begin setup. The host calls `connector_status`; when setup has
not started it calls `create_public_workspace` and receives an opaque
`workspace_id` beginning with `mw_`. The setup skill then:

1. selects an ERP and environment;
2. requests only the connector fields that are missing;
3. submits the values once and validates a low-impact read endpoint;
4. enables only public read capabilities;
5. retrieves ERP-specific RAG context with citations.

Keep the returned `workspace_id` in the current task. It routes state but is not
an authentication credential.

## Demo Prompts

```text
เริ่ม Mercury workspace แล้วแสดงโปรแกรมบัญชีที่เชื่อมได้
เชื่อม FlowAccount sandbox และตรวจ company info แบบ read-only
ค้นหา FlowAccount invoice endpoint แล้วทำ company health check แบบ dry run
```

The same flow supports PEAK UAT using the fields returned by its connector
manifest.

## What To Inspect

- `list_connectors` exposes ERP names, environments, required field names, and presets.
- `connector_capabilities` separates `read_capabilities` from `blocked_capabilities`.
- `search_knowledge` returns `inferred_connector`, applied filters, metadata, and citations.
- `retrieve_workspace_context_pack` stays inside the selected ERP documentation.
- `run_mercury_flow` blocks production-changing capabilities before connector dispatch.

## Verified Contest Build

Verification snapshot: **2026-07-10**

- Runtime commit: `7b1845bc1e3260aa51e41207711dc543c2d6aa4e`
- Render deploy: `dep-d985hj58nd3s73bp0fvg` (`live`)
- Hosted MCP: `https://mercury-tools-mcp.onrender.com/mcp`
- MCP contract: 22 tools; public stateful schemas use opaque `workspace_id` routing
- Public HTTP surface: legacy dashboard, upload, invite, and compatibility APIs disabled
- Local quality gate: 209 tests passed, 1 skipped; Ruff and plugin validation passed
- GitHub CI: passed on pull request #2
- Marketplace install: `mercury-finance` installed and enabled from the GitHub branch in an isolated Codex home
- Production smoke: 22 tools, 9 bundled skills, 4 connectors, public workspace creation, accounting skill execution, flow planning, and setup metadata all passed
- RAG evidence: production retrieval routes Thai accounting standards, Thai tax,
  FlowAccount endpoints, and PEAK endpoints into separate filtered domains with citations
- Abstention evidence: an unsupported `QZX-9999` accounting-standard query returned
  `no_relevant_knowledge` instead of unrelated context
- Supabase: hybrid-search endpoint terms are active; product and RAG tables use RLS with anonymous and authenticated DML denied

The verified runtime is deployed manually from pull request #2. Before the
judge handoff, merge that pull request so the `--ref main` install command and
Render auto-deploy both follow the same release commit.

## Contest Boundary

This build intentionally has no login or private tenant isolation. Use contest,
UAT, sandbox, or disposable demo credentials only. Credential values are
encrypted server-side and never returned, but `workspace_id` is routing state,
not authorization. Production mutations remain blocked. OAuth/private tenancy
is the post-contest hardening phase.
