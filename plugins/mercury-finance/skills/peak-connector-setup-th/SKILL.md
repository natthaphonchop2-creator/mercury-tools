---
name: peak-connector-setup-th
description: Use when a user wants to connect PEAK Accounting or a PEAK Open API task is blocked by incomplete connector setup
---

# PEAK Connector Setup TH

## Public Setup Sequence

1. Call `connector_status` with the current `workspace_id` when one exists.
2. If status is `requires_workspace`, call `create_public_workspace` and retain its `workspace_id` in this task.
3. Call `start_connector_setup` with `workspace_id`, `connector_id="peak"`, and `production`, `uat`, or `sandbox`.
4. Ask only for fields returned in `missing_fields` or `required_secret_fields`: normally `connect_id`, `connect_key`, `application_code`, and `user_token`.
5. Call `submit_connector_credentials` once with the collected values; do not echo them.
6. Call `validate_connector_connection`; do not continue until it returns `ready` or `connected_read_only`.
7. Call `retrieve_workspace_context_pack` for connector-specific knowledge.

Do not skip or reorder these steps. If PEAK returns a non-success `resCode`,
stay on step 6 and report only sanitized status and correlation data.

## Known Presets

- Auth: HMAC-SHA1 ClientToken via `POST /clienttoken`, then low-impact `GET /user` validation.
- Production: `https://api.peakaccount.com/api/v1`.
- UAT/sandbox: `https://peakengineapidev.azurewebsites.net/api/v1`.
- Official docs: `https://developers.peakaccount.com/reference/peak-open-api`.

Use `list_connectors` and `search_knowledge` for the PEAK Endpoint Data
Dictionary. Public contest mode enables GET/read capabilities only. Contact,
document, payment, approve, void, invitation, attachment, and journal mutations
remain blocked.

ตอบภาษาไทยแบบกระชับ พร้อมโปรแกรม environment บริษัท validation status และ
next tool. Never display ConnectId, ConnectKey, UserToken, ClientToken, or raw data.
