# Mercury Finance Public Plugin Submission

This directory is the review-ready source bundle for the public one-click
Mercury Finance plugin. It is an **app-plus-skills** submission backed by the
hosted Streamable HTTP MCP server. It does not include a custom UI or web setup
application.

## Production endpoints

- MCP: `https://mercury-tools-mcp.onrender.com/mcp`
- Website: `https://mercury-tools-mcp.onrender.com/`
- Privacy: `https://mercury-tools-mcp.onrender.com/privacy`
- Terms: `https://mercury-tools-mcp.onrender.com/terms`
- Support: `https://mercury-tools-mcp.onrender.com/support`
- Domain challenge: `https://mercury-tools-mcp.onrender.com/.well-known/openai-apps-challenge`

## Build the skills upload

```bash
uv run python scripts/build_openai_plugin_bundle.py
```

The command writes a deterministic ZIP to
`dist/openai-plugin/mercury-finance-skills-public.zip` and prints its SHA-256.

## Portal handoff

1. Choose the OpenAI organization with a verified Developer Identity and Apps
   Management Write permission.
2. Create a **With MCP** app-plus-skills submission.
3. Copy fields from `listing.json` and use the production MCP URL above.
4. Copy the portal challenge token into the Render environment variable
   `OPENAI_APPS_CHALLENGE_TOKEN`, deploy, and verify the challenge URL returns
   only that token.
5. Scan tools and confirm every tool exposes `readOnlyHint`, `openWorldHint`,
   and `destructiveHint`.
6. Upload the generated skills ZIP, starter prompts, and the exact five
   positive plus three negative cases from `test-cases.json`.
7. Review `release-notes.md`, complete policy attestations, and submit.

After OpenAI approves and the publisher selects Publish, end users install the
plugin from the Plugins Directory with one click. Repository marketplace setup
is not required for the public listing.

## Capability boundary

The public plugin provides accounting knowledge, cited context packs, ERP
endpoint catalogs, non-secret connector profiles, and Mercury Flow planning and
closed-workspace execution. It does not accept ERP secrets or directly post
production ERP transactions. The separate repository-local plugin remains the
optional path for direct ERP GET/POST/PUT/PATCH/DELETE with local credentials
and approval controls.
