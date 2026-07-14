"""Concrete, receipt-based hosted release surface inspection."""

from __future__ import annotations

import functools
import hashlib
import json
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Protocol
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from mcp.types import InitializeResult

from mercury_tools.release.models import (
    BUILTIN_SCANNER_VERSION,
    HostedSurfaceScanResult,
    SecretFinding,
    SecretScanPolicy,
)
from mercury_tools.release.scanner import (
    CommandResult,
    CommandRunner,
    SubprocessCommandRunner,
    _archive_candidate_crosses_boundary,
    _ArtifactSnapshot,
    _BudgetExceeded,
    _deduplicate_findings,
    _detect_archive_format,
    _GitReachableInventory,
    _is_archive_candidate,
    _parse_local_refs,
    _scan_archive_at_depth,
    _scan_bytes,
    _scan_reachable_blobs,
)

HOSTED_SCANNER_VERSION = BUILTIN_SCANNER_VERSION
HOSTED_PUBLIC_SURFACES = (
    "github_pull_request_refs",
    "github_releases_and_assets",
    "github_actions_logs_artifacts_caches",
    "github_packages_pages_wiki",
    "marketplace_snapshot",
    "render_build_and_runtime_logs",
    "supabase_knowledge_and_storage",
    "public_mcp_responses",
)
HOSTED_RECEIPT_INVENTORY = MappingProxyType(
    {
        "github_pull_request_refs": ("github_pr_refs_query",),
        "github_releases_and_assets": (
            "github_releases_query",
            "github_release_assets_query",
            "github_release_assets_download",
        ),
        "github_actions_logs_artifacts_caches": (
            "github_actions_runs_query",
            "github_actions_logs_download",
            "github_actions_artifacts_query",
            "github_actions_artifacts_download",
            "github_actions_caches_query",
            "github_actions_caches_content",
        ),
        "github_packages_pages_wiki": (
            "github_packages_query",
            "github_package_versions_query",
            "github_package_versions_content",
            "github_pages_query",
            "github_pages_download",
            "github_wiki_query",
            "github_wiki_download",
        ),
        "marketplace_snapshot": ("marketplace_snapshot_download",),
        "render_build_and_runtime_logs": (
            "render_build_logs_query",
            "render_runtime_logs_query",
        ),
        "supabase_knowledge_and_storage": (
            "supabase_knowledge_query",
            "supabase_storage_query",
            "supabase_storage_download",
        ),
        "public_mcp_responses": (
            "public_mcp_initialize",
            "public_mcp_tools_list",
            "public_mcp_response_stream",
        ),
    }
)
_PARENT_COUNT_RECEIPTS = frozenset(
    {
        "github_release_assets_query",
        "github_release_assets_download",
        "github_actions_logs_download",
        "github_actions_artifacts_download",
        "github_actions_caches_content",
        "github_package_versions_query",
        "github_package_versions_content",
        "github_pages_download",
        "github_wiki_download",
        "supabase_storage_download",
    }
)
_ARCHIVE_CAPABLE_RECEIPTS = frozenset(
    {
        "github_release_assets_download",
        "github_actions_logs_download",
        "github_actions_artifacts_download",
        "github_actions_caches_content",
        "github_package_versions_content",
        "github_pages_download",
        "github_wiki_download",
        "marketplace_snapshot_download",
        "supabase_storage_download",
    }
)
_PAGE_BOUND_ARCHIVE_RECEIPTS = frozenset({"marketplace_snapshot_download"})
_REQUEST_BOUND_ARCHIVE_RECEIPTS = _ARCHIVE_CAPABLE_RECEIPTS.difference(
    {"github_wiki_download", *_PAGE_BOUND_ARCHIVE_RECEIPTS}
)
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_RECEIPT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PACKAGE_TYPES = ("container", "docker", "maven", "npm", "nuget", "rubygems")
_MCP_PROTOCOL_VERSION = "2025-11-25"
_SUPPORTED_MCP_PROTOCOL_VERSIONS = frozenset({_MCP_PROTOCOL_VERSION})
_OBJECT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OBJECT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_./:-]{0,255}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WIKI_OBJECT_TYPES = frozenset(
    {
        "wiki_command_output",
        "wiki_mirror_inventory",
        "wiki_reachable_blob",
        "wiki_reachable_tag",
    }
)
_WIKI_COMMAND_OBJECT_IDS = frozenset(
    {
        "wiki/command/clone",
        "wiki/command/refs-initial",
        "wiki/command/head-initial",
        "wiki/command/refs-final",
        "wiki/command/head-final",
    }
)
_WIKI_INVENTORY_OBJECT_ID = "wiki/mirror-inventory/v1"
_WIKI_INVENTORY_KEYS = frozenset(
    {
        "schema_version",
        "remote_ref_count",
        "remote_ref_digest",
        "reachable_object_count",
        "reachable_blob_count",
        "payload_object_count",
        "payload_objects",
    }
)
_WIKI_PAYLOAD_KEYS = frozenset(
    {"object_type", "object_id", "byte_count", "content_sha256"}
)


@dataclass(frozen=True, repr=False)
class HostedObjectBoundary:
    """Consume the next transport chunks as one complete downloaded object."""

    chunk_count: int
    byte_count: int
    object_type: str | None = None
    object_id: str | None = None
    content_sha256: str | None = None


@dataclass(frozen=True, repr=False)
class HostedReceipt:
    name: str
    chunks: Iterable[bytes] = field(default=(), repr=False)
    complete: bool = False
    page_count: int = 0
    record_count: int = 0
    request_count: int = 1
    parent_record_count: int | None = None
    exit_codes: tuple[int, ...] = ()
    status_codes: tuple[int, ...] = ()
    findings: tuple[SecretFinding, ...] = field(default=(), repr=False)
    object_boundaries: tuple[HostedObjectBoundary, ...] | None = None
    expected_object_count: int | None = None

    def __post_init__(self) -> None:
        if self.name not in _ARCHIVE_CAPABLE_RECEIPTS:
            return
        if self.expected_object_count is not None:
            return
        expected: int | None = None
        if self.name in _PAGE_BOUND_ARCHIVE_RECEIPTS and (
            isinstance(self.page_count, int)
            and not isinstance(self.page_count, bool)
            and self.page_count >= 1
        ):
            expected = self.page_count
        elif self.name in _REQUEST_BOUND_ARCHIVE_RECEIPTS and (
            isinstance(self.request_count, int)
            and not isinstance(self.request_count, bool)
            and self.request_count >= 0
        ):
            expected = self.request_count
        elif (
            self.name == "github_wiki_download"
            and self.complete is True
            and self.object_boundaries == ()
            and self.record_count == 0
            and self.request_count == 0
            and self.parent_record_count == 0
        ):
            expected = 0
        if expected is not None:
            object.__setattr__(self, "expected_object_count", expected)


def _logical_object_boundary(
    chunks: tuple[bytes, ...],
    *,
    object_type: str,
    object_id: str,
) -> HostedObjectBoundary:
    data = b"".join(chunks)
    return HostedObjectBoundary(
        chunk_count=len(chunks),
        byte_count=len(data),
        object_type=object_type,
        object_id=object_id,
        content_sha256=hashlib.sha256(data).hexdigest(),
    )


def _object_boundaries_for_chunks(
    name: str,
    chunks: Iterable[bytes],
) -> tuple[HostedObjectBoundary, ...] | None:
    if name not in _ARCHIVE_CAPABLE_RECEIPTS:
        return None
    return tuple(
        _logical_object_boundary(
            (chunk,),
            object_type="hosted_transport_object",
            object_id=f"{name}/{index}",
        )
        for index, chunk in enumerate(chunks)
    )


def _merged_object_boundaries(
    name: str,
    receipts: tuple[HostedReceipt, ...],
) -> tuple[HostedObjectBoundary, ...] | None:
    if name not in _ARCHIVE_CAPABLE_RECEIPTS:
        return None
    if any(receipt.object_boundaries is None for receipt in receipts):
        return None
    merged: list[HostedObjectBoundary] = []
    for receipt_index, receipt in enumerate(receipts):
        for boundary_index, boundary in enumerate(receipt.object_boundaries or ()):
            if not isinstance(boundary, HostedObjectBoundary):
                return None
            if all(
                isinstance(value, str)
                for value in (
                    boundary.object_type,
                    boundary.object_id,
                    boundary.content_sha256,
                )
            ):
                boundary = replace(
                    boundary,
                    object_id=f"{name}/{receipt_index}/{boundary_index}",
                )
            merged.append(boundary)
    return tuple(merged)


@dataclass(frozen=True, repr=False)
class HostedInspection:
    receipts: tuple[HostedReceipt, ...]
    scanner_version: str | None


@dataclass(frozen=True, repr=False)
class HostedHttpResponse:
    status_code: int
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass
class _HostedArchiveBudget:
    limit: int
    used: int = 0

    def reserve(self, size: int) -> None:
        if size < 0 or size > self.limit - self.used:
            raise _BudgetExceeded
        self.used += size


@dataclass(frozen=True, repr=False)
class HostedAdapterConfig:
    repo: str
    gh_executable: Path | None = None
    github_token: str | None = field(default=None, repr=False)
    marketplace_url: str | None = None
    render_api_url: str | None = None
    render_service_id: str | None = None
    render_token: str | None = field(default=None, repr=False)
    supabase_url: str | None = None
    supabase_key: str | None = field(default=None, repr=False)
    supabase_knowledge_tables: tuple[str, ...] = ()
    supabase_storage_buckets: tuple[str, ...] = ()
    public_mcp_url: str | None = None
    public_mcp_token: str | None = field(default=None, repr=False)


class HostedSurfaceClient(Protocol):
    def inspect(self, surface: str, policy: SecretScanPolicy) -> HostedInspection: ...


class HostedHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: object | None = None,
        max_bytes: int,
    ) -> HostedHttpResponse: ...


class HttpxHostedTransport:
    """Bound every HTTP body before returning it to an adapter."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: object | None = None,
        max_bytes: int,
    ) -> HostedHttpResponse:
        chunks: list[bytes] = []
        total = 0
        with (
            httpx.Client(timeout=30.0, follow_redirects=False) as client,
            client.stream(
                method,
                url,
                headers=dict(headers),
                json=json_body,
            ) as response,
        ):
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                remaining = max_bytes - total
                if remaining <= 0:
                    break
                bounded = chunk[:remaining]
                chunks.append(bounded)
                total += len(bounded)
                if len(chunk) >= remaining:
                    break
            return HostedHttpResponse(
                status_code=response.status_code,
                body=b"".join(chunks),
                headers={key.casefold(): value for key, value in response.headers.items()},
            )


def _command_chunks(result: CommandResult) -> tuple[bytes, ...]:
    chunks: list[bytes] = []
    if isinstance(result.stdout, bytes) and result.stdout:
        chunks.append(result.stdout)
    if isinstance(result.stderr, bytes) and result.stderr:
        chunks.append(result.stderr)
    return tuple(chunks)


def _hosted_archive_code(reason: str, surface: str) -> str:
    return f"hosted_archive_{reason}:{surface}"


_HOSTED_ARCHIVE_REASON_MAP = MappingProxyType(
    {
        "artifact_archive_depth": "depth",
        "artifact_duplicate_member": "duplicate_member",
        "artifact_entry_limit": "entry_limit",
        "artifact_member_too_large": "member_too_large",
        "artifact_opaque_member": "opaque_member",
        "artifact_read_failed": "read_failed",
        "artifact_uncompressed_limit": "uncompressed_limit",
        "artifact_unparsed_data": "unparsed_data",
        "artifact_unsafe_member": "unsafe",
    }
)


def _map_hosted_archive_blockers(
    blockers: list[str],
    surface: str,
) -> list[str]:
    mapped: list[str] = []
    for blocker in blockers:
        local_reason = blocker.partition(":")[0]
        reason = _HOSTED_ARCHIVE_REASON_MAP.get(local_reason, "read_failed")
        mapped.append(_hosted_archive_code(reason, surface))
    return mapped


def _scan_complete_hosted_archive(
    data: bytes,
    surface: str,
    policy: SecretScanPolicy,
    budget: _HostedArchiveBudget,
    *,
    expected_formats: tuple[str, ...] | None = None,
) -> tuple[list[SecretFinding], list[str]]:
    if len(data) > policy.max_archive_bytes:
        return [], [_hosted_archive_code("too_large", surface)]
    findings, blockers = _scan_archive_at_depth(
        _ArtifactSnapshot(name=f"hosted/{surface}", data=data),
        None,
        policy,
        budget,
        depth=0,
        expected_formats=expected_formats,
    )
    return findings, _map_hosted_archive_blockers(blockers, surface)


def _scan_hosted_zip(
    data: bytes,
    surface: str,
    policy: SecretScanPolicy,
    budget: _HostedArchiveBudget,
) -> tuple[list[SecretFinding], list[str]]:
    return _scan_complete_hosted_archive(
        data,
        surface,
        policy,
        budget,
        expected_formats=("zip",),
    )


def _scan_hosted_tar(
    data: bytes,
    surface: str,
    policy: SecretScanPolicy,
    budget: _HostedArchiveBudget,
) -> tuple[list[SecretFinding], list[str]]:
    archive_format = _detect_archive_format(data)
    expected_formats = {
        "gzip": ("gzip", "tar"),
        "bz2": ("bz2", "tar"),
        "xz": ("xz", "tar"),
    }.get(archive_format, ("tar",))
    return _scan_complete_hosted_archive(
        data,
        surface,
        policy,
        budget,
        expected_formats=expected_formats,
    )


def _scan_hosted_archive(
    data: bytes,
    surface: str,
    policy: SecretScanPolicy,
    budget: _HostedArchiveBudget,
) -> tuple[list[SecretFinding], list[str]]:
    candidate = _is_archive_candidate(data)
    archive_format = _detect_archive_format(data)
    if candidate and archive_format is None:
        return [], [_hosted_archive_code("read_failed", surface)]
    if archive_format is None:
        return [], []
    return _scan_complete_hosted_archive(data, surface, policy, budget)


def _boundary_metadata_is_valid(
    boundary: HostedObjectBoundary,
    data: bytes,
    *,
    required: bool,
) -> bool:
    metadata = (
        boundary.object_type,
        boundary.object_id,
        boundary.content_sha256,
    )
    if not required and all(value is None for value in metadata):
        return True
    return bool(
        isinstance(boundary.object_type, str)
        and _OBJECT_TYPE_PATTERN.fullmatch(boundary.object_type)
        and isinstance(boundary.object_id, str)
        and _OBJECT_ID_PATTERN.fullmatch(boundary.object_id)
        and isinstance(boundary.content_sha256, str)
        and _SHA256_PATTERN.fullmatch(boundary.content_sha256)
        and hashlib.sha256(data).hexdigest() == boundary.content_sha256
    )


def _wiki_boundary_identity_is_valid(boundary: HostedObjectBoundary) -> bool:
    if boundary.object_type not in _WIKI_OBJECT_TYPES:
        return False
    if boundary.object_type == "wiki_command_output":
        return boundary.object_id in _WIKI_COMMAND_OBJECT_IDS
    if boundary.object_type == "wiki_mirror_inventory":
        return boundary.object_id == _WIKI_INVENTORY_OBJECT_ID
    if boundary.object_type == "wiki_reachable_blob":
        return bool(
            isinstance(boundary.object_id, str)
            and re.fullmatch(
                r"git/blob/(?:[0-9a-f]{40}|[0-9a-f]{64})",
                boundary.object_id,
            )
        )
    return bool(
        isinstance(boundary.object_id, str)
        and re.fullmatch(
            r"git/tag/(?:[0-9a-f]{40}|[0-9a-f]{64})",
            boundary.object_id,
        )
    )


def _valid_nonnegative_count(value: object, *, positive: bool = False) -> bool:
    return bool(
        isinstance(value, int)
        and not isinstance(value, bool)
        and (value > 0 if positive else value >= 0)
    )


def _wiki_inventory_reconciles(
    objects: tuple[tuple[HostedObjectBoundary, bytes], ...],
) -> bool:
    inventory_objects = tuple(
        data
        for boundary, data in objects
        if boundary.object_type == "wiki_mirror_inventory"
    )
    if len(inventory_objects) != 1:
        return False
    try:
        manifest = json.loads(inventory_objects[0])
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict) or frozenset(manifest) != _WIKI_INVENTORY_KEYS:
        return False
    payload_objects = manifest.get("payload_objects")
    if (
        manifest.get("schema_version") != 1
        or not _valid_nonnegative_count(manifest.get("remote_ref_count"), positive=True)
        or not isinstance(manifest.get("remote_ref_digest"), str)
        or _SHA256_PATTERN.fullmatch(manifest["remote_ref_digest"]) is None
        or not _valid_nonnegative_count(
            manifest.get("reachable_object_count"), positive=True
        )
        or not _valid_nonnegative_count(manifest.get("reachable_blob_count"))
        or not _valid_nonnegative_count(manifest.get("payload_object_count"))
        or not isinstance(payload_objects, list)
        or manifest["payload_object_count"] != len(payload_objects)
    ):
        return False
    actual_payloads = [
        {
            "object_type": boundary.object_type,
            "object_id": boundary.object_id,
            "byte_count": boundary.byte_count,
            "content_sha256": boundary.content_sha256,
        }
        for boundary, _data in objects
        if boundary.object_type in {"wiki_reachable_blob", "wiki_reachable_tag"}
    ]
    if payload_objects != actual_payloads:
        return False
    if any(
        not isinstance(item, dict) or frozenset(item) != _WIKI_PAYLOAD_KEYS
        for item in payload_objects
    ):
        return False
    blob_count = sum(
        item.get("object_type") == "wiki_reachable_blob"
        for item in payload_objects
    )
    return bool(
        manifest["reachable_blob_count"] == blob_count
        and manifest["reachable_object_count"] >= len(payload_objects)
    )


def _scan_hosted_archive_objects(
    chunks: tuple[bytes, ...],
    boundaries: tuple[HostedObjectBoundary, ...] | None,
    surface: str,
    policy: SecretScanPolicy,
    budget: _HostedArchiveBudget,
    *,
    expected_object_count: int,
    receipt_name: str | None = None,
) -> tuple[list[SecretFinding], list[str]]:
    boundary_code = f"hosted_archive_boundary_invalid:{surface}"
    if (
        not isinstance(boundaries, tuple)
        or len(boundaries) > policy.max_hosted_records
        or isinstance(expected_object_count, bool)
        or not isinstance(expected_object_count, int)
        or expected_object_count < 0
        or expected_object_count > policy.max_hosted_records
        or len(boundaries) != expected_object_count
        or (expected_object_count == 0 and bool(chunks))
        or (expected_object_count > 0 and not chunks)
    ):
        return [], [boundary_code]
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for boundary in boundaries:
        if (
            not isinstance(boundary, HostedObjectBoundary)
            or isinstance(boundary.chunk_count, bool)
            or not isinstance(boundary.chunk_count, int)
            or boundary.chunk_count <= 0
            or isinstance(boundary.byte_count, bool)
            or not isinstance(boundary.byte_count, int)
            or boundary.byte_count < 0
            or boundary.chunk_count > policy.max_hosted_records
        ):
            return [], [boundary_code]
        end = cursor + boundary.chunk_count
        if end > len(chunks):
            return [], [boundary_code]
        actual_bytes = sum(len(chunk) for chunk in chunks[cursor:end])
        if actual_bytes != boundary.byte_count:
            return [], [boundary_code]
        spans.append((cursor, end, boundary.byte_count))
        cursor = end
    if cursor != len(chunks):
        return [], [boundary_code]

    objects: list[tuple[HostedObjectBoundary, bytes]] = []
    identities: set[str] = set()
    strict_identities = receipt_name == "github_wiki_download"
    for boundary, (start, end, _byte_count) in zip(
        boundaries,
        spans,
        strict=True,
    ):
        data = b"".join(chunks[start:end])
        if not _boundary_metadata_is_valid(
            boundary,
            data,
            required=strict_identities,
        ):
            return [], [boundary_code]
        if boundary.object_id is not None:
            if boundary.object_id in identities:
                return [], [boundary_code]
            identities.add(boundary.object_id)
        if strict_identities and not _wiki_boundary_identity_is_valid(boundary):
            return [], [boundary_code]
        objects.append((boundary, data))

    material = tuple(objects)
    if (
        strict_identities
        and expected_object_count > 0
        and (
            not _wiki_inventory_reconciles(material)
            or any(
                _archive_candidate_crosses_boundary(left, right)
                for (_left_boundary, left), (_right_boundary, right) in zip(
                    material,
                    material[1:],
                    strict=False,
                )
            )
        )
    ):
        return [], [boundary_code]

    findings: list[SecretFinding] = []
    blockers: list[str] = []
    for (_boundary, data), (_start, _end, byte_count) in zip(
        material,
        spans,
        strict=True,
    ):
        if byte_count > policy.max_archive_bytes:
            blockers.append(_hosted_archive_code("too_large", surface))
            continue
        archive_findings, archive_blockers = _scan_hosted_archive(
            data,
            surface,
            policy,
            budget,
        )
        findings.extend(archive_findings)
        blockers.extend(archive_blockers)
    return findings, blockers


def _page_records(payload: object, record_key: str | None) -> list[object] | None:
    if record_key is None:
        if isinstance(payload, list):
            return list(payload)
        if isinstance(payload, dict):
            return [payload]
        return None
    if not isinstance(payload, dict):
        return None
    records = payload.get(record_key)
    return list(records) if isinstance(records, list) else None


def _github_page_route(route: str, *, page: int, per_page: int) -> str:
    parsed = urlsplit(route)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"page", "per_page"}
    ]
    query.extend((("per_page", str(per_page)), ("page", str(page))))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _valid_id_records(records: list[object]) -> bool:
    identifiers: list[int] = []
    for record in records:
        if not isinstance(record, dict):
            return False
        identifier = record.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            return False
        identifiers.append(identifier)
    return len(identifiers) == len(set(identifiers))


def _valid_pull_ref_records(records: list[object]) -> bool:
    refs: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            return False
        ref = record.get("ref")
        target = record.get("object")
        if (
            not isinstance(ref, str)
            or re.fullmatch(r"refs/pull/[1-9][0-9]*/head", ref) is None
            or not isinstance(target, dict)
            or not isinstance(target.get("sha"), str)
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", target["sha"]) is None
        ):
            return False
        refs.append(ref)
    return len(refs) == len(set(refs))


def _valid_package_records(records: list[object], expected_type: str) -> bool:
    names: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            return False
        name = record.get("name")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(character in name for character in "\r\n\0")
            or record.get("package_type") != expected_type
        ):
            return False
        names.append(name)
    return len(names) == len(set(names))


def _valid_repository_metadata(records: list[object]) -> bool:
    return (
        len(records) == 1
        and isinstance(records[0], dict)
        and isinstance(records[0].get("has_pages"), bool)
        and isinstance(records[0].get("has_wiki"), bool)
    )


def _valid_pages_records(records: list[object]) -> bool:
    return (
        len(records) == 1
        and isinstance(records[0], dict)
        and isinstance(records[0].get("html_url"), str)
        and bool(records[0]["html_url"])
    )


def _valid_git_ref_name(value: str) -> bool:
    peeled = value.endswith("^{}")
    candidate = value.removesuffix("^{}") if peeled else value
    if not candidate.startswith("refs/") or candidate.endswith(("/", ".")):
        return False
    if any(character in candidate for character in " ~^:?*[\\\r\n\0"):
        return False
    parts = candidate.split("/")
    return (
        len(parts) >= 3
        and all(
            part
            and not part.startswith(".")
            and not part.endswith(".lock")
            and ".." not in part
            for part in parts
        )
        and (not peeled or candidate.startswith("refs/tags/"))
    )


def _parse_git_ref_map(output: bytes) -> dict[str, str]:
    refs: dict[str, str] = {}
    if not output:
        raise ValueError("empty_git_ref_inventory")
    for line in output.splitlines():
        parts = line.split(b"\t", 1)
        if (
            len(parts) != 2
            or re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]) is None
        ):
            raise ValueError("malformed_git_ref_inventory")
        ref = parts[1].decode("utf-8", errors="strict")
        if (ref != "HEAD" and not _valid_git_ref_name(ref)) or ref in refs:
            raise ValueError("malformed_git_ref_inventory")
        refs[ref] = parts[0].decode("ascii")
    if "HEAD" not in refs or not any(ref.startswith("refs/") for ref in refs):
        raise ValueError("incomplete_git_ref_inventory")
    for ref in refs:
        if ref.endswith("^{}") and ref.removesuffix("^{}") not in refs:
            raise ValueError("orphaned_peeled_git_ref")
    return refs


def _valid_git_ref_map(output: bytes) -> bool:
    try:
        _parse_git_ref_map(output)
    except (UnicodeError, ValueError):
        return False
    return True


def _parse_head_oid(output: bytes) -> str:
    match = re.fullmatch(rb"([0-9a-f]{40}|[0-9a-f]{64})\n?", output)
    if match is None:
        raise ValueError("malformed_git_head")
    return match.group(1).decode("ascii")


def _wiki_inventory_manifest(
    inventory: _GitReachableInventory,
    remote_refs: Mapping[str, str],
) -> bytes:
    payload_objects: list[dict[str, object]] = []
    for item in inventory.public_objects:
        if item.object_type not in {"blob", "tag"}:
            raise ValueError("wiki_payload_object_type_invalid")
        data = item.data
        payload_objects.append(
            {
                "object_type": f"wiki_reachable_{item.object_type}",
                "object_id": f"git/{item.object_type}/{item.object_id}",
                "byte_count": len(data),
                "content_sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    ref_payload = json.dumps(
        sorted(remote_refs.items()),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return json.dumps(
        {
            "schema_version": 1,
            "remote_ref_count": len(remote_refs),
            "remote_ref_digest": hashlib.sha256(ref_payload).hexdigest(),
            "reachable_object_count": inventory.object_count,
            "reachable_blob_count": inventory.blob_count,
            "payload_object_count": len(payload_objects),
            "payload_objects": payload_objects,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


class GhApiHostedClient:
    """Inspect the compiled GitHub route plan with authenticated ``gh api`` calls."""

    def __init__(
        self,
        *,
        executable: Path,
        command_runner: CommandRunner,
        repo: str,
        http_transport: HostedHttpTransport | None = None,
    ) -> None:
        self._executable = executable
        self._command_runner = command_runner
        self._repo = repo
        self._http_transport = http_transport

    def _query(
        self,
        name: str,
        route: str,
        policy: SecretScanPolicy,
        *,
        record_key: str | None = None,
        validator: Callable[[list[object]], bool] | None = None,
        exact_total_key: str | None = None,
    ) -> tuple[HostedReceipt, list[object]]:
        page_size = min(100, policy.max_hosted_page_records, policy.max_hosted_records)
        records: list[object] = []
        chunks: list[bytes] = []
        exit_codes: list[int] = []
        complete = True
        page = 1
        proven_total: int | None = None
        while True:
            if page > policy.max_hosted_pages:
                complete = False
                break
            used_bytes = sum(len(chunk) for chunk in chunks)
            remaining_bytes = policy.max_hosted_receipt_bytes - used_bytes
            if remaining_bytes <= 0:
                complete = False
                break
            page_route = _github_page_route(
                route,
                page=page,
                per_page=page_size,
            )
            try:
                result = self._command_runner.run(
                    (str(self._executable), "api", page_route),
                    max_output_bytes=remaining_bytes,
                    timeout_seconds=300.0,
                )
            except Exception:
                result = CommandResult(exit_code=127, stdout=b"", stderr=b"")
            exit_codes.append(result.exit_code)
            chunks.extend(_command_chunks(result))
            if result.exit_code != 0:
                complete = False
                break
            try:
                payload = json.loads(result.stdout)
                page_records = _page_records(payload, record_key)
                if page_records is None or len(page_records) > page_size:
                    raise ValueError
                if exact_total_key is not None:
                    if not isinstance(payload, dict):
                        raise ValueError
                    total = payload.get(exact_total_key)
                    if (
                        isinstance(total, bool)
                        or not isinstance(total, int)
                        or total < 0
                        or total > policy.max_hosted_records
                    ):
                        raise ValueError
                    if proven_total is None:
                        proven_total = total
                    elif total != proven_total:
                        raise ValueError
            except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                complete = False
                break
            records.extend(page_records)
            if len(records) > policy.max_hosted_records:
                complete = False
                break
            if validator is not None and not validator(records):
                complete = False
                break
            if exact_total_key is not None:
                assert proven_total is not None
                if len(records) == proven_total:
                    break
                if len(records) > proven_total or len(page_records) < page_size:
                    complete = False
                    break
                page += 1
                continue
            if len(page_records) < page_size:
                break
            page += 1
        if exact_total_key is not None and (
            proven_total is None or len(records) != proven_total
        ):
            complete = False
        if sum(len(chunk) for chunk in chunks) > policy.max_hosted_receipt_bytes:
            complete = False
        receipt = HostedReceipt(
            name=name,
            chunks=tuple(chunks),
            complete=complete,
            page_count=max(1, len(exit_codes)),
            record_count=len(records),
            request_count=1,
            exit_codes=tuple(exit_codes),
        )
        return (
            receipt,
            records if complete else [],
        )

    def _merge(
        self,
        name: str,
        receipts: Iterable[HostedReceipt],
        *,
        parent_record_count: int | None = None,
    ) -> HostedReceipt:
        material = tuple(receipts)
        if not material:
            return HostedReceipt(
                name=name,
                object_boundaries=_object_boundaries_for_chunks(name, ()),
                complete=False,
                page_count=1,
                request_count=0,
                parent_record_count=parent_record_count,
            )
        request_count = sum(receipt.request_count for receipt in material)
        return HostedReceipt(
            name=name,
            chunks=tuple(chunk for receipt in material for chunk in receipt.chunks),
            object_boundaries=_merged_object_boundaries(name, material),
            complete=(
                all(receipt.complete for receipt in material)
                and (
                    parent_record_count is None
                    or request_count == parent_record_count
                )
            ),
            page_count=sum(receipt.page_count for receipt in material),
            record_count=sum(receipt.record_count for receipt in material),
            request_count=request_count,
            parent_record_count=parent_record_count,
            exit_codes=tuple(
                code for receipt in material for code in receipt.exit_codes
            ),
            status_codes=tuple(
                code for receipt in material for code in receipt.status_codes
            ),
            findings=_deduplicate_findings(
                [finding for receipt in material for finding in receipt.findings]
            ),
        )

    def _derived_empty(
        self,
        name: str,
        proof: HostedReceipt,
        *,
        parent_record_count: int = 0,
    ) -> HostedReceipt:
        return HostedReceipt(
            name=name,
            object_boundaries=_object_boundaries_for_chunks(name, ()),
            complete=proof.complete and parent_record_count == 0,
            page_count=max(1, proof.page_count),
            record_count=0,
            request_count=0,
            parent_record_count=parent_record_count,
            exit_codes=proof.exit_codes,
            status_codes=proof.status_codes,
        )

    def _unavailable_content(self, name: str, proof: HostedReceipt) -> HostedReceipt:
        if proof.record_count == 0:
            return self._derived_empty(name, proof)
        return HostedReceipt(
            name=name,
            object_boundaries=_object_boundaries_for_chunks(name, ()),
            complete=False,
            page_count=1,
            record_count=proof.record_count,
            request_count=0,
            parent_record_count=proof.record_count,
            exit_codes=proof.exit_codes,
        )

    def _download(
        self,
        name: str,
        routes: Iterable[str],
        proof: HostedReceipt,
        policy: SecretScanPolicy,
    ) -> HostedReceipt:
        route_list = tuple(routes)
        if not route_list:
            return self._derived_empty(
                name,
                proof,
                parent_record_count=proof.record_count,
            )
        receipts: list[HostedReceipt] = []
        used_bytes = 0
        for route in route_list:
            remaining_bytes = policy.max_hosted_receipt_bytes - used_bytes
            if remaining_bytes <= 0:
                break
            try:
                result = self._command_runner.run(
                    (
                        str(self._executable),
                        "api",
                        route,
                        "-H",
                        "Accept: application/octet-stream",
                    ),
                    max_output_bytes=remaining_bytes,
                    timeout_seconds=300.0,
                )
            except Exception:
                result = CommandResult(exit_code=127, stdout=b"", stderr=b"")
            result_chunks = _command_chunks(result)
            used_bytes += sum(len(chunk) for chunk in result_chunks)
            object_boundaries = (
                (
                    _logical_object_boundary(
                        result_chunks,
                        object_type="hosted_command_output",
                        object_id=f"{name}/{len(receipts)}",
                    ),
                )
                if result_chunks
                else ()
            )
            receipts.append(
                HostedReceipt(
                    name=name,
                    chunks=result_chunks,
                    object_boundaries=object_boundaries,
                    complete=result.exit_code == 0,
                    page_count=1,
                    record_count=1,
                    request_count=1,
                    exit_codes=(result.exit_code,),
                )
            )
        merged = self._merge(
            name,
            receipts,
            parent_record_count=proof.record_count,
        )
        return replace(
            merged,
            complete=(
                merged.complete
                and proof.complete
                and len(route_list) == len(set(route_list)) == proof.record_count
                and used_bytes <= policy.max_hosted_receipt_bytes
            ),
        )

    def _inspect_pull_refs(self, policy: SecretScanPolicy) -> HostedInspection:
        receipt, _records = self._query(
            "github_pr_refs_query",
            f"repos/{self._repo}/git/matching-refs/pull/?per_page=100",
            policy,
            validator=_valid_pull_ref_records,
        )
        return HostedInspection((receipt,), HOSTED_SCANNER_VERSION)

    def _inspect_releases(self, policy: SecretScanPolicy) -> HostedInspection:
        releases, release_records = self._query(
            "github_releases_query",
            f"repos/{self._repo}/releases?per_page=100",
            policy,
            validator=_valid_id_records,
        )
        release_ids = [record["id"] for record in release_records]  # type: ignore[index]
        asset_queries: list[HostedReceipt] = []
        asset_records: list[object] = []
        for release_id in release_ids:
            receipt, records = self._query(
                "github_release_assets_query",
                f"repos/{self._repo}/releases/{release_id}/assets?per_page=100",
                policy,
                validator=_valid_id_records,
            )
            asset_queries.append(receipt)
            asset_records.extend(records)
        assets = (
            self._merge(
                "github_release_assets_query",
                asset_queries,
                parent_record_count=releases.record_count,
            )
            if asset_queries
            else self._derived_empty(
                "github_release_assets_query",
                releases,
                parent_record_count=releases.record_count,
            )
        )
        if len({record["id"] for record in asset_records}) != len(asset_records):  # type: ignore[index]
            assets = replace(assets, complete=False)
            asset_records = []
        asset_ids = [record["id"] for record in asset_records]  # type: ignore[index]
        downloads = self._download(
            "github_release_assets_download",
            (
                f"repos/{self._repo}/releases/assets/{asset_id}"
                for asset_id in asset_ids
            ),
            assets,
            policy,
        )
        return HostedInspection((releases, assets, downloads), HOSTED_SCANNER_VERSION)

    def _inspect_actions(self, policy: SecretScanPolicy) -> HostedInspection:
        runs, run_records = self._query(
            "github_actions_runs_query",
            f"repos/{self._repo}/actions/runs?per_page=100",
            policy,
            record_key="workflow_runs",
            validator=_valid_id_records,
            exact_total_key="total_count",
        )
        run_ids = [record["id"] for record in run_records]  # type: ignore[index]
        logs = self._download(
            "github_actions_logs_download",
            (f"repos/{self._repo}/actions/runs/{run_id}/logs" for run_id in run_ids),
            runs,
            policy,
        )
        artifacts, artifact_records = self._query(
            "github_actions_artifacts_query",
            f"repos/{self._repo}/actions/artifacts?per_page=100",
            policy,
            record_key="artifacts",
            validator=_valid_id_records,
            exact_total_key="total_count",
        )
        artifact_ids = [record["id"] for record in artifact_records]  # type: ignore[index]
        artifact_downloads = self._download(
            "github_actions_artifacts_download",
            (
                f"repos/{self._repo}/actions/artifacts/{artifact_id}/zip"
                for artifact_id in artifact_ids
            ),
            artifacts,
            policy,
        )
        caches, _cache_records = self._query(
            "github_actions_caches_query",
            f"repos/{self._repo}/actions/caches?per_page=100",
            policy,
            record_key="actions_caches",
            validator=_valid_id_records,
            exact_total_key="total_count",
        )
        cache_content = self._unavailable_content(
            "github_actions_caches_content",
            caches,
        )
        return HostedInspection(
            (runs, logs, artifacts, artifact_downloads, caches, cache_content),
            HOSTED_SCANNER_VERSION,
        )

    def _inspect_packages_pages_wiki(self, policy: SecretScanPolicy) -> HostedInspection:
        owner = self._repo.split("/", 1)[0]
        package_queries: list[HostedReceipt] = []
        package_records: list[object] = []
        for package_type in _PACKAGE_TYPES:
            receipt, records = self._query(
                "github_packages_query",
                f"users/{owner}/packages?package_type={package_type}&per_page=100",
                policy,
                validator=lambda records, expected=package_type: _valid_package_records(
                    records,
                    expected,
                ),
            )
            package_queries.append(receipt)
            package_records.extend(records)
        packages = self._merge("github_packages_query", package_queries)
        package_keys = [
            (record["package_type"], record["name"])  # type: ignore[index]
            for record in package_records
        ]
        if len(package_keys) != len(set(package_keys)):
            packages = replace(packages, complete=False)
            package_records = []
        version_queries: list[HostedReceipt] = []
        version_records: list[object] = []
        for record in package_records:
            package_name = record["name"]  # type: ignore[index]
            package_type = record["package_type"]  # type: ignore[index]
            receipt, records = self._query(
                "github_package_versions_query",
                f"users/{owner}/packages/{package_type}/{quote(package_name, safe='')}/versions"
                "?per_page=100",
                policy,
                validator=_valid_id_records,
            )
            version_queries.append(receipt)
            version_records.extend(records)
        versions = (
            self._merge(
                "github_package_versions_query",
                version_queries,
                parent_record_count=packages.record_count,
            )
            if version_queries
            else self._derived_empty(
                "github_package_versions_query",
                packages,
                parent_record_count=packages.record_count,
            )
        )
        if len({record["id"] for record in version_records}) != len(version_records):  # type: ignore[index]
            versions = replace(versions, complete=False)
        version_content = self._unavailable_content(
            "github_package_versions_content",
            versions,
        )
        metadata, metadata_records = self._query(
            "github_pages_query",
            f"repos/{self._repo}",
            policy,
            validator=_valid_repository_metadata,
        )
        repository = metadata_records[0] if metadata_records else {}
        has_pages = isinstance(repository, dict) and repository.get("has_pages") is True
        has_wiki = isinstance(repository, dict) and repository.get("has_wiki") is True
        if has_pages:
            pages, page_records = self._query(
                "github_pages_query",
                f"repos/{self._repo}/pages",
                policy,
                validator=_valid_pages_records,
            )
            if len(page_records) != 1:
                pages = replace(pages, complete=False)
            pages = replace(
                pages,
                chunks=(*metadata.chunks, *pages.chunks),
                complete=metadata.complete and pages.complete,
                page_count=metadata.page_count + pages.page_count,
                record_count=len(page_records),
                request_count=metadata.request_count + pages.request_count,
                exit_codes=(*metadata.exit_codes, *pages.exit_codes),
            )
            page_download = self._unavailable_content("github_pages_download", pages)
        else:
            pages = HostedReceipt(
                name="github_pages_query",
                chunks=metadata.chunks,
                complete=metadata.complete,
                page_count=metadata.page_count,
                record_count=0,
                request_count=metadata.request_count,
                exit_codes=metadata.exit_codes,
            )
            page_download = self._derived_empty("github_pages_download", pages)
        if has_wiki:
            wiki_query, wiki_download = self._wiki_receipts(policy)
        else:
            wiki_query = self._derived_empty("github_wiki_query", metadata)
            wiki_download = self._derived_empty("github_wiki_download", wiki_query)
        return HostedInspection(
            (
                packages,
                versions,
                version_content,
                pages,
                page_download,
                wiki_query,
                wiki_download,
            ),
            HOSTED_SCANNER_VERSION,
        )

    def _wiki_receipts(
        self,
        policy: SecretScanPolicy,
    ) -> tuple[HostedReceipt, HostedReceipt]:
        wiki_url = f"https://github.com/{self._repo}.wiki.git"
        try:
            query_result = self._command_runner.run(
                ("git", "ls-remote", wiki_url),
                max_output_bytes=policy.max_hosted_receipt_bytes,
                timeout_seconds=300.0,
            )
        except Exception:
            query_result = CommandResult(127, b"", b"")
        try:
            remote_refs = _parse_git_ref_map(query_result.stdout)
        except (UnicodeError, ValueError):
            remote_refs = {}
        query = HostedReceipt(
            name="github_wiki_query",
            chunks=_command_chunks(query_result),
            complete=query_result.exit_code == 0 and bool(remote_refs),
            page_count=1,
            record_count=1,
            request_count=1,
            exit_codes=(query_result.exit_code,),
        )
        if not query.complete:
            return query, self._derived_empty(
                "github_wiki_download",
                query,
                parent_record_count=1,
            )
        chunks: list[bytes] = []
        object_boundaries: list[HostedObjectBoundary] = []
        exit_codes: list[int] = []
        inventory_findings: tuple[SecretFinding, ...] = ()
        complete = True

        def append_logical_object(
            object_chunks: tuple[bytes, ...],
            *,
            object_type: str,
            object_id: str,
        ) -> None:
            if not object_chunks:
                return
            chunks.extend(object_chunks)
            object_boundaries.append(
                _logical_object_boundary(
                    object_chunks,
                    object_type=object_type,
                    object_id=object_id,
                )
            )

        def run_command(
            argv: tuple[str, ...],
            *,
            identity: str,
            cwd: Path | None = None,
        ) -> CommandResult:
            remaining = policy.max_hosted_receipt_bytes - sum(map(len, chunks))
            if remaining <= 0:
                return CommandResult(127, b"", b"")
            try:
                result = self._command_runner.run(
                    argv,
                    cwd=cwd,
                    max_output_bytes=remaining,
                    timeout_seconds=300.0,
                )
            except Exception:
                result = CommandResult(127, b"", b"")
            command_chunks = _command_chunks(result) or (b"",)
            append_logical_object(
                command_chunks,
                object_type="wiki_command_output",
                object_id=f"wiki/command/{identity}",
            )
            exit_codes.append(result.exit_code)
            return result

        with tempfile.TemporaryDirectory(prefix="mercury-wiki-scan-") as temporary:
            clone = Path(temporary) / "wiki.git"
            clone_result = run_command(
                ("git", "clone", "--mirror", wiki_url, str(clone)),
                identity="clone",
            )
            complete = clone_result.exit_code == 0
            local_refs: dict[str, str] = {}
            if complete:
                refs_result = run_command(
                    (
                        "git",
                        "for-each-ref",
                        "--format=%(refname)%09%(objectname)%09%(*objectname)",
                    ),
                    identity="refs-initial",
                    cwd=clone,
                )
                head_result = run_command(
                    ("git", "rev-parse", "--verify", "HEAD"),
                    identity="head-initial",
                    cwd=clone,
                )
                try:
                    local_refs = _parse_local_refs(refs_result.stdout)
                    local_map = {
                        "HEAD": _parse_head_oid(head_result.stdout),
                        **local_refs,
                    }
                except Exception:
                    local_map = {}
                complete = (
                    refs_result.exit_code == 0
                    and head_result.exit_code == 0
                    and local_map == remote_refs
                )
            inventory_evidence: list[str] = []
            inventory_exit_codes: list[int] = []
            inventory_blockers: list[str] = []
            inventory = None
            if complete:
                inventory = _scan_reachable_blobs(
                    self._command_runner,
                    clone,
                    policy,
                    ref_oids=local_refs.values(),
                    evidence_hashes=inventory_evidence,
                    exit_codes=inventory_exit_codes,
                    blockers=inventory_blockers,
                )
                exit_codes.extend(inventory_exit_codes)
                complete = inventory is not None and not inventory_blockers
            if complete and inventory is not None:
                inventory_findings = inventory.findings
                final_refs_result = run_command(
                    (
                        "git",
                        "for-each-ref",
                        "--format=%(refname)%09%(objectname)%09%(*objectname)",
                    ),
                    identity="refs-final",
                    cwd=clone,
                )
                final_head_result = run_command(
                    ("git", "rev-parse", "--verify", "HEAD"),
                    identity="head-final",
                    cwd=clone,
                )
                try:
                    final_map = {
                        "HEAD": _parse_head_oid(final_head_result.stdout),
                        **_parse_local_refs(final_refs_result.stdout),
                    }
                except Exception:
                    final_map = {}
                complete = (
                    final_refs_result.exit_code == 0
                    and final_head_result.exit_code == 0
                    and final_map == remote_refs
                )
                if complete:
                    try:
                        manifest = _wiki_inventory_manifest(inventory, remote_refs)
                    except (TypeError, ValueError):
                        complete = False
                    else:
                        append_logical_object(
                            (manifest,),
                            object_type="wiki_mirror_inventory",
                            object_id=_WIKI_INVENTORY_OBJECT_ID,
                        )
                        for item in inventory.public_objects:
                            append_logical_object(
                                (item.data,),
                                object_type=f"wiki_reachable_{item.object_type}",
                                object_id=f"git/{item.object_type}/{item.object_id}",
                            )
        download = HostedReceipt(
            name="github_wiki_download",
            chunks=tuple(chunks),
            object_boundaries=tuple(object_boundaries),
            expected_object_count=len(object_boundaries),
            complete=complete,
            page_count=1,
            record_count=1,
            request_count=1,
            parent_record_count=1,
            exit_codes=tuple(exit_codes),
            findings=inventory_findings,
        )
        if sum(len(chunk) for chunk in download.chunks) > policy.max_hosted_receipt_bytes:
            download = replace(download, complete=False)
        return query, download

    def inspect(self, surface: str, policy: SecretScanPolicy) -> HostedInspection:
        if surface == "github_pull_request_refs":
            return self._inspect_pull_refs(policy)
        if surface == "github_releases_and_assets":
            return self._inspect_releases(policy)
        if surface == "github_actions_logs_artifacts_caches":
            return self._inspect_actions(policy)
        if surface == "github_packages_pages_wiki":
            return self._inspect_packages_pages_wiki(policy)
        return HostedInspection((), HOSTED_SCANNER_VERSION)


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == target), None)


def _content_range_total(value: str | None, *, offset: int, count: int) -> int | None:
    if value == "*/0":
        return 0 if offset == 0 and count == 0 else None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([0-9]+)-([0-9]+)/([0-9]+)", value)
    if match is None:
        return None
    start, end, total = (int(part) for part in match.groups())
    if (
        total <= 0
        or start != offset
        or end < start
        or end >= total
        or end - start + 1 != count
    ):
        return None
    return total


def _valid_session_id(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(character in value for character in "\r\n\0")
    ):
        return None
    return value


def _decode_mcp_json(body: bytes) -> dict[str, object]:
    try:
        payload = json.loads(body)
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        return payload
    try:
        stream = body.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("invalid_mcp_response") from error
    stream = stream.replace("\r\n", "\n")
    if "\r" in stream or not stream.endswith("\n\n"):
        raise ValueError("invalid_mcp_response")
    events = stream[:-2].split("\n\n")
    if len(events) != 1 or not events[0]:
        raise ValueError("invalid_mcp_response")
    event_type: str | None = None
    event_data: list[str] = []
    for line in events[0].split("\n"):
        if line.startswith("event:"):
            if event_type is not None:
                raise ValueError("invalid_mcp_response")
            event_type = line[6:].lstrip(" ")
            continue
        if line.startswith("data:"):
            event_data.append(line[5:].lstrip(" "))
            continue
        raise ValueError("invalid_mcp_response")
    if event_type not in {None, "message"} or not event_data:
        raise ValueError("invalid_mcp_response")
    try:
        payload = json.loads("\n".join(event_data))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid_mcp_response") from error
    if not isinstance(payload, dict):
        raise ValueError("invalid_mcp_response")
    return payload


def _mcp_result(body: bytes, *, request_id: int) -> dict[str, object]:
    payload = _decode_mcp_json(body)
    response_id = payload.get("id")
    if (
        payload.get("jsonrpc") != "2.0"
        or isinstance(response_id, bool)
        or response_id != request_id
        or "error" in payload
        or not isinstance(payload.get("result"), dict)
    ):
        raise ValueError("invalid_mcp_response")
    return payload["result"]  # type: ignore[return-value]


def _validate_mcp_initialize_result(result: dict[str, object]) -> InitializeResult:
    parsed = InitializeResult.model_validate(result)
    protocol_version = parsed.protocolVersion
    server_info = parsed.serverInfo
    if (
        not isinstance(protocol_version, str)
        or protocol_version not in _SUPPORTED_MCP_PROTOCOL_VERSIONS
        or parsed.capabilities.tools is None
        or not server_info.name.strip()
        or not server_info.version.strip()
        or len(server_info.name) > 255
        or len(server_info.version) > 255
        or any(
            character in value
            for value in (server_info.name, server_info.version)
            for character in "\r\n\0"
        )
    ):
        raise ValueError("invalid_mcp_initialize_result")
    return parsed


@functools.lru_cache(maxsize=1)
def _compiled_public_mcp_inventory() -> Mapping[str, dict[str, object]]:
    from mcp.types import Tool as McpTool

    from mercury_tools.mcp.server import mcp as public_mcp

    inventory: dict[str, dict[str, object]] = {}
    for tool in public_mcp._tool_manager.list_tools():
        record = {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "inputSchema": tool.parameters,
            "outputSchema": tool.output_schema,
            "annotations": tool.annotations,
            "icons": tool.icons,
            "_meta": tool.meta,
        }
        normalized = McpTool.model_validate(record).model_dump(
            by_alias=True,
            exclude_none=True,
        )
        name = normalized.get("name")
        if not isinstance(name, str) or name in inventory:
            raise ValueError("public_mcp_inventory_invalid")
        inventory[name] = normalized
    if not inventory:
        raise ValueError("public_mcp_inventory_invalid")
    return MappingProxyType(inventory)


def _normalize_mcp_tool(record: object) -> tuple[str, dict[str, object]]:
    from mcp.types import Tool as McpTool

    if not isinstance(record, dict):
        raise ValueError("invalid_mcp_tool")
    normalized = McpTool.model_validate(record).model_dump(
        by_alias=True,
        exclude_none=True,
    )
    name = normalized.get("name")
    if not isinstance(name, str) or normalized != record:
        raise ValueError("invalid_mcp_tool")
    return name, normalized


def _mcp_session_header_matches(
    headers: Mapping[str, str],
    session_id: str | None,
) -> bool:
    value = _header_value(headers, "mcp-session-id")
    return value is None or (session_id is not None and value == session_id)


def _next_link(headers: Mapping[str, str], current_url: str) -> str | None:
    link = _header_value(headers, "link")
    if not link:
        return None
    for item in link.split(","):
        section, *parameters = item.split(";")
        if any(parameter.strip() == 'rel="next"' for parameter in parameters):
            candidate = section.strip()
            if candidate.startswith("<") and candidate.endswith(">"):
                return urljoin(current_url, candidate[1:-1])
    return None


def _same_origin(left: str, right: str) -> bool:
    first = urlsplit(left)
    second = urlsplit(right)
    return (
        first.scheme == second.scheme
        and first.scheme in {"http", "https"}
        and first.netloc == second.netloc
    )


def _cursor_url(current_url: str, cursor: object) -> str | None:
    if (
        not isinstance(cursor, str)
        or not cursor
        or len(cursor) > 1024
        or any(character in cursor for character in "\r\n\0")
    ):
        return None
    parsed = urlsplit(current_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "cursor"
    ]
    query.append(("cursor", cursor))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _count_json_records(payload: object) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("logs", "items", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        return 1
    raise ValueError


def _http_receipt(
    name: str,
    transport: HostedHttpTransport,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    policy: SecretScanPolicy,
    json_body: object | None = None,
    expect_json: bool = True,
) -> HostedReceipt:
    current_url: str | None = url
    seen: set[str] = set()
    chunks: list[bytes] = []
    statuses: list[int] = []
    record_count = 0
    complete = True
    while current_url is not None:
        if current_url in seen or len(statuses) >= policy.max_hosted_pages:
            complete = False
            break
        remaining_bytes = policy.max_hosted_receipt_bytes - sum(
            len(chunk) for chunk in chunks
        )
        if remaining_bytes <= 0:
            complete = False
            break
        seen.add(current_url)
        response = transport.request(
            method,
            current_url,
            headers=headers,
            json_body=json_body,
            max_bytes=remaining_bytes + 1,
        )
        statuses.append(response.status_code)
        if not isinstance(response.body, bytes):
            complete = False
            break
        chunks.append(response.body)
        if not 200 <= response.status_code < 300:
            complete = False
        payload: object | None = None
        if expect_json:
            try:
                payload = json.loads(response.body)
                page_record_count = _count_json_records(payload)
                if page_record_count > policy.max_hosted_page_records:
                    complete = False
                record_count += page_record_count
            except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                complete = False
        else:
            record_count += 1
        if not complete:
            break
        next_url = _next_link(response.headers, current_url)
        if next_url is None and isinstance(payload, dict) and "nextCursor" in payload:
            next_url = _cursor_url(current_url, payload.get("nextCursor"))
            if next_url is None:
                complete = False
                break
        if next_url is not None and not _same_origin(url, next_url):
            complete = False
            break
        current_url = next_url
    if (
        sum(len(chunk) for chunk in chunks) > policy.max_hosted_receipt_bytes
        or record_count > policy.max_hosted_records
    ):
        complete = False
    return HostedReceipt(
        name=name,
        chunks=tuple(chunks),
        object_boundaries=_object_boundaries_for_chunks(name, chunks),
        complete=complete,
        page_count=max(1, len(statuses)),
        record_count=record_count,
        status_codes=tuple(statuses),
    )


def _single_http_receipt(
    name: str,
    transport: HostedHttpTransport,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    policy: SecretScanPolicy,
    json_body: object | None = None,
    expect_json: bool = True,
) -> HostedReceipt:
    return _http_receipt(
        name,
        transport,
        method,
        url,
        headers=headers,
        policy=policy,
        json_body=json_body,
        expect_json=expect_json,
    )


def _merge_receipts(
    name: str,
    receipts: Iterable[HostedReceipt],
    *,
    parent_record_count: int | None = None,
) -> HostedReceipt:
    material = tuple(receipts)
    if not material:
        return HostedReceipt(
            name=name,
            object_boundaries=_object_boundaries_for_chunks(name, ()),
            complete=False,
            request_count=0,
            parent_record_count=parent_record_count,
        )
    request_count = sum(receipt.request_count for receipt in material)
    return HostedReceipt(
        name=name,
        chunks=tuple(chunk for receipt in material for chunk in receipt.chunks),
        object_boundaries=_merged_object_boundaries(name, material),
        complete=(
            all(receipt.complete for receipt in material)
            and (
                parent_record_count is None
                or request_count == parent_record_count
            )
        ),
        page_count=sum(receipt.page_count for receipt in material),
        record_count=sum(receipt.record_count for receipt in material),
        request_count=request_count,
        parent_record_count=parent_record_count,
        exit_codes=tuple(code for receipt in material for code in receipt.exit_codes),
        status_codes=tuple(code for receipt in material for code in receipt.status_codes),
    )


def _derived_http_empty(name: str, proof: HostedReceipt) -> HostedReceipt:
    return HostedReceipt(
        name=name,
        object_boundaries=_object_boundaries_for_chunks(name, ()),
        complete=proof.complete and proof.record_count == 0,
        page_count=proof.page_count,
        record_count=0,
        request_count=0,
        parent_record_count=proof.record_count,
        exit_codes=proof.exit_codes,
        status_codes=proof.status_codes,
    )


class MarketplaceHostedClient:
    def __init__(self, *, snapshot_url: str, transport: HostedHttpTransport) -> None:
        self._snapshot_url = snapshot_url
        self._transport = transport

    def inspect(self, surface: str, policy: SecretScanPolicy) -> HostedInspection:
        if surface != "marketplace_snapshot":
            return HostedInspection((), HOSTED_SCANNER_VERSION)
        receipt = _http_receipt(
            "marketplace_snapshot_download",
            self._transport,
            "GET",
            self._snapshot_url,
            headers={},
            policy=policy,
        )
        names: list[str] = []
        records_valid = True
        try:
            for chunk in receipt.chunks:
                payload = json.loads(chunk)
                records = (
                    payload
                    if isinstance(payload, list)
                    else payload.get("items")
                    if isinstance(payload, dict)
                    else None
                )
                if not isinstance(records, list):
                    raise ValueError
                for record in records:
                    if not isinstance(record, dict):
                        raise ValueError
                    name = record.get("name")
                    if (
                        not isinstance(name, str)
                        or not name
                        or len(name) > 255
                        or any(character in name for character in "\r\n\0")
                    ):
                        raise ValueError
                    names.append(name)
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            records_valid = False
        if len(names) != len(set(names)):
            records_valid = False
        receipt = replace(receipt, complete=receipt.complete and records_valid)
        return HostedInspection((receipt,), HOSTED_SCANNER_VERSION)


class RenderHostedClient:
    def __init__(
        self,
        *,
        api_url: str,
        service_id: str,
        token: str,
        transport: HostedHttpTransport,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._service_id = service_id
        self._token = token
        self._transport = transport

    def inspect(self, surface: str, policy: SecretScanPolicy) -> HostedInspection:
        if surface != "render_build_and_runtime_logs":
            return HostedInspection((), HOSTED_SCANNER_VERSION)
        headers = {"Authorization": f"Bearer {self._token}"}
        receipts = tuple(
            _http_receipt(
                f"render_{log_type}_logs_query",
                self._transport,
                "GET",
                f"{self._api_url}/v1/logs?resource={quote(self._service_id)}"
                f"&type={log_type}",
                headers=headers,
                policy=policy,
            )
            for log_type in ("build", "runtime")
        )
        return HostedInspection(receipts, HOSTED_SCANNER_VERSION)


class SupabaseHostedClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_key: str,
        knowledge_tables: tuple[str, ...],
        storage_buckets: tuple[str, ...],
        transport: HostedHttpTransport,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_key = service_key
        self._knowledge_tables = knowledge_tables
        self._storage_buckets = storage_buckets
        self._transport = transport

    def _paginated_list(
        self,
        name: str,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        policy: SecretScanPolicy,
    ) -> tuple[HostedReceipt, list[object]]:
        page_size = min(policy.max_hosted_page_records, policy.max_hosted_records)
        chunks: list[bytes] = []
        statuses: list[int] = []
        records: list[object] = []
        offset = 0
        complete = True
        proven_total: int | None = None
        while True:
            if len(statuses) >= policy.max_hosted_pages:
                complete = False
                break
            page_headers = dict(headers)
            json_body: object | None = None
            page_headers["Range"] = f"{offset}-{offset + page_size - 1}"
            page_headers["Prefer"] = "count=exact"
            if method == "GET":
                pass
            else:
                json_body = {"limit": page_size, "offset": offset}
            remaining_bytes = policy.max_hosted_receipt_bytes - sum(
                len(chunk) for chunk in chunks
            )
            if remaining_bytes <= 0:
                complete = False
                break
            try:
                response = self._transport.request(
                    method,
                    url,
                    headers=page_headers,
                    json_body=json_body,
                    max_bytes=remaining_bytes + 1,
                )
                statuses.append(response.status_code)
                chunks.append(response.body)
                page = json.loads(response.body)
                if not isinstance(page, list):
                    raise ValueError
            except Exception:
                complete = False
                break
            if not 200 <= response.status_code < 300:
                complete = False
                break
            if len(page) > page_size:
                complete = False
                break
            page_total = _content_range_total(
                _header_value(response.headers, "content-range"),
                offset=offset,
                count=len(page),
            )
            if page_total is None:
                complete = False
                break
            if proven_total is None:
                proven_total = page_total
            elif page_total != proven_total:
                complete = False
                break
            if proven_total > policy.max_hosted_records:
                complete = False
                break
            records.extend(page)
            if (
                len(records) > policy.max_hosted_records
                or sum(len(chunk) for chunk in chunks) > policy.max_hosted_receipt_bytes
            ):
                complete = False
                break
            offset += len(page)
            if offset == proven_total:
                break
            if offset > proven_total or not page or len(page) < page_size:
                complete = False
                break
        if proven_total is None or len(records) != proven_total:
            complete = False
        return (
            HostedReceipt(
                name=name,
                chunks=tuple(chunks),
                complete=complete,
                page_count=max(1, len(statuses)),
                record_count=len(records),
                request_count=1,
                status_codes=tuple(statuses),
            ),
            records,
        )

    def inspect(self, surface: str, policy: SecretScanPolicy) -> HostedInspection:
        if surface != "supabase_knowledge_and_storage":
            return HostedInspection((), HOSTED_SCANNER_VERSION)
        headers = {
            "apikey": self._service_key,
            "Authorization": f"Bearer {self._service_key}",
        }
        knowledge_receipts = [
            self._paginated_list(
                "supabase_knowledge_query",
                "GET",
                f"{self._base_url}/rest/v1/{quote(table, safe='')}?select=*",
                headers=headers,
                policy=policy,
            )[0]
            for table in self._knowledge_tables
        ]
        knowledge = _merge_receipts("supabase_knowledge_query", knowledge_receipts)
        storage_queries: list[HostedReceipt] = []
        storage_objects: list[tuple[str, str]] = []
        storage_records_valid = True
        for bucket in self._storage_buckets:
            receipt, records = self._paginated_list(
                "supabase_storage_query",
                "POST",
                f"{self._base_url}/storage/v1/object/list/{quote(bucket, safe='')}",
                headers=headers,
                policy=policy,
            )
            storage_queries.append(receipt)
            names: list[str] = []
            for record in records:
                name = record.get("name") if isinstance(record, dict) else None
                candidate = PurePosixPath(name) if isinstance(name, str) else None
                if (
                    not isinstance(name, str)
                    or not name
                    or len(name) > 1024
                    or any(character in name for character in "\r\n\0")
                    or candidate is None
                    or candidate.is_absolute()
                    or any(part in {"", ".", ".."} for part in candidate.parts)
                ):
                    storage_records_valid = False
                    break
                names.append(name)
            if len(names) != len(records) or len(names) != len(set(names)):
                storage_records_valid = False
                storage_queries[-1] = HostedReceipt(
                    name=receipt.name,
                    chunks=receipt.chunks,
                    complete=False,
                    page_count=receipt.page_count,
                    record_count=receipt.record_count,
                    request_count=receipt.request_count,
                    status_codes=receipt.status_codes,
                )
            if storage_records_valid:
                storage_objects.extend((bucket, name) for name in names)
        storage = _merge_receipts("supabase_storage_query", storage_queries)
        if not storage_records_valid or len(storage_objects) != len(set(storage_objects)):
            storage = replace(storage, complete=False)
            storage_objects = []
        downloads = []
        if storage.complete:
            downloads = [
                _single_http_receipt(
                    "supabase_storage_download",
                    self._transport,
                    "GET",
                    f"{self._base_url}/storage/v1/object/authenticated/"
                    f"{quote(bucket, safe='')}/{quote(name, safe='/')}",
                    headers=headers,
                    policy=policy,
                    expect_json=False,
                )
                for bucket, name in storage_objects
            ]
        download = (
            _merge_receipts(
                "supabase_storage_download",
                downloads,
                parent_record_count=storage.record_count,
            )
            if downloads
            else _derived_http_empty("supabase_storage_download", storage)
        )
        return HostedInspection((knowledge, storage, download), HOSTED_SCANNER_VERSION)


class PublicMcpHostedClient:
    def __init__(
        self,
        *,
        endpoint: str,
        token: str | None,
        transport: HostedHttpTransport,
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._transport = transport

    def inspect(self, surface: str, policy: SecretScanPolicy) -> HostedInspection:
        if surface != "public_mcp_responses":
            return HostedInspection((), HOSTED_SCANNER_VERSION)
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        initialize_chunks: list[bytes] = []
        initialize_statuses: list[int] = []
        initialize_complete = False
        session_id: str | None = None
        try:
            response = self._transport.request(
                "POST",
                self._endpoint,
                headers=headers,
                json_body={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "mercury-release-secret-gate",
                            "version": HOSTED_SCANNER_VERSION,
                        },
                    },
                },
                max_bytes=policy.max_hosted_receipt_bytes + 1,
            )
            initialize_chunks.append(response.body)
            initialize_statuses.append(response.status_code)
            raw_session_id = _header_value(response.headers, "mcp-session-id")
            if raw_session_id is not None:
                session_id = _valid_session_id(raw_session_id)
                if session_id is None:
                    raise ValueError
            result = _mcp_result(response.body, request_id=1)
            _validate_mcp_initialize_result(result)
            initialize_complete = response.status_code == 200
        except Exception:
            initialize_complete = False
        if any(status != 200 for status in initialize_statuses):
            initialize_complete = False
        if sum(len(chunk) for chunk in initialize_chunks) > policy.max_hosted_receipt_bytes:
            initialize_complete = False
        initialize_receipt = HostedReceipt(
            name="public_mcp_initialize",
            chunks=tuple(initialize_chunks),
            complete=initialize_complete,
            page_count=max(1, len(initialize_statuses)),
            record_count=1 if initialize_complete else 0,
            request_count=1,
            status_codes=tuple(initialize_statuses),
        )

        request_headers = dict(headers)
        if session_id is not None:
            request_headers["Mcp-Session-Id"] = session_id
        initialized_chunks: list[bytes] = []
        initialized_statuses: list[int] = []
        initialized_complete = initialize_complete
        if initialize_complete:
            try:
                response = self._transport.request(
                    "POST",
                    self._endpoint,
                    headers=request_headers,
                    json_body={
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {},
                    },
                    max_bytes=policy.max_hosted_receipt_bytes + 1,
                )
                initialized_chunks.append(response.body)
                initialized_statuses.append(response.status_code)
                if (
                    response.status_code != 202
                    or response.body
                    or not _mcp_session_header_matches(response.headers, session_id)
                ):
                    initialized_complete = False
            except Exception:
                initialized_complete = False

        tools_chunks: list[bytes] = []
        tools_statuses: list[int] = []
        tools_by_name: dict[str, dict[str, object]] = {}
        tools_complete = initialize_complete and initialized_complete
        cursor: str | None = None
        seen_cursors: set[str] = set()
        try:
            expected_inventory = dict(_compiled_public_mcp_inventory())
        except Exception:
            expected_inventory = {}
            tools_complete = False
        while tools_complete:
            if len(tools_statuses) >= policy.max_hosted_pages:
                tools_complete = False
                break
            parameters: dict[str, str] = {}
            if cursor is not None:
                parameters["cursor"] = cursor
            request_id = len(tools_statuses) + 2
            remaining_bytes = policy.max_hosted_receipt_bytes - sum(
                len(chunk) for chunk in tools_chunks
            )
            if remaining_bytes <= 0:
                tools_complete = False
                break
            try:
                response = self._transport.request(
                    "POST",
                    self._endpoint,
                    headers=request_headers,
                    json_body={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/list",
                        "params": parameters,
                    },
                    max_bytes=remaining_bytes + 1,
                )
                tools_chunks.append(response.body)
                tools_statuses.append(response.status_code)
                if (
                    not 200 <= response.status_code < 300
                    or not _mcp_session_header_matches(response.headers, session_id)
                ):
                    raise ValueError
                result = _mcp_result(response.body, request_id=request_id)
                records = result.get("tools")
                if not isinstance(records, list):
                    raise ValueError
                if len(records) > policy.max_hosted_page_records:
                    raise ValueError
                for record in records:
                    name, normalized = _normalize_mcp_tool(record)
                    if name in tools_by_name:
                        raise ValueError
                    tools_by_name[name] = normalized
                if len(tools_by_name) > policy.max_hosted_records:
                    raise ValueError
                next_cursor = result.get("nextCursor")
                if next_cursor is None:
                    break
                if (
                    not isinstance(next_cursor, str)
                    or not next_cursor
                    or len(next_cursor) > 1024
                    or any(character in next_cursor for character in "\r\n\0")
                    or next_cursor in seen_cursors
                ):
                    raise ValueError
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            except Exception:
                tools_complete = False
                break
        if (
            sum(len(chunk) for chunk in tools_chunks) > policy.max_hosted_receipt_bytes
            or tools_by_name != expected_inventory
        ):
            tools_complete = False

        if session_id is not None:
            try:
                remaining_bytes = policy.max_hosted_receipt_bytes - sum(
                    len(chunk) for chunk in initialized_chunks
                )
                if remaining_bytes <= 0:
                    raise ValueError
                response = self._transport.request(
                    "DELETE",
                    self._endpoint,
                    headers=request_headers,
                    max_bytes=remaining_bytes + 1,
                )
                initialized_chunks.append(response.body)
                initialized_statuses.append(response.status_code)
                if (
                    not 200 <= response.status_code < 300
                    or response.body
                    or not _mcp_session_header_matches(response.headers, session_id)
                ):
                    initialized_complete = False
            except Exception:
                initialized_complete = False
        if sum(len(chunk) for chunk in initialized_chunks) > policy.max_hosted_receipt_bytes:
            initialized_complete = False

        initialized_receipt = HostedReceipt(
            name="public_mcp_response_stream",
            chunks=tuple(initialized_chunks),
            complete=initialized_complete,
            page_count=max(1, len(initialized_statuses)),
            record_count=0,
            request_count=len(initialized_statuses),
            status_codes=tuple(initialized_statuses),
        )
        tools_receipt = HostedReceipt(
            name="public_mcp_tools_list",
            chunks=tuple(tools_chunks),
            complete=tools_complete,
            page_count=max(1, len(tools_statuses)),
            record_count=len(tools_by_name),
            request_count=len(tools_statuses),
            status_codes=tuple(tools_statuses),
        )
        return HostedInspection(
            (initialize_receipt, tools_receipt, initialized_receipt),
            HOSTED_SCANNER_VERSION,
        )


def build_hosted_clients(
    config: HostedAdapterConfig,
    *,
    command_runner: CommandRunner | None = None,
    http_transport: HostedHttpTransport | None = None,
) -> dict[str, HostedSurfaceClient]:
    clients: dict[str, HostedSurfaceClient] = {}
    transport = http_transport or HttpxHostedTransport()
    if config.gh_executable is not None and config.github_token:
        runner = command_runner or SubprocessCommandRunner(
            environment={"GH_TOKEN": config.github_token}
        )
        github = GhApiHostedClient(
            executable=config.gh_executable,
            command_runner=runner,
            repo=config.repo,
            http_transport=transport,
        )
        for surface in HOSTED_PUBLIC_SURFACES[:4]:
            clients[surface] = github
    if config.marketplace_url:
        clients["marketplace_snapshot"] = MarketplaceHostedClient(
            snapshot_url=config.marketplace_url,
            transport=transport,
        )
    if config.render_api_url and config.render_service_id and config.render_token:
        clients["render_build_and_runtime_logs"] = RenderHostedClient(
            api_url=config.render_api_url,
            service_id=config.render_service_id,
            token=config.render_token,
            transport=transport,
        )
    if (
        config.supabase_url
        and config.supabase_key
        and config.supabase_knowledge_tables
        and config.supabase_storage_buckets
    ):
        clients["supabase_knowledge_and_storage"] = SupabaseHostedClient(
            base_url=config.supabase_url,
            service_key=config.supabase_key,
            knowledge_tables=config.supabase_knowledge_tables,
            storage_buckets=config.supabase_storage_buckets,
            transport=transport,
        )
    if config.public_mcp_url:
        clients["public_mcp_responses"] = PublicMcpHostedClient(
            endpoint=config.public_mcp_url,
            token=config.public_mcp_token,
            transport=transport,
        )
    return clients


def scan_hosted_surface(
    surface: str,
    client: HostedSurfaceClient,
    policy: SecretScanPolicy,
) -> HostedSurfaceScanResult:
    try:
        inspection = client.inspect(surface, policy)
    except Exception:
        return HostedSurfaceScanResult(
            surface=surface,
            scanner_version=None,
            blockers=(f"hosted_inspection_failed:{surface}",),
        )
    if not isinstance(inspection, HostedInspection) or not isinstance(
        inspection.receipts, tuple
    ):
        return HostedSurfaceScanResult(
            surface=surface,
            scanner_version=None,
            blockers=(f"hosted_inspection_malformed:{surface}",),
        )

    blockers: list[str] = []
    findings: list[SecretFinding] = []
    evidence_hashes: list[str] = []
    exit_codes: list[int] = []
    archive_budget = _HostedArchiveBudget(policy.max_archive_uncompressed_bytes)
    if not isinstance(inspection.scanner_version, str) or not _VERSION_PATTERN.fullmatch(
        inspection.scanner_version
    ):
        blockers.append(f"hosted_scanner_version_unverifiable:{surface}")
        scanner_version = None
    else:
        scanner_version = inspection.scanner_version
        if scanner_version != HOSTED_SCANNER_VERSION:
            blockers.append(f"hosted_scanner_version_unpinned:{surface}")

    expected = HOSTED_RECEIPT_INVENTORY.get(surface)
    receipt_names = tuple(
        receipt.name
        for receipt in inspection.receipts
        if isinstance(receipt, HostedReceipt)
    )
    if expected is None or receipt_names != expected or len(receipt_names) != len(
        inspection.receipts
    ):
        blockers.append(f"hosted_receipt_inventory_invalid:{surface}")
        return HostedSurfaceScanResult(
            surface=surface,
            scanner_version=scanner_version,
            blockers=tuple(sorted(set(blockers))),
        )

    total_bytes = 0
    total_records = 0
    for receipt in inspection.receipts:
        if not _RECEIPT_PATTERN.fullmatch(receipt.name):
            blockers.append(f"hosted_receipt_inventory_invalid:{surface}")
            continue
        valid_page_count = (
            not isinstance(receipt.page_count, bool)
            and isinstance(receipt.page_count, int)
            and receipt.page_count >= 1
        )
        if not valid_page_count:
            blockers.append(f"hosted_receipt_malformed:{surface}")
        valid_record_count = (
            not isinstance(receipt.record_count, bool)
            and isinstance(receipt.record_count, int)
            and receipt.record_count >= 0
        )
        if not valid_record_count:
            blockers.append(f"hosted_receipt_malformed:{surface}")
        valid_request_count = (
            not isinstance(receipt.request_count, bool)
            and isinstance(receipt.request_count, int)
            and receipt.request_count >= 0
        )
        valid_parent_count = receipt.parent_record_count is None or (
            not isinstance(receipt.parent_record_count, bool)
            and isinstance(receipt.parent_record_count, int)
            and receipt.parent_record_count >= 0
        )
        if not valid_request_count or not valid_parent_count:
            blockers.append(f"hosted_receipt_malformed:{surface}")
        elif (
            receipt.name in _PARENT_COUNT_RECEIPTS
            and receipt.parent_record_count is None
        ) or (
            receipt.parent_record_count is not None
            and receipt.request_count != receipt.parent_record_count
        ):
            blockers.append(f"hosted_receipt_reconciliation_failed:{surface}")
        if not isinstance(receipt.complete, bool):
            blockers.append(f"hosted_receipt_malformed:{surface}")
        valid_exit_codes = isinstance(receipt.exit_codes, tuple) and all(
            isinstance(code, int) and not isinstance(code, bool)
            for code in receipt.exit_codes
        )
        valid_status_codes = isinstance(receipt.status_codes, tuple) and all(
            isinstance(code, int) and not isinstance(code, bool)
            for code in receipt.status_codes
        )
        if not valid_exit_codes or not valid_status_codes:
            blockers.append(f"hosted_receipt_malformed:{surface}")
        valid_findings = isinstance(receipt.findings, tuple) and all(
            isinstance(finding, SecretFinding) for finding in receipt.findings
        )
        if not valid_findings:
            blockers.append(f"hosted_receipt_malformed:{surface}")
        else:
            findings.extend(receipt.findings)
        archive_capable = receipt.name in _ARCHIVE_CAPABLE_RECEIPTS
        valid_expected_object_count = not archive_capable or (
            isinstance(receipt.expected_object_count, int)
            and not isinstance(receipt.expected_object_count, bool)
            and 0 <= receipt.expected_object_count <= policy.max_hosted_records
        )
        archive_count_reconciled = valid_expected_object_count
        if archive_capable and valid_expected_object_count:
            declared_object_count = receipt.expected_object_count
            assert isinstance(declared_object_count, int)
            if receipt.name in _PAGE_BOUND_ARCHIVE_RECEIPTS:
                archive_count_reconciled = (
                    valid_page_count and declared_object_count == receipt.page_count
                )
            elif receipt.name in _REQUEST_BOUND_ARCHIVE_RECEIPTS:
                archive_count_reconciled = (
                    valid_request_count
                    and declared_object_count == receipt.request_count
                )
            if declared_object_count == 0:
                archive_count_reconciled = bool(
                    archive_count_reconciled
                    and receipt.complete is True
                    and valid_record_count
                    and receipt.record_count == 0
                    and valid_request_count
                    and receipt.request_count == 0
                    and valid_parent_count
                    and receipt.parent_record_count in {None, 0}
                )
        if (
            archive_capable
            and valid_page_count
            and valid_record_count
            and valid_request_count
            and valid_parent_count
            and isinstance(receipt.complete, bool)
            and (not valid_expected_object_count or not archive_count_reconciled)
        ):
            blockers.append(f"hosted_archive_boundary_invalid:{surface}")
        receipt_shape_valid = (
            valid_page_count
            and valid_record_count
            and valid_request_count
            and valid_parent_count
            and isinstance(receipt.complete, bool)
            and valid_exit_codes
            and valid_status_codes
            and valid_findings
            and valid_expected_object_count
            and archive_count_reconciled
        )
        if (
            receipt_shape_valid
            and receipt.name in _ARCHIVE_CAPABLE_RECEIPTS
            and receipt.object_boundaries is None
        ):
            blockers.append(f"hosted_archive_boundary_invalid:{surface}")
        if (
            valid_exit_codes
            and valid_status_codes
            and not receipt.exit_codes
            and not receipt.status_codes
        ):
            blockers.append(f"hosted_receipt_unproven:{surface}")
        if receipt.complete is False:
            blockers.append(f"hosted_receipt_incomplete:{surface}")
        if valid_exit_codes and any(code != 0 for code in receipt.exit_codes):
            blockers.append(f"hosted_command_failed:{surface}")
        if valid_status_codes and any(
            not 200 <= code < 300 for code in receipt.status_codes
        ):
            blockers.append(f"hosted_status_failed:{surface}")
        page_count = receipt.page_count if valid_page_count else 0
        record_count = receipt.record_count if valid_record_count else 0
        if page_count > policy.max_hosted_pages:
            blockers.append(f"hosted_page_limit:{surface}")
        if record_count > policy.max_hosted_records:
            blockers.append(f"hosted_record_limit:{surface}")
        total_records += record_count
        if total_records > policy.max_hosted_records:
            blockers.append(f"hosted_total_record_limit:{surface}")
        if valid_exit_codes:
            exit_codes.extend(receipt.exit_codes)
        evidence_hash = hashlib.sha256()
        evidence_hash.update(receipt.name.encode("ascii"))
        evidence_hash.update(str(page_count).encode("ascii"))
        evidence_hash.update(b"\0")
        evidence_hash.update(str(record_count).encode("ascii"))
        evidence_hash.update(b"\0")
        evidence_hash.update(
            str(receipt.request_count if valid_request_count else 0).encode("ascii")
        )
        evidence_hash.update(b"\0")
        evidence_hash.update(
            str(receipt.parent_record_count).encode("ascii")
            if valid_parent_count and receipt.parent_record_count is not None
            else b"none"
        )
        evidence_hash.update(b"\0expected-object-count\0")
        if archive_capable and valid_expected_object_count:
            assert isinstance(receipt.expected_object_count, int)
            evidence_hash.update(receipt.expected_object_count.to_bytes(8, "big"))
        elif archive_capable:
            evidence_hash.update(b"invalid")
        else:
            evidence_hash.update(b"none")
        evidence_hash.update(b"\0object-boundaries\0")
        if receipt.object_boundaries is None:
            evidence_hash.update(b"none")
        elif isinstance(receipt.object_boundaries, tuple):
            evidence_hash.update(len(receipt.object_boundaries).to_bytes(8, "big"))
            boundaries_for_evidence = (
                receipt.object_boundaries
                if len(receipt.object_boundaries) <= policy.max_hosted_records
                else ()
            )
            if not boundaries_for_evidence and receipt.object_boundaries:
                evidence_hash.update(b"invalid")
            for boundary in boundaries_for_evidence:
                if not isinstance(boundary, HostedObjectBoundary):
                    evidence_hash.update(b"invalid")
                    continue
                for value in (boundary.chunk_count, boundary.byte_count):
                    if (
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and 0 <= value < 2**64
                    ):
                        evidence_hash.update(value.to_bytes(8, "big"))
                    else:
                        evidence_hash.update(b"invalid")
                    evidence_hash.update(b"\0")
                for value in (
                    boundary.object_type,
                    boundary.object_id,
                    boundary.content_sha256,
                ):
                    if value is None:
                        evidence_hash.update(b"none")
                    elif isinstance(value, str):
                        try:
                            encoded = value.encode("ascii")
                        except UnicodeError:
                            evidence_hash.update(b"invalid")
                        else:
                            if len(encoded) <= 512:
                                evidence_hash.update(len(encoded).to_bytes(2, "big"))
                                evidence_hash.update(encoded)
                            else:
                                evidence_hash.update(b"invalid")
                    else:
                        evidence_hash.update(b"invalid")
                    evidence_hash.update(b"\0")
        else:
            evidence_hash.update(b"invalid")
        receipt_bytes = 0
        chunk_count = 0
        material_chunks: list[bytes] = []
        tail = b""
        stream_ok = True
        try:
            for chunk in receipt.chunks:
                if not isinstance(chunk, bytes):
                    stream_ok = False
                    break
                chunk_count += 1
                material_chunks.append(chunk)
                receipt_bytes += len(chunk)
                total_bytes += len(chunk)
                evidence_hash.update(len(chunk).to_bytes(8, "big"))
                evidence_hash.update(chunk)
                window = tail + chunk
                findings.extend(_scan_bytes(window, f"hosted/{surface}", policy))
                tail = window[-1024:]
        except Exception:
            stream_ok = False
        if not stream_ok:
            blockers.append(f"raw_evidence_handling_failed:{surface}")
            continue
        if record_count > 0 and chunk_count == 0:
            blockers.append(f"hosted_receipt_content_missing:{surface}")
        if receipt_bytes > policy.max_hosted_receipt_bytes:
            blockers.append(f"hosted_byte_limit:{surface}")
        if total_bytes > policy.max_hosted_total_bytes:
            blockers.append(f"hosted_total_byte_limit:{surface}")
        if (
            receipt_shape_valid
            and archive_capable
            and receipt.object_boundaries is not None
            and isinstance(receipt.expected_object_count, int)
            and not isinstance(receipt.expected_object_count, bool)
        ):
            archive_findings, archive_blockers = _scan_hosted_archive_objects(
                tuple(material_chunks),
                receipt.object_boundaries,
                surface,
                policy,
                archive_budget,
                expected_object_count=receipt.expected_object_count,
                receipt_name=receipt.name,
            )
            findings.extend(archive_findings)
            blockers.extend(archive_blockers)
        evidence_hashes.append(evidence_hash.hexdigest())

    return HostedSurfaceScanResult(
        surface=surface,
        scanner_version=scanner_version,
        findings=_deduplicate_findings(findings),
        blockers=tuple(sorted(set(blockers))),
        evidence_hashes=tuple(evidence_hashes),
        exit_codes=tuple(exit_codes),
    )
