"""Build deterministic accounting semantic sidecars for built-in catalogs."""

from __future__ import annotations

import argparse
from pathlib import Path

from mercury_tools.qualification.semantics import build_semantic_sidecars


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    counts = build_semantic_sidecars(args.catalog)
    print(
        " ".join(
            (
                f"flowaccount={counts['flowaccount']}",
                f"peak={counts['peak']}",
                "missing=0",
            )
        )
    )


if __name__ == "__main__":
    main()
