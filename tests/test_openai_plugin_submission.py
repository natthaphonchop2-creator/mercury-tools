from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
import stat
import struct
import zipfile
from pathlib import Path
from types import ModuleType
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SUBMISSION = ROOT / "submission" / "openai-plugin"
BUILD_SCRIPT = ROOT / "scripts" / "build_openai_plugin_bundle.py"


def _build_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_openai_plugin_bundle", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    boundary = " ".join(
        (
            listing["long_description"],
            listing["data_handling_summary"],
        )
    ).lower()
    assert "one hosted mcp" in boundary
    assert "sanitized connector profile and audit metadata" in boundary
    assert "no erp credentials" in boundary
    assert "connected mcp host or erp integration" in boundary


def test_chatgpt_submission_describes_the_same_connector_neutral_boundary() -> None:
    submission = json.loads(
        (ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8")
    )
    description = submission["app_info"]["description"].lower()

    assert "one hosted mcp" in description
    assert "sanitized erp connector profiles" in description
    assert "no erp credentials" in description
    assert "connected mcp host or erp integration" in description


def test_chatgpt_submission_annotations_match_the_hosted_registry() -> None:
    from mercury_tools.mcp.server import mcp

    submission = json.loads(
        (ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8")
    )
    hosted = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    assert set(submission["tools"]) == set(hosted)
    for name, tool in hosted.items():
        submitted = submission["tools"][name]
        annotations = submitted["annotations"]
        assert annotations["readOnlyHint"] is tool.annotations.readOnlyHint, name
        assert annotations["destructiveHint"] is tool.annotations.destructiveHint, name
        assert annotations["openWorldHint"] is tool.annotations.openWorldHint, name
        if tool.annotations.idempotentHint is None:
            assert "idempotentHint" not in annotations, name
        else:
            assert annotations["idempotentHint"] is tool.annotations.idempotentHint, name
        assert all(submitted["justifications"].values()), name


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


def test_submission_test_cases_cover_connector_neutral_review_paths() -> None:
    cases = _json("test-cases.json")

    assert [case["id"] for case in cases["positive"]] == [
        "positive-native-mcp-read-only",
        "positive-peak-api-driver-handoff",
        "positive-express-local-bridge-handoff",
        "positive-portable-skill-routing",
        "positive-cited-knowledge",
    ]
    assert [case["id"] for case in cases["negative"]] == [
        "negative-secret-in-chat",
        "negative-unavailable-provider-write",
        "negative-ambiguous-multi-profile",
    ]

    positive = {
        case["id"]: json.dumps(case, ensure_ascii=False).lower()
        for case in cases["positive"]
    }
    negative = {
        case["id"]: json.dumps(case, ensure_ascii=False).lower()
        for case in cases["negative"]
    }
    assert "native_mcp" in positive["positive-native-mcp-read-only"]
    assert "read-only" in positive["positive-native-mcp-read-only"]
    assert "peak" in positive["positive-peak-api-driver-handoff"]
    assert "api_driver" in positive["positive-peak-api-driver-handoff"]
    assert "express" in positive["positive-express-local-bridge-handoff"]
    assert "local_bridge" in positive["positive-express-local-bridge-handoff"]
    assert "run_accounting_skill" in positive["positive-portable-skill-routing"]
    assert "citation" in positive["positive-cited-knowledge"]
    assert "secret" in negative["negative-secret-in-chat"]
    assert "provider_capability_unavailable" in negative[
        "negative-unavailable-provider-write"
    ]
    assert "mode_required" in negative["negative-ambiguous-multi-profile"]


def test_public_submission_skills_only_reference_public_tools() -> None:
    from mercury_tools.mcp.server import mcp

    local_only = {
        "credential_status",
        "search_erp_actions",
        "get_erp_action_schema",
        "run_erp_read",
        "prepare_erp_mutation",
        "execute_erp_create",
        "execute_erp_update",
        "execute_sensitive_erp_action",
        "get_erp_request_status",
        "import_erp_spec",
        "list_connector_drivers",
        "run_mercury_flow",
        "preview_erp_write",
        "confirm_erp_write",
        "execute_erp_write",
        "run_erp_write",
    }
    skills = sorted((SUBMISSION / "skills").glob("*/SKILL.md"))

    assert len(skills) == 6
    combined = "\n".join(path.read_text(encoding="utf-8") for path in skills)
    normalized = " ".join(combined.split())
    hosted_tools = {tool.name for tool in asyncio.run(mcp.list_tools())}
    referenced_tools = set(re.findall(r"`([a-z][a-z0-9_]+)`", combined))
    assert referenced_tools <= hosted_tools
    assert not (local_only & referenced_tools)
    assert all(tool_name not in combined for tool_name in local_only)
    assert "Never pass an API key" in combined
    assert "no ERP credentials" in combined
    assert "user approval" in normalized


def test_connector_onboarding_skill_uses_the_exact_public_lifecycle() -> None:
    skill = (
        SUBMISSION / "skills" / "connector-onboarding-th" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.replace("`", "").split())

    lifecycle = (
        "list_connectors -> get_connector_setup -> link_connector_profile -> "
        "host/provider authorization -> validate_connector_connection -> "
        "connector_status"
    )
    assert lifecycle in normalized


def test_submission_bundle_is_deterministic_and_has_flat_skill_tree(tmp_path) -> None:
    build_module = _build_module()
    first_output = tmp_path / "first.zip"
    second_output = tmp_path / "second.zip"
    first_result = build_module.build_bundle(output=first_output)
    second_result = build_module.build_bundle(output=second_output)
    first = first_output.read_bytes()
    second = second_output.read_bytes()

    assert first == second
    digest = hashlib.sha256(first).hexdigest()
    assert first_result["sha256"] == digest
    assert second_result["sha256"] == digest
    with ZipFile(first_output) as archive:
        names = archive.namelist()
        expected_names = sorted(
            path.relative_to(SUBMISSION / "skills").as_posix()
            for path in (SUBMISSION / "skills").glob("*/SKILL.md")
        )
        assert names == expected_names
        assert all(
            info.date_time == build_module.ZIP_TIMESTAMP for info in archive.infolist()
        )
        assert archive.comment == b""
        for info in archive.infolist():
            assert info.create_system == 3
            assert info.create_version == 20
            assert info.extract_version == 20
            assert info.flag_bits == 0
            assert info.volume == 0
            assert info.internal_attr == 0
            assert info.external_attr == (stat.S_IFREG | 0o644) << 16
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert info.extra == b""
            assert info.comment == b""
            assert info.reserved == 0
        for name in names:
            assert archive.read(name) == (SUBMISSION / "skills" / name).read_bytes()


def test_submission_bundle_overrides_platform_zipinfo_defaults(
    tmp_path,
    monkeypatch,
) -> None:
    build_module = _build_module()
    unix_output = tmp_path / "unix-defaults.zip"
    platform_output = tmp_path / "simulated-windows-defaults.zip"
    build_module.build_bundle(output=unix_output)

    def simulated_platform_zip_info(filename, date_time):
        info = zipfile.ZipInfo(filename, date_time=date_time)
        info.create_system = 0
        info.create_version = 63
        info.extract_version = 63
        info.flag_bits = 0x800
        info.volume = 7
        info.internal_attr = 1
        info.external_attr = 0x20
        info.compress_type = zipfile.ZIP_STORED
        info.extra = b"platform-extra"
        info.comment = b"platform-comment"
        info.reserved = 1
        info._compresslevel = 1
        return info

    monkeypatch.setattr(build_module, "ZipInfo", simulated_platform_zip_info)

    build_module.build_bundle(output=platform_output)

    assert platform_output.read_bytes() == unix_output.read_bytes()
