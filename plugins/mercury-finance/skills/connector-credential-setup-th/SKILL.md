---
name: connector-credential-setup-th
description: Use when an accounting or ERP workflow cannot continue because a public Mercury workspace or connector credentials are not ready
---

# Connector Credential Setup TH

## Rule

Do not proceed to the next setup step until the current step succeeds. Ask only
for values Mercury reports as missing. Pass them to the credential tool once,
never repeat them in the response, and never place them in notes or flow YAML.

## Public Setup Sequence

1. Call `connector_status` with the current `workspace_id` when one exists.
2. If status is `requires_workspace`, call `create_public_workspace` and retain its `workspace_id` in this task.
3. Call `start_connector_setup` with `workspace_id`, connector ID, and environment.
4. Ask only for fields returned in `missing_fields` or `required_secret_fields`.
5. Call `submit_connector_credentials` once with the collected values.
6. Call `validate_connector_connection`; do not continue until it returns `ready` or `connected_read_only`.
7. Call `retrieve_workspace_context_pack` for connector-specific knowledge.

If validation fails, remain on step 6 and request only the correction indicated
by the sanitized error. Use `list_connectors` when the user has not chosen an ERP.

## Output

ตอบภาษาไทยแบบสั้น: โปรแกรม, environment, บริษัทถ้ามี, validation status,
enabled read capabilities, blocked capabilities, และ next tool. Never show raw
credentials, tokens, tax IDs, emails, or customer records.
