from __future__ import annotations

import time

import pytest

from mercury_tools.workspaces.public import (
    new_public_workspace_id,
    normalize_public_workspace_id,
    public_workspace_connect_request,
    public_workspace_token_payload,
)


def test_public_workspace_id_is_opaque_and_validated() -> None:
    workspace_id = new_public_workspace_id()

    assert workspace_id.startswith("mw_")
    assert len(workspace_id) >= 24
    assert normalize_public_workspace_id(workspace_id) == workspace_id


def test_public_workspace_id_rejects_invalid_values() -> None:
    for value in ("", "workspace", "mw_short", "mw_with spaces and invalid chars"):
        with pytest.raises(ValueError, match="Invalid Mercury public workspace ID"):
            normalize_public_workspace_id(value)


def test_public_workspace_payload_uses_workspace_id_as_internal_jti() -> None:
    workspace_id = "mw_abcdefghijklmnopqrstuvwxyz"

    payload = public_workspace_token_payload(workspace_id)
    request = public_workspace_connect_request(workspace_id, "Demo Company")

    assert payload["jti"] == workspace_id
    assert payload["workspace_key"] == workspace_id
    assert payload["scope"] == ["public:workspace"]
    assert payload["exp"] - payload["iat"] == 60 * 60 * 24 * 30
    assert payload["exp"] > int(time.time())
    assert request.company == "Demo Company"
    assert request.host_app == "generic"
    assert workspace_id not in request.email


def test_public_workspace_request_uses_default_company_name() -> None:
    request = public_workspace_connect_request(
        "mw_abcdefghijklmnopqrstuvwxyz",
        "   ",
    )

    assert request.company == "Mercury Public Workspace"
