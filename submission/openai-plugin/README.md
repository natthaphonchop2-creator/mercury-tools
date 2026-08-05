# Mercury Finance Public Plugin Submission

This directory is the review-ready source bundle for the Mercury Finance
app-plus-skills submission. It uses the hosted Streamable HTTP MCP server without a
custom UI. The V1 MCP is OAuth protected: first use opens secure Mercury sign-in.

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
5. Scan tools and confirm each published tool has the matching V1 annotation.
6. Upload the generated skills ZIP, starter prompts, and the exact five positive plus
   three negative cases from `test-cases.json`.
7. Review `release-notes.md`, complete policy attestations, and submit.

## Capability boundary

Provider credentials are encrypted server-side and never enter chat, model, RAG, log,
or audit output. The plugin exposes only qualified capabilities for the selected
connection. A document create requires its exact qualified capability, an immutable
preview, and explicit confirmation; it never claims arbitrary provider writes.
