from __future__ import annotations

import asyncio
import inspect
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import smoke_tagged_marketplace as tagged_marketplace  # noqa: E402
from scripts.smoke_local_plugin import EXPECTED_TOOLS as LOCAL_PLUGIN_TOOLS  # noqa: E402
from scripts.smoke_tagged_marketplace import (  # noqa: E402
    TaggedMarketplaceError,
    build_tagged_smoke_plan,
)
from scripts.smoke_tagged_marketplace import (  # noqa: E402
    main as tagged_marketplace_main,
)
from scripts.verify_public_release import (  # noqa: E402
    PublicReleaseError,
    build_public_release_plan,
)

HOSTED_MCP_URL = "https://mercury-tools-mcp.onrender.com/mcp"
V030_ADVANCED_LOCAL_TOOLS = {
    "connector_status",
    "credential_status",
    "execute_erp_create",
    "execute_erp_update",
    "execute_sensitive_erp_action",
    "get_document",
    "get_erp_action_schema",
    "get_erp_request_status",
    "import_erp_spec",
    "list_connector_drivers",
    "list_workspace_flows",
    "prepare_erp_mutation",
    "retrieve_context_pack",
    "run_accounting_skill",
    "run_erp_read",
    "run_mercury_flow",
    "run_workspace_flow",
    "save_workspace_flow",
    "search_erp_actions",
    "search_knowledge",
}
V030_HOSTED_TOOLS = {
    "check_flow_syntax",
    "connector_capabilities",
    "connector_status",
    "create_public_workspace",
    "flow_cheat_sheet",
    "get_accounting_skill_schema",
    "get_connector_setup",
    "get_document",
    "get_public_workspace",
    "inspect_flow_files",
    "link_connector_profile",
    "list_accounting_skills",
    "list_connectors",
    "list_workspace_flows",
    "retrieve_context_pack",
    "retrieve_workspace_context_pack",
    "run_accounting_skill",
    "run_flow_files",
    "run_inline_flow",
    "run_workspace_flow",
    "save_workspace_flow",
    "search_knowledge",
    "unlink_connector_profile",
    "validate_connector_connection",
}


def _installed_hosted_server(*, url: str = HOSTED_MCP_URL) -> dict[str, object]:
    return {
        "name": "mercury-finance",
        "enabled": True,
        "disabled_reason": None,
        "transport": {
            "type": "streamable_http",
            "url": url,
            "bearer_token_env_var": None,
            "http_headers": None,
            "env_http_headers": None,
        },
        "startup_timeout_sec": None,
        "tool_timeout_sec": None,
        "auth_status": "unsupported",
    }


def test_release_smokes_keep_v030_hosted_and_advanced_local_contracts_distinct() -> None:
    assert LOCAL_PLUGIN_TOOLS == V030_ADVANCED_LOCAL_TOOLS
    assert getattr(tagged_marketplace, "EXPECTED_HOSTED_TOOLS", frozenset()) == V030_HOSTED_TOOLS
    assert getattr(tagged_marketplace, "EXPECTED_HOSTED_MCP_URL", None) == HOSTED_MCP_URL
    assert not hasattr(tagged_marketplace, "_EXPECTED_LOCAL_TOOLS")


def test_tagged_marketplace_plan_uses_the_exact_hosted_contract(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex-home"
    parameters = inspect.signature(build_tagged_smoke_plan).parameters

    assert "expected_hosted_tools" in parameters
    assert "expected_tools" not in parameters

    plan = build_tagged_smoke_plan(
        repo="natthaphonchop2-creator/mercury-tools",
        tag="v0.3.0",
        expected_hosted_tools=24,
        codex_home=codex_home,
    )

    assert plan.hosted_mcp_url == HOSTED_MCP_URL
    assert plan.expected_hosted_tools == 24
    assert not hasattr(plan, "launcher_source")
    assert plan.environment == {
        "CODEX_HOME": str(codex_home),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert plan.commands == (
        (
            "codex",
            "plugin",
            "marketplace",
            "add",
            "natthaphonchop2-creator/mercury-tools",
            "--ref",
            "v0.3.0",
            "--sparse",
            ".agents/plugins",
            "--sparse",
            "plugins/mercury-finance",
        ),
        ("codex", "plugin", "add", "mercury-finance@mercury-tools"),
        ("codex", "mcp", "list", "--json"),
    )


def test_tagged_marketplace_cli_uses_hosted_tool_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = []
    monkeypatch.setattr(
        "scripts.smoke_tagged_marketplace.run_tagged_smoke",
        plans.append,
    )

    parser_destinations = {action.dest for action in tagged_marketplace._parser()._actions}
    assert "expected_hosted_tools" in parser_destinations
    assert "launcher_repo" not in parser_destinations
    assert "launcher_ref" not in parser_destinations

    assert (
        tagged_marketplace_main(
            [
                "--repo",
                "natthaphonchop2-creator/mercury-tools",
                "--tag",
                "v0.3.0",
                "--expected-hosted-tools",
                "24",
                "--codex-home",
                str(tmp_path / "codex-home"),
            ]
        )
        == 0
    )

    assert len(plans) == 1
    assert plans[0].expected_hosted_tools == 24
    assert plans[0].hosted_mcp_url == HOSTED_MCP_URL


@pytest.mark.parametrize("tag", ("main", "0.3.0", "v0.3", "v0.3.0^{commit}"))
def test_tagged_marketplace_plan_rejects_moving_or_ambiguous_refs(
    tmp_path: Path,
    tag: str,
) -> None:
    assert "expected_hosted_tools" in inspect.signature(build_tagged_smoke_plan).parameters
    with pytest.raises(TaggedMarketplaceError, match="tag_invalid"):
        build_tagged_smoke_plan(
            repo="natthaphonchop2-creator/mercury-tools",
            tag=tag,
            expected_hosted_tools=24,
            codex_home=tmp_path / "codex-home",
        )


def test_tagged_marketplace_plan_rejects_advanced_local_tool_count(tmp_path: Path) -> None:
    assert "expected_hosted_tools" in inspect.signature(build_tagged_smoke_plan).parameters
    with pytest.raises(TaggedMarketplaceError, match="hosted_mcp_tool_count_mismatch"):
        build_tagged_smoke_plan(
            repo="natthaphonchop2-creator/mercury-tools",
            tag="v0.3.0",
            expected_hosted_tools=20,
            codex_home=tmp_path / "codex-home",
        )


def test_tagged_marketplace_listing_requires_exact_hosted_transport() -> None:
    tagged_marketplace._verify_mcp_listing(
        json.dumps([_installed_hosted_server()]).encode("utf-8")
    )
    local = _installed_hosted_server()
    local["transport"] = {
        "type": "stdio",
        "command": "uvx",
        "args": ["mercury", "mcp", "serve-local"],
    }

    with pytest.raises(TaggedMarketplaceError, match="mcp_server_surface_mismatch"):
        tagged_marketplace._verify_mcp_listing(json.dumps([local]).encode("utf-8"))


def test_tagged_marketplace_smoke_runs_the_hosted_probe_after_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = build_tagged_smoke_plan(
        repo="natthaphonchop2-creator/mercury-tools",
        tag="v0.3.0",
        expected_hosted_tools=24,
        codex_home=tmp_path / "codex-home",
    )
    phases: list[str] = []
    probe_call: dict[str, object] = {}

    def run_command(
        _command: object,
        *,
        environment: object,
        phase: str,
    ) -> bytes:
        del environment
        phases.append(phase)
        return json.dumps([_installed_hosted_server()]).encode("utf-8")

    async def verify_hosted_endpoint(**kwargs: object) -> None:
        probe_call.update(kwargs)

    monkeypatch.setattr(tagged_marketplace, "_run_command", run_command)
    monkeypatch.setattr(
        tagged_marketplace,
        "_verify_tagged_hosted_endpoint",
        verify_hosted_endpoint,
    )

    tagged_marketplace.run_tagged_smoke(plan, base_environment={})

    assert phases == ["marketplace_add", "plugin_add", "mcp_list"]
    assert probe_call["endpoint"] == HOSTED_MCP_URL
    assert probe_call["expected_tools"] == V030_HOSTED_TOOLS


def test_tagged_marketplace_hosted_probe_uses_bounded_exact_transport() -> None:
    probe = getattr(tagged_marketplace, "_verify_tagged_hosted_endpoint", None)
    assert callable(probe)
    captured: dict[str, Any] = {}

    class FakeHttpClient:
        async def __aenter__(self) -> FakeHttpClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    def http_client_factory(**kwargs: object) -> FakeHttpClient:
        captured["http_client_kwargs"] = kwargs
        return FakeHttpClient()

    @asynccontextmanager
    async def streamable_client_factory(endpoint: str, *, http_client: object):
        captured["endpoint"] = endpoint
        captured["http_client"] = http_client
        yield object(), object(), "mocked-session"

    class FakeSession:
        def __init__(self, read_stream: object, write_stream: object) -> None:
            captured["streams"] = (read_stream, write_stream)

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> SimpleNamespace:
            return SimpleNamespace(serverInfo=SimpleNamespace(name="Mercury Tools"))

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(
                tools=[SimpleNamespace(name=name) for name in sorted(V030_HOSTED_TOOLS)]
            )

    asyncio.run(
        probe(
            endpoint=HOSTED_MCP_URL,
            expected_tools=frozenset(V030_HOSTED_TOOLS),
            http_client_factory=http_client_factory,
            streamable_client_factory=streamable_client_factory,
            session_factory=FakeSession,
        )
    )

    assert captured["endpoint"] == HOSTED_MCP_URL
    assert captured["http_client_kwargs"] == {
        "timeout": 30.0,
        "follow_redirects": False,
    }
    assert isinstance(captured["http_client"], FakeHttpClient)


def test_public_release_plan_is_anonymous_and_exact(tmp_path: Path) -> None:
    parameters = inspect.signature(build_public_release_plan).parameters
    assert "expected_hosted_tools" in parameters
    assert "expected_tools" not in parameters

    plan = build_public_release_plan(
        repo="natthaphonchop2-creator/mercury-tools",
        tag="v0.3.0",
        release="v0.3.0",
        expected_hosted_tools=24,
        workspace=tmp_path,
    )

    assert plan.tag == plan.release == "v0.3.0"
    assert plan.expected_hosted_tools == 24
    assert plan.environment == {
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert plan.clone_url == (
        "https://github.com/natthaphonchop2-creator/mercury-tools.git"
    )
    assert "GH_TOKEN" not in plan.environment
    assert "GITHUB_TOKEN" not in plan.environment


def test_public_release_plan_requires_matching_immutable_tag_and_release(
    tmp_path: Path,
) -> None:
    assert "expected_hosted_tools" in inspect.signature(build_public_release_plan).parameters
    with pytest.raises(PublicReleaseError, match="release_ref_mismatch"):
        build_public_release_plan(
            repo="natthaphonchop2-creator/mercury-tools",
            tag="v0.3.0",
            release="v0.3.1",
            expected_hosted_tools=24,
            workspace=tmp_path,
        )
