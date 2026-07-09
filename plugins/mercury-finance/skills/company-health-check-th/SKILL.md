---
name: company-health-check-th
description: Use when the user asks for company health, revenue, VAT, cash flow, or accounting status summaries
---

# Company Health Check TH

Call `connector_status` with the current `workspace_id` first. If workspace or
connector setup is incomplete, route to `connector-credential-setup-th` and do
not continue.

Use `retrieve_workspace_context_pack` for connector-specific accounting
knowledge and `run_mercury_flow` for a read-only health-check flow. Keep the
same `workspace_id` throughout the task.

ตอบภาษาไทยสำหรับผู้บริหาร: รายได้ VAT กระแสเงินสด ความเสี่ยง และจุดที่ควรให้
นักบัญชีตรวจทาน อ้าง evidence สั้น ๆ และไม่ dump audit paths เว้นแต่ผู้ใช้ขอ.
