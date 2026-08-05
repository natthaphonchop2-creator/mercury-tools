import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _render_environment() -> dict[str, dict[str, object]]:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    return {item["key"]: item for item in blueprint["services"][0]["envVars"]}


def test_render_blueprint_enables_the_authenticated_v1_surface() -> None:
    environment = _render_environment()

    assert environment["MERCURY_V1_ENABLED"]["value"] == "true"
    assert environment["MERCURY_TOOLS_HTTP_REQUIRE_AUTH"]["value"] == "true"
    assert environment["MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API"]["value"] == "false"


def test_render_blueprint_keeps_v1_secrets_out_of_source_control() -> None:
    environment = _render_environment()
    secret_names = {
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "MERCURY_VAULT_ACTIVE_KEY",
        "MERCURY_CONNECT_SIGNING_SECRET",
        "OPENAI_APPS_CHALLENGE_TOKEN",
    }

    for name in secret_names:
        assert environment[name] == {"key": name, "sync": False}


def test_render_blueprint_pins_reviewed_public_provider_endpoints() -> None:
    environment = _render_environment()
    expected = {
        "FLOWACCOUNT_MCP_SANDBOX_URL": "https://mcp.flowaccount.com/mcp",
        "FLOWACCOUNT_MCP_PRODUCTION_URL": "https://mcp.flowaccount.com/mcp",
        "FLOWACCOUNT_OAUTH_SANDBOX_AUTHORIZATION_SERVER_ORIGIN": ("https://mcp.flowaccount.com"),
        "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN": ("https://mcp.flowaccount.com"),
        "PEAK_MCP_UAT_URL": "https://mcp.peakaccount.com/mcp",
        "PEAK_MCP_PRODUCTION_URL": "https://mcp.peakaccount.com/mcp",
        "MERCURY_PROVIDER_CALLBACK_BASE_URL": ("https://mercury-tools-mcp.onrender.com"),
        "MERCURY_TOOLS_PUBLIC_BASE_URL": "https://mercury-tools-mcp.onrender.com",
    }

    for name, value in expected.items():
        assert environment[name] == {"key": name, "value": value}


def test_public_copy_describes_secure_sign_in_and_server_side_provider_vault() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "REMOTE_DEPLOYMENT.md").read_text(encoding="utf-8")
    plugin = json.loads(
        (ROOT / "plugins" / "mercury-finance" / ".codex-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    submission = json.loads((ROOT / "chatgpt-app-submission.json").read_text(encoding="utf-8"))
    public_copy = "\n".join(
        (
            readme,
            deployment,
            plugin["interface"]["longDescription"],
            submission["app_info"]["description"],
        )
    ).lower()

    assert "secure mercury sign-in" in public_copy
    assert "encrypted provider credentials" in public_copy
    assert "never enter chat or model context" in public_copy
    assert "requires no authentication" not in public_copy
    assert "provider calls remain with" not in public_copy
