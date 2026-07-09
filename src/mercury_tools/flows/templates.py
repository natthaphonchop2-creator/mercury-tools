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
    saveAs: connectorState
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
      connector_status: "{{ connectorState.status }}"
    evidenceMode: true
    saveAs: skill
- emitReport:
    title: "Company health check handoff"
    sections:
      - "Use skill {{ skill.skill_id }} with the retrieved cited context."
- emitReport:
    when:
      equals:
        value: "${environment}"
        expected: production
    title: "Production-only review"
    sections:
      - "Run this handoff only for production workspace checks."

Commands:
- connectorStatus: read sanitized connector state.
- searchKnowledge: run RAG search. Args: query, filters, topK, mode, saveAs.
- retrieveContextPack: retrieve cited context. Args: query, task, filters, maxChunks, saveAs.
- getDocument: fetch one indexed document. Args: documentId, saveAs.
- runSkill: package an accounting skill. Args: skillId, inputs, evidenceMode, saveAs.
- emitReport: create a structured handoff artifact. Args: title, sections, metadata.
- assert: fail the flow when required data checks do not pass.
  Args: exists, notExists, equals, notEquals, contains, status, minCount, saveAs.
- repeat: repeat a small command group. Args: times, while, maxIterations, commands, saveAs.
- runFlow: call another flow file relative to the current flow.
  Args: file/path, env, label, commands, saveAs.
- retry: retry a small file or inline command group on failure.
  Args: maxRetries 0-3, delayMs, file/path or commands, env, label, saveAs.

Template variables:
- ${jurisdiction} reads from env.
- {{ context.query }} reads a previous step saved with saveAs: context.
- CLI overrides use -e KEY=value or --env KEY=value and take precedence over flow/workspace env.
- MCP/HTTP run_flow accepts env: {"month": "2026-09"} and stores only env key names in run history.
- MCP run_flow_files accepts multiple in-memory YAML files so runFlow file
  references can resolve in one host call.

Conditional execution:
- Add when: to any command to skip it unless the condition passes.
- Supported conditions: true, exists, notExists, equals, notEquals.
- Multiple conditions are ANDed.
- Mercury does not evaluate arbitrary JavaScript in v1.

Assertions:
- Use assert to validate connector, RAG, skill, or report outputs before the
  host AI consumes them.
- Supported assertions: exists, notExists, equals, notEquals, contains, status,
  minCount.
- Exact template references such as ${rows} preserve list/dict values so
  minCount can count real collections.

Example:
- assert:
    exists: "${connectorState.status}"
    status:
      value: "${connectorState.status}"
      expected: ok
    minCount:
      value: "${context.context}"
      count: 2
    saveAs: validation

Repeat blocks:
- repeat runs a small command group multiple times.
- times sets the exact iteration count, up to 100.
- while uses Mercury's deterministic conditions and is checked before each iteration.
- If while is used without times, Mercury caps the loop with maxIterations, default 10.
- Each iteration exposes ${repeat.index}, ${repeat.iteration}, and ${repeat.remaining}.

Example:
- repeat:
    label: Monthly section draft
    times: 3
    commands:
      - emitReport:
          title: "Monthly section ${repeat.iteration}"
          sections:
            - "Use this slot for a repeated period or dataset handoff."
    saveAs: monthlySections

Inline subflows:
- runFlow can call file: another-flow.yaml or commands: [...] inline.
- label names the inline subflow in reports.
- env values are inherited from the parent flow and can be overridden per runFlow.

Example:
- runFlow:
    label: Production review handoff
    when:
      equals:
        value: "${environment}"
        expected: production
    env:
      review_level: controller
    commands:
      - emitReport:
          title: "Review ${review_level}"
          sections:
            - "Only generated for production runs."
    saveAs: productionReview

Retry blocks:
- retry can call file: flaky-step.yaml or commands: [...] inline.
- maxRetries is 0-3 and defaults to 1, matching Maestro's bounded retry model.
- Use retry around small transient connector/RAG steps, not whole accounting flows.
- delayMs optionally waits between attempts.

Example:
- retry:
    label: FlowAccount invoice context
    maxRetries: 2
    delayMs: 500
    commands:
      - retrieveContextPack:
          query: "invoice VAT review"
          task: invoice_review_th
          maxChunks: 6
          saveAs: invoiceContext
    saveAs: invoiceRetry

Workspace quickstart:
- mercury-tools flow init-workspace ./my-mercury-flows
- mercury-tools flow list ./my-mercury-flows
- mercury-tools flow run-suite ./my-mercury-flows --dry-run
- mercury-tools flow run-suite ./my-mercury-flows --dry-run -e month=2026-09
- mercury-tools flow watch ./my-mercury-flows --dry-run
- mercury-tools flow run-suite ./my-mercury-flows --format junit --output reports/junit.xml
- mercury-tools flow run-suite ./my-mercury-flows --format html --output reports/flow-report.html
- mercury-tools flow push ./my-mercury-flows --dry-run

Workspace config keys:
- flows: glob patterns for YAML flow discovery.
- includeTags / excludeTags: workspace-level flow selection gates.
- env: variables available as ${name}.
- -e / --env: runtime string overrides, useful for CI and host-specific runs.
- executionOrder.flowsOrder: deterministic ordered flow names or filenames.
- executionOrder.continueOnFailure: collect failures and continue when true.
- testOutputDir: writes suite-report.json for the run.

CI reporting:
- --format junit writes JUnit XML.
- --format html writes a readable HTML suite report.
- --output sets the report path, defaulting to report.xml or report.html.
- failed suites exit with code 1 unless --allow-failures is set.
"""


COMPANY_HEALTH_TEMPLATE = """name: Company Health Check
description: Read-only Mercury flow for a Thai accounting health-check handoff.
tags: [accounting, read-only, flowaccount]
env:
  jurisdiction: TH
  connector: flowaccount
onFlowStart:
  - connectorStatus:
      saveAs: connectorState
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
      - "Connector status is available in {{ connectorState.status }}."
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
