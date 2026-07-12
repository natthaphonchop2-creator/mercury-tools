from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mercury_tools.mcp.local_server import (
    active_root_paths,
    audit_resource,
    credential_status,
    import_erp_spec,
    list_workspace_flows,
    repository_from_context,
    run_workspace_flow,
    save_workspace_flow,
)


def _context(*uris: str) -> SimpleNamespace:
    class Session:
        async def list_roots(self) -> SimpleNamespace:
            return SimpleNamespace(
                roots=[SimpleNamespace(uri=uri) for uri in uris],
            )

    return SimpleNamespace(session=Session())


@pytest.mark.asyncio
async def test_active_root_paths_uses_symlink_resolved_mcp_roots(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)

    assert await active_root_paths(_context(alias.as_uri())) == (root.resolve(),)
    repository = await repository_from_context(_context(alias.as_uri()), None)
    assert repository.root == root.resolve()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uris", "repo_root", "status"),
    [
        ((), None, "mcp_roots_required"),
        (("https://example.test/repo",), None, "unsupported_root_uri"),
    ],
)
async def test_zero_and_invalid_roots_fail_closed(
    uris: tuple[str, ...],
    repo_root: str | None,
    status: str,
) -> None:
    result = await credential_status(
        connector="flowaccount",
        environment="production",
        ctx=_context(*uris),
        repo_root=repo_root,
    )
    assert result == {"status": status}


@pytest.mark.asyncio
async def test_multiple_roots_require_an_exact_explicit_selection(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    nested = first / "nested"
    first.mkdir()
    second.mkdir()
    nested.mkdir()
    ctx = _context(first.as_uri(), second.as_uri())

    implicit = await credential_status(
        connector="flowaccount",
        environment="production",
        ctx=ctx,
        repo_root=None,
    )
    nested_selection = await credential_status(
        connector="flowaccount",
        environment="production",
        ctx=ctx,
        repo_root=str(nested),
    )
    explicit = await credential_status(
        connector="flowaccount",
        environment="production",
        ctx=ctx,
        repo_root=str(second),
    )

    assert implicit == {"status": "multiple_mcp_roots"}
    assert nested_selection == {"status": "repo_root_not_active"}
    assert explicit["connector_id"] == "flowaccount"
    assert explicit["environment"] == "production"
    assert explicit["configured"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../outside.yaml", "/tmp/outside.yaml"])
async def test_workspace_flow_save_rejects_traversal_and_absolute_escape(
    tmp_path: Path,
    path: str,
) -> None:
    result = await save_workspace_flow(
        path=path,
        content="name: blocked\ncommands: []\n",
        ctx=_context(tmp_path.as_uri()),
        repo_root=None,
    )
    assert result == {"status": "path_outside_repository_root"}


@pytest.mark.asyncio
async def test_workspace_flow_save_rejects_symlink_escape(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    outside = tmp_path / "outside"
    repository.mkdir()
    outside.mkdir()
    (repository / "flows").symlink_to(outside, target_is_directory=True)

    result = await save_workspace_flow(
        path="flows/escape.yaml",
        content="name: blocked\ncommands: []\n",
        ctx=_context(repository.as_uri()),
        repo_root=None,
    )

    assert result == {"status": "path_outside_repository_root"}
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "run"])
async def test_workspace_flow_list_and_run_reject_symlink_escape(
    tmp_path: Path,
    operation: str,
) -> None:
    repository = tmp_path / "repository"
    outside = tmp_path / "outside"
    repository.mkdir()
    outside.mkdir()
    (outside / "outside.yaml").write_text(
        'name: Outside\n---\n- emitReport:\n    title: "Outside"\n'
    )
    (repository / "escaped").symlink_to(outside, target_is_directory=True)

    if operation == "list":
        result = await list_workspace_flows(
            path="escaped",
            ctx=_context(repository.as_uri()),
        )
    else:
        result = await run_workspace_flow(
            path="escaped/outside.yaml",
            ctx=_context(repository.as_uri()),
            dry_run=True,
        )

    assert result == {"status": "path_outside_repository_root"}


@pytest.mark.asyncio
async def test_workspace_flow_save_list_and_run_stay_in_one_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.mcp.local_runtime import LocalMercuryRuntime

    async def offline_refresh(self) -> None:
        return None

    monkeypatch.setattr(LocalMercuryRuntime, "refresh_catalog", offline_refresh)
    content = 'name: Local\n---\n- emitReport:\n    title: "Local"\n'
    ctx = _context(tmp_path.as_uri())

    saved = await save_workspace_flow(path="local.yaml", content=content, ctx=ctx)
    listed = await list_workspace_flows(path=".", ctx=ctx)
    ran = await run_workspace_flow(path="local.yaml", ctx=ctx, dry_run=True)

    assert saved["status"] == "saved"
    assert listed["flows"] == [
        {"path": "local.yaml", "name": "Local", "tags": [], "command_count": 1}
    ]
    assert ran["status"] == "planned"


@pytest.mark.asyncio
async def test_nested_workspace_flow_cannot_follow_symlink_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.mcp.local_runtime import LocalMercuryRuntime

    async def offline_refresh(self) -> None:
        return None

    monkeypatch.setattr(LocalMercuryRuntime, "refresh_catalog", offline_refresh)
    repository = tmp_path / "repository"
    outside = tmp_path / "outside"
    repository.mkdir()
    outside.mkdir()
    (outside / "child.yaml").write_text(
        'name: Child\n---\n- emitReport:\n    title: "Child"\n'
    )
    (repository / "child.yaml").symlink_to(outside / "child.yaml")
    (repository / "main.yaml").write_text(
        'name: Main\n---\n- runFlow:\n    file: "child.yaml"\n'
    )

    result = await run_workspace_flow(
        path="main.yaml",
        ctx=_context(repository.as_uri()),
    )

    assert result == {"status": "flow_invalid"}


@pytest.mark.asyncio
async def test_import_spec_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    outside = tmp_path / "outside"
    repository.mkdir()
    outside.mkdir()
    outside_spec = outside / "openapi.json"
    outside_spec.write_text("{}")
    linked_spec = repository / "linked.json"
    linked_spec.symlink_to(outside_spec)
    ctx = _context(repository.as_uri())

    traversed = await import_erp_spec(
        connector_id="custom",
        source_path="../outside/openapi.json",
        ctx=ctx,
    )
    linked = await import_erp_spec(
        connector_id="custom",
        source_path="linked.json",
        ctx=ctx,
    )

    assert traversed == {"status": "spec_source_outside_root"}
    assert linked == {"status": "spec_source_symlink"}


@pytest.mark.asyncio
async def test_audit_resource_fails_closed_without_roots() -> None:
    payload = await audit_resource(event_id="evt_1234567890abcdef12345678", ctx=_context())

    assert json.loads(payload) == {"status": "mcp_roots_required"}
