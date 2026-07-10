from __future__ import annotations

import httpx
import pytest

from mercury_tools.connectors.flowaccount_journal import (
    FlowAccountJournalClient,
    FlowAccountJournalError,
    FlowAccountOutcomeUnknown,
)


def make_client(handler) -> FlowAccountJournalClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return FlowAccountJournalClient(
        api_base_url="https://openapi.flowaccount.com/v1",
        token_url="https://openapi.flowaccount.com/v1/token",
        client_id="client-id",
        client_secret="client-secret",
        http_client=http,
    )


def test_client_reads_chart_and_creates_then_approves_draft() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/token":
            assert b"grant_type=client_credentials" in request.content
            assert b"scope=flowaccount-api" in request.content
            return httpx.Response(200, json={"access_token": "access-token"})
        assert request.headers["Authorization"] == "Bearer access-token"
        if request.url.path == "/v1/chart-of-accounts/accounts":
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "data": {"accounts": [{"id": 501, "code": "52010"}]},
                },
            )
        if request.url.path == "/v1/journal-entries/draft":
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "data": {
                        "recordId": 9001,
                        "documentSerial": "JV2026070001",
                        "status": 1,
                        "debit": 4236,
                        "credit": 4236,
                    },
                },
            )
        if request.url.path == "/v1/journal-entries/9001/approve":
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={"status": True, "data": {"recordId": 9001, "status": 5}},
            )
        raise AssertionError(request.url)

    client = make_client(handler)

    assert client.list_chart_accounts()[0]["id"] == 501
    assert client.create_draft({"documentType": 51})["recordId"] == 9001
    assert client.approve_draft(9001)["status"] == 5
    assert calls == [
        ("POST", "/v1/token"),
        ("GET", "/v1/chart-of-accounts/accounts"),
        ("POST", "/v1/token"),
        ("POST", "/v1/journal-entries/draft"),
        ("POST", "/v1/token"),
        ("POST", "/v1/journal-entries/9001/approve"),
    ]


def test_write_5xx_is_outcome_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        return httpx.Response(503, json={"message": "temporary failure"})

    with pytest.raises(FlowAccountOutcomeUnknown) as caught:
        make_client(handler).create_draft({"documentType": 51})

    assert caught.value.code == "outcome_unknown"
    assert caught.value.status_code == 503


def test_write_4xx_is_definitively_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        return httpx.Response(400, json={"status": False, "message": "Unbalanced"})

    with pytest.raises(FlowAccountJournalError) as caught:
        make_client(handler).create_draft({"documentType": 51})

    assert not isinstance(caught.value, FlowAccountOutcomeUnknown)
    assert caught.value.code == "rejected"
    assert caught.value.status_code == 400
    assert "Unbalanced" in str(caught.value)


def test_authentication_error_never_contains_client_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "client-secret was rejected",
            },
        )

    with pytest.raises(FlowAccountJournalError) as caught:
        make_client(handler).list_chart_accounts()

    assert caught.value.code == "authentication_failed"
    assert "client-secret" not in str(caught.value)


def test_chart_response_requires_accounts_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/token":
            return httpx.Response(200, json={"access_token": "access-token"})
        return httpx.Response(200, json={"status": True, "data": {"accounts": {}}})

    with pytest.raises(FlowAccountJournalError) as caught:
        make_client(handler).list_chart_accounts()

    assert caught.value.code == "invalid_response"
