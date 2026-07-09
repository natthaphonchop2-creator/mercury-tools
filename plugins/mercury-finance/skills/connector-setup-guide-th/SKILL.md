---
name: connector-setup-guide-th
description: Use when the user asks which accounting connector to choose or what FlowAccount, PEAK, Express, or custom ERP setup requires
---

# Connector Setup Guide TH

Call `connector_status` with the current `workspace_id`. If it returns
`requires_workspace`, call `create_public_workspace` and retain the returned
`workspace_id` in this task.

Use `list_connectors` to show neutral connector states. After the user chooses
an ERP and environment, call `start_connector_setup` for exact presets and
required field names. Never invent requirements from memory.

Route FlowAccount to `flowaccount-connector-setup-th`, PEAK to
`peak-connector-setup-th`, and other ERP/API systems to
`connector-credential-setup-th`. Call `validate_connector_connection` only
after all required values have been submitted. Stay on the failed step until
validation succeeds.

ตอบภาษาไทยแบบ checklist สั้น ๆ: connector, environment, preset, missing field
names, validation result, enabled read capabilities, and next tool. Never repeat
raw credentials.
