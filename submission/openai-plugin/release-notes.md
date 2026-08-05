# Mercury Finance V1 public submission

- Migrates the submission to the OAuth-protected Mercury V1 hosted MCP.
- First use opens secure Mercury sign-in, followed by the provider connection lifecycle
  and connection-scoped qualified capabilities.
- Provider credentials are encrypted server-side and never enter chat, model, RAG, log,
  or audit output.
- The tool map contains the complete 15-tool V1 hosted registry, including
  `prepare_document_create`, `render_document_preview`,
  `confirm_document_create`, and `get_operation_status`.
- Accounting Skills perform only exact qualified reads and cited context work.
- Document creation follows `prepare_document_create -> render_document_preview ->
  confirm_document_create -> get_operation_status`: it is capability-gated, uses an
  immutable preview, requires explicit confirmation, and returns sanitized status.
- `render_document_preview` is the only custom UI surface on hosts that support the
  published preview resource. Every other tool returns text and structured data.
- Includes five V1 positive cases and three V1 negative cases.
