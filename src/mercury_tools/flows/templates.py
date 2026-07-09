"""Built-in Mercury Flow templates and command reference."""

from __future__ import annotations

FLOW_CHEAT_SHEET = """# Mercury Flow Cheat Sheet

Mercury Flows are YAML workflows inspired by Maestro's interpreted flow model.
They are designed for accounting agents and MCP hosts.

Shape:

name: Company Health Check
description: Retrieve cited accounting context and package a skill for the host AI.
tags: [accounting, read-only]
env:
  jurisdiction: TH
---
- connectorStatus:
    saveAs: connector
- retrieveContextPack:
    query: "FlowAccount company health check VAT revenue"
    task: "company_health_check_th"
    filters:
      jurisdiction: "${jurisdiction}"
      connector: flowaccount
    maxChunks: 8
    saveAs: context
- runSkill:
    skillId: company-health-check-th
    inputs:
      context: "{{ context.query }}"
      connector_status: "{{ connector.status }}"
    evidenceMode: true
    saveAs: skill
- emitReport:
    title: "Company health check handoff"
    sections:
      - "Use skill {{ skill.skill_id }} with the retrieved cited context."

Commands:
- connectorStatus: read sanitized connector state.
- searchKnowledge: run RAG search. Args: query, filters, topK, mode, saveAs.
- retrieveContextPack: retrieve cited context. Args: query, task, filters, maxChunks, saveAs.
- getDocument: fetch one indexed document. Args: documentId, saveAs.
- runSkill: package an accounting skill. Args: skillId, inputs, evidenceMode, saveAs.
- emitReport: create a structured handoff artifact. Args: title, sections, metadata.
- assert: fail the flow on missing required values. Args: exists or minCount.
- runFlow: call another flow file relative to the current flow.

Template variables:
- ${jurisdiction} reads from env.
- {{ context.query }} reads a previous step saved with saveAs: context.
"""


COMPANY_HEALTH_TEMPLATE = """name: Company Health Check
description: Read-only Mercury flow for a Thai accounting health-check handoff.
tags: [accounting, read-only, flowaccount]
env:
  jurisdiction: TH
  connector: flowaccount
onFlowStart:
  - connectorStatus:
      saveAs: connector
---
- retrieveContextPack:
    query: "company health check revenue VAT cash flow accounting Thailand"
    task: "company_health_check_th"
    filters:
      jurisdiction: "${jurisdiction}"
      connector: "${connector}"
      review_status: reviewed
    maxChunks: 8
    saveAs: context
- runSkill:
    skillId: company-health-check-th
    inputs:
      task: "Prepare a concise Thai company health-check answer."
      connector: "${connector}"
      context_query: "{{ context.query }}"
    evidenceMode: true
    saveAs: skill
- emitReport:
    title: "Company health-check context pack"
    sections:
      - "Connector status is available in {{ connector.status }}."
      - "Use skill {{ skill.skill_id }} and the cited context pack to answer in Thai."
      - "Do not expose raw tax IDs, emails, bearer tokens, or API keys."
"""


VAT_SUMMARY_TEMPLATE = """name: VAT Summary TH
description: Read-only Mercury flow for VAT summary context and skill packaging.
tags: [accounting, read-only, vat, flowaccount]
env:
  jurisdiction: TH
  connector: flowaccount
---
- retrieveContextPack:
    query: "VAT summary output tax input tax Thailand FlowAccount"
    task: "vat_summary_th"
    filters:
      jurisdiction: "${jurisdiction}"
      connector: "${connector}"
      review_status: reviewed
    maxChunks: 10
    saveAs: vatContext
- runSkill:
    skillId: vat-summary-th
    inputs:
      month: "${month}"
      context_query: "{{ vatContext.query }}"
    evidenceMode: true
    saveAs: vatSkill
- emitReport:
    title: "VAT summary handoff"
    sections:
      - "Use skill {{ vatSkill.skill_id }} with the retrieved citations."
      - "State clearly when figures are estimates or require accountant review."
"""

TEMPLATES = {
    "company-health": COMPANY_HEALTH_TEMPLATE,
    "vat-summary": VAT_SUMMARY_TEMPLATE,
}
