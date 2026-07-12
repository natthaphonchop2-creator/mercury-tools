---
name: company-health-check-th
description: Use when the user asks for company health, revenue, VAT, cash flow, or accounting status summaries
---

# Company Health Check TH

1. Call `credential_status` for the active repository, connector, and environment. Stop
   and route to local connector setup unless status is connected.
2. Call `retrieve_context_pack` for the company, period, accounting policy, and requested
   metrics. Preserve its citations for claims that depend on Mercury knowledge.
3. Call `search_erp_actions` with `risk_tier=0` for each required safe read. Stop on
   ambiguity.
4. Call `get_erp_action_schema` for the exact selected action. Inspect the returned schema
   and prepare only its inputs.
5. Call `run_erp_read`; repeat the search, schema, and read steps only when another metric
   requires a separate action.

ตอบภาษาไทยแบบกระชับ: ภาพรวม รายได้ VAT กระแสเงินสด ความเสี่ยง และจุดที่ควรให้
นักบัญชีตรวจทาน. Do not include evidence counts, audit paths, or verbose evidence unless the user explicitly requests audit detail.
