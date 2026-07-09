---
name: management-report-th
description: Use when the user asks for Thai management reports, owner summaries, CFO packs, or monthly accounting narratives
---

# Management Report TH

Call `connector_status` with the current `workspace_id` first. If setup is
incomplete, route to `connector-credential-setup-th` and stop.

Use `retrieve_workspace_context_pack` for the selected ERP's period, company,
KPI, VAT, cash, receivable, payable, and evidence context. Use
`run_mercury_flow` only for a read-only management-report flow and keep the same
`workspace_id` throughout the task.

ตอบภาษาไทยสำหรับผู้บริหาร: executive summary, key numbers, changes vs prior
period, risks, actions, and accountant review points. Keep evidence concise.
