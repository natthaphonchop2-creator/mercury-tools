---
name: flowaccount-connector-setup-th
description: Use when a FlowAccount task needs local connector setup or connection troubleshooting
---

# FlowAccount Connector Setup TH

Use the selected FlowAccount environment and this gate without skipping or reordering:

1. Call `credential_status` for the active repository, connector, and environment.
2. If required credentials are missing, stop. Instruct the user to run locally:
   `mercury credentials setup <connector> --env <environment> --repo-root "<repo>"`
3. After the user confirms setup is complete, call `credential_status` again.
4. If it is still missing or not configured, stop and return to local setup. Do not run
   the connection test.
5. Only when the second status is configured, ask the user to run locally:
   `mercury credentials test <connector> --env <environment> --repo-root "<repo>"`
6. Continue only when the test reports `connected`.

Never ask for, accept, or paste credentials in chat. Never change environments
implicitly. On failure, report only the sanitized status and the next local command.
Respond in concise Thai.
