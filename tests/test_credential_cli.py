from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from mercury_tools.catalog.models import CatalogAction, RiskTier
from mercury_tools.cli import main
from mercury_tools.drivers.models import ConnectionProbe, CredentialField
from mercury_tools.execution.models import PreparedRequest, canonical_payload_hash
from mercury_tools.execution.policy import RiskDecision
from mercury_tools.execution.store import LocalRequestStore
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import (
    configure_connector,
    ensure_repository_state,
    load_repository_config,
    record_connector_validation,
)


def seed_flow_credentials(
    root: Path,
    *,
    client_id: str = "visible-client",
    client_secret: str = "hidden-secret",
) -> None:
    context = ensure_repository_state(root)
    CredentialStore(context).save(
        "flowaccount",
        "production",
        {"client_id": client_id, "client_secret": client_secret},
        (
            CredentialField("client_id", secret=False, label="FlowAccount Client ID"),
            CredentialField("client_secret", secret=True, label="FlowAccount Client Secret"),
        ),
    )


def test_setup_prompts_only_required_fields_and_hides_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "visible-client")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "hidden-secret")

    code = main(
        [
            "credentials",
            "setup",
            "flowaccount",
            "--env",
            "production",
            "--repo-root",
            str(tmp_path),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "configured" in output
    assert "hidden-secret" not in output
    assert "visible-client" not in output


def test_status_never_prints_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed_flow_credentials(tmp_path)

    assert main(["credentials", "status", "--repo-root", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "client_id" in output
    assert "client_secret" in output
    assert "visible-client" not in output
    assert "hidden-secret" not in output


def test_clear_all_removes_file(tmp_path: Path) -> None:
    seed_flow_credentials(tmp_path)

    assert main(["credentials", "clear", "--all", "--repo-root", str(tmp_path)]) == 0

    assert not (tmp_path / ".mercury/credentials.env").exists()


def test_clear_scoped_invalidates_pending_preview_and_resets_matching_validation(
    tmp_path: Path,
    catalog_action: CatalogAction,
) -> None:
    context = ensure_repository_state(tmp_path)
    action = catalog_action
    payload_hash = canonical_payload_hash(
        {
            "repository_id": context.repository_id,
            "connector_id": "flowaccount",
            "environment": "production",
            "action_id": action.action_id,
            "version_id": action.version_id,
        }
    )
    prepared = PreparedRequest.from_template(
        repository=context,
        action=action,
        environment="production",
        request={
            "method": "POST",
            "final_path": "/invoices",
            "sanitized_summary": {"document_type": "invoice"},
            "request_inputs": {"body": {"amount": 1000}},
        },
        risk=RiskDecision(RiskTier.STANDARD_WRITE, 1, ()),
        payload_hash=payload_hash,
    )
    request = LocalRequestStore(context).create_preview(prepared)
    record_connector_validation(
        context,
        connector_id="flowaccount",
        environment="production",
        company_name=None,
        probe_action="GET /company/info",
        validated_at="2026-07-12T00:00:00+00:00",
    )
    record_connector_validation(
        context,
        connector_id="flowaccount",
        environment="sandbox",
        company_name=None,
        probe_action="GET /company/info",
        validated_at="2026-07-12T00:00:00+00:00",
    )

    assert (
        main(
            [
                "credentials",
                "clear",
                "flowaccount",
                "--env",
                "production",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    stored = LocalRequestStore(context).get(request.request_id)
    assert stored.failure_reason == "credentials_cleared"
    config = load_repository_config(context)
    assert "production" not in config.validations.get("flowaccount", {})
    assert "sandbox" in config.validations["flowaccount"]
    assert config.connectors == {}


def test_custom_connector_requires_exact_host_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(["trust api.example-books.com"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    code = main(
        [
            "connector",
            "configure",
            "custom-books",
            "--env",
            "production",
            "--driver",
            "api_key_header",
            "--base-url",
            "https://api.example-books.com/v2",
            "--key-name",
            "X-API-Key",
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert code == 0
    config = load_repository_config(ensure_repository_state(tmp_path))
    assert config.trusted_hosts["custom-books"]["production"] == (
        "api.example-books.com",
    )


def test_custom_connector_rejects_inexact_host_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "trust wrong.example-books.com")

    code = main(
        [
            "connector",
            "configure",
            "custom-books",
            "--env",
            "production",
            "--driver",
            "api_key_header",
            "--base-url",
            "https://api.example-books.com/v2",
            "--key-name",
            "X-API-Key",
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert code == 2
    assert load_repository_config(ensure_repository_state(tmp_path)).connectors == {}
    assert "wrong.example-books.com" not in capsys.readouterr().out


def test_setup_fails_without_interactive_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def no_input(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)

    assert (
        main(
            [
                "credentials",
                "setup",
                "flowaccount",
                "--env",
                "production",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 2
    )

    assert "interactive_input_required" in capsys.readouterr().out
    assert not (tmp_path / ".mercury/credentials.env").exists()


def test_credentials_test_saves_only_sanitized_validation_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_flow_credentials(tmp_path)
    context = ensure_repository_state(tmp_path)
    configure_connector(
        context,
        connector_id="custom-books",
        environment="production",
        driver_id="api_key_header",
        base_url="https://api.example-books.com/v2",
        auth_settings={"key_name": "X-API-Key"},
    )
    before = json.loads(context.config_path.read_text())

    class FakeDriver:
        def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
            assert environment == "production"
            return (
                CredentialField("client_id", secret=False, label="Client ID"),
                CredentialField("client_secret", secret=True, label="Client secret"),
            )

        def safe_probe_action(self, environment: str) -> str:
            assert environment == "production"
            return "GET /company/info"

        async def validate_credentials(
            self,
            *,
            environment: str,
            credentials: dict[str, str],
            client: object,
        ) -> ConnectionProbe:
            assert credentials["client_secret"] == "hidden-secret"
            return ConnectionProbe(
                status="connected",
                connector_id="flowaccount",
                environment=environment,
                company_name="Demo Books hidden-secret",
                details={"http_status": 200},
            )

    class FakeRegistry:
        def get(self, connector_id: str) -> FakeDriver:
            assert connector_id == "flowaccount"
            return FakeDriver()

    from mercury_tools.local import credential_cli

    monkeypatch.setattr(
        credential_cli.DriverRegistry,
        "for_repository",
        lambda config: FakeRegistry(),
    )

    assert (
        main(
            [
                "credentials",
                "test",
                "flowaccount",
                "--env",
                "production",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    payload = json.loads(context.config_path.read_text())
    validation = payload["validations"]["flowaccount"]["production"]
    assert payload["schema_version"] == before["schema_version"]
    assert payload["trusted_hosts"] == before["trusted_hosts"]
    assert payload["connectors"] == before["connectors"]
    assert validation["connector_id"] == "flowaccount"
    assert validation["environment"] == "production"
    assert validation["company_name"] == "[REDACTED]"
    assert validation["validation_state"] == "connected"
    assert validation["probe_action"] == "GET /company/info"
    assert datetime.fromisoformat(validation["validated_at"])
    assert "hidden-secret" not in output
    assert "visible-client" not in output
    assert "hidden-secret" not in context.config_path.read_text()
    assert "visible-client" not in context.config_path.read_text()
    assert "fingerprint" not in context.config_path.read_text()


@pytest.mark.parametrize(
    "company_name",
    [
        "Demo Books hidden%252Dsecret",
        "Demo Books aGlkZGVuLXNlY3JldA",
        "Demo Books Basic dmlzaWJsZS1jbGllbnQ6aGlkZGVuLXNlY3JldA",
    ],
)
def test_credentials_test_never_prints_or_persists_reversible_credential_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    company_name: str,
) -> None:
    seed_flow_credentials(tmp_path)
    context = ensure_repository_state(tmp_path)

    class FakeDriver:
        def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
            return (
                CredentialField("client_id", secret=False, label="Client ID"),
                CredentialField("client_secret", secret=True, label="Client secret"),
            )

        def safe_probe_action(self, environment: str) -> str:
            return "GET /company/info"

        async def validate_credentials(
            self,
            *,
            environment: str,
            credentials: dict[str, str],
            client: object,
        ) -> ConnectionProbe:
            return ConnectionProbe(
                status="connected",
                connector_id="flowaccount",
                environment=environment,
                company_name=company_name,
                details={"http_status": 200},
            )

    class FakeRegistry:
        def get(self, connector_id: str) -> FakeDriver:
            return FakeDriver()

    from mercury_tools.local import credential_cli

    monkeypatch.setattr(
        credential_cli.DriverRegistry,
        "for_repository",
        lambda config: FakeRegistry(),
    )

    assert (
        main(
            [
                "credentials",
                "test",
                "flowaccount",
                "--env",
                "production",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    persisted = context.config_path.read_text()
    validation = json.loads(persisted)["validations"]["flowaccount"]["production"]
    assert validation["company_name"] == "[REDACTED]"
    assert company_name not in output
    assert company_name not in persisted
    assert "hidden-secret" not in output
    assert "hidden-secret" not in persisted


def test_credentials_test_hides_unexpected_driver_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_flow_credentials(tmp_path)

    class FailingDriver:
        def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
            return (
                CredentialField("client_id", secret=False, label="Client ID"),
                CredentialField("client_secret", secret=True, label="Client secret"),
            )

        async def validate_credentials(
            self,
            *,
            environment: str,
            credentials: dict[str, str],
            client: object,
        ) -> ConnectionProbe:
            raise RuntimeError(credentials["client_secret"])

    class FakeRegistry:
        def get(self, connector_id: str) -> FailingDriver:
            return FailingDriver()

    from mercury_tools.local import credential_cli

    monkeypatch.setattr(
        credential_cli.DriverRegistry,
        "for_repository",
        lambda config: FakeRegistry(),
    )

    assert (
        main(
            [
                "credentials",
                "test",
                "flowaccount",
                "--env",
                "production",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "hidden-secret" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_credentials_test_returns_safe_error_with_an_active_event_loop(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_flow_credentials(tmp_path)

    code = main(
        [
            "credentials",
            "test",
            "flowaccount",
            "--env",
            "production",
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert code == 2
    assert "credentials_test_requires_synchronous_cli" in capsys.readouterr().out


def test_doctor_reports_repository_state_without_credential_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_flow_credentials(tmp_path)

    assert main(["doctor", "--repo-root", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert {
        "python_version",
        "uvx_available",
        "repository",
        "posix_permissions",
        "cloud_url",
        "local_catalog_count",
        "configured_connectors",
        "missing_fields",
    } <= set(payload)
    assert "visible-client" not in str(payload)
    assert "hidden-secret" not in str(payload)


def test_doctor_extends_the_previous_runtime_diagnostics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from mercury_tools.local import credential_cli

    monkeypatch.setattr(
        credential_cli,
        "load_settings",
        lambda: SimpleNamespace(
            supabase_configured=True,
            openai_configured=False,
            embedding_provider="hash",
            embedding_configured=True,
            embedding_model="hash-embedding-v1",
            embedding_dim=384,
            mercury_agent_path=tmp_path / "agent",
            mercury_home=tmp_path / "home",
            mcp_transport="streamable-http",
            mcp_host="127.0.0.1",
            mcp_port=8787,
            mcp_path="/mcp",
            mcp_endpoint="http://127.0.0.1:8787/mcp",
            http_require_auth=True,
            http_auth_configured=False,
            public_base_url=None,
        ),
    )

    assert main(["doctor", "--repo-root", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["supabase"] is True
    assert payload["openai"] is False
    assert payload["embedding_provider"] == "hash"
    assert payload["embedding_configured"] is True
    assert payload["embedding_model"] == "hash-embedding-v1"
    assert payload["embedding_dim"] == 384
    assert payload["mercury_agent_path"] == str(tmp_path / "agent")
    assert payload["mercury_home"] == str(tmp_path / "home")
    assert payload["mcp"] == {
        "transport": "streamable-http",
        "host": "127.0.0.1",
        "port": 8787,
        "path": "/mcp",
        "endpoint": "http://127.0.0.1:8787/mcp",
        "http_auth_required": True,
        "http_auth_configured": False,
    }


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.example-books.com/v2?query=1",
        "https://api.example-books.com/v2#fragment",
        "https://user@api.example-books.com/v2",
        "https://api.example-books.com:invalid/v2",
        "https://api.example-books.com/%ZZ",
        "https://metadata.google.internal/v2",
        "https://127.0.0.1/v2",
    ],
)
def test_connector_configure_rejects_invalid_urls_before_prompting_or_printing_trust_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    base_url: str,
) -> None:
    def unexpected_input(prompt: str) -> str:
        raise AssertionError("input must not be called for invalid URLs")

    monkeypatch.setattr("builtins.input", unexpected_input)

    assert (
        main(
            [
                "connector",
                "configure",
                "custom-books",
                "--env",
                "production",
                "--driver",
                "api_key_header",
                "--base-url",
                base_url,
                "--key-name",
                "X-API-Key",
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 2
    )

    output = capsys.readouterr().out
    assert "Trust candidate:" not in output
    assert load_repository_config(ensure_repository_state(tmp_path)).connectors == {}


@pytest.mark.parametrize(
    "token_url",
    [
        "https://auth.example-books.com/oauth/token?query=1",
        "https://user@auth.example-books.com/oauth/token",
        "https://auth.example-books.com/%ZZ",
        "https://169.254.169.254/oauth/token",
    ],
)
def test_connector_configure_validates_token_url_before_prompting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    token_url: str,
) -> None:
    def unexpected_input(prompt: str) -> str:
        raise AssertionError("input must not be called for invalid URLs")

    monkeypatch.setattr("builtins.input", unexpected_input)

    assert (
        main(
            [
                "connector",
                "configure",
                "custom-books",
                "--env",
                "production",
                "--driver",
                "oauth_client_credentials",
                "--base-url",
                "https://api.example-books.com/v2",
                "--token-url",
                token_url,
                "--repo-root",
                str(tmp_path),
            ]
        )
        == 2
    )

    assert "Trust candidate:" not in capsys.readouterr().out
    assert load_repository_config(ensure_repository_state(tmp_path)).connectors == {}
