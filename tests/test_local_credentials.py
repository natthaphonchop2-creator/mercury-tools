from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from mercury_tools.drivers.models import CredentialField
from mercury_tools.local.credentials import CredentialStore, credential_env_name
from mercury_tools.local.repository import ensure_repository_state

FIELDS = (
    CredentialField("client_id", secret=False, label="Client ID"),
    CredentialField("client_secret", secret=True, label="Client Secret"),
)


def test_store_rejects_a_context_with_a_non_repository_credential_path(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    forged_context = replace(context, credentials_path=tmp_path / "outside.env")

    with pytest.raises(ValueError, match="^invalid_credentials_path$"):
        CredentialStore(forged_context)


def test_save_quotes_values_and_uses_posix_0600(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)

    status = store.save(
        "flowaccount",
        "production",
        {"client_id": "client one", "client_secret": 'a"b\\nc'},
        FIELDS,
    )

    text = context.credentials_path.read_text()
    assert 'MERCURY_FLOWACCOUNT_PRODUCTION_CLIENT_ID="client one"' in text
    assert 'MERCURY_FLOWACCOUNT_PRODUCTION_CLIENT_SECRET="a\\"b\\\\nc"' in text
    assert status.configured is True
    if os.name == "posix":
        assert stat.S_IMODE(context.credentials_path.stat().st_mode) == 0o600


def test_load_returns_only_requested_profile(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    store.save(
        "flowaccount",
        "production",
        {"client_id": "id", "client_secret": "production-secret"},
        FIELDS,
    )
    store.save(
        "flowaccount",
        "sandbox",
        {"client_id": "test", "client_secret": "sandbox-secret"},
        FIELDS,
    )

    assert store.load("flowaccount", "production", FIELDS) == {
        "client_id": "id",
        "client_secret": "production-secret",
    }


def test_status_is_public_and_partial_save_reports_missing_required_fields(tmp_path: Path) -> None:
    store = CredentialStore(ensure_repository_state(tmp_path))

    status = store.save("flowaccount", "production", {"client_id": "id"}, FIELDS)

    assert status.required_fields == ("client_id", "client_secret")
    assert status.present_fields == ("client_id",)
    assert status.missing_fields == ("client_secret",)
    assert status.configured is False
    assert status.public_dict() == {
        "connector_id": "flowaccount",
        "environment": "production",
        "required_fields": ["client_id", "client_secret"],
        "present_fields": ["client_id"],
        "missing_fields": ["client_secret"],
        "configured": False,
    }


def test_clear_profile_preserves_other_profiles(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    store.save(
        "flowaccount",
        "production",
        {"client_id": "id", "client_secret": "production-secret"},
        FIELDS,
    )
    store.save(
        "flowaccount",
        "sandbox",
        {"client_id": "test", "client_secret": "sandbox-secret"},
        FIELDS,
    )

    assert store.clear("flowaccount", "production") == 2
    assert store.status("flowaccount", "production", FIELDS).configured is False
    assert store.status("flowaccount", "sandbox", FIELDS).configured is True


def test_clear_all_unlinks_the_credentials_file(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    store.save(
        "flowaccount",
        "production",
        {"client_id": "id", "client_secret": "production-secret"},
        FIELDS,
    )

    assert store.clear(clear_all=True) == 2
    assert not context.credentials_path.exists()


def test_clear_without_a_scope_is_rejected(tmp_path: Path) -> None:
    store = CredentialStore(ensure_repository_state(tmp_path))

    with pytest.raises(ValueError, match="^credential_clear_scope_required$"):
        store.clear()


@pytest.mark.parametrize("value", ["bad\nvalue", "bad\u200bvalue", "bad\x7fvalue"])
def test_control_and_format_characters_are_rejected_without_leaking_values(
    tmp_path: Path,
    value: str,
) -> None:
    store = CredentialStore(ensure_repository_state(tmp_path))

    with pytest.raises(ValueError, match="^credential_control_character$") as error:
        store.save(
            "flowaccount",
            "production",
            {"client_id": "id", "client_secret": value},
            FIELDS,
        )

    assert value not in str(error.value)


def test_save_rejects_undeclared_fields_without_leaking_values(tmp_path: Path) -> None:
    store = CredentialStore(ensure_repository_state(tmp_path))
    unexpected_value = "should-not-appear"

    with pytest.raises(ValueError, match="^undeclared_credential_field$") as error:
        store.save(
            "flowaccount",
            "production",
            {"client_id": "id", "client_secret": "secret", "extra": unexpected_value},
            FIELDS,
        )

    assert unexpected_value not in str(error.value)


def test_ambiguous_declared_field_names_are_rejected(tmp_path: Path) -> None:
    store = CredentialStore(ensure_repository_state(tmp_path))
    ambiguous_fields = (
        CredentialField("client-id", secret=False, label="Client ID"),
        CredentialField("client_id", secret=True, label="Client Secret"),
    )

    with pytest.raises(ValueError, match="^ambiguous_credential_identifier$"):
        store.save(
            "flowaccount",
            "production",
            {"client-id": "id", "client_id": "secret"},
            ambiguous_fields,
        )


def test_credential_environment_names_reject_control_characters_and_normalize(
    tmp_path: Path,
) -> None:
    del tmp_path

    assert credential_env_name("flow-account", "sandbox", "client-id") == (
        "MERCURY_FLOW_ACCOUNT_SANDBOX_CLIENT_ID"
    )
    with pytest.raises(ValueError, match="^credential_control_character$"):
        credential_env_name("flowaccount", "production", "client\u200bsecret")


def test_load_uses_dotenv_without_mutating_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ensure_repository_state(tmp_path)
    name = credential_env_name("flowaccount", "production", "client_id")
    context.credentials_path.write_text(f'{name}="stored-value"\n')
    monkeypatch.delenv(name, raising=False)

    loaded = CredentialStore(context).load(
        "flowaccount",
        "production",
        (CredentialField("client_id", secret=False, label="Client ID"),),
    )

    assert loaded == {"client_id": "stored-value"}
    assert name not in os.environ


def test_load_does_not_interpolate_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = ensure_repository_state(tmp_path)
    name = credential_env_name("flowaccount", "production", "client_id")
    monkeypatch.setenv("CREDENTIAL_INTERPOLATION_SOURCE", "environment-value")
    context.credentials_path.write_text(f'{name}="${{CREDENTIAL_INTERPOLATION_SOURCE}}"\n')

    assert CredentialStore(context).load(
        "flowaccount",
        "production",
        (CredentialField("client_id", secret=False, label="Client ID"),),
    ) == {"client_id": "${CREDENTIAL_INTERPOLATION_SOURCE}"}


def test_load_reflects_file_changes_instead_of_a_process_global_cache(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    store.save(
        "flowaccount",
        "production",
        {"client_id": "old-id", "client_secret": "old-secret"},
        FIELDS,
    )
    client_id_name = credential_env_name("flowaccount", "production", "client_id")
    client_secret_name = credential_env_name("flowaccount", "production", "client_secret")
    context.credentials_path.write_text(
        f'{client_id_name}="new-id"\n{client_secret_name}="new-secret"\n'
    )

    assert CredentialStore(context).load("flowaccount", "production", FIELDS) == {
        "client_id": "new-id",
        "client_secret": "new-secret",
    }


def test_serialized_keys_are_lexically_sorted(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    store = CredentialStore(context)
    store.save(
        "flowaccount",
        "production",
        {"client_id": "id", "client_secret": "production-secret"},
        FIELDS,
    )
    store.save(
        "flowaccount",
        "sandbox",
        {"client_id": "test", "client_secret": "sandbox-secret"},
        FIELDS,
    )

    lines = context.credentials_path.read_text().splitlines()
    assert lines == sorted(lines)


def test_duplicate_stored_names_are_rejected_without_revealing_values(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    name = credential_env_name("flowaccount", "production", "client_secret")
    context.credentials_path.write_text(f'{name}="first-secret"\n{name}="second-secret"\n')

    with pytest.raises(ValueError, match="^ambiguous_credential_identifier$") as error:
        CredentialStore(context).load(
            "flowaccount",
            "production",
            (CredentialField("client_secret", secret=True, label="Client Secret"),),
        )

    assert "first-secret" not in str(error.value)
    assert "second-secret" not in str(error.value)


def test_save_rejects_a_credentials_symlink_without_touching_its_target(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    target = tmp_path / "outside.env"
    target.write_text("outside=unchanged\n")
    context.credentials_path.symlink_to(target)

    with pytest.raises(ValueError, match="^credential_path_symlink$"):
        CredentialStore(context).save(
            "flowaccount",
            "production",
            {"client_id": "id", "client_secret": "secret"},
            FIELDS,
        )

    assert target.read_text() == "outside=unchanged\n"


def test_load_rejects_a_dangling_credentials_symlink(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    context.credentials_path.symlink_to(tmp_path / "missing.env")

    with pytest.raises(ValueError, match="^credential_path_symlink$"):
        CredentialStore(context).status("flowaccount", "production", FIELDS)


def test_status_and_repr_never_include_credential_values(tmp_path: Path) -> None:
    secret = "credential-value-must-not-leak"
    store = CredentialStore(ensure_repository_state(tmp_path))
    status = store.save(
        "flowaccount",
        "production",
        {"client_id": "public-id", "client_secret": secret},
        FIELDS,
    )

    assert secret not in repr(status)
    assert secret not in repr(store)
    assert secret not in str(status.public_dict())
