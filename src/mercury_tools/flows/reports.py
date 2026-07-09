"""Report writers for Mercury Flow suites."""

from __future__ import annotations

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
