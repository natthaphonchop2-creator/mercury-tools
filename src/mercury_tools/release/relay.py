"""Bounded decoder for sanitized release-control attestation relays."""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import hashlib
import io
import os
import re
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_ENCODED_CHARS = 60_000
DEFAULT_MAX_COMPRESSED_BYTES = 45_000
DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AttestationRelayError(RuntimeError):
    """Fail-closed relay error with a constant, non-sensitive message."""


@dataclass(frozen=True)
class AttestationRelayResult:
    output_path: Path
    payload_sha256: str
    compressed_bytes: int
    output_bytes: int


def _remove_failed_output(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise AttestationRelayError("attestation_relay_cleanup_failed") from exc


def decode_attestation_gzip_b64(
    encoded: bytes,
    *,
    output_path: Path,
    expected_sha256: str,
    max_encoded_chars: int = DEFAULT_MAX_ENCODED_CHARS,
    max_compressed_bytes: int = DEFAULT_MAX_COMPRESSED_BYTES,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> AttestationRelayResult:
    """Decode one bounded gzip+base64 payload and persist it only after SHA verification."""

    if not _SHA256_PATTERN.fullmatch(expected_sha256):
        raise AttestationRelayError("attestation_relay_expected_sha256_invalid")
    if min(max_encoded_chars, max_compressed_bytes, max_output_bytes) < 1:
        raise AttestationRelayError("attestation_relay_limit_invalid")
    if not encoded or len(encoded) > max_encoded_chars:
        raise AttestationRelayError("attestation_relay_encoded_limit_exceeded")

    try:
        compressed = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AttestationRelayError("attestation_relay_base64_invalid") from exc
    if not compressed or len(compressed) > max_compressed_bytes:
        raise AttestationRelayError("attestation_relay_compressed_limit_exceeded")

    output_path = Path(output_path)
    parent = output_path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise AttestationRelayError("attestation_relay_output_parent_invalid")
    if output_path.exists() or output_path.is_symlink():
        raise AttestationRelayError("attestation_relay_output_exists")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output_path, flags, 0o600)
    except FileExistsError as exc:
        raise AttestationRelayError("attestation_relay_output_exists") from exc
    except OSError as exc:
        raise AttestationRelayError("attestation_relay_output_open_failed") from exc

    digest = hashlib.sha256()
    output_bytes = 0
    try:
        with (
            gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as source,
            os.fdopen(descriptor, "wb") as output,
        ):
            while chunk := source.read(65_536):
                output_bytes += len(chunk)
                if output_bytes > max_output_bytes:
                    raise AttestationRelayError(
                        "attestation_relay_output_limit_exceeded"
                    )
                digest.update(chunk)
                output.write(chunk)
    except AttestationRelayError:
        _remove_failed_output(output_path)
        raise
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        _remove_failed_output(output_path)
        raise AttestationRelayError("attestation_relay_gzip_invalid") from exc
    except OSError as exc:
        _remove_failed_output(output_path)
        raise AttestationRelayError("attestation_relay_io_failed") from exc

    payload_sha256 = digest.hexdigest()
    if payload_sha256 != expected_sha256:
        _remove_failed_output(output_path)
        raise AttestationRelayError("attestation_relay_sha256_mismatch")

    return AttestationRelayResult(
        output_path=output_path,
        payload_sha256=payload_sha256,
        compressed_bytes=len(compressed),
        output_bytes=output_bytes,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decode one bounded Mercury release-control attestation relay."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument(
        "--max-encoded-chars",
        type=int,
        default=DEFAULT_MAX_ENCODED_CHARS,
    )
    parser.add_argument(
        "--max-compressed-bytes",
        type=int,
        default=DEFAULT_MAX_COMPRESSED_BYTES,
    )
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=DEFAULT_MAX_OUTPUT_BYTES,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    encoded = sys.stdin.buffer.read(args.max_encoded_chars + 1)
    try:
        decode_attestation_gzip_b64(
            encoded,
            output_path=args.output,
            expected_sha256=args.expected_sha256,
            max_encoded_chars=args.max_encoded_chars,
            max_compressed_bytes=args.max_compressed_bytes,
            max_output_bytes=args.max_output_bytes,
        )
    except AttestationRelayError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
