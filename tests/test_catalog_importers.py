from __future__ import annotations

import json
import os
import shutil
import socket
from pathlib import Path

import httpx
import pytest

from mercury_tools.catalog import importers
from mercury_tools.catalog.identity import validate_action_identity
from mercury_tools.catalog.importers import service
from mercury_tools.catalog.importers.sanitize import sanitize_spec
from mercury_tools.catalog.importers.service import MAX_SPEC_BYTES, import_spec
from mercury_tools.catalog.models import CatalogAction
from mercury_tools.local.repository import ensure_repository_state
from mercury_tools.safety.network import NetworkPolicy, NetworkPolicyError

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"


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


def test_rejects_unknown_and_ambiguous_structured_formats(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    unknown = tmp_path / "unknown.json"
    unknown.write_text('{"name": "not a supported specification"}')
    ambiguous = tmp_path / "ambiguous.json"
    ambiguous.write_text(
        json.dumps(
            {
                "openapi": "3.0.0",
                "swagger": "2.0",
                "info": {"version": "1"},
                "paths": {},
            }
        )
    )

    with pytest.raises(ValueError, match="unknown_spec_format"):
        import_spec(context, connector_id="custom", source_path=unknown)
    with pytest.raises(ValueError, match="ambiguous_spec_format"):
        import_spec(context, connector_id="custom", source_path=ambiguous)


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


def test_network_policy_rejects_remote_credential_query_before_dns(monkeypatch) -> None:
    def unexpected_dns(*_args, **_kwargs):
        raise AssertionError("credential-bearing URL must be rejected before DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)

    with pytest.raises(NetworkPolicyError, match="remote_credential_query_forbidden"):
        NetworkPolicy().resolve_https_target(
            "https://example.test/openapi.json?api_key=raw-value"
        )


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
        return httpx.Response(200, json=document)

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
