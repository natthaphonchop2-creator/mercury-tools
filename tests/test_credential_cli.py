from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import queue
from datetime import datetime
from pathlib import Path
from typing import Any

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

_MP_CLEAR_ENTERED: Any = None
_MP_CLEAR_RELEASE: Any = None
_MP_ORIGINAL_CLEAR: Any = None
_MP_SAVE_ENTERED: Any = None
_MP_SAVE_RELEASE: Any = None
_MP_ORIGINAL_WRITE: Any = None
_MP_PROBE_ENTERED: Any = None
_MP_PROBE_RELEASE: Any = None


def _paused_credential_clear(self: CredentialStore, *args: Any, **kwargs: Any) -> int:
    _MP_CLEAR_ENTERED.set()
    if not _MP_CLEAR_RELEASE.wait(10):
        raise RuntimeError("test_clear_release_timeout")
    return _MP_ORIGINAL_CLEAR(self, *args, **kwargs)


def _paused_credential_write(self: CredentialStore, *args: Any, **kwargs: Any) -> None:
    _MP_SAVE_ENTERED.set()
    if not _MP_SAVE_RELEASE.wait(10):
        raise RuntimeError("test_save_release_timeout")
    _MP_ORIGINAL_WRITE(self, *args, **kwargs)


def _run_clear_cli(root: Path, results: Any) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        code = main(
            [
                "credentials",
                "clear",
                "flowaccount",
                "--env",
                "production",
                "--repo-root",
                str(root),
            ]
        )
    results.put(code)


def _create_and_confirm_preview(
    context: Any,
    prepared: PreparedRequest,
    action: CatalogAction,
    started: Any,
    results: Any,
) -> None:
    started.set()
    try:
        store = LocalRequestStore(context)
        created = store.create_preview(prepared, action=action)
        confirmed = store.confirm(created.request_id, created.payload_hash)
        credentials = CredentialStore(context).load(
            "flowaccount",
            "production",
            (
                CredentialField("client_id", secret=False, label="Client ID"),
                CredentialField("client_secret", secret=True, label="Client Secret"),
            ),
        )
        results.put((confirmed.state.value, bool(credentials)))
    except Exception as exc:  # pragma: no cover - assertion reports child outcome
        results.put(("error", type(exc).__name__, str(exc)))


def _save_flow_credentials_process(root: Path, results: Any) -> None:
    try:
        seed_flow_credentials(root)
        results.put("saved")
    except Exception as exc:  # pragma: no cover - assertion reports child outcome
        results.put(("error", type(exc).__name__, str(exc)))


def _clear_flow_credentials_process(context: Any, started: Any, results: Any) -> None:
    started.set()
    try:
        cleared = CredentialStore(context).clear("flowaccount", "production")
        results.put(("cleared", cleared))
    except Exception as exc:  # pragma: no cover - assertion reports child outcome
        results.put(("error", type(exc).__name__, str(exc)))


async def _paused_connected_probe(
    driver: Any,
    *,
    environment: str,
    credentials: dict[str, str],
) -> ConnectionProbe:
    _MP_PROBE_ENTERED.set()
    if not _MP_PROBE_RELEASE.wait(10):
        raise RuntimeError("test_probe_release_timeout")
    return ConnectionProbe(
        status="connected",
        connector_id="flowaccount",
        environment=environment,
        company_name="Example Books",
        details={"http_status": 200},
    )


def _run_credentials_test_cli(root: Path, results: Any) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        code = main(
            [
                "credentials",
                "test",
                "flowaccount",
                "--env",
                "production",
                "--repo-root",
                str(root),
            ]
        )
    results.put(code)


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


def test_clear_all_removes_file_and_invalidates_every_pending_preview(
    tmp_path: Path,
    catalog_action: CatalogAction,
) -> None:
    seed_flow_credentials(tmp_path)
    context = ensure_repository_state(tmp_path)
    store = LocalRequestStore(context)
    request_ids: list[str] = []
    for environment, amount in (("production", 1000), ("sandbox", 2000)):
        request_inputs = {"body": {"amount": amount}}
        payload_hash = canonical_payload_hash(
            {
                "repository_id": context.repository_id,
                "connector_id": catalog_action.connector_id,
                "environment": environment,
                "action_id": catalog_action.action_id,
                "version_id": catalog_action.version_id,
                "method": "POST",
                "final_path": "/invoices",
                "request_inputs": request_inputs,
                "risk_tier": 1,
                "required_confirmations": 1,
            }
        )
        prepared = PreparedRequest.from_template(
            repository=context,
            action=catalog_action,
            environment=environment,
            request={
                "method": "POST",
                "final_path": "/invoices",
                "request_inputs": request_inputs,
            },
            risk=RiskDecision(RiskTier.STANDARD_WRITE, 1, ()),
            payload_hash=payload_hash,
        )
        created = store.create_preview(prepared, action=catalog_action)
        store.confirm(created.request_id, created.payload_hash)
        request_ids.append(created.request_id)

    assert main(["credentials", "clear", "--all", "--repo-root", str(tmp_path)]) == 0

    assert not (tmp_path / ".mercury/credentials.env").exists()
    assert all(
        store.get(request_id).failure_reason == "credentials_cleared"
        for request_id in request_ids
    )


def test_clear_scoped_invalidates_pending_preview_and_resets_matching_validation(
    tmp_path: Path,
    catalog_action: CatalogAction,
) -> None:
    context = ensure_repository_state(tmp_path)
    action = catalog_action
    request_template = {
        "method": "POST",
        "final_path": "/invoices",
        "sanitized_summary": {"document_type": "invoice"},
        "request_inputs": {"body": {"amount": 1000}},
    }
    risk = RiskDecision(RiskTier.STANDARD_WRITE, 1, ())
    payload_hash = canonical_payload_hash(
        {
            "repository_id": context.repository_id,
            "connector_id": "flowaccount",
            "environment": "production",
            "action_id": action.action_id,
            "version_id": action.version_id,
            "method": "POST",
            "final_path": "/invoices",
            "request_inputs": {"body": {"amount": 1000}},
            "risk_tier": 1,
            "required_confirmations": 1,
        }
    )
    prepared = PreparedRequest.from_template(
        repository=context,
        action=action,
        environment="production",
        request=request_template,
        risk=risk,
        payload_hash=payload_hash,
    )
    request = LocalRequestStore(context).create_preview(prepared, action=action)
    LocalRequestStore(context).confirm(request.request_id, request.payload_hash)
    seed_flow_credentials(tmp_path)
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
    assert "hidden-secret" not in context.credentials_path.read_text()
    config = load_repository_config(context)
    assert "production" not in config.validations.get("flowaccount", {})
    assert "sandbox" in config.validations["flowaccount"]
    assert config.connectors == {}


def _seed_clear_failure_state(
    tmp_path: Path,
    catalog_action: CatalogAction,
) -> tuple[LocalRequestStore, str]:
    context = ensure_repository_state(tmp_path)
    seed_flow_credentials(tmp_path)
    request_inputs = {"body": {"amount": 1000}}
    risk = RiskDecision(RiskTier.STANDARD_WRITE, 1, ())
    payload_hash = canonical_payload_hash(
        {
            "repository_id": context.repository_id,
            "connector_id": catalog_action.connector_id,
            "environment": "production",
            "action_id": catalog_action.action_id,
            "version_id": catalog_action.version_id,
            "method": "POST",
            "final_path": "/invoices",
            "request_inputs": request_inputs,
            "risk_tier": 1,
            "required_confirmations": 1,
        }
    )
    prepared = PreparedRequest.from_template(
        repository=context,
        action=catalog_action,
        environment="production",
        request={
            "method": "POST",
            "final_path": "/invoices",
            "request_inputs": request_inputs,
        },
        risk=risk,
        payload_hash=payload_hash,
    )
    store = LocalRequestStore(context)
    created = store.create_preview(prepared, action=catalog_action)
    store.confirm(created.request_id, created.payload_hash)
    record_connector_validation(
        context,
        connector_id="flowaccount",
        environment="production",
        company_name=None,
        probe_action="GET /company/info",
        validated_at="2026-07-12T00:00:00+00:00",
    )
    return store, created.request_id


def test_clear_failure_during_invalidation_keeps_credentials_and_ready_preview(
    tmp_path: Path,
    catalog_action: CatalogAction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request_id = _seed_clear_failure_state(tmp_path, catalog_action)

    def fail_invalidation(*args: object, **kwargs: object) -> int:
        raise OSError("injected")

    monkeypatch.setattr(LocalRequestStore, "invalidate_pending", fail_invalidation)

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
        == 2
    )
    assert ensure_repository_state(tmp_path).credentials_path.exists()
    assert store.get(request_id).state.value == "ready_to_execute"


def test_clear_failure_during_validation_reset_keeps_credentials_after_invalidation(
    tmp_path: Path,
    catalog_action: CatalogAction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request_id = _seed_clear_failure_state(tmp_path, catalog_action)
    from mercury_tools.local import credential_cli

    def fail_validation_reset(*args: object, **kwargs: object) -> object:
        raise OSError("injected")

    monkeypatch.setattr(credential_cli, "clear_connector_validations", fail_validation_reset)

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
        == 2
    )
    assert ensure_repository_state(tmp_path).credentials_path.exists()
    assert store.get(request_id).failure_reason == "credentials_cleared"


def test_clear_failure_during_credential_delete_happens_after_safety_state_updates(
    tmp_path: Path,
    catalog_action: CatalogAction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, request_id = _seed_clear_failure_state(tmp_path, catalog_action)

    def fail_credential_delete(*args: object, **kwargs: object) -> int:
        raise OSError("injected")

    monkeypatch.setattr(CredentialStore, "clear", fail_credential_delete)

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
        == 2
    )
    context = ensure_repository_state(tmp_path)
    assert context.credentials_path.exists()
    assert store.get(request_id).failure_reason == "credentials_cleared"
    assert "production" not in load_repository_config(context).validations.get(
        "flowaccount", {}
    )


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires inherited process instrumentation",
)
def test_clear_holds_repository_lock_until_credentials_are_deleted(
    tmp_path: Path,
    catalog_action: CatalogAction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_clear_failure_state(tmp_path, catalog_action)
    context = ensure_repository_state(tmp_path)
    request_inputs = {"body": {"amount": 2000}}
    payload_hash = canonical_payload_hash(
        {
            "repository_id": context.repository_id,
            "connector_id": catalog_action.connector_id,
            "environment": "production",
            "action_id": catalog_action.action_id,
            "version_id": catalog_action.version_id,
            "method": "POST",
            "final_path": "/invoices",
            "request_inputs": request_inputs,
            "risk_tier": 1,
            "required_confirmations": 1,
        }
    )
    prepared = PreparedRequest.from_template(
        repository=context,
        action=catalog_action,
        environment="production",
        request={
            "method": "POST",
            "final_path": "/invoices",
            "request_inputs": request_inputs,
        },
        risk=RiskDecision(RiskTier.STANDARD_WRITE, 1, ()),
        payload_hash=payload_hash,
    )
    process_context = multiprocessing.get_context("fork")
    clear_entered = process_context.Event()
    clear_release = process_context.Event()
    preview_started = process_context.Event()
    clear_results = process_context.Queue()
    preview_results = process_context.Queue()

    global _MP_CLEAR_ENTERED, _MP_CLEAR_RELEASE, _MP_ORIGINAL_CLEAR
    _MP_CLEAR_ENTERED = clear_entered
    _MP_CLEAR_RELEASE = clear_release
    _MP_ORIGINAL_CLEAR = CredentialStore.clear
    monkeypatch.setattr(CredentialStore, "clear", _paused_credential_clear)

    clear_process = process_context.Process(
        target=_run_clear_cli,
        args=(tmp_path, clear_results),
    )
    preview_process = process_context.Process(
        target=_create_and_confirm_preview,
        args=(context, prepared, catalog_action, preview_started, preview_results),
    )
    clear_process.start()
    try:
        assert clear_entered.wait(10)
        preview_process.start()
        assert preview_started.wait(10)
        with pytest.raises(queue.Empty):
            preview_results.get(timeout=0.5)
        clear_release.set()
        clear_process.join(10)
        preview_process.join(10)
    finally:
        clear_release.set()
        for process in (clear_process, preview_process):
            if process.pid is not None and process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(5)

    assert clear_process.exitcode == 0
    assert preview_process.exitcode == 0
    assert clear_results.get(timeout=1) == 0
    assert preview_results.get(timeout=1) == ("ready_to_execute", False)


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires inherited process instrumentation",
)
def test_credential_save_and_clear_are_cross_process_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ensure_repository_state(tmp_path)
    process_context = multiprocessing.get_context("fork")
    save_entered = process_context.Event()
    save_release = process_context.Event()
    clear_started = process_context.Event()
    save_results = process_context.Queue()
    clear_results = process_context.Queue()

    global _MP_SAVE_ENTERED, _MP_SAVE_RELEASE, _MP_ORIGINAL_WRITE
    _MP_SAVE_ENTERED = save_entered
    _MP_SAVE_RELEASE = save_release
    _MP_ORIGINAL_WRITE = CredentialStore._write
    monkeypatch.setattr(CredentialStore, "_write", _paused_credential_write)

    save_process = process_context.Process(
        target=_save_flow_credentials_process,
        args=(tmp_path, save_results),
    )
    clear_process = process_context.Process(
        target=_clear_flow_credentials_process,
        args=(context, clear_started, clear_results),
    )
    save_process.start()
    try:
        assert save_entered.wait(10)
        clear_process.start()
        assert clear_started.wait(10)
        with pytest.raises(queue.Empty):
            clear_results.get(timeout=0.5)
        save_release.set()
        save_process.join(10)
        clear_process.join(10)
    finally:
        save_release.set()
        for process in (save_process, clear_process):
            if process.pid is not None and process.is_alive():
                process.terminate()
            if process.pid is not None:
                process.join(5)

    assert save_process.exitcode == 0
    assert clear_process.exitcode == 0
    assert save_results.get(timeout=1) == "saved"
    assert clear_results.get(timeout=1) == ("cleared", 2)
    assert CredentialStore(context).load(
        "flowaccount",
        "production",
        (
            CredentialField("client_id", secret=False, label="Client ID"),
            CredentialField("client_secret", secret=True, label="Client Secret"),
        ),
    ) == {}


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


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="requires inherited process instrumentation",
)
def test_credentials_test_does_not_restore_validation_after_concurrent_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_flow_credentials(tmp_path)
    context = ensure_repository_state(tmp_path)
    process_context = multiprocessing.get_context("fork")
    probe_entered = process_context.Event()
    probe_release = process_context.Event()
    results = process_context.Queue()
    from mercury_tools.local import credential_cli

    global _MP_PROBE_ENTERED, _MP_PROBE_RELEASE
    _MP_PROBE_ENTERED = probe_entered
    _MP_PROBE_RELEASE = probe_release
    monkeypatch.setattr(credential_cli, "_validate_credentials", _paused_connected_probe)

    process = process_context.Process(
        target=_run_credentials_test_cli,
        args=(tmp_path, results),
    )
    process.start()
    try:
        assert probe_entered.wait(10)
        with contextlib.redirect_stdout(io.StringIO()):
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
        probe_release.set()
        process.join(10)
    finally:
        probe_release.set()
        if process.is_alive():
            process.terminate()
        process.join(5)

    assert process.exitcode == 0
    assert results.get(timeout=1) == 2
    config = load_repository_config(context)
    assert "production" not in config.validations.get("flowaccount", {})
    assert CredentialStore(context).load(
        "flowaccount",
        "production",
        (
            CredentialField("client_id", secret=False, label="Client ID"),
            CredentialField("client_secret", secret=True, label="Client Secret"),
        ),
    ) == {}
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
