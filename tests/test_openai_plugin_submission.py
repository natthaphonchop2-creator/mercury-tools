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
LEGACY_TOOL_NAMES = {
    "create_public_workspace",
    "list_connectors",
    "get_connector_setup",
    "link_connector_profile",
    "validate_connector_connection",
    "connector_capabilities",
    "get_accounting_skill_schema",
    "retrieve_workspace_context_pack",
    "list_workspace_flows",
    "run_workspace_flow",
    "save_workspace_flow",
    "flow_cheat_sheet",
    "check_flow_syntax",
    "inspect_flow_files",
    "run_inline_flow",
    "run_flow_files",
}


def _build_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_openai_plugin_bundle", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(name: str) -> dict:
    return json.loads((SUBMISSION / name).read_text(encoding="utf-8"))


def _v1_hosted_tools() -> dict[str, object]:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 OpenAI submission")
    configure_v1_tools(server, enabled=True)
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def test_submission_listing_uses_oauth_protected_v1_mcp_without_custom_ui() -> None:
    listing = _json("listing.json")

    assert listing["submission_type"] == "app-plus-skills"
    assert listing["mcp"] == {
        "url": "https://mercury-tools-mcp.onrender.com/mcp",
        "authentication": "oauth",
        "custom_ui": False,
        "challenge_url": (
            "https://mercury-tools-mcp.onrender.com/.well-known/openai-apps-challenge"
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
    assert "oauth-protected hosted mcp" in boundary
    assert "secure mercury sign-in" in boundary
    assert "encrypted server-side" in boundary
    assert "never enter chat, model, rag, log, or audit output" in boundary


def test_chatgpt_submission_describes_the_v1_connection_and_write_boundary() -> None:
    submission = json.loads((ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8"))
    description = submission["app_info"]["description"].lower()

    assert "one oauth-protected hosted mcp" in description
    assert "secure mercury sign-in" in description
    assert "encrypted provider credentials" in description
    assert "never enter chat, model, rag, log, or audit output" in description
    assert "qualified capability" in description
    assert "immutable preview" in description
    assert "explicit confirmation" in description


def test_chatgpt_submission_annotations_match_the_fresh_v1_registry() -> None:
    from mercury_tools.mcp.contracts import V1_HOSTED_TOOL_NAMES

    submission = json.loads((ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8"))
    hosted = _v1_hosted_tools()

    assert set(hosted) == V1_HOSTED_TOOL_NAMES
    assert set(submission["tools"]) == V1_HOSTED_TOOL_NAMES
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


def test_submission_test_cases_cover_v1_connection_and_capability_paths() -> None:
    cases = _json("test-cases.json")

    assert [case["id"] for case in cases["positive"]] == [
        "positive-v1-connection-lifecycle",
        "positive-v1-qualified-capability-discovery",
        "positive-v1-qualified-skill-read",
        "positive-v1-document-create-guard",
        "positive-v1-cited-knowledge",
    ]
    assert [case["id"] for case in cases["negative"]] == [
        "negative-v1-secret-in-chat",
        "negative-v1-unqualified-provider-write",
        "negative-v1-unconfirmed-document-create",
    ]

    combined = json.dumps(cases, ensure_ascii=False).lower()
    lifecycle = (
        "get_mercury_context -> list_accounting_providers -> "
        "start_provider_connection -> secure authorization_url/setup_url -> "
        "list_provider_connections -> connector_status -> "
        "list_provider_capabilities"
    )
    assert lifecycle in combined
    assert "encrypted server-side" in combined
    assert "qualified capability" in combined
    assert "immutable preview" in combined
    assert "explicit confirmation" in combined
    assert "secret" in combined
    assert "provider_capability_unavailable" in combined


def test_public_submission_skills_only_reference_v1_hosted_tools() -> None:
    from mercury_tools.mcp.contracts import V1_HOSTED_TOOL_NAMES

    skills = sorted((SUBMISSION / "skills").glob("*/SKILL.md"))

    assert len(skills) == 6
    combined = "\n".join(path.read_text(encoding="utf-8") for path in skills)
    normalized = " ".join(combined.split())
    referenced_tools = set(re.findall(r"`([a-z][a-z0-9_]+)`", combined))
    assert referenced_tools <= V1_HOSTED_TOOL_NAMES
    assert not (LEGACY_TOOL_NAMES & referenced_tools)
    assert "Never pass an API key" in combined
    assert "no ERP credentials" in combined
    assert "user approval" in normalized
    assert "qualified capability" in normalized
    assert "immutable preview" in normalized
    assert "explicit confirmation" in normalized


def test_connector_onboarding_skill_uses_the_exact_v1_lifecycle() -> None:
    skill = (SUBMISSION / "skills" / "connector-onboarding-th" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(skill.replace("`", "").split())

    lifecycle = (
        "get_mercury_context -> list_accounting_providers -> "
        "start_provider_connection -> secure authorization_url/setup_url -> "
        "list_provider_connections -> connector_status -> "
        "list_provider_capabilities"
    )
    assert lifecycle in normalized


def test_submission_artifacts_do_not_reference_legacy_hosted_tools() -> None:
    artifact_paths = [
        ROOT / "chatgpt-app-submission.json",
        *SUBMISSION.rglob("*.json"),
        *SUBMISSION.rglob("*.md"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in artifact_paths)

    assert not (LEGACY_TOOL_NAMES & set(re.findall(r"\b[a-z][a-z0-9_]+\b", combined)))


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
        assert all(info.date_time == build_module.ZIP_TIMESTAMP for info in archive.infolist())
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
