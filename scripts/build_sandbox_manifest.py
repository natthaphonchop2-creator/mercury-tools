"""Build the reviewed FlowAccount sandbox execution manifest."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from mercury_tools.qualification.manifest import (
    SandboxDisposition,
    write_sandbox_execution_manifest,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the reviewed FlowAccount sandbox execution manifest."
    )
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = write_sandbox_execution_manifest(args.catalog, args.output)
    except ValueError:
        print("sandbox_manifest_build_failed")
        return 2

    dispositions = Counter(policy.disposition for policy in manifest.actions)
    missing = 190 - len(manifest.actions)
    print(
        " ".join(
            (
                f"total={len(manifest.actions)}",
                f"sandbox_executable={dispositions[SandboxDisposition.SANDBOX_EXECUTABLE]}",
                f"blocked_external_effect={dispositions[SandboxDisposition.BLOCKED_EXTERNAL_EFFECT]}",
                f"contract_only={dispositions[SandboxDisposition.CONTRACT_ONLY]}",
                f"unsupported_by_sandbox={dispositions[SandboxDisposition.UNSUPPORTED_BY_SANDBOX]}",
                f"missing={missing}",
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
