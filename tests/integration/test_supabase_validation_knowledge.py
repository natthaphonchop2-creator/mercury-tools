from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import pytest

pytestmark = pytest.mark.integration

_OPT_IN = "MERCURY_SUPABASE_VALIDATION_TEST"
_ISOLATED_OPT_IN = "MERCURY_SUPABASE_TEST_ISOLATED"
_REQUIRED_ENV = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_AUTHENTICATED_TEST_JWT",
)
_UNAVAILABLE_REASON = (
    "requires a disposable local or explicitly isolated Supabase environment with "
    "the Task 3 migration applied"
)


@dataclass(frozen=True)
class _SupabaseTestEnvironment:
    rest_url: str
    service_headers: dict[str, str]
    anon_headers: dict[str, str]
    authenticated_headers: dict[str, str]


def _test_environment() -> _SupabaseTestEnvironment:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"{_UNAVAILABLE_REASON}; set {_OPT_IN}=1 to opt in")

    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.skip(f"{_UNAVAILABLE_REASON}; missing environment names: {', '.join(missing)}")

    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    hostname = (urlparse(supabase_url).hostname or "").lower()
    is_loopback = hostname in {"127.0.0.1", "::1", "localhost"}
    if not is_loopback and os.environ.get(_ISOLATED_OPT_IN) != "1":
        pytest.skip(
            f"{_UNAVAILABLE_REASON}; non-loopback environments require "
            f"{_ISOLATED_OPT_IN}=1"
        )

    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    anon_key = os.environ["SUPABASE_ANON_KEY"]
    authenticated_jwt = os.environ["SUPABASE_AUTHENTICATED_TEST_JWT"]
    return _SupabaseTestEnvironment(
        rest_url=f"{supabase_url}/rest/v1",
        service_headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        },
        anon_headers={"apikey": anon_key, "Authorization": f"Bearer {anon_key}"},
        authenticated_headers={
            "apikey": anon_key,
            "Authorization": f"Bearer {authenticated_jwt}",
        },
    )


def _assert_status(response: httpx.Response, expected: int | set[int]) -> None:
    expected_statuses = {expected} if isinstance(expected, int) else expected
    assert response.status_code in expected_statuses, (
        f"unexpected Supabase HTTP status {response.status_code}"
    )


def _post_rows(
    client: httpx.Client,
    environment: _SupabaseTestEnvironment,
    table: str,
    rows: dict[str, object] | list[dict[str, object]],
    *,
    prefer: str = "return=minimal",
) -> httpx.Response:
    return client.post(
        f"{environment.rest_url}/{table}",
        headers={**environment.service_headers, "Prefer": prefer},
        json=rows,
    )


def test_validation_knowledge_allows_typed_schema_names_and_enforces_security() -> None:
    environment = _test_environment()
    suffix = uuid.uuid4().hex
    connector_id = f"synthetic_{suffix[:12]}"
    source_id = f"src_{suffix[:24]}"
    action_id = f"act_{suffix[:24]}"
    version_id = f"av_{suffix}{suffix}"
    evidence_id = f"ev_{suffix[:26]}"
    run_id = f"run_{suffix[:26]}"

    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        for unsafe_key in (
            "authorization_header",
            "client_secret",
            "raw_payload",
            "source_path",
        ):
            response = client.post(
                f"{environment.rest_url}/rpc/jsonb_has_forbidden_validation_key",
                headers=environment.service_headers,
                json={"value": {"nested": [{unsafe_key: "synthetic_value"}]}},
            )
            _assert_status(response, 200)
            assert response.json() is True

        response = client.post(
            f"{environment.rest_url}/rpc/jsonb_has_forbidden_validation_key",
            headers=environment.service_headers,
            json={
                "value": {
                    "nested": {
                        "document_id": "string",
                        "email": "string",
                        "record_id": "string",
                        "counterparty_tax_id": "string",
                    }
                }
            },
        )
        _assert_status(response, 200)
        assert response.json() is False

        response = _post_rows(
            client,
            environment,
            "erp_spec_sources",
            {
                "source_id": source_id,
                "connector_id": connector_id,
                "source_type": "documentation",
                "source_uri": "https://example.invalid/synthetic-contract",
                "source_hash": "a" * 64,
                "imported_version": "synthetic-v1",
                "sanitization": {},
                "metadata": {},
                "imported_at": "2026-07-13T00:00:00Z",
            },
        )
        _assert_status(response, 201)

        response = _post_rows(
            client,
            environment,
            "erp_action_versions",
            {
                "action_id": action_id,
                "version_id": version_id,
                "connector_id": connector_id,
                "method": "GET",
                "path_template": "/synthetic",
                "definition": {},
                "source_id": source_id,
            },
        )
        _assert_status(response, 201)

        record: dict[str, object] = {
            "opaque_evidence_id": evidence_id,
            "run_id": run_id,
            "action_id": action_id,
            "version_id": version_id,
            "connector_id": connector_id,
            "environment": "test",
            "validation_status": "contract_validated",
            "evidence_level": "contract_validated",
            "execution_eligibility": "discovery_only",
            "run_state": "completed",
            "approved_public": False,
            "summary_th": "synthetic_th_summary",
            "summary_en": "synthetic_en_summary",
            "prerequisites": ["synthetic_prerequisite"],
            "limitations": ["synthetic_limitation"],
            "recommended_next_step": "synthetic_next_step",
            "response_shape": {
                "data": {
                    "document_id": "string",
                    "email": "string",
                    "record_id": "string",
                }
            },
            "status_class": "not_attempted",
            "latency_ms": None,
            "semantic_contract": {
                "business_object": "synthetic_document",
                "operation": "read",
                "output_semantics": {
                    "counterparty_tax_id": "string",
                    "document_id": "string",
                    "record_id": "string",
                },
            },
            "evidence_sha256": "b" * 64,
            "reviewed_by": "synthetic_reviewer_role",
            "runner_version": "synthetic_runner",
            "evaluated_at": "2026-07-13T00:00:00Z",
        }
        response = _post_rows(
            client,
            environment,
            "erp_action_validation_knowledge",
            record,
            prefer="return=representation",
        )
        _assert_status(response, 201)
        assert response.json()[0]["opaque_evidence_id"] == evidence_id

        unsafe_record = {
            **record,
            "opaque_evidence_id": f"ev_{suffix[1:27]}",
            "run_id": f"run_{suffix[1:27]}",
            "response_shape": {"nested": {"authorization_header": "string"}},
        }
        response = _post_rows(
            client,
            environment,
            "erp_action_validation_knowledge",
            unsafe_record,
        )
        _assert_status(response, 400)

        for role_headers in (environment.anon_headers, environment.authenticated_headers):
            response = client.get(
                f"{environment.rest_url}/erp_action_validation_knowledge",
                headers=role_headers,
                params={"select": "id", "limit": "1"},
            )
            _assert_status(response, {401, 403})

        mutation_url = f"{environment.rest_url}/erp_action_validation_knowledge"
        mutation_params = {"opaque_evidence_id": f"eq.{evidence_id}"}
        response = client.patch(
            mutation_url,
            headers=environment.service_headers,
            params=mutation_params,
            json={"summary_en": "synthetic_changed_summary"},
        )
        _assert_status(response, 400)
        assert response.json().get("message") == "erp_validation_evidence_is_append_only"
        response = client.delete(
            mutation_url,
            headers=environment.service_headers,
            params=mutation_params,
        )
        _assert_status(response, 400)
        assert response.json().get("message") == "erp_validation_evidence_is_append_only"

        observation_id = f"event_{suffix}"
        response = _post_rows(
            client,
            environment,
            "erp_action_observations",
            {
                "opaque_event_id": observation_id,
                "action_id": action_id,
                "version_id": version_id,
                "connector_id": connector_id,
                "method": "GET",
                "observed_state": "success",
                "status_class": "2xx",
                "latency_ms": 1,
                "metadata": {"source": "synthetic_test"},
            },
        )
        _assert_status(response, 201)

        observation_url = f"{environment.rest_url}/erp_action_observations"
        observation_params = {"opaque_event_id": f"eq.{observation_id}"}
        response = client.patch(
            observation_url,
            headers=environment.service_headers,
            params=observation_params,
            json={"status_class": "3xx"},
        )
        _assert_status(response, 400)
        assert response.json().get("message") == "erp_validation_evidence_is_append_only"
        response = client.delete(
            observation_url,
            headers=environment.service_headers,
            params=observation_params,
        )
        _assert_status(response, 400)
        assert response.json().get("message") == "erp_validation_evidence_is_append_only"
