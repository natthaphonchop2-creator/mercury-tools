from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from mercury_tools.release.public_tree import (
    PublicTreeError,
    build_public_tree,
    write_public_tree,
)


def _archive(
    entries: list[tuple[str, bytes, int, bytes | None, dict[str, str] | None]],
    *,
    global_pax_headers: dict[str, str] | None = None,
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(
        fileobj=output,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers=global_pax_headers,
    ) as archive:
        for name, data, mode, kind, pax_headers in entries:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.type = kind or tarfile.REGTYPE
            member.pax_headers = pax_headers or {}
            if member.isfile():
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
    return output.getvalue()


def _file(
    name: str,
    data: bytes = b"payload\n",
    mode: int = 0o644,
    *,
    pax_headers: dict[str, str] | None = None,
) -> tuple[str, bytes, int, bytes | None, dict[str, str] | None]:
    return name, data, mode, None, pax_headers


def _special(
    name: str,
    kind: bytes,
) -> tuple[str, bytes, int, bytes | None, dict[str, str] | None]:
    return name, b"", 0o777, kind, None


def test_public_tree_v1_filters_paths_normalizes_modes_and_binds_digest() -> None:
    archive = _archive(
        [
            _file("bin/run", b"run\n", 0o100775),
            _file("README.md", b"read\n", 0o100664),
            _file(".env", b"secret"),
            _file("nested/.ENV.production", b"secret"),
            _file("build/output.txt", b"build"),
            _file("state/provider-response.json", b"response"),
        ]
    )

    snapshot = build_public_tree(archive)

    assert [(entry.path, entry.mode) for entry in snapshot.entries] == [
        ("README.md", 0o644),
        ("bin/run", 0o755),
    ]
    records = b"".join(
        f"{entry.mode:o} {entry.path}\0{entry.sha256}".encode()
        for entry in snapshot.entries
    )
    assert snapshot.digest == hashlib.sha256(records).hexdigest()
    assert snapshot.entries[0].sha256 == hashlib.sha256(b"read\n").hexdigest()
    assert snapshot.entries[1].sha256 == hashlib.sha256(b"run\n").hexdigest()


def test_public_tree_v1_is_deterministic_across_archive_order() -> None:
    first = build_public_tree(_archive([_file("b.txt", b"b"), _file("a.txt", b"a")]))
    second = build_public_tree(_archive([_file("a.txt", b"a"), _file("b.txt", b"b")]))

    assert first.digest == second.digest
    assert first.public_inventory() == second.public_inventory()


def test_public_tree_v1_matches_versioned_cross_repository_fixture() -> None:
    fixture_path = Path(__file__).parent / "fixtures/release/public-tree-v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    members = [
        _file(
            member["path"],
            base64.b64decode(member["content_b64"], validate=True),
            member["mode"],
        )
        for member in fixture["members"]
    ]

    snapshot = build_public_tree(_archive(members))

    assert snapshot.digest == fixture["expected_digest"]
    assert list(snapshot.public_inventory()) == fixture["entries"]


@pytest.mark.parametrize(
    "name",
    ("../secret", "/absolute", "a/../secret", "a\\b", "./relative", "a//b"),
)
def test_public_tree_v1_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(PublicTreeError, match="^public_tree_archive_invalid$"):
        build_public_tree(_archive([_file(name)]))


def test_public_tree_v1_rejects_casefolded_duplicate_files() -> None:
    with pytest.raises(PublicTreeError, match="^public_tree_archive_invalid$"):
        build_public_tree(_archive([_file("Docs/Guide.md"), _file("docs/guide.md")]))


@pytest.mark.parametrize("kind", (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE))
def test_public_tree_v1_rejects_non_regular_payload_members(kind: bytes) -> None:
    with pytest.raises(PublicTreeError, match="^public_tree_archive_invalid$"):
        build_public_tree(_archive([_special("unsafe", kind)]))


def test_public_tree_v1_allows_directory_members_but_does_not_digest_them() -> None:
    directory = _special("docs", tarfile.DIRTYPE)
    snapshot = build_public_tree(_archive([directory, _file("docs/index.md", b"index")]))

    assert [entry.path for entry in snapshot.entries] == ["docs/index.md"]


def test_public_tree_v1_rejects_pax_member_metadata() -> None:
    with pytest.raises(PublicTreeError, match="^public_tree_archive_invalid$"):
        build_public_tree(_archive([_file("doc.txt", pax_headers={"comment": "untrusted"})]))


def test_public_tree_v1_accepts_only_git_commit_global_pax_comment() -> None:
    commit = "a" * 40
    snapshot = build_public_tree(
        _archive([_file("doc.txt", b"doc")], global_pax_headers={"comment": commit})
    )
    assert [entry.path for entry in snapshot.entries] == ["doc.txt"]

    with pytest.raises(PublicTreeError, match="^public_tree_archive_invalid$"):
        build_public_tree(
            _archive(
                [_file("doc.txt", b"doc")],
                global_pax_headers={"comment": "not-a-commit"},
            )
        )


def test_public_tree_v1_enforces_archive_member_and_total_limits(monkeypatch) -> None:
    import mercury_tools.release.public_tree as public_tree

    monkeypatch.setattr(public_tree, "MAX_MEMBER_BYTES", 3)
    with pytest.raises(PublicTreeError, match="^public_tree_archive_too_large$"):
        build_public_tree(_archive([_file("large.txt", b"1234")]))

    monkeypatch.setattr(public_tree, "MAX_MEMBER_BYTES", 8)
    monkeypatch.setattr(public_tree, "MAX_TOTAL_BYTES", 3)
    with pytest.raises(PublicTreeError, match="^public_tree_archive_too_large$"):
        build_public_tree(_archive([_file("a", b"12"), _file("b", b"34")]))


def test_write_public_tree_publishes_exact_modes_and_content(tmp_path: Path) -> None:
    snapshot = build_public_tree(
        _archive([_file("README.md", b"read\n"), _file("bin/run", b"run\n", 0o755)])
    )
    destination = tmp_path / "public"

    write_public_tree(snapshot, destination)

    assert (destination / "README.md").read_bytes() == b"read\n"
    assert (destination / "bin/run").read_bytes() == b"run\n"
    assert (destination / "README.md").stat().st_mode & 0o777 == 0o644
    assert (destination / "bin/run").stat().st_mode & 0o777 == 0o755


def test_write_public_tree_does_not_replace_existing_destination(tmp_path: Path) -> None:
    snapshot = build_public_tree(_archive([_file("README.md")]))
    destination = tmp_path / "public"
    destination.mkdir()

    with pytest.raises(PublicTreeError, match="^public_tree_destination_exists$"):
        write_public_tree(snapshot, destination)
