---
name: peak-connector-setup-th
description: Use when a PEAK task needs local connector setup or connection troubleshooting
---

# PEAK Connector Setup TH

Use the selected PEAK environment and this gate without skipping or reordering:

1. Call `credential_status` for the active repository, connector, and environment.
2. If required credentials are missing, stop. Instruct the user to run locally:
   `mercury credentials setup <connector> --env <environment> --repo-root "<repo>"`
3. After the user confirms setup is complete, call `credential_status` again.
4. Ask the user to run locally:
   `mercury credentials test <connector> --env <environment> --repo-root "<repo>"`
5. Continue only when the test reports `connected`.

Never ask for, accept, or paste credentials in chat. Never change environments
implicitly. Treat a provider-level failure as disconnected even when transport succeeds.
Report only sanitized status and the next local command in concise Thai.
