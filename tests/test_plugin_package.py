import builtins
import importlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from mercury_tools.db.product import SKILL_CATALOG_SEED

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins/mercury-finance"
SKILLS_ROOT = PLUGIN_ROOT / "skills"

SETUP_SKILLS = (
    "connector-setup-guide-th",
    "connector-credential-setup-th",
    "flowaccount-connector-setup-th",
    "peak-connector-setup-th",
)
READ_SKILLS = (
    "company-health-check-th",
    "vat-summary-th",
    "invoice-review-th",
    "management-report-th",
)
EXPECTED_DESCRIPTIONS = {
    "connector-setup-guide-th": (
        "Use when the user needs to choose or configure an accounting or ERP connector"
    ),
    "connector-credential-setup-th": (
        "Use when an accounting or ERP task is blocked because local connector "
        "credentials are not ready"
    ),
    "flowaccount-connector-setup-th": (
        "Use when a FlowAccount task needs local connector setup or connection "
        "troubleshooting"
    ),
    "peak-connector-setup-th": (
        "Use when a PEAK task needs local connector setup or connection troubleshooting"
    ),
    "company-health-check-th": (
        "Use when the user asks for company health, revenue, VAT, cash flow, or "
        "accounting status summaries"
    ),
    "vat-summary-th": (
        "Use when the user asks for Thai VAT output tax, input tax, filing context, "
        "or tax-period summaries"
    ),
    "invoice-review-th": (
        "Use when the user asks to review invoices, tax invoices, receipts, missing "
        "fields, or accounting evidence"
    ),
    "management-report-th": (
        "Use when the user asks for Thai management reports, owner summaries, CFO "
        "packs, or monthly accounting narratives"
    ),
    "mercury-flow-runner": (
        "Use when the user asks to list, save, preview, or run Mercury Flows for "
        "accounting workflows"
    ),
    "flowaccount-journal-posting-th": (
        "Use when the user asks to record, draft, post, or approve a FlowAccount "
        "journal entry"
    ),
}
READ_TOOL_ORDER = (
    "credential_status",
    "retrieve_context_pack",
    "search_erp_actions",
    "get_erp_action_schema",
    "run_erp_read",
)
PACKAGE_FORBIDDEN_TERMS = {
    "approve_flowaccount_journal",
    "connector_status",
    "create_flowaccount_journal_draft",
    "create_public_workspace",
    "list_connectors",
    "preview_flowaccount_journal",
    "required_secret_fields",
    "retrieve_workspace_context_pack",
    "run_mercury_flow",
    "start_connector_setup",
    "submit_connector_credentials",
    "validate_connector_connection",
    "workspace_id",
}
CREDENTIAL_FIELD_NAMES = {
    "application_code",
    "client_id",
    "client_secret",
    "connect_id",
    "connect_key",
    "user_token",
}


def skill_text(skill_name: str) -> str:
    return (SKILLS_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")


def assert_terms_in_order(text: str, terms: tuple[str, ...]) -> None:
    text = " ".join(text.split())
    cursor = 0
    for term in terms:
        position = text.find(term, cursor)
        assert position >= 0, f"missing {term!r} after offset {cursor}"
        cursor = position + len(term)


def frontmatter_description(text: str) -> str:
    match = re.search(r"(?m)^description: (.+)$", text)
    assert match is not None
    return match.group(1)


def test_product_catalog_contains_every_bundled_plugin_skill() -> None:
    bundled = {path.parent.name for path in SKILLS_ROOT.glob("*/SKILL.md")}
    catalog = {row["skill_id"] for row in SKILL_CATALOG_SEED}

    assert catalog == bundled == set(EXPECTED_DESCRIPTIONS)


def test_marketplace_contains_exactly_one_mercury_plugin() -> None:
    marketplace = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )

    assert [item["name"] for item in marketplace["plugins"]] == ["mercury-finance"]
    assert not (ROOT / "plugins/mercury-finance-private").exists()
    assert not (ROOT / "tests/test_private_mcp.py").exists()


def test_marketplace_points_to_plugin_folder() -> None:
    data = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    mercury = data["plugins"][0]

    assert data["name"] == "mercury-tools"
    assert data["interface"]["displayName"] == "Mercury Tools"
    assert mercury["source"]["path"] == "./plugins/mercury-finance"
    assert mercury["source"]["source"] == "local"
    assert mercury["policy"]["installation"] == "AVAILABLE"
    assert mercury["policy"]["authentication"] == "ON_INSTALL"
    assert mercury["category"] == "Finance"


def test_skill_frontmatter_descriptions_are_trigger_only() -> None:
    for skill_name, expected in EXPECTED_DESCRIPTIONS.items():
        assert frontmatter_description(skill_text(skill_name)) == expected


def test_setup_skills_use_the_exact_local_credential_gate() -> None:
    required_order = (
        "credential_status",
        "If required credentials are missing, stop",
        "mercury credentials setup",
        "After the user confirms setup is complete",
        "credential_status",
        "If it is still missing or not configured, stop and return to local setup",
        "mercury credentials test",
        "Continue only when the test reports `connected`",
    )

    for skill_name in SETUP_SKILLS:
        text = skill_text(skill_name)
        assert_terms_in_order(text, required_order)
        assert "Never ask for, accept, or paste credentials in chat." in text


def test_read_skills_use_only_the_generic_read_sequence() -> None:
    disallowed_tools = {
        "preview_erp_write",
        "confirm_erp_write",
        "execute_erp_write",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
    }

    for skill_name in READ_SKILLS:
        text = skill_text(skill_name)
        assert_terms_in_order(text, READ_TOOL_ORDER)
        assert "citations" in text
        assert "ตอบภาษาไทยแบบกระชับ" in text
        assert "unless the user explicitly requests audit detail" in text
        assert not any(tool in text for tool in disallowed_tools)


def test_read_skills_explicitly_filter_safe_actions_and_inspect_schema() -> None:
    required_order = (
        "search_erp_actions",
        "`risk_tier=0`",
        "get_erp_action_schema",
        "Inspect the returned schema",
        "run_erp_read",
    )

    for skill_name in READ_SKILLS:
        assert_terms_in_order(skill_text(skill_name), required_order)


def test_journal_skill_branches_every_mutation_on_returned_risk_contract() -> None:
    text = skill_text("flowaccount-journal-posting-th")
    common_order = (
        "required accounting context",
        "total debit equals total credit",
        "search_erp_actions",
        "get_erp_action_schema",
        "preview_erp_write",
        "returned `risk_tier` and `required_confirmations`",
    )
    tier_one_order = (
        "Tier 1",
        "one distinct explicit user confirmation",
        "request_id",
        "payload_hash",
        "confirm_erp_write",
        "execute_erp_write",
    )
    tier_two_order = (
        "risk_tier >= 2 or `required_confirmations >= 2`",
        "same fresh bound preview",
        "first distinct explicit user confirmation",
        "first `confirm_erp_write`",
        "second distinct explicit user confirmation",
        "second `confirm_erp_write`",
        "execute_erp_write",
    )

    assert_terms_in_order(text, common_order)
    assert_terms_in_order(text, tier_one_order)
    assert_terms_in_order(text, tier_two_order)
    assert "for every journal mutation, not only approval" in text.lower()
    assert "Call `execute_erp_write` exactly once" in text
    assert "get_erp_request_status" in text
    assert "never replay or retry" in text


def test_journal_skill_discards_invalidated_previews_before_restarting() -> None:
    text = skill_text("flowaccount-journal-posting-th")
    invalidation_causes = (
        "stale or expired preview",
        "payload hash mismatch",
        "action-version or binding mismatch",
        "state mismatch",
        "changed inputs",
    )
    recovery_order = (
        "Stop; discard the old request",
        "Never reuse its `request_id` or `payload_hash`",
        "redo `search_erp_actions`",
        "get_erp_action_schema",
        "fresh `preview_erp_write`",
        "collect confirmations again",
    )

    assert all(cause in text for cause in invalidation_causes)
    assert_terms_in_order(text, recovery_order)


def test_journal_skill_restarts_approval_with_a_new_bound_request() -> None:
    text = skill_text("flowaccount-journal-posting-th")
    approval_order = (
        "Approval is a separate action",
        "new `search_erp_actions`",
        "get_erp_action_schema",
        "fresh `preview_erp_write`",
        "new `request_id`",
    )

    assert_terms_in_order(text, approval_order)


def test_flow_runner_cannot_confirm_execute_or_retry_writes() -> None:
    text = skill_text("mercury-flow-runner")

    for tool in (
        "credential_status",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
    ):
        assert tool in text
    assert "read actions or `preview_erp_write`" in text
    assert "Never self-confirm or execute a write" in text
    assert "Never retry a write" in text
    assert "confirm_erp_write" not in text
    assert "execute_erp_write" not in text


def test_public_journal_catalog_tags_exclude_private() -> None:
    journal = next(
        row
        for row in SKILL_CATALOG_SEED
        if row["skill_id"] == "flowaccount-journal-posting-th"
    )

    assert journal["tags"] == ["flowaccount", "journal", "write", "thai"]


def test_skill_package_has_no_private_or_workspace_tool_terms() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    )

    assert not PACKAGE_FORBIDDEN_TERMS.intersection(combined.split())
    for term in PACKAGE_FORBIDDEN_TERMS:
        assert term not in combined


def test_skill_package_has_no_secret_fields_or_credential_chat_flow() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    ).lower()

    for field_name in CREDENTIAL_FIELD_NAMES:
        assert field_name not in combined
    for unsafe_phrase in (
        "ask the user for credentials",
        "ask the user to paste",
        "provide your credentials",
        "send your credentials",
        "send credentials in chat",
        "submit credentials",
    ):
        assert unsafe_phrase not in combined


def test_plugin_registers_one_pinned_local_stdio_server() -> None:
    data = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))

    assert list(data["mcpServers"]) == ["mercury-finance"]
    server = data["mcpServers"]["mercury-finance"]
    assert server["command"] == "uvx"
    assert server["args"] == [
        "--from",
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.0",
        "mercury",
        "mcp",
        "serve-local",
    ]
    assert server["cwd"] == "."
    assert server["tool_timeout_sec"] == 900
    assert "type" not in server
    assert "url" not in server
    assert "env" not in server
    assert "bearer_token_env_var" not in server


def test_plugin_declares_read_and_write_without_embedded_secrets() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    serialized = json.dumps(manifest)

    assert manifest["version"] == "0.2.0+codex.20260711"
    assert manifest["interface"]["capabilities"] == ["Interactive", "Read", "Write"]
    assert manifest["interface"]["defaultPrompt"] == [
        "Set up local FlowAccount access for this repository and verify it.",
        "Search the local ERP action catalog and run a safe read action.",
        "Preview an approval-gated PEAK write for this repository without executing it.",
    ]
    assert "MERCURY_PRIVATE_MCP_TOKEN" not in serialized
    assert "client_secret" not in serialized


def test_release_runtime_dependencies_are_exactly_pinned() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]

    assert data["project"]["version"] == "0.2.0"
    assert all("==" in dependency for dependency in dependencies)
    assert dependencies == [
        "cryptography==49.0.0",
        "httpx==0.28.1",
        "mcp==1.26.0",
        "pydantic==2.13.4",
        "python-dotenv==1.2.2",
        "pyyaml==6.0.3",
        "starlette==1.3.1",
        "uvicorn==0.50.0",
    ]
    assert data["project"]["optional-dependencies"]["openai"] == ["openai==2.44.0"]
    assert "openai" not in "\n".join(dependencies)
    assert (ROOT / "src/mercury_tools/__init__.py").read_text(encoding="utf-8").strip().endswith(
        '__version__ = "0.2.0"'
    )


def test_embeddings_import_without_openai_and_fail_with_actionable_extra_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "mercury_tools.rag.embeddings"
    sys.modules.pop(module_name, None)
    original_import = builtins.__import__

    def without_openai(name: str, *args: object, **kwargs: object):
        if name == "openai" or name.startswith("openai."):
            raise ModuleNotFoundError("No module named 'openai'", name="openai")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_openai)
    embeddings = importlib.import_module(module_name)

    from mercury_tools.config import Settings

    settings = Settings(
        supabase_url="",
        supabase_service_role_key="",
        openai_api_key="",
    )
    with pytest.raises(
        RuntimeError,
        match=re.escape("Install mercury-tools[openai] to use OpenAI embeddings."),
    ):
        embeddings.OpenAIEmbeddingProvider(settings)
    default_provider = embeddings.create_embedding_provider(Settings("", "", ""))
    assert isinstance(default_provider, embeddings.HashEmbeddingProvider)


def test_openai_embedding_provider_preserves_nested_dependency_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.config import Settings
    from mercury_tools.rag import embeddings

    original_import = builtins.__import__

    def without_nested_dependency(name: str, *args: object, **kwargs: object):
        if name == "openai":
            raise ModuleNotFoundError("No module named 'jiter'", name="jiter")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_nested_dependency)
    with pytest.raises(ModuleNotFoundError) as error:
        embeddings.OpenAIEmbeddingProvider(Settings("", "", "test-key"))
    assert error.value.name == "jiter"


def _release_layout(tmp_path: Path, *, pinned_launcher: bool = False) -> Path:
    release_root = tmp_path / "release"
    for relative_path in (
        ".agents/plugins/marketplace.json",
        "plugins/mercury-finance/.mcp.json",
        "plugins/mercury-finance/.codex-plugin/plugin.json",
        "pyproject.toml",
    ):
        source = ROOT / relative_path
        destination = release_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    if pinned_launcher:
        (release_root / "plugins/mercury-finance/.mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "mercury-finance": {
                            "command": "uvx",
                            "args": [
                                "--from",
                                "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.0",
                                "mercury",
                                "mcp",
                                "serve-local",
                            ],
                            "cwd": ".",
                            "tool_timeout_sec": 900,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
    return release_root


def _run_release_validator(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_release_plugin.py"), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def _rewrite_release_json(root: Path, relative_path: str, mutate) -> None:
    path = root / relative_path
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def _rewrite_mcp(root: Path, mutate) -> None:
    _rewrite_release_json(root, "plugins/mercury-finance/.mcp.json", mutate)


def _rewrite_plugin(root: Path, mutate) -> None:
    _rewrite_release_json(root, "plugins/mercury-finance/.codex-plugin/plugin.json", mutate)


def _rewrite_marketplace(root: Path, mutate) -> None:
    _rewrite_release_json(root, ".agents/plugins/marketplace.json", mutate)


def test_release_validator_accepts_the_offline_release_contract(tmp_path: Path) -> None:
    result = _run_release_validator(_release_layout(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "release plugin validation passed" in result.stdout


def test_release_validator_rejects_empty_server_with_every_required_contract(
    tmp_path: Path,
) -> None:
    release_root = _release_layout(tmp_path, pinned_launcher=True)
    _rewrite_mcp(
        release_root,
        lambda data: data["mcpServers"].__setitem__("mercury-finance", {}),
    )

    result = _run_release_validator(release_root)

    assert result.returncode == 1
    for expected_error in (
        "command must be uvx",
        "immutable v0.2.0 Git tag",
        "cwd must be .",
        "tool_timeout_sec must be 900",
    ):
        assert expected_error in result.stdout


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (
            "notes",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
        ),
        ("notes", "eyJhbGciOiJIUzI1NiJ9.payload.signature"),
        ("api_key", "sk-live-51f89c816a374ad6b62be6a1"),
        ("client_secret", "v1.N9x4pQ7sT2wL8mK6rH3c"),
    ],
    ids=["bearer", "jwt", "api-key", "secret"],
)
def test_release_validator_rejects_high_confidence_credential_values(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    release_root = _release_layout(tmp_path, pinned_launcher=True)
    _rewrite_plugin(
        release_root,
        lambda data: data.update({"review_fixture": {key: value}}),
    )

    result = _run_release_validator(release_root)

    assert result.returncode == 1
    assert "credential literal values" in result.stdout


def test_release_validator_allows_documentation_credential_placeholders(tmp_path: Path) -> None:
    release_root = _release_layout(tmp_path, pinned_launcher=True)
    _rewrite_plugin(
        release_root,
        lambda data: data.update(
            {
                "review_fixture": {
                    "authorization": "Bearer <token>",
                    "api_key": "${FLOWACCOUNT_API_KEY}",
                    "client_secret": "YOUR_CLIENT_SECRET",
                    "password": "[REDACTED]",
                }
            }
        ),
    )

    result = _run_release_validator(release_root)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda data: data["mcpServers"]["mercury-finance"].update(
                {"url": "https://example.invalid/mcp"}
            ),
            "must not declare an HTTP URL",
        ),
        (
            lambda data: data["mcpServers"]["mercury-finance"].update(
                {
                    "args": [
                        "--from",
                        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@main",
                        "mercury",
                        "mcp",
                        "serve-local",
                    ]
                }
            ),
            "immutable v0.2.0 Git tag",
        ),
        (
            lambda data: data["mcpServers"].update(
                {"mercury-tools": {"command": "uvx", "args": ["mercury"]}}
            ),
            "exactly one server",
        ),
        (
            lambda data: data["mcpServers"]["mercury-finance"].update(
                {"env": {"FLOWACCOUNT_CLIENT_SECRET": "should-not-ship"}}
            ),
            "must not declare environment or credential values",
        ),
        (
            lambda data: data["mcpServers"]["mercury-finance"].update(
                {"private_token_env_var": "MERCURY_PRIVATE_TOKEN"}
            ),
            "private token names",
        ),
    ],
    ids=["http-url", "moving-ref", "second-server", "credential-env", "private-token-name"],
)
def test_release_validator_rejects_mutated_unsafe_launchers(
    tmp_path: Path,
    mutate,
    expected_error: str,
) -> None:
    release_root = _release_layout(tmp_path, pinned_launcher=True)
    _rewrite_mcp(release_root, mutate)

    result = _run_release_validator(release_root)

    assert result.returncode == 1
    assert expected_error in result.stdout


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda data: data["plugins"].append(dict(data["plugins"][0])),
            "exactly one mercury-finance plugin",
        ),
        (
            lambda data: data["plugins"][0]["source"].update({"path": "./plugins/other"}),
            "source must be local ./plugins/mercury-finance",
        ),
        (
            lambda data: data["plugins"][0]["policy"].update(
                {"installation": "INSTALLED_BY_DEFAULT"}
            ),
            "installation policy must be AVAILABLE",
        ),
        (
            lambda data: data["plugins"][0]["policy"].update({"authentication": "ON_USE"}),
            "authentication policy must be ON_INSTALL",
        ),
    ],
    ids=["multiple", "source-path", "installation", "authentication"],
)
def test_release_validator_rejects_invalid_marketplace_contract(
    tmp_path: Path,
    mutate,
    expected_error: str,
) -> None:
    release_root = _release_layout(tmp_path, pinned_launcher=True)
    _rewrite_marketplace(release_root, mutate)

    result = _run_release_validator(release_root)

    assert result.returncode == 1
    assert expected_error in result.stdout


def test_judge_quickstart_matches_current_public_plugin() -> None:
    text = (ROOT / "docs/JUDGE_QUICKSTART.md").read_text(encoding="utf-8")

    assert "Mercury Finance" in text
    assert "codex plugin marketplace add" in text
    assert "v0.2.0" in text
    assert "repository-local" in text
    assert "Task 18" in text
    assert "remote-tag smoke" in text
    assert "client_token" not in text
    assert "Mercury Connect" not in text
    assert "token provided by the Mercury demo owner" not in text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in text
    assert "client_secret =" not in text


def test_task_16_report_uses_dev_extra_for_pytest_commands() -> None:
    report = (ROOT / ".superpowers/sdd/task-16-report.md").read_text(encoding="utf-8")
    pytest_commands = [line for line in report.splitlines() if "pytest" in line]

    assert pytest_commands
    assert all("uv run --extra dev pytest" in line for line in pytest_commands)


def test_plugin_package_has_no_embedded_secret_env_names_or_values() -> None:
    files = [
        ROOT / ".agents/plugins/marketplace.json",
        PLUGIN_ROOT / ".codex-plugin/plugin.json",
        PLUGIN_ROOT / ".mcp.json",
        *sorted(SKILLS_ROOT.glob("*/SKILL.md")),
    ]
    serialized = "\n".join(file.read_text(encoding="utf-8") for file in files)
    env_names = set(
        re.findall(
            r"\b[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS)"
            r"[A-Z0-9_]*\b",
            serialized,
        )
    )

    assert "MERCURY_TOOLS_MCP_TOKEN" not in serialized
    assert env_names == set()
    assert "SUPABASE_SERVICE_ROLE_KEY" not in serialized
    assert "FLOWACCOUNT_CLIENT_SECRET" not in serialized
    assert "PEAK_CLIENT_SECRET" not in serialized
    assert "sk-" not in serialized
    assert "service_role" not in serialized
