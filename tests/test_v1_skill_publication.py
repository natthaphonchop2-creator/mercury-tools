from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from mercury_tools.skills.catalog import ACCOUNTING_SKILL_CATALOG

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts/build_v1_skill_publication_migration.py"
PUBLICATION_MIGRATION = (
    ROOT / "supabase/migrations/20260731100000_mercury_v1_publish_first_party_skills.sql"
)


def _publication_builder() -> ModuleType:
    assert BUILD_SCRIPT.exists(), "release-owned Skill publication builder is missing"
    spec = importlib.util.spec_from_file_location(
        "build_v1_skill_publication_migration",
        BUILD_SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_publication_payload_covers_every_git_canonical_skill() -> None:
    builder = _publication_builder()

    payload = builder.build_publication_payload()
    expected = tuple(
        {
            "visibility_scope": "global",
            "tenant_id": None,
            "workspace_id": None,
            "skill_id": skill.skill_id,
            "skill_version": skill.skill_version,
            "publication_status": "published",
            "projection": skill.published_projection(),
            "projection_sha256": skill.projection_sha256,
            "git_source_path": skill.git_source_path,
        }
        for skill in ACCOUNTING_SKILL_CATALOG
    )

    assert payload == expected
    assert len(payload) == len(ACCOUNTING_SKILL_CATALOG)
    assert len({(row["skill_id"], row["skill_version"]) for row in payload}) == len(payload)


def test_checked_in_publication_migration_is_exact_deterministic_projection() -> None:
    builder = _publication_builder()
    assert PUBLICATION_MIGRATION.exists(), "first-party Skill publication migration is missing"

    expected = builder.render_publication_migration()
    actual = PUBLICATION_MIGRATION.read_text(encoding="utf-8")
    sql = actual.lower()

    assert actual == expected
    assert "do $mercury_v1_publish_first_party_skills$" in sql
    assert "insert into public.mercury_published_skills" in sql
    assert "on conflict do nothing" in sql
    assert "mercury_first_party_skill_publication_mismatch" in sql
    assert "create function" not in sql
    assert "create or replace function" not in sql
    assert "grant execute" not in sql
    assert "grant insert" not in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql
    assert "update public.mercury_published_skills" not in sql
    assert "delete from public.mercury_published_skills" not in sql


def test_generated_publication_payload_is_multiline_and_reviewable() -> None:
    builder = _publication_builder()
    rendered = builder.render_publication_migration()
    _, payload, _ = rendered.split("$mercury_v1_first_party_skill_payload$")
    payload_lines = payload.splitlines()

    assert json.loads(payload) == list(builder.build_publication_payload())
    assert len(payload_lines) > len(ACCOUNTING_SKILL_CATALOG)
    credential_title_line = next(
        line for line in payload_lines if "Connector Credential Setup TH" in line
    )
    assert "projection_sha256" not in credential_title_line
