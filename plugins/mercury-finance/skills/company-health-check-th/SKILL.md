---
name: company-health-check-th
description: Use when the user asks for company health, revenue, VAT, cash flow, or accounting status summaries
---

# Company Health Check TH

Use `workspace_connector_status` with `client_token` first. If connector setup is incomplete, route to `connector-credential-setup-th`.

Use `retrieve_workspace_context_pack` for accounting context and `run_mercury_flow` for approved health-check flows.

ตอบภาษาไทยสำหรับผู้บริหาร: สรุปสถานะรายได้ VAT กระแสเงินสด ความเสี่ยง และจุดที่ควรให้บัญชีตรวจทาน โดยอ้าง evidence สั้น ๆ และไม่ dump audit paths เว้นแต่ผู้ใช้ขอ.
