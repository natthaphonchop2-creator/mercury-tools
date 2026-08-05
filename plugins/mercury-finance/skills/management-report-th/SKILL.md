---
name: management-report-th
description: Use when the user asks for a Thai management report covering revenue, VAT, cash flow, margin, or accounting risks
---

# Management Report TH

## V1 route

1. Call `get_mercury_context` and choose one authorized workspace.
2. Call `connector_status`, then `list_provider_capabilities`. Continue only with exact read
   capability versions whose qualification is enabled.
3. Call `run_accounting_skill` with `skill_id=management-report-th`,
   `skill_version=0.1.0`, the period, objective, query, workspace, and connection.

## Result

ตอบภาษาไทยสำหรับผู้บริหาร: executive summary, KPI ที่มีหลักฐาน, การเปลี่ยนแปลงสำคัญ,
ความเสี่ยง, ข้อมูลที่ยังขาด และรายการที่ต้องให้นักบัญชีตรวจทาน. Distinguish source totals,
derived calculations, estimates, and accounting interpretation. Preserve citation lineage and
do not imply that incomplete data is a closed financial statement.

This Skill is read-only. Provider credentials never enter chat or model context; Mercury stores
encrypted provider authorization server-side and returns sanitized evidence and audit data.
