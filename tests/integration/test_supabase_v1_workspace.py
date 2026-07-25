from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import UUID

import httpx
import pytest

pytestmark = pytest.mark.integration

_OPT_IN = "MERCURY_V1_SUPABASE_TEST"
_ISOLATED_OPT_IN = "MERCURY_V1_SUPABASE_TEST_ISOLATED"
_KNOWN_PRODUCTION_PROJECT_REF = "vbnlkqvauqwnjbxngkas"
_REQUIRED_ENV = (
    "SUPABASE_PUBLISHABLE_KEY",
    "MERCURY_V1_TEST_USER_A_TOKEN",
    "MERCURY_V1_TEST_USER_B_TOKEN",
)
_UNAVAILABLE = (
    "requires an explicitly isolated Supabase branch/test project with the "
    "Mercury V1 identity migration applied"
)


@dataclass(frozen=True)
class _Environment:
    rest_url: str
    publishable_key: str
    user_a_token: str
    user_b_token: str


def _environment() -> _Environment:
    supabase_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    hostname = (urlparse(supabase_url).hostname or "").lower()
    project_ref = hostname.split(".", 1)[0]
    if project_ref == _KNOWN_PRODUCTION_PROJECT_REF:
        raise RuntimeError("mercury_v1_workspace_test_production_refused")

    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"{_UNAVAILABLE}; set {_OPT_IN}=1 to opt in")
    if os.environ.get(_ISOLATED_OPT_IN) != "1":
        pytest.skip(f"{_UNAVAILABLE}; set {_ISOLATED_OPT_IN}=1")
    if not supabase_url or urlparse(supabase_url).scheme not in {"http", "https"}:
        pytest.skip(f"{_UNAVAILABLE}; SUPABASE_URL is missing or invalid")

    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.skip(f"{_UNAVAILABLE}; missing environment names: {', '.join(missing)}")

    return _Environment(
        rest_url=f"{supabase_url}/rest/v1",
        publishable_key=os.environ["SUPABASE_PUBLISHABLE_KEY"],
        user_a_token=os.environ["MERCURY_V1_TEST_USER_A_TOKEN"],
        user_b_token=os.environ["MERCURY_V1_TEST_USER_B_TOKEN"],
    )


def _headers(environment: _Environment, token: str) -> dict[str, str]:
    return {
        "apikey": environment.publishable_key,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _bootstrap(environment: _Environment, token: str) -> dict[str, object]:
    response = httpx.post(
        f"{environment.rest_url}/rpc/bootstrap_mercury_context",
        headers=_headers(environment, token),
        json={},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    assert isinstance(rows, list) and len(rows) == 1
    assert isinstance(rows[0], dict)
    return rows[0]


def _visible_rows(
    environment: _Environment,
    token: str,
    table: str,
    *,
    params: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    response = httpx.get(
        f"{environment.rest_url}/{table}",
        headers=_headers(environment, token),
        params=params or {"select": "*"},
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()
    assert isinstance(rows, list)
    return rows


def test_bootstrap_converges_and_rls_blocks_cross_tenant_access() -> None:
    environment = _environment()

    with ThreadPoolExecutor(max_workers=12) as executor:
        contexts = list(
            executor.map(
                lambda _: _bootstrap(environment, environment.user_a_token),
                range(24),
            )
        )

    active_workspace_ids = {
        UUID(str(context["active_workspace_id"])) for context in contexts
    }
    assert len(active_workspace_ids) == 1
    active_workspace_id = next(iter(active_workspace_ids))

    first_context = contexts[0]
    assert first_context["status"] == "ok"
    assert first_context["next_allowed_actions"] == [
        "list_accounting_providers",
        "start_provider_connection",
    ]
    memberships = first_context["memberships"]
    assert isinstance(memberships, list) and len(memberships) == 1
    assert memberships[0]["workspace_id"] == str(active_workspace_id)
    assert memberships[0]["role"] == "owner"
    serialized = str(first_context).lower()
    assert "email" not in serialized
    assert "token" not in serialized
    assert "credential" not in serialized
    assert "provider_state" not in serialized
    assert "provider_credentials" not in serialized

    assert len(_visible_rows(environment, environment.user_a_token, "mercury_tenants")) == 1
    assert len(_visible_rows(environment, environment.user_a_token, "mercury_workspaces")) == 1
    assert (
        len(
            _visible_rows(
                environment,
                environment.user_a_token,
                "mercury_workspace_members",
            )
        )
        == 1
    )

    other_context = _bootstrap(environment, environment.user_b_token)
    other_workspace_id = UUID(str(other_context["active_workspace_id"]))
    assert other_workspace_id != active_workspace_id
    assert _visible_rows(
        environment,
        environment.user_b_token,
        "mercury_workspaces",
        params={
            "select": "id",
            "id": f"eq.{active_workspace_id}",
        },
    ) == []
    assert _visible_rows(
        environment,
        environment.user_a_token,
        "mercury_workspaces",
        params={
            "select": "id",
            "id": f"eq.{other_workspace_id}",
        },
    ) == []
