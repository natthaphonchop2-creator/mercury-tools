from __future__ import annotations

import inspect
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONNECTION_MIGRATION = (
    ROOT / "supabase/migrations/20260726101000_mercury_v1_provider_connections.sql"
)
VAULT_MIGRATION = ROOT / "supabase/migrations/20260726102000_mercury_v1_credential_vault.sql"
OAUTH_CLEANUP_MIGRATION = (
    ROOT / "supabase/migrations/20260727100000_mercury_v1_provider_oauth_cleanup.sql"
)
OAUTH_RECONNECT_MIGRATION = (
    ROOT / "supabase/migrations/20260728100000_mercury_v1_provider_oauth_reconnect.sql"
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = UUID("22222222-2222-4222-8222-222222222222")
AUTH_USER_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_AUTH_USER_ID = UUID("44444444-4444-4444-8444-444444444444")
WORKSPACE_ID = UUID("55555555-5555-4555-8555-555555555555")
OTHER_WORKSPACE_ID = UUID("66666666-6666-4666-8666-666666666666")
CONNECTION_ID = UUID("77777777-7777-4777-8777-777777777777")
SECOND_CONNECTION_ID = UUID("88888888-8888-4888-8888-888888888888")
THIRD_CONNECTION_ID = UUID("99999999-9999-4999-8999-999999999999")
NOW = datetime(2026, 7, 26, 11, 0, tzinfo=UTC)
TOKEN_HASH = "a" * 64
OTHER_TOKEN_HASH = "b" * 64
PLAINTEXT = b"provider-store-secret-b69d"
KEY = bytes(range(32))
KEY_VERSION = "v1"


def _store(*, clock=None, vault=None):
    from mercury_tools.providers.store import ProviderConnectionStore

    return ProviderConnectionStore(
        vault=vault or _vault(),
        clock=clock or (lambda: NOW),
    )


def _vault():
    from mercury_tools.credentials.vault import CredentialVault

    return CredentialVault(
        active_key_version=KEY_VERSION,
        keys={KEY_VERSION: KEY},
        clock=lambda: NOW,
    )


def _binding(
    credential_type: str,
    **updates: object,
):
    from mercury_tools.credentials.models import CredentialBinding

    values: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "auth_user_id": AUTH_USER_ID,
        "connection_id": CONNECTION_ID,
        "provider": "flowaccount",
        "company_or_merchant_id": "company-123",
        "environment": "sandbox",
        "credential_type": credential_type,
    }
    values.update(updates)
    return CredentialBinding(**values)


def _envelopes():
    vault = _vault()
    return (
        vault.seal(_binding("access_token"), PLAINTEXT),
        vault.seal(_binding("refresh_token"), b"second-request-scoped-secret"),
    )


def _save_connection(store, *, envelopes=None, **updates: object):
    from mercury_tools.providers.models import (
        AuthorizationMethod,
        ConnectionReadiness,
        ProviderId,
    )

    values: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "auth_user_id": AUTH_USER_ID,
        "connection_id": CONNECTION_ID,
        "provider": ProviderId.FLOWACCOUNT,
        "environment": "sandbox",
        "company_or_merchant_id": "company-123",
        "account_display_name": "Mercury Test Company",
        "authorization_method": AuthorizationMethod.OAUTH2_PKCE,
        "granted_permissions": ("documents.read", "profile.read"),
        "readiness": ConnectionReadiness.READY,
        "revision": 1,
        "validated_at": NOW,
        "envelopes": envelopes or _envelopes(),
    }
    values.update(updates)
    return store.save_connection(**values)


def _normalized_sql(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_store_requires_a_credential_vault() -> None:
    from mercury_tools.providers.store import ProviderConnectionStore

    with pytest.raises(TypeError):
        ProviderConnectionStore()


def test_setup_attempt_stores_only_hash_and_consumes_once_for_exact_binding() -> None:
    store = _store()

    attempt = store.create_attempt(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        provider="peak",
        environment="production",
        token_hash=TOKEN_HASH,
        expires_at=NOW + timedelta(minutes=10),
    )

    assert attempt.consumed_at is None
    assert TOKEN_HASH not in repr(attempt)
    assert "token_hash" not in attempt.model_dump(mode="json")

    consumed = store.consume_attempt(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        provider="peak",
        environment="production",
        token_hash=TOKEN_HASH,
    )

    assert consumed.id == attempt.id
    assert consumed.consumed_at == NOW

    from mercury_tools.providers.store import ProviderStoreError

    with pytest.raises(ProviderStoreError, match="^provider_setup_attempt_invalid$"):
        store.consume_attempt(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=AUTH_USER_ID,
            provider="peak",
            environment="production",
            token_hash=TOKEN_HASH,
        )


def test_setup_attempt_rejects_cross_tenant_user_workspace_and_expiry() -> None:
    from mercury_tools.providers.store import ProviderStoreError

    now = [NOW]
    store = _store(clock=lambda: now[0])
    store.create_attempt(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        provider="peak",
        environment="production",
        token_hash=TOKEN_HASH,
        expires_at=NOW + timedelta(minutes=10),
    )

    for overrides in (
        {"tenant_id": OTHER_TENANT_ID},
        {"workspace_id": OTHER_WORKSPACE_ID},
        {"auth_user_id": OTHER_AUTH_USER_ID},
        {"provider": "flowaccount"},
        {"environment": "sandbox"},
    ):
        values = {
            "tenant_id": TENANT_ID,
            "workspace_id": WORKSPACE_ID,
            "auth_user_id": AUTH_USER_ID,
            "provider": "peak",
            "environment": "production",
            "token_hash": TOKEN_HASH,
        }
        values.update(overrides)
        with pytest.raises(ProviderStoreError, match="^provider_setup_attempt_invalid$"):
            store.consume_attempt(**values)

    now[0] = NOW + timedelta(minutes=11)
    with pytest.raises(ProviderStoreError, match="^provider_setup_attempt_invalid$"):
        store.consume_attempt(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=AUTH_USER_ID,
            provider="peak",
            environment="production",
            token_hash=TOKEN_HASH,
        )


@pytest.mark.parametrize(
    "invalid_hash",
    [
        "",
        "a" * 63,
        "A" * 64,
        "g" * 64,
        "raw-setup-token-that-must-not-be-stored",
    ],
)
def test_setup_attempt_accepts_only_sha256_hex_hashes(invalid_hash: str) -> None:
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()

    with pytest.raises(ProviderStoreError, match="^provider_setup_attempt_invalid$") as exc_info:
        store.create_attempt(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=AUTH_USER_ID,
            provider="peak",
            environment="production",
            token_hash=invalid_hash,
            expires_at=NOW + timedelta(minutes=10),
        )

    if invalid_hash:
        assert invalid_hash not in str(exc_info.value)
        assert invalid_hash not in repr(exc_info.value)


def test_setup_attempt_expiry_is_positive_and_at_most_ten_minutes() -> None:
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()

    for expires_at in (NOW, NOW + timedelta(minutes=10, microseconds=1)):
        with pytest.raises(ProviderStoreError, match="^provider_setup_attempt_invalid$"):
            store.create_attempt(
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=AUTH_USER_ID,
                provider="peak",
                environment="production",
                token_hash=OTHER_TOKEN_HASH,
                expires_at=expires_at,
            )


def test_provider_models_reject_nil_tenant_user_workspace_bindings() -> None:
    from pydantic import ValidationError

    store = _store()
    attempt = store.create_attempt(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        provider="peak",
        environment="production",
        token_hash=TOKEN_HASH,
        expires_at=NOW + timedelta(minutes=10),
    )
    connection = _save_connection(store)

    with pytest.raises(ValidationError, match="provider_setup_attempt_invalid"):
        type(attempt).model_validate(attempt.model_copy(update={"tenant_id": UUID(int=0)}))
    with pytest.raises(ValidationError, match="provider_connection_invalid"):
        type(connection).model_validate(connection.model_copy(update={"workspace_id": UUID(int=0)}))


def test_save_and_list_connection_returns_only_tenant_bound_summaries() -> None:
    store = _store()
    envelopes = _envelopes()
    connection = _save_connection(store, envelopes=envelopes)

    own = store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
    )
    cross_tenant = store.list_for_workspace(
        tenant_id=OTHER_TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
    )
    cross_workspace = store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=OTHER_WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
    )
    cross_user = store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=OTHER_AUTH_USER_ID,
    )

    assert connection.id == CONNECTION_ID
    assert connection.credential_envelope_ids == tuple(envelope.id for envelope in envelopes)
    assert len(own) == 1
    assert own[0].connection_id == CONNECTION_ID
    assert own[0].account_display_name == "Mercury Test Company"
    assert own[0].granted_permissions == ("documents.read", "profile.read")
    assert cross_tenant == ()
    assert cross_workspace == ()
    assert cross_user == ()
    assert "credential_envelope_ids" not in own[0].model_dump(mode="json")
    assert "provider_account_id" not in connection.model_dump(mode="json")
    assert "company-123" not in connection.model_dump_json()
    assert PLAINTEXT.decode() not in repr(connection)
    assert PLAINTEXT.decode() not in own[0].model_dump_json()


def test_save_rejects_envelope_bound_to_another_company_or_provider() -> None:
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()
    vault = _vault()
    wrong_company = vault.seal(
        _binding("access_token", company_or_merchant_id="company-999"),
        PLAINTEXT,
    )

    with pytest.raises(ProviderStoreError, match="^provider_credential_binding_invalid$"):
        _save_connection(store, envelopes=(wrong_company,))

    wrong_provider = vault.seal(
        _binding("access_token", provider="peak"),
        PLAINTEXT,
    )
    with pytest.raises(ProviderStoreError, match="^provider_credential_binding_invalid$"):
        _save_connection(store, envelopes=(wrong_provider,))


def test_save_rejects_duplicate_credential_types() -> None:
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()
    vault = _vault()
    first = vault.seal(_binding("access_token"), PLAINTEXT)
    second = vault.seal(_binding("access_token"), b"another-secret")

    with pytest.raises(ProviderStoreError, match="^provider_credential_binding_invalid$"):
        _save_connection(store, envelopes=(first, second))


@pytest.mark.parametrize(
    "permissions",
    [
        ("documents.read", None),
        ("documents.read", "documents.read"),
        ("documents.read", 7),
    ],
)
def test_save_rejects_null_duplicate_or_non_string_permissions(
    permissions: tuple[object, ...],
) -> None:
    from mercury_tools.providers.store import ProviderStoreError

    with pytest.raises(ProviderStoreError, match="^provider_connection_invalid$"):
        _save_connection(_store(), granted_permissions=permissions)


def test_save_authenticates_each_envelope_before_connection_becomes_ready() -> None:
    from mercury_tools.credentials.vault import CredentialVault
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()
    unknown_key_vault = CredentialVault(
        active_key_version="v2",
        keys={"v2": bytes(reversed(range(32)))},
        clock=lambda: NOW,
    )
    unknown_key = unknown_key_vault.seal(_binding("access_token"), PLAINTEXT)

    with pytest.raises(
        ProviderStoreError,
        match="^provider_credential_binding_invalid$",
    ):
        _save_connection(store, envelopes=(unknown_key,))

    valid = _vault().seal(_binding("access_token"), PLAINTEXT)
    forged = valid.model_copy(
        update={"ciphertext": bytes([valid.ciphertext[0] ^ 1]) + valid.ciphertext[1:]}
    )

    with pytest.raises(
        ProviderStoreError,
        match="^provider_credential_binding_invalid$",
    ):
        _save_connection(store, envelopes=(forged,))

    assert (
        store.list_for_workspace(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=AUTH_USER_ID,
        )
        == ()
    )


def test_save_clears_each_request_scoped_opened_plaintext_copy() -> None:
    from mercury_tools.credentials.vault import CredentialVault

    class TrackingVault(CredentialVault):
        opened: list[bytearray]

        def __init__(self) -> None:
            super().__init__(
                active_key_version=KEY_VERSION,
                keys={KEY_VERSION: KEY},
                clock=lambda: NOW,
            )
            self.opened = []

        def open(self, binding, envelope):
            plaintext = super().open(binding, envelope)
            self.opened.append(plaintext)
            return plaintext

    vault = TrackingVault()
    envelopes = (
        vault.seal(_binding("access_token"), PLAINTEXT),
        vault.seal(_binding("refresh_token"), b"second-request-scoped-secret"),
    )

    _save_connection(_store(vault=vault), envelopes=envelopes)

    assert len(vault.opened) == 2
    assert all(bytes(value) == b"\x00" * len(value) for value in vault.opened)


def test_save_rejects_an_envelope_id_owned_by_another_connection() -> None:
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()
    first = _envelopes()[0]
    _save_connection(store, envelopes=(first,))

    vault = _vault()
    second = vault.seal(
        _binding(
            "access_token",
            connection_id=SECOND_CONNECTION_ID,
            company_or_merchant_id="company-456",
        ),
        b"second-connection-secret",
    ).model_copy(update={"id": first.id})

    with pytest.raises(ProviderStoreError, match="^provider_credential_binding_invalid$"):
        _save_connection(
            store,
            connection_id=SECOND_CONNECTION_ID,
            company_or_merchant_id="company-456",
            envelopes=(second,),
        )


def test_disconnect_deletes_usable_envelope_material_idempotently() -> None:
    from mercury_tools.providers.models import ConnectionReadiness

    store = _store()
    _save_connection(store)

    first = store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
        provider_revocation_required=True,
    )
    second = store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
        provider_revocation_required=True,
    )
    summaries = store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
    )

    assert first.deleted_envelope_count == 2
    assert first.already_disconnected is False
    assert first.provider_revocation_required is True
    assert second.deleted_envelope_count == 0
    assert second.already_disconnected is True
    assert second.provider_revocation_required is True
    assert summaries[0].readiness is ConnectionReadiness.DISCONNECTED
    assert summaries[0].revision == 2


def test_revocation_marker_clears_only_after_atomic_disconnection() -> None:
    from mercury_tools.providers.models import ConnectionReadiness
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()
    _save_connection(store)

    with pytest.raises(ProviderStoreError, match="^provider_connection_invalid$"):
        store.complete_revocation(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=AUTH_USER_ID,
            connection_id=CONNECTION_ID,
        )

    store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
        provider_revocation_required=True,
    )
    completed = store.complete_revocation(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
    )
    summary = store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
    )[0]

    assert completed.provider_revocation_required is False
    assert completed.deleted_envelope_count == 0
    assert summary.readiness is ConnectionReadiness.DISCONNECTED
    assert summary.provider_revocation_required is False
    assert store._envelopes == {}


def test_reconnect_reactivates_only_the_same_disconnected_connection_id() -> None:
    from mercury_tools.providers.models import ConnectionReadiness
    from mercury_tools.providers.store import ProviderStoreError

    vault = _vault()
    store = _store(vault=vault)
    original = _save_connection(store)
    store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
        provider_revocation_required=True,
    )
    replacements = (
        vault.seal(_binding("access_token"), b"replacement-access-token"),
        vault.seal(_binding("refresh_token"), b"replacement-refresh-token"),
    )
    other_id_envelope = vault.seal(
        _binding("access_token", connection_id=SECOND_CONNECTION_ID),
        b"other-id-access-token",
    )

    with pytest.raises(ProviderStoreError, match="^provider_connection_conflict$"):
        _save_connection(
            store,
            connection_id=SECOND_CONNECTION_ID,
            envelopes=(other_id_envelope,),
        )

    reconnected = _save_connection(
        store,
        revision=3,
        envelopes=replacements,
    )

    assert reconnected.id == original.id
    assert reconnected.created_at == original.created_at
    assert reconnected.revision == 3
    assert reconnected.readiness is ConnectionReadiness.READY
    assert reconnected.provider_revocation_required is False
    assert reconnected.disconnected_at is None
    assert reconnected.credential_envelope_ids == tuple(envelope.id for envelope in replacements)


def test_atomic_finalization_reuses_disconnected_company_and_clears_staged_obligation() -> None:
    from mercury_tools.providers.models import (
        AuthorizationMethod,
        ConnectionReadiness,
        ProviderId,
    )

    vault = _vault()
    store = _store(vault=vault)
    original = _save_connection(store)
    disconnected = store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
    )
    staged_account_id = "oauth-pending-attempt"
    staged_envelopes = (
        vault.seal(
            _binding(
                "access_token",
                connection_id=SECOND_CONNECTION_ID,
                company_or_merchant_id=staged_account_id,
            ),
            b"staged-access-token",
        ),
    )
    staged = store.stage_connection(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=SECOND_CONNECTION_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        company_or_merchant_id=staged_account_id,
        account_display_name="FlowAccount",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read", "profile.read"),
        readiness=ConnectionReadiness.REQUIRES_VALIDATION,
        revision=1,
        validated_at=None,
        envelopes=staged_envelopes,
    )
    target = store.resolve_connection_target(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        company_or_merchant_id="company-123",
        proposed_connection_id=THIRD_CONNECTION_ID,
    )
    exact_envelopes = (
        vault.seal(
            _binding(
                "access_token",
                connection_id=target.connection_id,
            ),
            b"replacement-access-token",
        ),
    )
    finalized = store.finalize_connection(
        staged_connection_id=staged.id,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=target.connection_id,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        company_or_merchant_id="company-123",
        account_display_name="FlowAccount Test Company",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read", "profile.read"),
        readiness=ConnectionReadiness.READY,
        revision=target.revision,
        validated_at=NOW,
        envelopes=exact_envelopes,
    )
    summaries = store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
    )

    assert target.connection_id == original.id
    assert target.revision == disconnected.revision + 1
    assert finalized.id == original.id
    assert finalized.revision == target.revision
    assert finalized.provider_revocation_required is False
    staged_summary = next(item for item in summaries if item.connection_id == staged.id)
    assert staged_summary.readiness is ConnectionReadiness.DISCONNECTED
    assert staged_summary.provider_revocation_required is False
    assert not any(envelope.connection_id == staged.id for envelope in store._envelopes.values())


@pytest.mark.parametrize(
    ("revision", "environment", "company_or_merchant_id"),
    [
        (2, "sandbox", "company-123"),
        (3, "production", "company-123"),
        (3, "sandbox", "company-456"),
    ],
)
def test_reconnect_rejects_stale_revision_or_changed_account_binding(
    revision: int,
    environment: str,
    company_or_merchant_id: str,
) -> None:
    from mercury_tools.providers.store import ProviderStoreError

    vault = _vault()
    store = _store(vault=vault)
    _save_connection(store)
    store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
    )
    envelope = vault.seal(
        _binding(
            "access_token",
            environment=environment,
            company_or_merchant_id=company_or_merchant_id,
        ),
        b"replacement-access-token",
    )

    with pytest.raises(ProviderStoreError, match="^provider_connection_conflict$"):
        _save_connection(
            store,
            revision=revision,
            environment=environment,
            company_or_merchant_id=company_or_merchant_id,
            envelopes=(envelope,),
        )


def test_cross_tenant_disconnect_fails_without_deleting_owner_envelopes() -> None:
    from mercury_tools.providers.store import ProviderStoreError

    store = _store()
    _save_connection(store)

    with pytest.raises(ProviderStoreError, match="^provider_connection_not_found$"):
        store.disconnect(
            tenant_id=OTHER_TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=AUTH_USER_ID,
            connection_id=CONNECTION_ID,
        )

    owner_result = store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        connection_id=CONNECTION_ID,
    )
    assert owner_result.deleted_envelope_count == 2


def test_store_repr_serialization_logs_errors_and_audit_exclude_plaintext(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mercury_tools.providers.store import ProviderStoreError

    caplog.set_level(logging.DEBUG)
    store = _store()
    connection = _save_connection(store)
    audit = connection.audit_reference()

    with pytest.raises(ProviderStoreError) as exc_info:
        store.disconnect(
            tenant_id=OTHER_TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=AUTH_USER_ID,
            connection_id=CONNECTION_ID,
        )

    representations = (
        repr(store),
        repr(connection),
        connection.model_dump_json(),
        str(audit),
        str(exc_info.value),
        repr(exc_info.value),
        caplog.text,
    )
    assert all(PLAINTEXT.decode() not in value for value in representations)
    assert "ciphertext" not in connection.model_dump_json()
    assert "nonce" not in connection.model_dump_json()


def test_store_interface_requires_explicit_uuid_tenant_user_workspace_binding() -> None:
    from mercury_tools.providers.store import ProviderConnectionStore

    for method_name in (
        "create_attempt",
        "consume_attempt",
        "save_connection",
        "list_for_workspace",
        "disconnect",
    ):
        signature = inspect.signature(getattr(ProviderConnectionStore, method_name))
        for field_name in ("tenant_id", "workspace_id", "auth_user_id"):
            parameter = signature.parameters[field_name]
            assert parameter.default is inspect.Parameter.empty
            assert parameter.annotation in {UUID, "UUID"}

    signature = inspect.signature(ProviderConnectionStore.list_for_workspace)
    assert "active_workspace_id" not in signature.parameters


def test_provider_connection_migration_is_expand_first_and_secretless() -> None:
    sql = _normalized_sql(CONNECTION_MIGRATION)

    for table_name in (
        "mercury_provider_connections",
        "mercury_provider_setup_attempts",
        "mercury_provider_oauth_states",
    ):
        assert f"create table if not exists public.{table_name}" in sql
        assert f"alter table public.{table_name} enable row level security" in sql
        assert f"revoke all on table public.{table_name} from public, anon, authenticated" in sql

    for field_name in (
        "tenant_id",
        "workspace_id",
        "auth_user_id",
        "provider",
        "environment",
        "provider_account_id",
        "account_display_name",
        "authorization_method",
        "granted_permissions",
        "readiness",
        "revision",
        "last_validated_at",
    ):
        assert re.search(rf"\b{field_name}\b", sql)

    assert "token_hash pg_catalog.text not null" in sql
    assert "state_hash pg_catalog.text not null" in sql
    assert "pkce_verifier_ciphertext pg_catalog.bytea" in sql
    assert "callback_state pg_catalog.jsonb" in sql
    assert "access_token" not in sql
    assert "refresh_token" not in sql
    assert "user_token" not in sql
    assert "connect_key" not in sql
    assert "connect_id" not in sql
    assert "credential_metadata" not in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    assert "mercury_client_tokens" not in sql


def test_credential_vault_migration_has_exact_envelope_boundary_and_narrow_rpcs() -> None:
    sql = _normalized_sql(VAULT_MIGRATION)

    assert "create table if not exists public.mercury_provider_credential_envelopes" in sql
    for field_name in (
        "id",
        "tenant_id",
        "workspace_id",
        "auth_user_id",
        "connection_id",
        "provider",
        "environment",
        "credential_type",
        "key_version",
        "nonce",
        "ciphertext",
        "aad_hash",
        "created_at",
        "rotated_at",
        "revoked_at",
    ):
        assert re.search(rf"\b{field_name}\b", sql)

    assert (
        "alter table public.mercury_provider_credential_envelopes enable row level security"
    ) in sql
    assert (
        "revoke all on table public.mercury_provider_credential_envelopes "
        "from public, anon, authenticated"
    ) in sql
    assert "plaintext" not in sql
    assert "credential_json" not in sql
    assert "secret_value" not in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    assert "key_version pg_catalog.text not null" in sql

    for function_name in (
        "save_mercury_provider_connection",
        "list_mercury_provider_connections",
        "load_mercury_provider_credential_envelopes",
        "disconnect_mercury_provider_connection",
    ):
        assert f"create or replace function public.{function_name}(" in sql

    assert sql.count("security definer") >= 4
    assert sql.count("set search_path = ''") >= 4
    assert "delete from public.mercury_provider_credential_envelopes" in sql
    assert (
        "create or replace function public.mercury_assert_provider_backend_workspace_access("
    ) in sql
    for function_name in (
        "save_mercury_provider_connection",
        "load_mercury_provider_credential_envelopes",
        "disconnect_mercury_provider_connection",
    ):
        assert re.search(
            rf"grant execute on function public\.{function_name}\([^;]*\) "
            r"to service_role;",
            sql,
        )
        assert not re.search(
            rf"grant execute on function public\.{function_name}\([^;]*\) "
            r"to authenticated;",
            sql,
        )
    assert re.search(
        r"grant execute on function public\.list_mercury_provider_connections"
        r"\([^;]*\) to authenticated;",
        sql,
    )
    assert "vault_active_key" not in sql
    assert "master_key" not in sql


def test_migrations_expose_no_public_mcp_tool_or_ambient_workspace_contract() -> None:
    connection_sql = _normalized_sql(CONNECTION_MIGRATION)
    vault_sql = _normalized_sql(VAULT_MIGRATION)
    combined = f"{connection_sql} {vault_sql}"

    assert "mcp tool" not in combined
    assert "active_workspace_id" not in combined
    assert "auth.uid()" in combined
    assert "workspace_access_denied" in combined
    assert "p_envelopes is null" in vault_sql


def test_migrations_use_valid_pg_catalog_type_names_for_postgres_17() -> None:
    combined = " ".join(
        _normalized_sql(path)
        for path in (
            CONNECTION_MIGRATION,
            VAULT_MIGRATION,
            OAUTH_CLEANUP_MIGRATION,
            OAUTH_RECONNECT_MIGRATION,
        )
    )

    assert not re.search(r"pg_catalog\.(boolean|integer|bigint)\b", combined)
    for type_name in ("bool", "int4", "int8"):
        assert f"pg_catalog.{type_name}" in combined
    assert "pkce_key_version pg_catalog.text" in combined


def test_oauth_cleanup_migration_has_atomic_cancel_cleanup_and_revocation_rpcs() -> None:
    sql = _normalized_sql(OAUTH_CLEANUP_MIGRATION)

    for function_name in (
        "cancel_mercury_provider_oauth_state",
        "cleanup_expired_mercury_provider_oauth_states",
        "complete_mercury_provider_revocation",
    ):
        assert f"create or replace function public.{function_name}(" in sql
        assert re.search(
            rf"grant execute on function public\.{function_name}\([^;]*\) "
            rf"to {'authenticated' if function_name.startswith('cancel_') else 'service_role'};",
            sql,
        )

    assert sql.count("for update") >= 2
    assert "for update skip locked" in sql
    for column in (
        "pkce_verifier_ciphertext",
        "pkce_key_version",
        "pkce_nonce",
        "pkce_aad_hash",
    ):
        assert f"{column} = null" in sql
    assert "readiness <> 'disconnected'" in sql
    assert "credential_envelope_ids <> '{}'::pg_catalog.uuid[]" in sql
    assert "provider_revocation_required = false" in sql
    assert "drop table" not in sql
    assert "truncate" not in sql


def test_oauth_reconnect_migration_has_atomic_stage_target_finalize_and_obligation_rpcs() -> None:
    sql = _normalized_sql(OAUTH_RECONNECT_MIGRATION)

    for function_name in (
        "stage_mercury_provider_connection",
        "resolve_mercury_provider_connection_target",
        "finalize_mercury_provider_connection",
        "record_mercury_provider_revocation_obligation",
    ):
        assert f"create or replace function public.{function_name}(" in sql
        assert re.search(
            rf"grant execute on function public\.{function_name}\([^;]*\) "
            r"to service_role;",
            sql,
        )

    assert "provider_revocation_required = true" in sql
    assert "readiness <> 'disconnected'" in sql
    assert "for update" in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
