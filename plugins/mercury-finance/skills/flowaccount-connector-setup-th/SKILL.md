---
name: flowaccount-connector-setup-th
description: Use when a user wants to connect FlowAccount or a FlowAccount accounting task is blocked by incomplete connector setup
---

# FlowAccount Connector Setup TH

## Public Setup Sequence

1. Call `connector_status` with the current `workspace_id` when one exists.
2. If status is `requires_workspace`, call `create_public_workspace` and retain its `workspace_id` in this task.
3. Call `start_connector_setup` with `workspace_id`, `connector_id="flowaccount"`, and `production` or `sandbox`.
4. Ask only for fields returned in `missing_fields` or `required_secret_fields`: normally `client_id` and `client_secret`.
5. Call `submit_connector_credentials` once with the collected values; do not echo them.
6. Call `validate_connector_connection`; do not continue until it returns `ready` or `connected_read_only`.
7. Call `retrieve_workspace_context_pack` for connector-specific knowledge.

Do not skip or reorder these steps. If validation fails, stay on step 6.

## Known Presets

- Grant type: `client_credentials`; scope: `flowaccount-api`.
- Production: `https://openapi.flowaccount.com/v1` and `https://openapi.flowaccount.com/v1/token`.
- Sandbox: `https://openapi.flowaccount.com/test` and `https://openapi.flowaccount.com/test/token`.
- Official docs: `https://developers.flowaccount.com/`.

Use `list_connectors` to confirm availability and `search_knowledge` for the
FlowAccount Endpoint Data Dictionary. Public contest mode may inspect all
documented endpoints but executes only validation and declared read capabilities.
Create, update, delete, payment, email, share, attachment, and journal mutations
remain blocked.

ตอบภาษาไทยแบบกระชับ พร้อมโปรแกรม environment บริษัท validation status และ
next tool. Never display raw credentials or access tokens.
