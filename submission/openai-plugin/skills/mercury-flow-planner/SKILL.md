---
name: mercury-flow-planner
description: Use when the user asks for a controlled accounting action plan for a connected provider.
---

# Mercury Action Planner

1. Establish the workspace with `get_mercury_context` and identify the intended
   provider connection with `list_provider_connections`.
2. Call `connector_status` and `list_provider_capabilities` before proposing an action.
3. Use `get_capability_schema` only for an exact qualified capability and reviewed
   version. State any unmet qualification or provider readiness requirement.
4. Use `run_accounting_skill` only when the requested work is supported by its exact
   qualified read capability.
5. For document creation, follow this lifecycle exactly:
   `prepare_document_create` -> `render_document_preview` ->
   `confirm_document_create` -> `get_operation_status`.
6. `render_document_preview` is the only custom UI surface on hosts that support it.
   It is read-only and exposes sanitized review data; all other lifecycle steps use
   text and structured output.
7. Do not call `confirm_document_create` until the user explicitly confirms the
   displayed immutable preview version. Then use `get_operation_status` to report the
   sanitized result.

Mercury does not perform arbitrary writes. A plan remains a plan until the selected
connection exposes the qualified capability and the user gives explicit user approval.
