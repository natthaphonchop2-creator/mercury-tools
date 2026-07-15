#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from mercury_tools.release.scanner import (
    ReleaseGateError,
    load_secret_scan_allowlist,
    verify_trufflehog_report,
)

MAX_REPORT_BYTES = 64 * 1024 * 1024


def _read_regular_report(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REPORT_BYTES:
            raise ReleaseGateError("trufflehog_report_invalid")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > MAX_REPORT_BYTES:
                raise ReleaseGateError("trufflehog_report_invalid")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a redaction-sensitive TruffleHog report")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _read_regular_report(args.report)
        allowlist = load_secret_scan_allowlist(args.allowlist)
        finding_count = verify_trufflehog_report(
            report,
            allowlist,
            at=datetime.now(UTC),
        )
    except (OSError, ReleaseGateError):
        print(json.dumps({"scanner": "trufflehog", "status": "blocked"}))
        return 1
    print(
        json.dumps(
            {
                "allowlisted_finding_count": finding_count,
                "scanner": "trufflehog",
                "status": "passed",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
