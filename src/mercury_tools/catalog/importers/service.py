from __future__ import annotations

import errno
import json
import os
import queue
import stat
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ConfigDict
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken

from mercury_tools.catalog.importers.markdown import has_explicit_endpoints, parse_markdown
from mercury_tools.catalog.importers.openapi import (
    parse_openapi,
    security_driver_suggestion,
)
from mercury_tools.catalog.importers.postman import parse_postman
from mercury_tools.catalog.importers.sanitize import SanitizationReport, sanitize_spec
from mercury_tools.catalog.local_store import LocalCatalogStore
from mercury_tools.catalog.models import CatalogAction, CatalogSource
from mercury_tools.local.repository import RepositoryContext
from mercury_tools.safety.network import NetworkPolicy, NetworkPolicyError, ResolvedTarget

MAX_SPEC_BYTES = 10 * 1024 * 1024
REMOTE_IMPORT_DEADLINE_SECONDS = 20.0
_READ_CHUNK_BYTES = 64 * 1024
_YAML_MARKERS = {"info", "openapi", "swagger"}
_monotonic = time.monotonic


class ImportResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    source: CatalogSource
    actions: tuple[CatalogAction, ...]
    sanitization: SanitizationReport


def import_spec(
    context: RepositoryContext,
    *,
    connector_id: str,
    source_path: str | Path | None = None,
    source_url: str | None = None,
) -> ImportResult:
    if (source_path is None) == (source_url is None):
        raise ValueError("exactly_one_spec_source_required")
    raw, uri = _read_spec_source(
        context,
        source_path=source_path,
        source_url=source_url,
    )
    structured = _parse_structured_document(raw)
    if isinstance(structured, dict):
        source_format = _detect_structured_format(structured)
        raw_suggestion = (
            security_driver_suggestion(structured, swagger=source_format == 1)
            if source_format in {0, 1}
            else {}
        )
        sanitized, report = sanitize_spec(structured)
        if not isinstance(sanitized, dict):
            raise ValueError("spec_document_invalid")
        safe_suggestion, _ = sanitize_spec(raw_suggestion)
        suggestion = safe_suggestion if isinstance(safe_suggestion, dict) else {}
        source = _build_source(uri, connector_id, sanitized, report, suggestion)
        if source_format in {0, 1}:
            actions = parse_openapi(
                sanitized,
                source,
                connector_id,
                swagger=source_format == 1,
            )
        else:
            actions = parse_postman(sanitized, source, connector_id)
    else:
        if not has_explicit_endpoints(raw):
            raise ValueError("unknown_spec_format")
        sanitized, report = sanitize_spec({"version": "unknown", "content": raw})
        if not isinstance(sanitized, dict) or not isinstance(sanitized.get("content"), str):
            raise ValueError("spec_document_invalid")
        source = _build_source(uri, connector_id, sanitized, report, {})
        actions = parse_markdown(sanitized["content"], source, connector_id)

    LocalCatalogStore(context).write_import(source, actions)
    return ImportResult(source=source, actions=tuple(actions), sanitization=report)


def _build_source(
    uri: str,
    connector_id: str,
    document: dict[str, Any],
    report: SanitizationReport,
    driver_suggestion: dict[str, Any],
) -> CatalogSource:
    base = CatalogSource.from_document(
        uri=uri,
        connector_id=connector_id,
        document=document,
        report=report.model_dump(mode="json"),
    )
    if not driver_suggestion:
        return base
    values = base.model_dump(mode="python")
    values["driver_suggestion"] = driver_suggestion
    return CatalogSource.model_validate(values)


def _detect_structured_format(document: dict[str, Any]) -> int:
    info = document.get("info")
    postman_schema = info.get("schema") if isinstance(info, Mapping) else None
    markers = (
        isinstance(document.get("openapi"), str)
        and document["openapi"].startswith("3."),
        document.get("swagger") == "2.0",
        isinstance(postman_schema, str)
        and "/v2.1.0/" in postman_schema.casefold(),
    )
    for index, present in enumerate(markers):
        if present:
            return index
    raise ValueError("unknown_spec_format")


def _parse_structured_document(text: str) -> dict[str, Any] | None:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            value = json.loads(text, object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("spec_document_invalid") from None
        return value if isinstance(value, dict) else None
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        if _contains_yaml_marker_line(text):
            raise ValueError("spec_document_invalid") from None
        return None
    if not _yaml_root_has_marker(root):
        return None
    try:
        if any(isinstance(token, AliasToken) for token in yaml.scan(text)):
            raise ValueError("yaml_alias_forbidden")
        _validate_yaml_nodes(root)
        value = yaml.safe_load(text)
    except (TypeError, ValueError, yaml.YAMLError):
        raise ValueError("spec_document_invalid") from None
    return value if isinstance(value, dict) else None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _yaml_root_has_marker(node: Node | None) -> bool:
    if not isinstance(node, MappingNode):
        return False
    return any(
        isinstance(key_node, ScalarNode)
        and key_node.value.strip().casefold() in _YAML_MARKERS
        for key_node, _ in node.value
    )


def _contains_yaml_marker_line(text: str) -> bool:
    markers = tuple(f"{marker}:" for marker in sorted(_YAML_MARKERS))
    return any(line.strip().casefold().startswith(markers) for line in text.splitlines())


def _validate_yaml_nodes(node: Node | None, *, depth: int = 0) -> int:
    if node is None:
        return 0
    if depth > 100:
        raise ValueError("yaml_nesting_too_deep")
    count = 1
    if isinstance(node, MappingNode):
        keys: set[str] = set()
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                raise ValueError("yaml_key_invalid")
            key = f"{key_node.tag}:{key_node.value}"
            if key in keys:
                raise ValueError("duplicate_yaml_key")
            keys.add(key)
            count += _validate_yaml_nodes(value_node, depth=depth + 1)
    elif isinstance(node, SequenceNode):
        for item in node.value:
            count += _validate_yaml_nodes(item, depth=depth + 1)
    if count > 100_000:
        raise ValueError("yaml_document_too_complex")
    return count


def _read_spec_source(
    context: RepositoryContext,
    *,
    source_path: str | Path | None,
    source_url: str | None,
) -> tuple[str, str]:
    if source_path is not None:
        return _read_local_source(context, source_path)
    if source_url is None:
        raise ValueError("exactly_one_spec_source_required")
    return _read_remote_source(source_url)


def _read_local_source(context: RepositoryContext, source_path: str | Path) -> tuple[str, str]:
    try:
        root = context.root.resolve(strict=True)
    except OSError:
        raise ValueError("spec_source_unreadable") from None
    requested = Path(source_path).expanduser()
    if not requested.is_absolute():
        requested = root / requested
    candidate = Path(os.path.abspath(requested))
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError("spec_source_outside_root")
    parts = candidate.relative_to(root).parts
    if not parts:
        raise ValueError("spec_source_not_regular")

    directory_flags = _local_open_flags(directory=True)
    file_flags = _local_open_flags(directory=False)
    chunks: list[bytes] = []
    try:
        with ExitStack() as descriptors:
            root_descriptor = os.open(root, directory_flags)
            descriptors.callback(_close_descriptor, root_descriptor)
            root_identity = _descriptor_identity(root_descriptor)
            parent_descriptor = root_descriptor
            for component in parts[:-1]:
                parent_descriptor = os.open(
                    component,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
                descriptors.callback(_close_descriptor, parent_descriptor)
            descriptor = os.open(parts[-1], file_flags, dir_fd=parent_descriptor)
            descriptors.callback(_close_descriptor, descriptor)

            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("spec_source_not_regular")
            if opened.st_size > MAX_SPEC_BYTES:
                raise ValueError("spec_source_too_large")
            size = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(_READ_CHUNK_BYTES, MAX_SPEC_BYTES + 1 - size),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_SPEC_BYTES:
                    raise ValueError("spec_source_too_large")
            _verify_root_identity(root, directory_flags, root_identity)
    except FileNotFoundError:
        raise ValueError("spec_source_not_regular") from None
    except ValueError:
        raise
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("spec_source_symlink") from None
        raise ValueError("spec_source_unreadable") from None
    try:
        text = b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("spec_source_invalid_utf8") from None
    return text, candidate.as_uri()


def _local_open_flags(*, directory: bool) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _descriptor_identity(descriptor: int) -> tuple[int, int]:
    status = os.fstat(descriptor)
    return status.st_dev, status.st_ino


def _close_descriptor(descriptor: int) -> None:
    with suppress(OSError):
        os.close(descriptor)


def _verify_root_identity(
    root: Path,
    directory_flags: int,
    expected: tuple[int, int],
) -> None:
    try:
        current_descriptor = os.open(root, directory_flags)
    except OSError:
        raise ValueError("spec_source_changed") from None
    try:
        if _descriptor_identity(current_descriptor) != expected:
            raise ValueError("spec_source_changed")
    finally:
        with suppress(OSError):
            os.close(current_descriptor)


def _read_remote_source(
    source_url: str,
    *,
    monotonic: Callable[[], float] | None = None,
) -> tuple[str, str]:
    clock = monotonic or _monotonic
    deadline = clock() + REMOTE_IMPORT_DEADLINE_SECONDS
    target = _call_with_deadline(
        lambda: NetworkPolicy().resolve_https_target(source_url),
        deadline=deadline,
        monotonic=clock,
    )
    remaining = _deadline_remaining(deadline, clock)
    timeout = httpx.Timeout(
        connect=min(5.0, remaining),
        read=min(15.0, remaining),
        write=min(5.0, remaining),
        pool=min(5.0, remaining),
    )
    headers = {"Accept": "application/json, application/yaml, text/yaml, text/markdown"}
    client = httpx.Client(
        follow_redirects=False,
        timeout=timeout,
        headers=headers,
        trust_env=False,
    )
    response: httpx.Response | None = None
    try:
        request = client.build_request("GET", target.url)
        response = _call_with_deadline(
            lambda: client.send(request, stream=True),
            deadline=deadline,
            monotonic=clock,
            on_timeout=client.close,
        )
        if 300 <= response.status_code < 400:
            raise ValueError("remote_redirect_forbidden")
        if not 200 <= response.status_code < 300:
            raise ValueError("remote_http_error")
        _verify_response_peer(response, target)
        chunks: list[bytes] = []
        size = 0
        iterator = iter(response.iter_bytes())
        while True:
            try:
                chunk = _call_with_deadline(
                    lambda: next(iterator),
                    deadline=deadline,
                    monotonic=clock,
                    on_timeout=response.close,
                )
            except StopIteration:
                break
            size += len(chunk)
            if size > MAX_SPEC_BYTES:
                raise ValueError("spec_source_too_large")
            chunks.append(chunk)
    except (NetworkPolicyError, ValueError):
        raise
    except httpx.TimeoutException:
        raise ValueError("remote_request_timeout") from None
    except (httpx.HTTPError, OSError):
        raise ValueError("remote_request_failed") from None
    finally:
        if response is not None:
            with suppress(Exception):
                response.close()
        with suppress(Exception):
            client.close()
    try:
        text = b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("spec_source_invalid_utf8") from None
    return text, target.url


def _verify_response_peer(response: httpx.Response, target: ResolvedTarget) -> None:
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        raise NetworkPolicyError("remote_peer_unverified")
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception:
        raise NetworkPolicyError("remote_peer_unverified") from None
    if isinstance(peer, tuple) and peer and isinstance(peer[0], str):
        target.verify_peer(peer[0])
        return
    raise NetworkPolicyError("remote_peer_unverified")


def _deadline_remaining(deadline: float, monotonic: Callable[[], float]) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ValueError("remote_import_deadline_exceeded")
    return remaining


def _call_with_deadline(
    operation: Callable[[], Any],
    *,
    deadline: float,
    monotonic: Callable[[], float],
    on_timeout: Callable[[], Any] | None = None,
) -> Any:
    results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result = (True, operation())
        except BaseException as error:
            result = (False, error)
        with suppress(queue.Full):
            results.put_nowait(result)

    threading.Thread(target=run, daemon=True).start()
    try:
        succeeded, value = results.get(timeout=_deadline_remaining(deadline, monotonic))
        _deadline_remaining(deadline, monotonic)
    except (queue.Empty, ValueError):
        if on_timeout is not None:
            with suppress(Exception):
                on_timeout()
        raise ValueError("remote_import_deadline_exceeded") from None
    if succeeded:
        return value
    raise value
