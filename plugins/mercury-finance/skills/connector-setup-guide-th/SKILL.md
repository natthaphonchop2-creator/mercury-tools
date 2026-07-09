---
name: connector-setup-guide-th
description: Use when the user asks which accounting connector to use, what setup requires, or how to prepare FlowAccount, PEAK, Express, or ERP access
---

# Connector Setup Guide TH

Use `list_connectors` to show current connector options and neutral setup states.

If the user chooses a connector, use `start_connector_setup` to get exact required fields and preset values. Do not invent credential requirements from memory.

Use `validate_connector_connection` after setup. If credentials are needed, route to `connector-credential-setup-th` and keep the user on the current validated step.

ตอบภาษาไทยแบบ checklist สั้น ๆ: connector, environment, permissions needed, secure input path, validation result, and next safe command. Never ask for secrets in normal chat.
