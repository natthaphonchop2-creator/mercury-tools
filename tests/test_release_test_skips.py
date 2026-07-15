from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_test_skips import SkipVerificationError, verify_test_skips  # noqa: E402


def _write_junit(path: Path, skipped: tuple[tuple[str, str], ...]) -> None:
    suite = ET.Element("testsuite", tests=str(len(skipped)), skipped=str(len(skipped)))
    for test_id, reason in skipped:
        file_name, name = test_id.split("::", 1)
        classname = file_name.removesuffix(".py").replace("/", ".")
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        ET.SubElement(case, "skipped", message=reason)
    ET.ElementTree(ET.Element("testsuites")).write(path, encoding="utf-8", xml_declaration=True)
    root = ET.parse(path).getroot()
    root.append(suite)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _write_waivers(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def _waiver(test_id: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "test_id": test_id,
        "rationale": "Optional platform capability is unavailable in this release job.",
        "owner_role": "release-manager",
        "expires_at": "2026-08-15",
        "release_approved": True,
    }
    row.update(overrides)
    return row


def test_verify_test_skips_accepts_no_skips_and_no_waivers(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    waivers = tmp_path / "waivers.json"
    _write_junit(junit, ())
    _write_waivers(waivers, [])

    assert verify_test_skips(junit, waivers, today=date(2026, 7, 15)) == ()


def test_verify_test_skips_accepts_exact_approved_unexpired_waiver(tmp_path: Path) -> None:
    test_id = "tests/test_optional_platform.py::test_optional_platform_probe"
    junit = tmp_path / "pytest.xml"
    waivers = tmp_path / "waivers.json"
    _write_junit(junit, ((test_id, "optional platform capability"),))
    _write_waivers(waivers, [_waiver(test_id)])

    assert verify_test_skips(junit, waivers, today=date(2026, 7, 15)) == (test_id,)


@pytest.mark.parametrize(
    ("rows", "error"),
    (
        ([], "unknown_skip"),
        ([_waiver("tests/test_other.py::test_other")], "waiver_set_mismatch"),
        ([_waiver("tests/test_optional.py::test_optional", release_approved=False)], "unapproved"),
        ([_waiver("tests/test_optional.py::test_optional", expires_at="2026-07-14")], "expired"),
        ([_waiver("tests/test_optional.py::test_optional", unexpected="value")], "schema"),
    ),
)
def test_verify_test_skips_rejects_non_exact_waivers(
    tmp_path: Path,
    rows: list[dict[str, object]],
    error: str,
) -> None:
    test_id = "tests/test_optional.py::test_optional"
    junit = tmp_path / "pytest.xml"
    waivers = tmp_path / "waivers.json"
    _write_junit(junit, ((test_id, "optional"),))
    _write_waivers(waivers, rows)

    with pytest.raises(SkipVerificationError, match=error):
        verify_test_skips(junit, waivers, today=date(2026, 7, 15))


@pytest.mark.parametrize(
    "test_id",
    (
        "tests/test_release_secret_scanner.py::test_scans_history",
        "tests/test_validation_migration.py::test_migration_applies",
        "tests/test_mcp_contract.py::test_tools_list",
        "tests/test_release_verify.py::test_release_tree",
        "tests/test_plugin_clean_install.py::test_clean_wheel",
        (
            "tests/integration/test_flowaccount_sandbox_qualification.py::"
            "test_required_live_sandbox"
        ),
    ),
)
def test_verify_test_skips_never_waives_required_release_categories(
    tmp_path: Path,
    test_id: str,
) -> None:
    junit = tmp_path / "pytest.xml"
    waivers = tmp_path / "waivers.json"
    _write_junit(junit, ((test_id, "required gate unavailable"),))
    _write_waivers(waivers, [_waiver(test_id)])

    with pytest.raises(SkipVerificationError, match="non_waivable_skip"):
        verify_test_skips(junit, waivers, today=date(2026, 7, 15))


def test_verify_test_skips_cli_reports_only_bounded_test_ids(tmp_path: Path) -> None:
    test_id = "tests/test_optional.py::test_optional"
    junit = tmp_path / "pytest.xml"
    waivers = tmp_path / "waivers.json"
    _write_junit(junit, ((test_id, "provider-value-must-not-be-printed"),))
    _write_waivers(waivers, [_waiver(test_id)])

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_test_skips.py"),
            "--junit",
            str(junit),
            "--waivers",
            str(waivers),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert test_id in result.stdout
    assert "provider-value-must-not-be-printed" not in result.stdout
