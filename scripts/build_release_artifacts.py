#!/usr/bin/env python3
"""Build deterministic Mercury release artifacts through the release CLI."""

from __future__ import annotations

import sys

from mercury_tools.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["release", "build-artifacts", *sys.argv[1:]]))
