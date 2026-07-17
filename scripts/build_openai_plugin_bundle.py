"""Build the deterministic skills ZIP used by the OpenAI plugin portal."""

from __future__ import annotations

import hashlib
import json
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


def build_bundle(source: Path = SOURCE, output: Path = OUTPUT) -> dict[str, object]:
    skill_files = sorted(source.glob("*/SKILL.md"))
    if not skill_files:
        raise RuntimeError(f"No skills found under {source}")
    if any(path.is_symlink() for path in skill_files):
        raise RuntimeError("Skill bundle must not contain symlinks")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in skill_files:
            relative = path.relative_to(source).as_posix()
            info = ZipInfo(relative, date_time=ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())

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
