---
name: connector-setup-guide-th
description: Use when the user needs to choose or configure an accounting or ERP connector
---

# Connector Setup Guide TH

Confirm the active repository, connector, and environment. Explain connector choices in
neutral terms. Once the user chooses them, use this gate without skipping or reordering:

1. Call `credential_status` for the active repository, connector, and environment.
2. If required credentials are missing, stop. Instruct the user to run locally:
   `mercury credentials setup <connector> --env <environment> --repo-root "<repo>"`
3. After the user confirms setup is complete, call `credential_status` again.
4. If it is still missing or not configured, stop and return to local setup. Do not run
   the connection test.
5. Only when the second status is configured, ask the user to run locally:
   `mercury credentials test <connector> --env <environment> --repo-root "<repo>"`
6. Continue only when the test reports `connected`.

Never ask for, accept, or paste credentials in chat. Do not proceed while setup is
missing, unconfirmed, or untested. Respond in concise Thai with connector, environment,
connection status, and the next local command.
