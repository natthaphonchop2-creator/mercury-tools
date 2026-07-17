from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SUBMISSION = ROOT / "submission" / "openai-plugin"


def _json(name: str) -> dict:
    return json.loads((SUBMISSION / name).read_text(encoding="utf-8"))


def test_submission_listing_uses_public_mcp_without_custom_ui() -> None:
    listing = _json("listing.json")

    assert listing["submission_type"] == "app-plus-skills"
    assert listing["mcp"] == {
        "url": "https://mercury-tools-mcp.onrender.com/mcp",
        "authentication": "none",
        "custom_ui": False,
        "challenge_url": (
            "https://mercury-tools-mcp.onrender.com/"
            ".well-known/openai-apps-challenge"
        ),
        "content_security_policy": {
            "connect_domains": ["https://mercury-tools-mcp.onrender.com"],
            "resource_domains": [],
        },
    }
    assert listing["privacy_policy_url"].endswith("/privacy")
    assert listing["terms_url"].endswith("/terms")
    assert listing["support_url"].endswith("/support")
    assert listing["logo_file"] == "assets/mercury-finance-logo.png"
    assert "does not accept or store erp credentials" in listing[
        "data_handling_summary"
    ].lower()


def test_submission_logo_is_square_png() -> None:
    logo = (SUBMISSION / "assets" / "mercury-finance-logo.png").read_bytes()

    assert logo.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", logo[16:24])
    assert width == height
    assert width >= 1024


def test_submission_has_exact_required_test_case_counts() -> None:
    cases = _json("test-cases.json")

    assert len(cases["positive"]) == 5
    assert len(cases["negative"]) == 3
    assert len({case["id"] for case in cases["positive"] + cases["negative"]}) == 8


def test_public_submission_skills_only_reference_public_tools() -> None:
    forbidden = {
        "credential_status",
        "search_erp_actions",
        "get_erp_action_schema",
        "run_erp_read",
        "run_erp_write",
        "preview_erp_write",
    }
    skills = sorted((SUBMISSION / "skills").glob("*/SKILL.md"))

    assert len(skills) == 6
    combined = "\n".join(path.read_text(encoding="utf-8") for path in skills)
    normalized = " ".join(combined.split())
    for tool_name in forbidden:
        assert f"`{tool_name}`" not in combined
    assert "Never pass an API key" in combined
    assert "cannot directly execute a production ERP mutation" in normalized


def test_submission_bundle_is_deterministic_and_has_flat_skill_tree(tmp_path) -> None:
    script = ROOT / "scripts" / "build_openai_plugin_bundle.py"
    subprocess.run([sys.executable, str(script)], check=True, cwd=ROOT)
    output = ROOT / "dist" / "openai-plugin" / "mercury-finance-skills-public.zip"
    first = output.read_bytes()
    subprocess.run([sys.executable, str(script)], check=True, cwd=ROOT)
    second = output.read_bytes()

    assert first == second
    with ZipFile(output) as archive:
        names = archive.namelist()
        assert len(names) == 6
        assert all(name.count("/") == 1 and name.endswith("/SKILL.md") for name in names)
        assert all(info.date_time == (2026, 7, 17, 0, 0, 0) for info in archive.infolist())
