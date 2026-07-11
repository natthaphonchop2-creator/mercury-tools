#!/usr/bin/env python3
"""Publish reviewed, canonical global catalog artifacts to Supabase."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from mercury_tools.catalog.models import (
    CatalogAction,
    CatalogSource,
    revalidate_catalog_action,
    revalidate_catalog_source,
)
from mercury_tools.config import load_settings
from mercury_tools.db.catalog import SupabaseCatalogStore


def discover_catalog(path: Path) -> list[tuple[CatalogSource, list[CatalogAction]]]:
    if not path.is_dir():
        raise ValueError("catalog_path_not_found")

    source_paths = sorted(path.rglob("source.json"), key=lambda item: item.as_posix())
    action_paths = sorted(path.rglob("actions.json"), key=lambda item: item.as_posix())
    source_directories = {item.parent for item in source_paths}
    action_directories = {item.parent for item in action_paths}
    if not source_paths or source_directories != action_directories:
        raise ValueError("catalog_artifact_pair_invalid")

    catalog: list[tuple[CatalogSource, list[CatalogAction]]] = []
    for source_path in source_paths:
        try:
            source = revalidate_catalog_source(
                CatalogSource.model_validate(_read_json(source_path))
            )
            action_rows = _read_json(source_path.with_name("actions.json"))
            if not isinstance(action_rows, list):
                raise TypeError
            actions = [
                revalidate_catalog_action(CatalogAction.model_validate(row)) for row in action_rows
            ]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("catalog_artifact_invalid") from None
        catalog.append((source, actions))
    return catalog


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish canonical ERP catalog artifacts.")
    parser.add_argument("--path", type=Path, required=True, help="Catalog artifact root")
    args = parser.parse_args(argv)
    try:
        catalog = discover_catalog(args.path)
        store = SupabaseCatalogStore(load_settings())
        published = [store.publish(source, actions) for source, actions in catalog]
    except (RuntimeError, ValueError) as error:
        print(f"publish_catalog failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Published {sum(item.activated_actions for item in published)} actions "
        f"from {len(published)} sources."
    )
    return 0


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
