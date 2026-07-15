#!/usr/bin/env python3
"""Verify a Mercury release candidate through the release CLI."""

from __future__ import annotations

import sys

from mercury_tools.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["release", "verify", *sys.argv[1:]]))
