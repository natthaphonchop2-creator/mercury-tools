from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

import pytest

from mercury_tools.release.relay import (
    AttestationRelayError,
    decode_attestation_gzip_b64,
)


def _gzip_b64(payload: bytes) -> bytes:
    return base64.b64encode(gzip.compress(payload, compresslevel=9, mtime=0))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_decode_attestation_gzip_b64_writes_exact_verified_payload(
    tmp_path: Path,
) -> None:
    payload = b'{"schema_version":3,"status":"passed"}'
    encoded = _gzip_b64(payload)
    output = tmp_path / "trusted-hosted-attestation.json"

    result = decode_attestation_gzip_b64(
        encoded,
        output_path=output,
        expected_sha256=_sha256(payload),
        max_encoded_chars=len(encoded),
        max_compressed_bytes=len(base64.b64decode(encoded)),
        max_output_bytes=len(payload),
    )

    assert result.output_path == output
    assert result.payload_sha256 == _sha256(payload)
    assert result.compressed_bytes == len(base64.b64decode(encoded))
    assert result.output_bytes == len(payload)
    assert output.read_bytes() == payload
    assert output.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize(
    ("encoded", "expected_error"),
    [
        (b"not-base64!", "attestation_relay_base64_invalid"),
        (
            base64.b64encode(b'{"legacy":"raw-base64"}'),
            "attestation_relay_gzip_invalid",
        ),
        (
            base64.b64encode(gzip.compress(b"truncated", mtime=0)[:-3]),
            "attestation_relay_gzip_invalid",
        ),
    ],
)
def test_decode_attestation_rejects_invalid_transport_without_output(
    tmp_path: Path,
    encoded: bytes,
    expected_error: str,
) -> None:
    output = tmp_path / "trusted-hosted-attestation.json"

    with pytest.raises(AttestationRelayError, match=f"^{expected_error}$"):
        decode_attestation_gzip_b64(
            encoded,
            output_path=output,
            expected_sha256="0" * 64,
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("limit_name", "expected_error"),
    [
        ("encoded", "attestation_relay_encoded_limit_exceeded"),
        ("compressed", "attestation_relay_compressed_limit_exceeded"),
        ("output", "attestation_relay_output_limit_exceeded"),
    ],
)
def test_decode_attestation_enforces_every_size_limit_and_cleans_up(
    tmp_path: Path,
    limit_name: str,
    expected_error: str,
) -> None:
    payload = b"A" * 4096
    encoded = _gzip_b64(payload)
    compressed = base64.b64decode(encoded)
    limits = {
        "max_encoded_chars": len(encoded),
        "max_compressed_bytes": len(compressed),
        "max_output_bytes": len(payload),
    }
    limits[f"max_{limit_name}_{'chars' if limit_name == 'encoded' else 'bytes'}"] -= 1
    output = tmp_path / "trusted-hosted-attestation.json"

    with pytest.raises(AttestationRelayError, match=f"^{expected_error}$"):
        decode_attestation_gzip_b64(
            encoded,
            output_path=output,
            expected_sha256=_sha256(payload),
            **limits,
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_decode_attestation_rejects_wrong_sha_and_removes_output(tmp_path: Path) -> None:
    payload = b'{"status":"passed"}'
    output = tmp_path / "trusted-hosted-attestation.json"

    with pytest.raises(AttestationRelayError, match="^attestation_relay_sha256_mismatch$"):
        decode_attestation_gzip_b64(
            _gzip_b64(payload),
            output_path=output,
            expected_sha256="0" * 64,
        )

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_decode_attestation_rejects_preexisting_output(tmp_path: Path) -> None:
    payload = b'{"status":"passed"}'
    output = tmp_path / "trusted-hosted-attestation.json"
    output.write_text("do-not-overwrite", encoding="utf-8")

    with pytest.raises(AttestationRelayError, match="^attestation_relay_output_exists$"):
        decode_attestation_gzip_b64(
            _gzip_b64(payload),
            output_path=output,
            expected_sha256=_sha256(payload),
        )

    assert output.read_text(encoding="utf-8") == "do-not-overwrite"
