#!/usr/bin/env python3
"""Build a history-free Mercury public staging repository through the CLI."""

from __future__ import annotations

import sys

from mercury_tools.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["release", "build-public-staging", *sys.argv[1:]]))
