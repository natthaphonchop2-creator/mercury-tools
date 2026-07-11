import json
import os
from pathlib import Path

import pytest

from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.catalog.local_store import LocalCatalogStore, merge_actions


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
