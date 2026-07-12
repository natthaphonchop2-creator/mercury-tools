from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from mercury_tools.catalog import importers
from mercury_tools.catalog.identity import validate_action_identity
from mercury_tools.catalog.importers import service
from mercury_tools.catalog.importers.sanitize import sanitize_spec
from mercury_tools.catalog.importers.service import MAX_SPEC_BYTES, import_spec
from mercury_tools.catalog.models import CatalogAction, CatalogSource
from mercury_tools.local.repository import ensure_repository_state
from mercury_tools.safety.network import NetworkPolicy, NetworkPolicyError, ResolvedTarget

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"


class _VerifiedPeerStream:
    def __init__(self, address: str = "93.184.216.34") -> None:
        self.address = address

    def get_extra_info(self, info: str) -> Any:
        return (self.address, 443) if info == "server_addr" else None


def _mock_response(
    status_code: int,
    *,
    peer_address: str = "93.184.216.34",
    **kwargs: Any,
) -> httpx.Response:
    extensions = dict(kwargs.pop("extensions", {}))
    extensions["network_stream"] = _VerifiedPeerStream(peer_address)
    return httpx.Response(status_code, extensions=extensions, **kwargs)


def _fixture_in_root(tmp_path: Path, filename: str) -> Path:
    destination = tmp_path / filename
    shutil.copyfile(FIXTURES / filename, destination)
    return destination


def test_importer_package_exports_brief_interfaces() -> None:
    assert callable(importers.parse_openapi)
    assert callable(importers.parse_postman)
    assert callable(importers.parse_markdown)
    assert callable(importers.sanitize_spec)
    assert callable(importers.import_spec)


@pytest.mark.parametrize(
    ("filename", "expected_method", "expected_confidence", "expected_source_type"),
    [
        ("openapi3.json", "GET", "exact", "openapi3"),
        ("swagger2.yaml", "POST", "exact", "swagger2"),
        ("postman21.json", "POST", "example_derived", "postman2.1"),
        ("endpoints.md", "DELETE", "inferred", "documentation"),
    ],
)
def test_supported_formats_normalize_complete_actions(
    tmp_path: Path,
    filename: str,
    expected_method: str,
    expected_confidence: str,
    expected_source_type: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = _fixture_in_root(tmp_path, filename)

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.method == expected_method
    assert action.confidence == expected_confidence
    assert action.version_id.startswith("av_")
    assert action.action_id.startswith("act_")
    assert action.source_hash == result.source.source_hash
    assert action.input_schema.keys() == {"path", "query", "headers", "body", "files"}
    assert result.source.source_type == expected_source_type
    assert result.sanitization.safe is True


def test_imported_content_is_credential_safe_and_schema_metadata_survives(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = _fixture_in_root(tmp_path, "swagger2.yaml")

    result = import_spec(context, connector_id="custom", source_path=source_path)

    serialized = json.dumps(result.model_dump(mode="json"))
    body_schema = result.actions[0].input_schema["body"]
    assert "fake-client-secret" not in serialized
    assert "finance@example.com" not in serialized
    assert "[REDACTED]" in serialized
    assert "client_secret" in body_schema["properties"]
    assert body_schema["x-mercury-property-descriptions"] == (
        {
            "name": "client_secret",
            "description": "Credential field supplied during setup",
        },
    )


def test_postman_secrets_are_removed_from_source_and_examples(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = _fixture_in_root(tmp_path, "postman21.json")

    result = import_spec(context, connector_id="custom", source_path=source_path)

    serialized = json.dumps(result.model_dump(mode="json"))
    assert "Bearer real-token" not in serialized
    assert "secret@example.com" not in serialized
    assert "real-postman-variable-token" not in serialized
    assert "[REDACTED]" in serialized


def test_openapi_security_scheme_is_non_secret_driver_suggestion(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = _fixture_in_root(tmp_path, "openapi3.json")
    config_before = context.config_path.read_bytes()

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.driver_suggestion == {
        "driver_id": "bearer",
        "auth_settings": {"key_name": "Authorization"},
    }
    assert context.config_path.read_bytes() == config_before


def test_action_order_and_identities_are_deterministic(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    document = json.loads((FIXTURES / "openapi3.json").read_text())
    document["paths"]["/accounts"] = {
        "get": {"operationId": "listAccounts", "responses": {"200": {"description": "OK"}}}
    }
    source_path = tmp_path / "multiple.json"
    source_path.write_text(json.dumps(document))

    first = import_spec(context, connector_id="custom", source_path=source_path)
    second = import_spec(context, connector_id="custom", source_path=source_path)

    assert [action.path_template for action in first.actions] == [
        "/accounts",
        "/invoices/{invoice_id}",
    ]
    assert [action.action_id for action in first.actions] == [
        action.action_id for action in second.actions
    ]
    assert [action.version_id for action in first.actions] == [
        action.version_id for action in second.actions
    ]


@pytest.mark.parametrize(
    ("method", "risk_tier"),
    [("GET", 0), ("POST", 1), ("PUT", 1), ("PATCH", 1), ("DELETE", 2)],
)
def test_markdown_requires_explicit_method_path_and_infers_safe_risk(
    tmp_path: Path,
    method: str,
    risk_tier: int,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "endpoint.md"
    source_path.write_text(f"{method} /records/{{record_id}} - Explicit endpoint")

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.actions[0].risk_tier == risk_tier
    assert result.actions[0].required_confirmations == risk_tier
    assert result.actions[0].confidence == "inferred"
    assert result.actions[0].observed_state == "untested"


def test_markdown_rejects_prose_that_only_mentions_a_path(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "prose.md"
    source_path.write_text("Use the endpoint at /records to list records.")

    with pytest.raises(ValueError, match="unknown_spec_format"):
        import_spec(context, connector_id="custom", source_path=source_path)


def test_markdown_accepts_explicit_table_records(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "table.md"
    source_path.write_text(
        "| Method | Path | Description |\n| DELETE | /records/{id} | Remove record |"
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.actions[0].method == "DELETE"
    assert result.actions[0].path_template == "/records/{id}"


def test_rejects_unknown_structured_format_and_uses_structured_precedence(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    unknown = tmp_path / "unknown.json"
    unknown.write_text('{"name": "not a supported specification"}')
    multiple_markers = tmp_path / "multiple-markers.json"
    multiple_markers.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "swagger": "2.0",
                "info": {"version": "1"},
                "paths": {
                    "/items": {
                        "get": {"responses": {"200": {"description": "OK"}}}
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="unknown_spec_format"):
        import_spec(context, connector_id="custom", source_path=unknown)
    result = import_spec(context, connector_id="custom", source_path=multiple_markers)

    assert result.source.source_type == "openapi3"


def test_openapi_precedence_controls_parser_semantics(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "openapi-over-swagger.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "swagger": "2.0",
                "info": {"version": "1"},
                "consumes": ["application/x-www-form-urlencoded"],
                "paths": {
                    "/items": {
                        "post": {
                            "requestBody": {
                                "content": {
                                    "application/xml": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"name": {"type": "string"}},
                                        }
                                    }
                                }
                            },
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.source_type == "openapi3"
    assert result.actions[0].content_type == "application/xml"
    assert result.actions[0].input_schema["body"]["properties"] == {
        "name": {"type": "string"}
    }


def test_openapi_preserves_parameter_and_request_body_required_semantics(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "required-openapi.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {
                    "/items/{item_id}": {
                        "parameters": [
                            {
                                "name": "item_id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "mode",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                        ],
                        "post": {
                            "parameters": [
                                {
                                    "name": "X-Request-Mode",
                                    "in": "header",
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            ],
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"name": {"type": "string"}},
                                            "required": ["name"],
                                        }
                                    }
                                },
                            },
                            "responses": {"201": {"description": "Created"}},
                        },
                    }
                },
            }
        )
    )

    action = import_spec(
        context,
        connector_id="custom",
        source_path=source_path,
    ).actions[0]

    assert action.input_schema["path"]["item_id"]["required"] is True
    assert action.input_schema["query"]["mode"]["required"] is True
    assert action.input_schema["headers"]["X-Request-Mode"]["required"] is True
    assert action.input_schema["body"]["x-mercury-required"] is True
    assert action.input_schema["body"]["required"] == ("name",)


@pytest.mark.parametrize(
    "parameter",
    [
        {"name": "item_id", "in": "path", "schema": {"type": "string"}},
        {"name": "item_id", "in": "path", "required": False, "schema": {"type": "string"}},
        {"name": "mode", "in": "query", "required": "true", "schema": {"type": "string"}},
        {"name": "session", "in": "cookie", "required": True, "schema": {"type": "string"}},
        {"name": "mode", "in": "matrix", "required": True, "schema": {"type": "string"}},
    ],
)
def test_openapi_fails_closed_for_invalid_or_unsupported_required_parameters(
    tmp_path: Path,
    parameter: dict[str, Any],
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "invalid-required-openapi.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {
                    "/items/{item_id}": {
                        "get": {
                            "parameters": [parameter],
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="required"):
        import_spec(context, connector_id="custom", source_path=source_path)


def test_openapi_fails_closed_for_non_list_parameter_container(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "invalid-parameter-container.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {
                    "/items": {
                        "get": {
                            "parameters": {
                                "name": "mode",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="spec_parameters_invalid"):
        import_spec(context, connector_id="custom", source_path=source_path)


def test_openapi_fails_closed_for_unresolved_request_body_reference(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "request-body-reference.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {
                    "/items": {
                        "post": {
                            "requestBody": {"$ref": "#/components/requestBodies/Item"},
                            "responses": {"201": {"description": "Created"}},
                        }
                    }
                },
                "components": {
                    "requestBodies": {
                        "Item": {
                            "required": True,
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        }
                    }
                },
            }
        )
    )

    with pytest.raises(ValueError, match="spec_request_body_reference_unsupported"):
        import_spec(context, connector_id="custom", source_path=source_path)


def test_openapi_multipart_preserves_required_file_and_body_semantics(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "required-multipart-openapi.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {
                    "/documents": {
                        "post": {
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "multipart/form-data": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "document": {
                                                    "type": "string",
                                                    "format": "binary",
                                                },
                                                "caption": {"type": "string"},
                                            },
                                            "required": ["document", "caption"],
                                        }
                                    }
                                },
                            },
                            "responses": {"201": {"description": "Created"}},
                        }
                    }
                },
            }
        )
    )

    action = import_spec(
        context,
        connector_id="custom",
        source_path=source_path,
    ).actions[0]

    assert action.content_type == "multipart/form-data"
    assert action.input_schema["files"] == {
        "document": {"type": "string", "format": "binary", "required": True}
    }
    assert action.input_schema["body"] == {
        "type": "object",
        "properties": {"caption": {"type": "string"}},
        "required": ("caption",),
        "x-mercury-required": True,
    }


def test_swagger_precedes_postman_marker(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "swagger-over-postman.json"
    source_path.write_text(
        json.dumps(
            {
                "swagger": "2.0",
                "info": {
                    "version": "1",
                    "schema": (
                        "https://schema.getpostman.com/json/collection/"
                        "v2.1.0/collection.json"
                    ),
                },
                "paths": {
                    "/items": {
                        "get": {"responses": {"200": {"description": "OK"}}}
                    }
                },
                "item": [],
            }
        )
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.source_type == "swagger2"
    assert result.actions[0].confidence == "exact"


def test_invalid_openapi_marker_selects_and_stores_exact_swagger_source_type(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "invalid-openapi-valid-swagger.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "invalid-marker",
                "swagger": "2.0",
                "info": {"version": "1"},
                "paths": {
                    "/items": {
                        "get": {"responses": {"200": {"description": "OK"}}}
                    }
                },
            }
        )
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)
    revalidated = CatalogSource.model_validate(result.source.model_dump(mode="python"))

    assert result.source.source_type == "swagger2"
    assert revalidated.source_hash == result.source.source_hash


@pytest.mark.parametrize("prefix", ["---\n", "%YAML 1.2\n---\n"])
def test_yaml_directives_and_document_markers_are_parsed(
    tmp_path: Path,
    prefix: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "directive.yaml"
    source_path.write_text(
        prefix
        + 'swagger: "2.0"\n'
        + 'info: {version: "1"}\n'
        + "paths:\n"
        + "  /items:\n"
        + "    get:\n"
        + "      responses:\n"
        + '        "200": {description: OK}\n'
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.source_type == "swagger2"
    assert result.actions[0].path_template == "/items"


def test_markdown_with_document_marker_remains_markdown(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "marked.md"
    source_path.write_text("---\ntitle: Endpoints\n---\nGET /items - List items\n")

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.source_type == "documentation"


def test_yaml_detection_is_key_order_independent_and_uses_format_precedence(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "ordered.yaml"
    source_path.write_text(
        "paths:\n"
        "  /items:\n"
        "    get:\n"
        "      responses:\n"
        '        "200": {description: OK}\n'
        "item: []\n"
        "info:\n"
        '  version: "1"\n'
        "  schema: https://schema.getpostman.com/json/collection/v2.1.0/collection.json\n"
        'swagger: "2.0"\n'
        'openapi: "3.0.0"\n'
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.source_type == "openapi3"
    assert result.actions[0].path_template == "/items"


def test_requires_exactly_one_source_and_local_source_inside_root(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    inside = _fixture_in_root(tmp_path, "openapi3.json")
    outside = FIXTURES / "openapi3.json"

    with pytest.raises(ValueError, match="exactly_one_spec_source_required"):
        import_spec(context, connector_id="custom")
    with pytest.raises(ValueError, match="exactly_one_spec_source_required"):
        import_spec(
            context,
            connector_id="custom",
            source_path=inside,
            source_url="https://example.test/openapi.json",
        )
    with pytest.raises(ValueError, match="spec_source_outside_root"):
        import_spec(context, connector_id="custom", source_path=outside)


def test_local_source_rejects_symlink_nonregular_oversize_and_invalid_utf8(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    target = _fixture_in_root(tmp_path, "openapi3.json")
    symlink = tmp_path / "linked.json"
    if os.name == "posix":
        symlink.symlink_to(target)
        with pytest.raises(ValueError, match="spec_source_symlink"):
            import_spec(context, connector_id="custom", source_path=symlink)

    with pytest.raises(ValueError, match="spec_source_not_regular"):
        import_spec(context, connector_id="custom", source_path=tmp_path)

    oversize = tmp_path / "oversize.json"
    with oversize.open("wb") as handle:
        handle.truncate(MAX_SPEC_BYTES + 1)
    with pytest.raises(ValueError, match="spec_source_too_large"):
        import_spec(context, connector_id="custom", source_path=oversize)

    invalid_utf8 = tmp_path / "invalid.json"
    invalid_utf8.write_bytes(b"{\xff}")
    with pytest.raises(ValueError, match="spec_source_invalid_utf8"):
        import_spec(context, connector_id="custom", source_path=invalid_utf8)


def test_local_source_reads_from_pinned_directory_during_intermediate_swap(
    tmp_path_factory,
    monkeypatch,
) -> None:
    root = tmp_path_factory.mktemp("repository")
    outside = tmp_path_factory.mktemp("outside")
    context = ensure_repository_state(root)
    nested = root / "nested"
    nested.mkdir()
    source_path = nested / "spec.json"
    trusted_document = {
        "openapi": "3.0.0",
        "info": {"version": "1"},
        "paths": {"/inside": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    outside_document = {
        "openapi": "3.0.0",
        "info": {"version": "1"},
        "paths": {"/outside": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
    source_path.write_text(json.dumps(trusted_document))
    (outside / "spec.json").write_text(json.dumps(outside_document))
    original_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        is_final_open = Path(path) == source_path or (path == "spec.json" and dir_fd is not None)
        if is_final_open and not swapped:
            swapped = True
            nested.rename(root / "pinned-nested")
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(service.os, "open", swapping_open)

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert swapped is True
    assert [action.path_template for action in result.actions] == ["/inside"]


@pytest.mark.parametrize(
    "payload",
    [
        '{"openapi":"3.0.0","openapi":"3.1.0","info":{"version":"1"},"paths":{}}',
        'swagger: "2.0"\ninfo:\n  version: "1"\n  version: "2"\npaths: {}\n',
        'swagger: "2.0"\ninfo: !unsafe {}\npaths: {}\n',
        'swagger: &version "2.0"\ncopy: *version\ninfo: {version: "1"}\npaths: {}\n',
    ],
)
def test_structured_parser_rejects_duplicate_keys_custom_tags_and_aliases(
    tmp_path: Path,
    payload: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "unsafe-spec.txt"
    source_path.write_text(payload)

    with pytest.raises(ValueError, match="spec_document_invalid"):
        import_spec(context, connector_id="custom", source_path=source_path)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/openapi.json",
        "https://user:password@example.test/openapi.json",
        "https://127.0.0.1/openapi.json",
        "https://2130706433/openapi.json",
        "https://0177.0.0.1/openapi.json",
        "https://169.254.169.254/openapi.json",
        "https://metadata.google.internal/openapi.json",
        "https://192.0.2.1/openapi.json",
    ],
)
def test_remote_import_rejects_unsafe_urls_before_request(tmp_path: Path, url: str) -> None:
    context = ensure_repository_state(tmp_path)

    with pytest.raises(NetworkPolicyError):
        import_spec(context, connector_id="custom", source_url=url)


def test_network_policy_rejects_host_when_any_dns_answer_is_unsafe(monkeypatch) -> None:
    def mixed_answers(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", mixed_answers)

    with pytest.raises(NetworkPolicyError, match="unsafe_resolved_address"):
        NetworkPolicy().resolve_https_target("https://example.test/openapi.json")


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "::",
        "127.0.0.1",
        "::1",
        "169.254.1.1",
        "fe80::1",
        "10.0.0.1",
        "fc00::1",
        "224.0.0.1",
        "ff02::1",
        "240.0.0.1",
        "2001:db8::1",
        "169.254.169.254",
        "192.0.2.1",
    ],
)
def test_network_policy_rejects_every_non_global_unicast_dns_answer(
    monkeypatch,
    address: str,
) -> None:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (family, socket.SOCK_STREAM, 6, "", (address, 443))
        ],
    )

    with pytest.raises(NetworkPolicyError, match="^unsafe_resolved_address$"):
        NetworkPolicy().resolve_https_target("https://example.test/openapi.json")


def test_network_policy_rejects_remote_credential_query_before_dns(monkeypatch) -> None:
    def unexpected_dns(*_args, **_kwargs):
        raise AssertionError("credential-bearing URL must be rejected before DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)

    with pytest.raises(NetworkPolicyError, match="remote_query_or_fragment_forbidden"):
        NetworkPolicy().resolve_https_target(
            "https://example.test/openapi.json?api_key=raw-value"
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/openapi.json?page=1",
        "https://example.test/openapi.json?",
        "https://example.test/openapi.json#section",
    ],
)
def test_network_policy_rejects_every_query_or_fragment_before_dns(
    monkeypatch,
    url: str,
) -> None:
    def unexpected_dns(*_args, **_kwargs):
        raise AssertionError("query and fragment URLs must be rejected before DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)

    with pytest.raises(NetworkPolicyError, match="remote_query_or_fragment_forbidden"):
        NetworkPolicy().resolve_https_target(url)


def test_postman_body_modes_normalize_content_type_body_and_files(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    document = {
        "info": {
            "name": "Body modes",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [
            {
                "name": "Multipart",
                "request": {
                    "method": "POST",
                    "url": "https://api.example.test/multipart",
                    "body": {
                        "mode": "formdata",
                        "formdata": [
                            {"key": "document", "type": "file", "src": "/tmp/demo.pdf"},
                            {"key": "caption", "type": "text", "value": "Invoice"},
                        ],
                    },
                },
            },
            {
                "name": "Form encoded",
                "request": {
                    "method": "POST",
                    "url": "https://api.example.test/form",
                    "body": {
                        "mode": "urlencoded",
                        "urlencoded": [{"key": "status", "value": "draft"}],
                    },
                },
            },
            {
                "name": "Raw text",
                "request": {
                    "method": "POST",
                    "header": [{"key": "Content-Type", "value": "text/plain"}],
                    "url": "https://api.example.test/raw",
                    "body": {"mode": "raw", "raw": "plain text"},
                },
            },
        ],
    }
    source_path = tmp_path / "postman.json"
    source_path.write_text(json.dumps(document))

    result = import_spec(context, connector_id="custom", source_path=source_path)
    actions = {action.path_template: action for action in result.actions}

    multipart = actions["/multipart"]
    assert multipart.content_type == "multipart/form-data"
    assert multipart.input_schema["body"] == {
        "type": "object",
        "properties": {"caption": {"type": "string"}},
    }
    assert multipart.input_schema["files"] == {
        "type": "object",
        "properties": {"document": {"type": "string", "format": "binary"}},
    }
    form = actions["/form"]
    assert form.content_type == "application/x-www-form-urlencoded"
    assert form.input_schema["body"]["properties"] == {"status": {"type": "string"}}
    raw = actions["/raw"]
    assert raw.content_type == "text/plain"
    assert raw.input_schema["body"] == {"type": "string"}


@pytest.mark.parametrize(
    ("document", "expected_suggestion"),
    [
        (
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "components": {
                    "securitySchemes": {
                        "queryKey": {"type": "apiKey", "in": "query", "name": "api_key"}
                    }
                },
                "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
            },
            {"driver_id": "api_key_query", "auth_settings": {"key_name": "api_key"}},
        ),
        (
            {
                "swagger": "2.0",
                "info": {"version": "1"},
                "securityDefinitions": {"basicAuth": {"type": "basic"}},
                "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
            },
            {"driver_id": "basic", "auth_settings": {}},
        ),
        (
            {
                "openapi": "3.1.0",
                "info": {"version": "1"},
                "components": {
                    "securitySchemes": {
                        "oauth": {
                            "type": "oauth2",
                            "flows": {
                                "clientCredentials": {
                                    "tokenUrl": "https://auth.example.test/token",
                                    "scopes": {"items.read": "Read items"},
                                }
                            },
                        }
                    }
                },
                "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
            },
            {
                "driver_id": "oauth_client_credentials",
                "auth_settings": {
                    "client_id_name": "client_id",
                    "client_secret_name": "client_secret",
                    "grant_type": "client_credentials",
                    "scope": "items.read",
                    "token_url": "https://auth.example.test/token",
                },
            },
        ),
    ],
)
def test_security_schemes_map_to_non_secret_driver_suggestions(
    tmp_path: Path,
    document: dict,
    expected_suggestion: dict,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "security.json"
    source_path.write_text(json.dumps(document))

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.driver_suggestion == expected_suggestion


def test_imported_source_is_idempotently_sanitized_and_actions_revalidate(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = _fixture_in_root(tmp_path, "postman21.json")

    result = import_spec(context, connector_id="custom", source_path=source_path)
    sanitized_again, report = sanitize_spec(result.source.sanitization["document"])

    dumped_document = result.source.model_dump(mode="json")["sanitization"]["document"]
    assert sanitized_again == dumped_document
    assert report.redacted_values == 0
    for action in result.actions:
        revalidated = CatalogAction.model_validate(action.model_dump(mode="python"))
        validate_action_identity(revalidated)


def test_source_and_action_validation_errors_do_not_echo_values(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = _fixture_in_root(tmp_path, "openapi3.json")
    result = import_spec(context, connector_id="custom", source_path=source_path)
    secret = "validation-error-secret-value"

    source_values = result.source.model_dump(mode="python")
    source_values["driver_suggestion"] = {"password": secret}
    with pytest.raises(ValueError, match="catalog_credentials_unsafe") as source_error:
        CatalogSource.model_validate(source_values)

    action_values = result.actions[0].model_dump(mode="python")
    action_values["examples"] = ({"access_token": secret},)
    with pytest.raises(ValueError, match="catalog_credentials_unsafe") as action_error:
        CatalogAction.model_validate(action_values)

    assert secret not in str(source_error.value)
    assert secret not in str(action_error.value)


@pytest.mark.parametrize("source_format", ["openapi3", "swagger2", "postman2.1", "markdown"])
def test_import_rejects_credential_bearing_endpoint_paths_without_echo(
    tmp_path: Path,
    source_format: str,
) -> None:
    context = ensure_repository_state(tmp_path)
    secret = "task-4-path-secret-must-not-echo"
    unsafe_path = f"/v1/client_secret={secret}"
    source_path = tmp_path / f"unsafe-{source_format}.txt"
    if source_format == "openapi3":
        document: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": {"version": "1"},
            "paths": {
                unsafe_path: {
                    "get": {"responses": {"200": {"description": "OK"}}}
                }
            },
        }
        source_path.write_text(json.dumps(document))
    elif source_format == "swagger2":
        document = {
            "swagger": "2.0",
            "info": {"version": "1"},
            "paths": {
                unsafe_path: {
                    "get": {"responses": {"200": {"description": "OK"}}}
                }
            },
        }
        source_path.write_text(json.dumps(document))
    elif source_format == "postman2.1":
        source_path.write_text(
            json.dumps(
                {
                    "info": {
                        "name": "Unsafe",
                        "schema": (
                            "https://schema.getpostman.com/json/collection/"
                            "v2.1.0/collection.json"
                        ),
                    },
                    "item": [
                        {
                            "name": "Unsafe",
                            "request": {"method": "GET", "url": unsafe_path},
                        }
                    ],
                }
            )
        )
    else:
        source_path.write_text(f"GET {unsafe_path} - Unsafe endpoint")

    with pytest.raises(ValueError, match="^catalog_credential_path_unsafe$") as raised:
        import_spec(context, connector_id="custom", source_path=source_path)

    assert secret not in str(raised.value)


def test_import_rejects_encoded_structural_endpoint_path_key_without_echo(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    secret = "task-4-import-encoded-structural-secret-must-not-echo"
    source_path = tmp_path / "unsafe-encoded-path-key.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {f"%2Fv1%2Fclient_secret%3D{secret}": {}},
            }
        )
    )

    with pytest.raises(ValueError, match="^catalog_credential_path_unsafe$") as raised:
        import_spec(context, connector_id="custom", source_path=source_path)

    assert secret not in str(raised.value)


def test_import_rejects_multi_layer_encoded_path_field_name_without_echo(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    secret = "task-4-import-encoded-field-secret"
    source_path = tmp_path / "unsafe-encoded-path-field.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
                "%2570ath": f"/v1/client_secret={secret}",
            }
        )
    )

    with pytest.raises(ValueError, match="^catalog_credential_path_unsafe$") as raised:
        import_spec(context, connector_id="custom", source_path=source_path)

    assert secret not in str(raised.value)


def test_import_rejects_raw_in_list_under_multi_layer_encoded_url_without_echo(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    secret = "task-4-import-encoded-url-list-secret-must-not-echo"
    source_path = tmp_path / "unsafe-encoded-url-list.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
                "%2575rl": [{"raw": f"/v1/ghp_{secret}"}],
            }
        )
    )

    with pytest.raises(ValueError, match="^catalog_credential_path_unsafe$") as raised:
        import_spec(context, connector_id="custom", source_path=source_path)

    assert secret not in str(raised.value)


def test_import_keeps_malformed_encoded_path_like_metadata(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    key = "p%61th%"
    value = "/v1/ghp_task-4-import-malformed-field-metadata"
    source_path = tmp_path / "malformed-encoded-path-field.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
                key: value,
            }
        )
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)

    assert result.source.sanitization["document"][key] == value


def test_import_inspects_list_items_under_encoded_url_without_inspecting_metadata(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "encoded-url-metadata.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
                "%2575rl": [
                    {
                        "raw": "/safe",
                        "metadata": {
                            "ghp_documentation_field": "ordinary metadata",
                            "client_secret_like_text": "ordinary metadata",
                        },
                    },
                ],
            }
        )
    )

    result = import_spec(context, connector_id="custom", source_path=source_path)

    url_item = result.source.sanitization["document"]["%2575rl"][0]
    assert url_item["raw"] == "/safe"
    metadata = url_item["metadata"]
    assert metadata["ghp_documentation_field"] == "ordinary metadata"
    assert metadata["client_secret_like_text"] == "ordinary metadata"


def test_import_rejects_deeply_encoded_structural_endpoint_path_key_without_echo(
    tmp_path: Path,
) -> None:
    context = ensure_repository_state(tmp_path)
    secret = "task-4-import-deep-structural-secret-must-not-echo"
    encoded_path = f"/v1/client_secret={secret}"
    for _ in range(4):
        encoded_path = quote(encoded_path, safe="")
    source_path = tmp_path / "unsafe-deeply-encoded-path-key.json"
    source_path.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"version": "1"},
                "paths": {encoded_path: {}},
            }
        )
    )

    with pytest.raises(ValueError, match="^catalog_credential_path_unsafe$") as raised:
        import_spec(context, connector_id="custom", source_path=source_path)

    assert secret not in str(raised.value)


def test_remote_import_uses_mocked_dns_and_transport_without_auth_headers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = ensure_repository_state(tmp_path)
    document = {
        "openapi": "3.0.0",
        "info": {"version": "1"},
        "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }

    def public_answer(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return _mock_response(200, json=document)

    original_client = httpx.Client

    def client_factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", public_answer)
    monkeypatch.setattr(service.httpx, "Client", client_factory)

    result = import_spec(
        context,
        connector_id="custom",
        source_url="https://specs.example.test/openapi.json",
    )

    assert result.actions[0].path_template == "/items"
    assert result.source.source_uri == "https://specs.example.test/openapi.json"


def test_remote_import_fails_closed_without_verified_peer_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = ensure_repository_state(tmp_path)

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    original_client = httpx.Client

    def client_factory(**kwargs):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"openapi": "3.0.0"})
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(service.httpx, "Client", client_factory)

    with pytest.raises(NetworkPolicyError, match="remote_peer_unverified"):
        import_spec(
            context,
            connector_id="custom",
            source_url="https://specs.example.test/openapi.json",
        )


def test_remote_import_enforces_total_deadline_during_stream(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = ensure_repository_state(tmp_path)

    class FakeClock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

    class AdvancingStream(httpx.SyncByteStream):
        def __iter__(self):
            clock.now += service.REMOTE_IMPORT_DEADLINE_SECONDS + 1
            yield b'{"openapi":"3.0.0"}'

    clock = FakeClock()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    original_client = httpx.Client

    def client_factory(**kwargs):
        timeout = kwargs["timeout"]
        assert max(timeout.connect, timeout.read, timeout.write, timeout.pool) <= (
            service.REMOTE_IMPORT_DEADLINE_SECONDS
        )
        transport = httpx.MockTransport(
            lambda _request: _mock_response(200, stream=AdvancingStream())
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(service.httpx, "Client", client_factory)
    monkeypatch.setattr(service, "_monotonic", clock)

    with pytest.raises(ValueError, match="remote_import_deadline_exceeded"):
        import_spec(
            context,
            connector_id="custom",
            source_url="https://specs.example.test/openapi.json",
        )


def test_remote_deadline_bounds_blocking_dns_in_daemon_worker(monkeypatch) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_resolver(_self, _url):
        if threading.current_thread() is threading.main_thread():
            raise AssertionError("DNS resolution ran on the importing thread")
        entered.set()
        release.wait()
        return ResolvedTarget(
            url="https://specs.example.test/openapi.json",
            hostname="specs.example.test",
            port=443,
            addresses=("93.184.216.34",),
        )

    monkeypatch.setattr(NetworkPolicy, "resolve_https_target", blocking_resolver)
    monkeypatch.setattr(service, "REMOTE_IMPORT_DEADLINE_SECONDS", 0.05)

    try:
        with pytest.raises(ValueError, match="^remote_import_deadline_exceeded$"):
            service._read_remote_source("https://specs.example.test/openapi.json")
        assert entered.is_set()
    finally:
        release.set()


def test_remote_deadline_bounds_blocking_request_and_headers(monkeypatch) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_handler(_request: httpx.Request) -> httpx.Response:
        if threading.current_thread() is threading.main_thread():
            raise AssertionError("request ran on the importing thread")
        entered.set()
        release.wait()
        return _mock_response(200, json={"openapi": "3.0.0"})

    monkeypatch.setattr(
        NetworkPolicy,
        "resolve_https_target",
        lambda _self, url: ResolvedTarget(
            url=url,
            hostname="specs.example.test",
            port=443,
            addresses=("93.184.216.34",),
        ),
    )
    original_client = httpx.Client

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(blocking_handler), **kwargs)

    monkeypatch.setattr(service.httpx, "Client", client_factory)
    monkeypatch.setattr(service, "REMOTE_IMPORT_DEADLINE_SECONDS", 0.05)

    try:
        with pytest.raises(ValueError, match="^remote_import_deadline_exceeded$"):
            service._read_remote_source("https://specs.example.test/openapi.json")
        assert entered.is_set()
    finally:
        release.set()


def test_remote_deadline_never_runs_blocking_client_cleanup_on_caller(monkeypatch) -> None:
    request_entered = threading.Event()
    request_release = threading.Event()
    cleanup_entered = threading.Event()
    cleanup_threads: list[threading.Thread] = []

    class BlockingCleanupClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def build_request(self, method: str, url: str) -> httpx.Request:
            return httpx.Request(method, url)

        def send(self, _request: httpx.Request, *, stream: bool) -> httpx.Response:
            assert stream is True
            request_entered.set()
            request_release.wait()
            return _mock_response(200, json={"openapi": "3.0.0"})

        def close(self) -> None:
            cleanup_threads.append(threading.current_thread())
            cleanup_entered.set()
            time.sleep(0.3)

    monkeypatch.setattr(
        NetworkPolicy,
        "resolve_https_target",
        lambda _self, url: ResolvedTarget(
            url=url,
            hostname="specs.example.test",
            port=443,
            addresses=("93.184.216.34",),
        ),
    )
    monkeypatch.setattr(service.httpx, "Client", BlockingCleanupClient)
    monkeypatch.setattr(service, "REMOTE_IMPORT_DEADLINE_SECONDS", 0.05)

    started = time.monotonic()
    try:
        with pytest.raises(ValueError, match="^remote_import_deadline_exceeded$"):
            service._read_remote_source("https://specs.example.test/openapi.json")
        elapsed = time.monotonic() - started
        assert request_entered.is_set()
        assert cleanup_entered.wait(0.1)
        assert elapsed < 0.2
        assert cleanup_threads
        assert all(thread is not threading.main_thread() for thread in cleanup_threads)
    finally:
        request_release.set()


def test_remote_deadline_bounds_each_blocking_stream_read_and_closes_response(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class BlockingStream(httpx.SyncByteStream):
        def __iter__(self):
            if threading.current_thread() is threading.main_thread():
                raise AssertionError("stream read ran on the importing thread")
            entered.set()
            release.wait()
            yield b'{' + b'"openapi":"3.0.0"}'

        def close(self) -> None:
            closed.set()
            release.set()

    monkeypatch.setattr(
        NetworkPolicy,
        "resolve_https_target",
        lambda _self, url: ResolvedTarget(
            url=url,
            hostname="specs.example.test",
            port=443,
            addresses=("93.184.216.34",),
        ),
    )
    original_client = httpx.Client

    def client_factory(**kwargs):
        transport = httpx.MockTransport(
            lambda _request: _mock_response(200, stream=BlockingStream())
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(service.httpx, "Client", client_factory)
    monkeypatch.setattr(service, "REMOTE_IMPORT_DEADLINE_SECONDS", 0.05)

    try:
        with pytest.raises(ValueError, match="^remote_import_deadline_exceeded$"):
            service._read_remote_source("https://specs.example.test/openapi.json")
        assert entered.is_set()
        assert closed.is_set()
    finally:
        release.set()


def test_remote_errors_use_constant_codes_without_url_or_body_echo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = ensure_repository_state(tmp_path)
    secret_url = "https://specs.example.test/private-openapi.json"
    secret_body = "upstream-secret-body"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    original_client = httpx.Client

    def client_factory(**kwargs):
        transport = httpx.MockTransport(
            lambda _request: _mock_response(500, text=secret_body)
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(service.httpx, "Client", client_factory)

    with pytest.raises(ValueError, match="^remote_http_error$") as error:
        import_spec(context, connector_id="custom", source_url=secret_url)
    assert secret_url not in str(error.value)
    assert secret_body not in str(error.value)


def test_remote_network_errors_use_constant_code_without_url_echo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = ensure_repository_state(tmp_path)
    secret_url = "https://specs.example.test/private-network-spec.json"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    original_client = httpx.Client

    def client_factory(**kwargs):
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"failed for {request.url}", request=request)

        return original_client(transport=httpx.MockTransport(fail), **kwargs)

    monkeypatch.setattr(service.httpx, "Client", client_factory)

    with pytest.raises(ValueError, match="^remote_request_failed$") as error:
        import_spec(context, connector_id="custom", source_url=secret_url)
    assert secret_url not in str(error.value)


def test_local_os_errors_use_constant_code_without_path_echo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = ensure_repository_state(tmp_path)
    source_path = tmp_path / "private-spec-name.json"
    source_path.write_text("{}")
    original_open = service.os.open

    def failing_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == source_path.name and dir_fd is not None:
            raise OSError(f"cannot open {source_path}")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(service.os, "open", failing_open)

    with pytest.raises(ValueError, match="^spec_source_unreadable$") as error:
        import_spec(context, connector_id="custom", source_path=source_path)
    assert str(source_path) not in str(error.value)


def test_remote_import_rejects_redirect_from_mock_transport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = ensure_repository_state(tmp_path)

    def public_answer(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    original_client = httpx.Client

    def client_factory(**kwargs):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                302,
                headers={"Location": "https://other.example.test/spec.json"},
            )
        )
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", public_answer)
    monkeypatch.setattr(service.httpx, "Client", client_factory)

    with pytest.raises(ValueError, match="remote_redirect_forbidden"):
        import_spec(
            context,
            connector_id="custom",
            source_url="https://specs.example.test/openapi.json",
        )
    assert list((context.catalog_dir / "sources").iterdir()) == []
    assert list((context.catalog_dir / "actions").iterdir()) == []
