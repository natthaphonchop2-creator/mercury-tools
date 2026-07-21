"""Build the deterministic skills ZIP used by the OpenAI plugin portal."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "submission" / "openai-plugin" / "skills"
OUTPUT = (
    REPO_ROOT
    / "dist"
    / "openai-plugin"
    / "mercury-finance-skills-public.zip"
)
ZIP_TIMESTAMP = (2026, 7, 17, 0, 0, 0)
ZIP_CREATE_SYSTEM = 3
ZIP_CREATE_VERSION = 20
ZIP_EXTRACT_VERSION = 20
ZIP_COMPRESSION_LEVEL = 9


def _zip_info(relative: str) -> ZipInfo:
    info = ZipInfo(relative, date_time=ZIP_TIMESTAMP)
    info.create_system = ZIP_CREATE_SYSTEM
    info.create_version = ZIP_CREATE_VERSION
    info.extract_version = ZIP_EXTRACT_VERSION
    info.flag_bits = 0
    info.volume = 0
    info.internal_attr = 0
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = ZIP_DEFLATED
    info.extra = b""
    info.comment = b""
    info.reserved = 0
    info._compresslevel = ZIP_COMPRESSION_LEVEL
    return info


def build_bundle(source: Path = SOURCE, output: Path = OUTPUT) -> dict[str, object]:
    skill_files = sorted(source.glob("*/SKILL.md"))
    if not skill_files:
        raise RuntimeError(f"No skills found under {source}")
    if any(path.is_symlink() for path in skill_files):
        raise RuntimeError("Skill bundle must not contain symlinks")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(
        output,
        "w",
        compression=ZIP_DEFLATED,
        compresslevel=ZIP_COMPRESSION_LEVEL,
    ) as archive:
        archive.comment = b""
        for path in skill_files:
            relative = path.relative_to(source).as_posix()
            archive.writestr(_zip_info(relative), path.read_bytes())

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "status": "ok",
        "path": str(output),
        "sha256": digest,
        "skill_count": len(skill_files),
        "skills": [path.parent.name for path in skill_files],
    }


if __name__ == "__main__":
    print(json.dumps(build_bundle(), indent=2, sort_keys=True))
