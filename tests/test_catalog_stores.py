import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mercury_tools.catalog import local_store
from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.catalog.identity import build_action_id, build_version_id
from mercury_tools.catalog.local_store import LocalCatalogStore, merge_actions
from mercury_tools.catalog.models import HttpMethod


def test_local_action_wins_for_same_action_identity(action_factory) -> None:
    global_action = action_factory(source_uri="global://flow", description="global")
    local_action = action_factory(source_uri="local://flow", description="local")

    merged = merge_actions([global_action], [local_action])

    assert len(merged) == 1
    assert merged[0].description == "local"


def test_merge_sorts_actions_deterministically(action_factory) -> None:
    actions = [
        action_factory(
            connector_id="peak",
            capability="contacts.create",
            path_template="/contacts",
        ),
        action_factory(
            connector_id="flowaccount",
            capability="company.info.read",
            path_template="/company",
        ),
    ]

    merged = merge_actions(reversed(actions), ())

    assert [item.connector_id for item in merged] == ["flowaccount", "peak"]


@pytest.mark.parametrize("side", ["global", "local"])
def test_merge_rejects_duplicate_ids_within_each_input(action_factory, side: str) -> None:
    action = action_factory()
    global_actions = [action, action] if side == "global" else []
    local_actions = [action, action] if side == "local" else []

    with pytest.raises(ValueError, match="catalog_action_duplicate"):
        merge_actions(global_actions, local_actions)


def test_local_store_round_trips_canonical_actions(
    repository_context,
    catalog_source,
    catalog_action,
) -> None:
    store = LocalCatalogStore(repository_context)

    store.write_import(catalog_source, [catalog_action])

    assert store.list_actions() == [catalog_action]
    source_path = repository_context.catalog_dir / "sources" / f"{catalog_source.source_id}.json"
    action_path = repository_context.catalog_dir / "actions" / f"{catalog_action.action_id}.json"
    assert source_path.is_file()
    assert action_path.is_file()


def _catalog_bytes(repository_context, source_id: str, action_id: str) -> dict[Path, bytes]:
    paths = (
        repository_context.catalog_dir / "sources" / f"{source_id}.json",
        repository_context.catalog_dir / "actions" / f"{action_id}.json",
    )
    return {path: path.read_bytes() for path in paths}


def _changed_import(catalog_source, action_factory) -> tuple[Any, Any]:
    source = catalog_source.model_copy(
        update={"imported_at": catalog_source.imported_at + timedelta(seconds=1)}
    )
    action = action_factory(description="replacement")
    return source, action


def test_local_store_prevalidates_entire_import_before_changing_destinations(
    repository_context,
    catalog_source,
    catalog_action,
    action_factory,
) -> None:
    store = LocalCatalogStore(repository_context)
    store.write_import(catalog_source, [catalog_action])
    before = _catalog_bytes(repository_context, catalog_source.source_id, catalog_action.action_id)
    replacement_source, replacement_action = _changed_import(catalog_source, action_factory)

    with pytest.raises(ValueError, match="catalog_action_duplicate"):
        store.write_import(replacement_source, [replacement_action, replacement_action])

    assert _catalog_bytes(
        repository_context, catalog_source.source_id, catalog_action.action_id
    ) == before


def test_local_store_staging_failure_leaves_destinations_unchanged(
    repository_context,
    catalog_source,
    catalog_action,
    action_factory,
    monkeypatch,
) -> None:
    store = LocalCatalogStore(repository_context)
    store.write_import(catalog_source, [catalog_action])
    before = _catalog_bytes(repository_context, catalog_source.source_id, catalog_action.action_id)
    replacement_source, replacement_action = _changed_import(catalog_source, action_factory)
    original_mkstemp = local_store.tempfile.mkstemp
    calls = 0

    def fail_second_stage(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic staging failure")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(local_store.tempfile, "mkstemp", fail_second_stage)

    with pytest.raises(OSError, match="synthetic staging failure"):
        store.write_import(replacement_source, [replacement_action])

    assert _catalog_bytes(
        repository_context, catalog_source.source_id, catalog_action.action_id
    ) == before


def test_local_store_replace_failure_rolls_back_all_destinations(
    repository_context,
    catalog_source,
    catalog_action,
    action_factory,
    monkeypatch,
) -> None:
    store = LocalCatalogStore(repository_context)
    store.write_import(catalog_source, [catalog_action])
    before = _catalog_bytes(repository_context, catalog_source.source_id, catalog_action.action_id)
    replacement_source, replacement_action = _changed_import(catalog_source, action_factory)
    original_replace = local_store.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(local_store.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        store.write_import(replacement_source, [replacement_action])

    assert _catalog_bytes(
        repository_context, catalog_source.source_id, catalog_action.action_id
    ) == before


def test_local_store_fsync_failure_rolls_back_all_destinations(
    repository_context,
    catalog_source,
    catalog_action,
    action_factory,
    monkeypatch,
) -> None:
    store = LocalCatalogStore(repository_context)
    store.write_import(catalog_source, [catalog_action])
    before = _catalog_bytes(repository_context, catalog_source.source_id, catalog_action.action_id)
    replacement_source, replacement_action = _changed_import(catalog_source, action_factory)
    calls = 0

    def fail_first_directory_fsync(directory: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic fsync failure")

    monkeypatch.setattr(
        local_store,
        "_fsync_directory",
        fail_first_directory_fsync,
        raising=False,
    )

    with pytest.raises(OSError, match="synthetic fsync failure"):
        store.write_import(replacement_source, [replacement_action])

    assert _catalog_bytes(
        repository_context, catalog_source.source_id, catalog_action.action_id
    ) == before


def test_local_store_fails_closed_for_tampered_action_content(
    repository_context,
    catalog_source,
    catalog_action,
) -> None:
    store = LocalCatalogStore(repository_context)
    store.write_import(catalog_source, [catalog_action])
    action_path = repository_context.catalog_dir / "actions" / f"{catalog_action.action_id}.json"
    data = json.loads(action_path.read_text())
    data["description"] = "tampered"
    action_path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="catalog_action_version_invalid"):
        store.list_actions()


def test_local_store_rejects_symlinked_action_file(
    repository_context,
    catalog_source,
    catalog_action,
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("symlink protection requires POSIX")
    store = LocalCatalogStore(repository_context)
    store.write_import(catalog_source, [catalog_action])
    action_path = repository_context.catalog_dir / "actions" / f"{catalog_action.action_id}.json"
    action_path.unlink()
    action_path.symlink_to(tmp_path / "outside.json")

    with pytest.raises(ValueError, match="catalog_symlink"):
        store.list_actions()


def test_local_store_rejects_symlinked_write_destination(
    repository_context,
    catalog_source,
    catalog_action,
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("symlink protection requires POSIX")
    action_path = repository_context.catalog_dir / "actions" / f"{catalog_action.action_id}.json"
    action_path.symlink_to(tmp_path / "outside.json")

    with pytest.raises(ValueError, match="catalog_symlink"):
        LocalCatalogStore(repository_context).write_import(catalog_source, [catalog_action])


def test_local_store_rejects_credential_unsafe_tampered_models_before_writing(
    repository_context,
    catalog_source,
    catalog_action,
) -> None:
    store = LocalCatalogStore(repository_context)
    rejected_value = "raw-value"
    tampered_source = catalog_source.model_copy(
        update={"driver_suggestion": {"client_secret": rejected_value}}
    )
    tampered_action = catalog_action.model_copy(
        update={"examples": ({"key": "X-API-Key", "value": rejected_value},)}
    )
    tampered_action = tampered_action.model_copy(
        update={"version_id": build_version_id(tampered_action)}
    )

    with pytest.raises(ValueError, match="catalog_source_invalid") as source_error:
        store.write_import(tampered_source, [catalog_action])
    with pytest.raises(ValueError, match="catalog_action_invalid") as action_error:
        store.write_import(catalog_source, [tampered_action])

    assert rejected_value not in str(source_error.value)
    assert rejected_value not in str(action_error.value)
    assert list((repository_context.catalog_dir / "sources").iterdir()) == []
    assert list((repository_context.catalog_dir / "actions").iterdir()) == []


def test_cache_replaces_snapshot_and_clears_etag(repository_context, action_factory) -> None:
    cache = CatalogCache(repository_context)
    first = action_factory(description="first")
    second = action_factory(path_template="/expenses", operation_id="createExpense")

    cache.replace_global([first], etag='"first"')
    cache.replace_global([second], etag=None)

    assert cache.list_global() == [second]
    assert cache.conditional_headers() == {}


def test_cache_returns_conditional_etag_header(repository_context, catalog_action) -> None:
    cache = CatalogCache(repository_context)
    cache.replace_global([catalog_action], etag='W/"catalog-v1"')

    assert cache.conditional_headers() == {"If-None-Match": 'W/"catalog-v1"'}


def test_cache_fails_closed_for_tampered_row(repository_context, catalog_action) -> None:
    cache = CatalogCache(repository_context)
    cache.replace_global([catalog_action], etag=None)
    with cache._connect() as connection:
        connection.execute(
            "UPDATE catalog_actions SET payload = ? WHERE action_id = ?",
            ("{}", catalog_action.action_id),
        )

    with pytest.raises(ValueError, match="catalog_cache_row_invalid"):
        cache.list_global()


def test_cache_rejects_duplicate_ids_before_replacing_snapshot(
    repository_context,
    catalog_action,
    action_factory,
) -> None:
    cache = CatalogCache(repository_context)
    cache.replace_global([catalog_action], etag='"original"')

    with pytest.raises(ValueError, match="catalog_cache_duplicate"):
        cache.replace_global([action_factory(), action_factory()], etag='"replacement"')

    assert cache.list_global() == [catalog_action]
    assert cache.conditional_headers() == {"If-None-Match": '"original"'}


def test_cache_rejects_credential_unsafe_tampered_model_before_transaction(
    repository_context,
    catalog_action,
) -> None:
    cache = CatalogCache(repository_context)
    cache.replace_global([catalog_action], etag='"original"')
    rejected_value = "raw-value"
    tampered = catalog_action.model_copy(
        update={"examples": ({"key": "X-API-Key", "value": rejected_value},)}
    )
    tampered = tampered.model_copy(update={"version_id": build_version_id(tampered)})

    with pytest.raises(ValueError, match="catalog_cache_action_invalid") as raised:
        cache.replace_global([tampered], etag='"replacement"')

    assert rejected_value not in str(raised.value)
    assert cache.list_global() == [catalog_action]
    assert cache.conditional_headers() == {"If-None-Match": '"original"'}


def test_cache_rejects_method_risk_tampering_before_transaction(
    repository_context,
    catalog_action,
) -> None:
    cache = CatalogCache(repository_context)
    cache.replace_global([catalog_action], etag='"original"')
    tampered = catalog_action.model_copy(update={"method": HttpMethod.GET})
    tampered = tampered.model_copy(update={"action_id": build_action_id(tampered)})
    tampered = tampered.model_copy(update={"version_id": build_version_id(tampered)})

    with pytest.raises(ValueError, match="catalog_cache_action_invalid"):
        cache.replace_global([tampered], etag='"replacement"')

    assert cache.list_global() == [catalog_action]
    assert cache.conditional_headers() == {"If-None-Match": '"original"'}
