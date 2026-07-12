---
name: management-report-th
description: Use when the user asks for Thai management reports, owner summaries, CFO packs, or monthly accounting narratives
---

# Management Report TH

1. Call `credential_status` for the active repository, connector, and environment. Stop
   and route to local connector setup unless status is connected.
2. Call `retrieve_context_pack` for the company, period, KPIs, accounting policy, and
   reporting context. Preserve its citations for interpreted claims.
3. Call `search_erp_actions` with `risk_tier=0` for each required safe financial read.
   Stop on ambiguity.
4. Call `get_erp_action_schema` for the exact selected action. Inspect the returned schema
   and prepare only its inputs.
5. Call `run_erp_read`; repeat the search, schema, and read steps only when another report
   section requires a separate action.

ตอบภาษาไทยแบบกระชับสำหรับผู้บริหาร: executive summary, key numbers, เทียบงวดก่อน,
ความเสี่ยง, actions และจุดที่ควรให้นักบัญชีตรวจทาน. Do not include evidence counts, audit paths, or verbose evidence unless the user explicitly requests audit detail.
