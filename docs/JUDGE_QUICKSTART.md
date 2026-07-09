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

## Contest Boundary

This build intentionally has no login or private tenant isolation. Use contest,
UAT, sandbox, or disposable demo credentials only. Credential values are
encrypted server-side and never returned, but `workspace_id` is routing state,
not authorization. Production mutations remain blocked. OAuth/private tenancy
is the post-contest hardening phase.
