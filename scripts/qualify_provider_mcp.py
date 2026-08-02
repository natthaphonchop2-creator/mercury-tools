#!/usr/bin/env python3
"""Persist a controlled provider-MCP qualification artifact without network access."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from mercury_tools.qualification.artifacts import (
    QualificationArtifact,
    write_qualification_artifact,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write one sanitized, version-bound provider-MCP qualification artifact."
    )
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="A controlled JSON artifact containing hashes and reviewed identifiers only.",
    )
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        artifact = QualificationArtifact.model_validate(payload)
        write_qualification_artifact(args.catalog_root, artifact)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        print("qualify_provider_mcp failed: qualification_artifact_invalid", file=sys.stderr)
        return 1

    print(
        f"qualification artifact written: {artifact.provider}/{artifact.capability_version_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
