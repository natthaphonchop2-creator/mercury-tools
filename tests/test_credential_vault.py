from __future__ import annotations

import base64
import inspect
import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = UUID("22222222-2222-4222-8222-222222222222")
AUTH_USER_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_AUTH_USER_ID = UUID("44444444-4444-4444-8444-444444444444")
WORKSPACE_ID = UUID("55555555-5555-4555-8555-555555555555")
OTHER_WORKSPACE_ID = UUID("66666666-6666-4666-8666-666666666666")
CONNECTION_ID = UUID("77777777-7777-4777-8777-777777777777")
NOW = datetime(2026, 7, 26, 10, 30, tzinfo=UTC)
ACTIVE_KEY = bytes(range(32))
PREVIOUS_KEY = bytes(reversed(range(32)))
PLAINTEXT = b"request-scoped-provider-secret-7f3a"
ACTIVE_KEY_VERSION = "v1"
PREVIOUS_KEY_VERSION = "v0"


def _binding(**updates: object):
    from mercury_tools.credentials.models import CredentialBinding

    values: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "auth_user_id": AUTH_USER_ID,
        "connection_id": CONNECTION_ID,
        "provider": "flowaccount",
        "company_or_merchant_id": "company-123",
        "environment": "sandbox",
        "credential_type": "refresh_token",
    }
    values.update(updates)
    return CredentialBinding(**values)


def _vault(
    *,
    active_key_version: str = ACTIVE_KEY_VERSION,
    keys: dict[str, bytes] | None = None,
):
    from mercury_tools.credentials.vault import CredentialVault

    return CredentialVault(
        active_key_version=active_key_version,
        keys=keys
        or {
            PREVIOUS_KEY_VERSION: PREVIOUS_KEY,
            ACTIVE_KEY_VERSION: ACTIVE_KEY,
        },
        clock=lambda: NOW,
    )


def _settings(**updates: object):
    from mercury_tools.config import Settings

    values: dict[str, object] = {
        "supabase_url": "",
        "supabase_service_role_key": "",
        "openai_api_key": "",
        "vault_active_key": base64.b64encode(ACTIVE_KEY).decode("ascii"),
        "vault_active_key_version": ACTIVE_KEY_VERSION,
        "vault_previous_key": base64.b64encode(PREVIOUS_KEY).decode("ascii"),
        "vault_previous_key_version": PREVIOUS_KEY_VERSION,
    }
    values.update(updates)
    return Settings(**values)


def test_aes_256_gcm_round_trip_returns_request_scoped_mutable_plaintext() -> None:
    vault = _vault()
    binding = _binding()

    envelope = vault.seal(binding, PLAINTEXT)
    opened = vault.open(binding, envelope)

    assert envelope.key_version == ACTIVE_KEY_VERSION
    assert len(envelope.nonce) == 12
    assert len(envelope.ciphertext) == len(PLAINTEXT) + 16
    assert envelope.ciphertext != PLAINTEXT
    assert isinstance(opened, bytearray)
    assert bytes(opened) == PLAINTEXT

    opened[:] = b"\x00" * len(opened)
    assert bytes(opened) == b"\x00" * len(PLAINTEXT)


def test_random_nonce_changes_ciphertext_for_identical_plaintext() -> None:
    vault = _vault()
    binding = _binding()

    first = vault.seal(binding, PLAINTEXT)
    second = vault.seal(binding, PLAINTEXT)

    assert first.id != second.id
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert first.aad_hash == second.aad_hash


@pytest.mark.parametrize(
    ("field_name", "other_value"),
    [
        ("tenant_id", OTHER_TENANT_ID),
        ("auth_user_id", OTHER_AUTH_USER_ID),
        ("workspace_id", OTHER_WORKSPACE_ID),
        ("provider", "peak"),
        ("company_or_merchant_id", "merchant-456"),
        ("environment", "production"),
        ("credential_type", "access_token"),
    ],
)
def test_aad_hash_binds_every_provider_identity_dimension(
    field_name: str,
    other_value: object,
) -> None:
    from mercury_tools.credentials.models import credential_aad_hash

    binding = _binding()
    changed = _binding(**{field_name: other_value})

    assert credential_aad_hash(
        binding,
        key_version=ACTIVE_KEY_VERSION,
    ) != credential_aad_hash(
        changed,
        key_version=ACTIVE_KEY_VERSION,
    )


def test_aad_hash_binds_key_version() -> None:
    from mercury_tools.credentials.models import credential_aad_hash

    binding = _binding()

    assert credential_aad_hash(
        binding,
        key_version=PREVIOUS_KEY_VERSION,
    ) != credential_aad_hash(
        binding,
        key_version=ACTIVE_KEY_VERSION,
    )


@pytest.mark.parametrize(
    "changed_binding",
    [
        {"tenant_id": OTHER_TENANT_ID},
        {"provider": "peak"},
        {"environment": "production"},
    ],
)
def test_cross_tenant_provider_or_environment_open_fails_closed(
    changed_binding: dict[str, object],
) -> None:
    from mercury_tools.credentials.vault import CredentialVaultError

    vault = _vault()
    envelope = vault.seal(_binding(), PLAINTEXT)

    with pytest.raises(CredentialVaultError) as exc_info:
        vault.open(_binding(**changed_binding), envelope)

    assert exc_info.value.code in {
        "credential_binding_mismatch",
        "credential_decryption_failed",
    }
    assert PLAINTEXT.decode() not in str(exc_info.value)
    assert PLAINTEXT.decode() not in repr(exc_info.value)


def test_tampered_company_binding_fails_authenticated_decryption() -> None:
    from mercury_tools.credentials.vault import CredentialVaultError

    vault = _vault()
    envelope = vault.seal(_binding(), PLAINTEXT)

    with pytest.raises(CredentialVaultError, match="^credential_decryption_failed$"):
        vault.open(
            _binding(company_or_merchant_id="merchant-456"),
            envelope,
        )


def test_active_and_previous_keys_open_and_rotate_to_active_version() -> None:
    old_vault = _vault(active_key_version=PREVIOUS_KEY_VERSION)
    binding = _binding()
    old_envelope = old_vault.seal(binding, PLAINTEXT)
    rotating_vault = _vault(active_key_version=ACTIVE_KEY_VERSION)

    assert bytes(rotating_vault.open(binding, old_envelope)) == PLAINTEXT

    rotated = rotating_vault.rotate(binding, old_envelope)

    assert rotated.id == old_envelope.id
    assert rotated.created_at == old_envelope.created_at
    assert rotated.rotated_at == NOW
    assert rotated.key_version == ACTIVE_KEY_VERSION
    assert rotated.nonce != old_envelope.nonce
    assert rotated.ciphertext != old_envelope.ciphertext
    assert bytes(rotating_vault.open(binding, rotated)) == PLAINTEXT


def test_rotate_clears_the_mutable_decrypted_copy_in_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.credentials.vault import CredentialVault

    vault = _vault()
    binding = _binding()
    envelope = vault.seal(binding, PLAINTEXT)
    decrypted_copy = bytearray(PLAINTEXT)

    monkeypatch.setattr(
        CredentialVault,
        "open",
        lambda self, supplied_binding, supplied_envelope: decrypted_copy,
    )

    vault.rotate(binding, envelope)

    assert bytes(decrypted_copy) == b"\x00" * len(PLAINTEXT)


def test_unknown_key_version_fails_closed_without_trying_another_key() -> None:
    from mercury_tools.credentials.vault import CredentialVaultError

    old_vault = _vault(active_key_version=PREVIOUS_KEY_VERSION)
    envelope = old_vault.seal(_binding(), PLAINTEXT)
    active_only = _vault(
        active_key_version=ACTIVE_KEY_VERSION,
        keys={ACTIVE_KEY_VERSION: ACTIVE_KEY},
    )

    with pytest.raises(CredentialVaultError) as exc_info:
        active_only.open(_binding(), envelope)

    assert exc_info.value.code == "credential_key_version_unknown"
    assert PLAINTEXT.decode() not in str(exc_info.value)


def test_vault_rejects_non_256_bit_keys_with_a_sanitized_error() -> None:
    from mercury_tools.credentials.vault import CredentialVault

    key_material = b"short-key-material"

    with pytest.raises(ValueError, match="^credential_vault_configuration_invalid$") as exc_info:
        CredentialVault(
            active_key_version=ACTIVE_KEY_VERSION,
            keys={ACTIVE_KEY_VERSION: key_material},
        )

    assert key_material.decode() not in str(exc_info.value)
    assert key_material.decode() not in repr(exc_info.value)


def test_repr_serialization_logs_errors_and_audit_exclude_plaintext(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mercury_tools.credentials.vault import CredentialVaultError

    caplog.set_level(logging.DEBUG)
    vault = _vault()
    binding = _binding()
    envelope = vault.seal(binding, PLAINTEXT)

    public_serialization = envelope.model_dump(mode="json")
    storage_serialization = envelope.storage_record()
    audit_record = envelope.audit_reference()

    assert {"nonce", "ciphertext", "aad_hash"}.isdisjoint(public_serialization)
    assert {"nonce", "ciphertext", "aad_hash"} <= storage_serialization.keys()
    assert PLAINTEXT.decode() not in repr(vault)
    assert PLAINTEXT.decode() not in repr(envelope)
    assert PLAINTEXT.decode() not in envelope.model_dump_json()
    assert PLAINTEXT.decode() not in str(storage_serialization)
    assert PLAINTEXT.decode() not in str(audit_record)

    with pytest.raises(CredentialVaultError) as exc_info:
        vault.open(_binding(provider="peak"), envelope)

    assert PLAINTEXT.decode() not in str(exc_info.value)
    assert PLAINTEXT.decode() not in repr(exc_info.value)
    assert PLAINTEXT.decode() not in caplog.text


def test_storage_record_matches_the_narrow_connection_save_rpc_contract() -> None:
    vault = _vault()

    record = vault.seal(_binding(), PLAINTEXT).storage_record()

    assert set(record) == {
        "id",
        "credential_type",
        "key_version",
        "nonce",
        "ciphertext",
        "aad_hash",
        "created_at",
        "rotated_at",
        "revoked_at",
    }


def test_company_binding_is_excluded_from_ordinary_model_serialization() -> None:
    binding = _binding()

    assert "company_or_merchant_id" not in binding.model_dump(mode="json")
    assert "company-123" not in binding.model_dump_json()


def test_vault_from_settings_decodes_v1_and_v0_for_rotation() -> None:
    from mercury_tools.credentials.vault import CredentialVault

    vault = CredentialVault.from_settings(_settings(), clock=lambda: NOW)
    old_vault = _vault(active_key_version=PREVIOUS_KEY_VERSION)
    old_envelope = old_vault.seal(_binding(), PLAINTEXT)

    assert bytes(vault.open(_binding(), old_envelope)) == PLAINTEXT
    assert vault.rotate(_binding(), old_envelope).key_version == ACTIVE_KEY_VERSION
    assert base64.b64encode(ACTIVE_KEY).decode("ascii") not in repr(vault)


def test_vault_from_settings_fails_closed_for_unknown_key_versions() -> None:
    from mercury_tools.credentials.vault import CredentialVault, CredentialVaultError

    old_envelope = _vault(active_key_version=PREVIOUS_KEY_VERSION).seal(
        _binding(),
        PLAINTEXT,
    )
    vault = CredentialVault.from_settings(
        _settings(vault_previous_key="", vault_previous_key_version=""),
        clock=lambda: NOW,
    )

    with pytest.raises(CredentialVaultError, match="^credential_key_version_unknown$"):
        vault.open(_binding(), old_envelope)


@pytest.mark.parametrize(
    "updates",
    (
        {"vault_active_key": "not-base64"},
        {"vault_active_key_version": "V1"},
        {"vault_previous_key": "", "vault_previous_key_version": PREVIOUS_KEY_VERSION},
        {"vault_previous_key_version": ACTIVE_KEY_VERSION},
    ),
)
def test_vault_from_settings_rejects_invalid_config_without_exposing_key_material(
    updates: dict[str, object],
) -> None:
    from mercury_tools.credentials.vault import CredentialVault

    with pytest.raises(ValueError, match="^credential_vault_configuration_invalid$") as exc_info:
        CredentialVault.from_settings(_settings(**updates))

    assert "not-base64" not in str(exc_info.value)
    assert "not-base64" not in repr(exc_info.value)


@pytest.mark.parametrize("key_version", ("", "V1", "v 1", "1", 1))
def test_aad_rejects_invalid_string_key_versions(key_version: object) -> None:
    from mercury_tools.credentials.models import credential_aad

    with pytest.raises(ValueError, match="^credential_key_version_invalid$"):
        credential_aad(_binding(), key_version=key_version)  # type: ignore[arg-type]


def test_vault_interface_requires_explicit_binding_and_envelope() -> None:
    from mercury_tools.credentials.vault import CredentialVault

    for method_name in ("seal", "open", "rotate"):
        signature = inspect.signature(getattr(CredentialVault, method_name))
        assert signature.parameters["binding"].default is inspect.Parameter.empty

    assert inspect.signature(CredentialVault.open).return_annotation in {
        bytearray,
        "bytearray",
    }
