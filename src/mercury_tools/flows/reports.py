"""Report writers for Mercury Flow suites."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from mercury_tools.flows.workspace import FlowSuiteRun


def _failure_message(result: dict[str, Any]) -> str:
    artifacts = result.get("artifacts") or []
    for artifact in artifacts:
        if isinstance(artifact, dict) and artifact.get("message"):
            return str(artifact["message"])
    return f"Flow status: {result.get('status') or 'error'}"


def write_junit_report(suite: FlowSuiteRun, output_path: Path) -> Path:
    """Write a minimal JUnit XML report for CI systems."""
    payload = suite.as_dict()
    results = payload["results"]
    failures = sum(1 for result in results if result["status"] == "error")

    testsuite = ElementTree.Element(
        "testsuite",
        {
            "name": "Mercury Flow Suite",
            "tests": str(len(results)),
            "failures": str(failures),
            "errors": "0",
            "skipped": "0",
        },
    )
    properties = ElementTree.SubElement(testsuite, "properties")
    ElementTree.SubElement(
        properties,
        "property",
        {"name": "workspace", "value": payload["workspace"]["config"]["root"]},
    )
    ElementTree.SubElement(
        properties,
        "property",
        {"name": "status", "value": payload["status"]},
    )

    for result in results:
        flow = result["flow"]
        testcase = ElementTree.SubElement(
            testsuite,
            "testcase",
            {
                "classname": "MercuryFlow",
                "name": str(flow.get("name") or "Unnamed Flow"),
                "file": str(flow.get("path") or ""),
            },
        )
        if result["status"] == "error":
            failure = ElementTree.SubElement(
                testcase,
                "failure",
                {
                    "message": _failure_message(result),
                    "type": "MercuryFlowFailure",
                },
            )
            failure.text = _failure_message(result)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ElementTree.ElementTree(testsuite)
    ElementTree.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def _status_class(status: str) -> str:
    if status in {"ok", "planned"}:
        return "ok"
    if status in {"failed", "error"}:
        return "failed"
    return "muted"


def _artifact_titles(result: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for artifact in result.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        title = artifact.get("title") or artifact.get("message") or artifact.get("status")
        if title:
            titles.append(str(title))
    return titles


def write_html_report(suite: FlowSuiteRun, output_path: Path) -> Path:
    """Write a human-readable HTML report for demos and review handoffs."""
    payload = suite.as_dict()
    workspace = payload["workspace"]
    results = payload["results"]
    generated_at = datetime.now(UTC).isoformat()
    workspace_root = escape(str(workspace["config"]["root"]))
    suite_status = escape(str(payload["status"]))
    selected_count = escape(str(workspace["selected_count"]))
    discovered_count = escape(str(workspace["flow_count"]))
    run_mode = escape("dry-run" if all(result.get("dry_run") for result in results) else "run")
    empty_report = "<section class='flow-card'><p>No selected flows.</p></section>"

    result_cards: list[str] = []
    for result in results:
        flow = result["flow"]
        status = str(result["status"])
        steps = result.get("steps") or []
        artifacts = _artifact_titles(result)
        failure = _failure_message(result) if status == "error" else ""
        step_rows = "\n".join(
            "<tr>"
            f"<td>{escape(str(step.get('index') or ''))}</td>"
            f"<td>{escape(str(step.get('command') or ''))}</td>"
            f"<td><span class='badge {escape(_status_class(str(step.get('status') or '')))}'>"
            f"{escape(str(step.get('status') or ''))}</span></td>"
            f"<td>{escape(str(step.get('saved_as') or ''))}</td>"
            "</tr>"
            for step in steps
        )
        artifact_list = "".join(f"<li>{escape(title)}</li>" for title in artifacts)
        result_cards.append(
            f"""
            <section class="flow-card">
              <div class="flow-head">
                <div>
                  <h2>{escape(str(flow.get("name") or "Unnamed Flow"))}</h2>
                  <p>{escape(str(flow.get("path") or ""))}</p>
                </div>
                <span class="badge {escape(_status_class(status))}">{escape(status)}</span>
              </div>
              {"<p class='failure'>" + escape(failure) + "</p>" if failure else ""}
              <table>
                <thead><tr><th>#</th><th>Command</th><th>Status</th><th>Saved as</th></tr></thead>
                <tbody>{step_rows or "<tr><td colspan='4'>No steps recorded.</td></tr>"}</tbody>
              </table>
              {"<h3>Artifacts</h3><ul>" + artifact_list + "</ul>" if artifact_list else ""}
            </section>
            """
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Mercury Flow Suite Report</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #111923;
      --panel: #182331;
      --line: #314455;
      --text: #f5f8fb;
      --muted: #93a1ad;
      --teal: #42c6bb;
      --gold: #f5bf45;
      --ok: #54d47f;
      --danger: #ff7070;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 44px;
    }}
    header, .flow-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 18px;
      margin-bottom: 14px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 18px; }}
    h3 {{ color: var(--teal); font-size: 13px; margin-top: 14px; }}
    p {{ color: var(--muted); overflow-wrap: anywhere; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 16px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #101720;
      padding: 12px;
    }}
    .metric b {{
      display: block;
      color: var(--teal);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .metric span {{ font-size: 18px; font-weight: 850; }}
    .flow-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 14px;
    }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      padding: 4px 9px;
      background: #223142;
      color: #dce7ef;
      font-size: 12px;
      font-weight: 850;
      white-space: nowrap;
    }}
    .badge.ok {{ background: rgba(84, 212, 127, .16); color: var(--ok); }}
    .badge.failed {{ background: rgba(255, 112, 112, .16); color: var(--danger); }}
    .badge.muted {{ color: var(--muted); }}
    .failure {{
      border: 1px solid rgba(255, 112, 112, .38);
      border-radius: 8px;
      color: #ffd7d7;
      background: rgba(255, 112, 112, .08);
      padding: 10px;
      margin-bottom: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 8px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 9px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--teal); font-size: 12px; }}
    ul {{ margin: 6px 0 0; padding-left: 20px; color: #dce7ef; }}
    @media (max-width: 760px) {{
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .flow-head {{ display: grid; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Mercury Flow Suite Report</h1>
      <p>{workspace_root}</p>
      <p>Generated {escape(generated_at)}</p>
      <div class="summary">
        <div class="metric"><b>Status</b><span>{suite_status}</span></div>
        <div class="metric"><b>Selected</b><span>{selected_count}</span></div>
        <div class="metric"><b>Discovered</b><span>{discovered_count}</span></div>
        <div class="metric"><b>Mode</b><span>{run_mode}</span></div>
      </div>
    </header>
    {''.join(result_cards) or empty_report}
  </main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
