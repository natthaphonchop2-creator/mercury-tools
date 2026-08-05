---
name: company-health-check-th
description: Use when the user asks for company health, revenue, VAT, cash flow, or accounting status summaries
---

# Company Health Check TH

## V1 route

1. Call `get_mercury_context` and use one authorized workspace.
2. Call `connector_status`, then `list_provider_capabilities` for the selected ERP connection.
   Continue only when `provider_profile.get` and any requested document reads have passed exact
   capability-version qualification.
3. Call `run_accounting_skill` with `skill_id=company-health-check-th`,
   `skill_version=0.1.0`, the period, query, workspace, and connection.

## Result

ตอบภาษาไทยแบบผู้บริหาร: ภาพรวม รายได้/ยอดเอกสาร แนวโน้ม ความเสี่ยง และรายการที่ควรให้
นักบัญชีตรวจทาน. Separate source facts from interpretation. Preserve citations internally but
show detailed evidence only when the user requests it. Do not describe estimates as closed
financial statements.

This Skill is read-only. Provider credentials never enter chat or model context; Mercury stores
encrypted provider authorization server-side and returns only sanitized evidence and audit data.
