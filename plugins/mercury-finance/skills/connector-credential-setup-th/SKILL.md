---
name: connector-credential-setup-th
description: Use when a user needs to connect FlowAccount, PEAK Accounting, Express Account, or another ERP/API system before running accounting workflows
---

# Connector Credential Setup TH

## Rule

Do not proceed to the next setup step until the current step is complete and validated.

Do not ask the user to paste API keys, client secrets, bearer tokens, or refresh tokens into normal chat. Use the host app's secure MCP credential path or a server-side credential vault flow.

## Steps

1. Call `list_connectors`.
2. Ask the user to choose one connector if none is selected.
3. Call `start_connector_setup` with connector id and environment.
4. Show preset values that Mercury already knows.
5. Ask only for required missing credential fields through a secure input path.
6. Call `submit_connector_credentials`.
7. Call `validate_connector_connection`.
8. If validation returns `ready`, continue to the requested accounting workflow.
9. If validation fails, stay on the failed step and ask for only the missing correction.

## Output

ตอบเป็นภาษาไทยแบบกระชับ ระบุโปรแกรม บริษัทถ้ามี environment, enabled capabilities, และ next safe command. Never show raw credentials.
