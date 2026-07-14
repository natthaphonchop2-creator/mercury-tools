#!/usr/bin/env python3
"""Run the fail-closed public release secret gate."""

from __future__ import annotations

import sys

from mercury_tools.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["release", "scan-secrets", *sys.argv[1:]]))
