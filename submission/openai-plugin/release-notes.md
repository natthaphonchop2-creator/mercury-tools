# Mercury Finance V1 public submission

- Migrates the submission to the OAuth-protected Mercury V1 hosted MCP.
- First use opens secure Mercury sign-in, followed by the provider connection lifecycle
  and connection-scoped qualified capabilities.
- Provider credentials are encrypted server-side and never enter chat, model, RAG, log,
  or audit output.
- The tool map contains only the currently published V1 hosted tools.
- Accounting Skills perform only exact qualified reads and cited context work.
- Document creation is capability-gated and requires an immutable preview plus explicit
  confirmation; the submission does not claim arbitrary provider writes.
- Includes five V1 positive cases and three V1 negative cases.
