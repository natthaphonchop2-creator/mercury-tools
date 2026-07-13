from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from mercury_tools.drivers.flowaccount import FlowAccountDriver
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import ensure_repository_state, load_repository_config
from mercury_tools.qualification.flowaccount import (
    SandboxRunApproval,
    create_flowaccount_qualification_runner,
)
from mercury_tools.qualification.manifest import LIVE_READS
from mercury_tools.qualification.models import QualificationRunState

ROOT = Path(__file__).resolve().parents[2]


class RecordingSandboxTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.delegate = httpx.AsyncHTTPTransport()
        self.requests: list[tuple[str, str]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, str(request.url)))
        return await self.delegate.handle_async_request(request)

    async def aclose(self) -> None:
        await self.delegate.aclose()


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("MERCURY_LIVE_FLOWACCOUNT_SANDBOX") != "1",
    reason="requires explicit FlowAccount sandbox qualification opt-in",
)
@pytest.mark.asyncio
async def test_live_flowaccount_sandbox_qualification_uses_only_reviewed_safe_reads() -> None:
    repository_root = Path(
        os.getenv("MERCURY_FLOWACCOUNT_SANDBOX_REPO_ROOT", str(ROOT))
    ).expanduser()
    context = ensure_repository_state(repository_root)
    config = load_repository_config(context)
    validation = config.validations.get("flowaccount", {}).get("sandbox")
    if not validation or not validation.get("company_name"):
        pytest.skip("requires explicit local FlowAccount sandbox tenant validation")

    driver = FlowAccountDriver()
    snapshot = CredentialStore(context).snapshot(
        "flowaccount",
        "sandbox",
        driver.credential_fields("sandbox"),
    )
    try:
        if not snapshot.status.configured:
            pytest.skip("requires explicit local FlowAccount sandbox credentials")
    finally:
        snapshot.credentials.clear()

    transport = RecordingSandboxTransport()
    runner = create_flowaccount_qualification_runner(
        repository_root,
        transport=transport,
    )
    report = await runner.qualify_all(approval=SandboxRunApproval(reads=True, writes=False))

    assert report.run_state is QualificationRunState.COMPLETED
    assert len(report.records) == 190
    assert len({(record.action_id, record.version_id) for record in report.records}) == 190
    assert runner.request_count <= 40
    assert len(transport.requests) == 2 + len(LIVE_READS)
    assert transport.requests[0] == (
        "POST",
        "https://openapi.flowaccount.com/test/token",
    )
    assert all(
        method == "GET" and url.startswith("https://openapi.flowaccount.com/test/")
        for method, url in transport.requests[1:]
    )
    assert all("/v1" not in url for _method, url in transport.requests)
    assert not any(
        method in {"POST", "PUT", "PATCH", "DELETE"} for method, _url in transport.requests[1:]
    )
