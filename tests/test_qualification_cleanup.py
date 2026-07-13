from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from mercury_tools.qualification import run_store as run_store_module
from mercury_tools.qualification.fixtures import (
    CleanupCoordinator,
    CleanupOutcome,
    FixtureRegistry,
)
from mercury_tools.qualification.models import QualificationRunState
from mercury_tools.qualification.run_store import (
    CleanupStatus,
    QualificationRunStore,
)

RUN_ID = "run_01ARZ3NDEKTSV4RRFFQ69G5FAV"
CONTACT_HANDLE = "fx_01ARZ3NDEKTSV4RRFFQ69G5FAA"
INVOICE_HANDLE = "fx_01ARZ3NDEKTSV4RRFFQ69G5FAB"
CONTACT_ACTION = "act_" + "1" * 24
CONTACT_VERSION = "av_" + "1" * 64
INVOICE_ACTION = "act_" + "2" * 24
INVOICE_VERSION = "av_" + "2" * 64
DELETE_CONTACT_ACTION = "act_" + "3" * 24
DELETE_CONTACT_VERSION = "av_" + "3" * 64
VOID_INVOICE_ACTION = "act_" + "4" * 24
VOID_INVOICE_VERSION = "av_" + "4" * 64


class RecordingCleanupAdapter:
    def __init__(
        self,
        *,
        outcomes: dict[str, CleanupOutcome] | None = None,
        failures: frozenset[str] = frozenset(),
    ) -> None:
        self.outcomes = outcomes or {}
        self.failures = failures
        self.calls: list[tuple[str, str, tuple[str, str]]] = []

    async def cleanup(self, fixture) -> CleanupOutcome:
        self.calls.append((fixture.handle, fixture.provider_id, fixture.cleanup_action_ref))
        if fixture.handle in self.failures:
            raise RuntimeError("provider cleanup failed for provider-private-999")
        return self.outcomes.get(fixture.handle, CleanupOutcome.CLEANED)


def _registry() -> FixtureRegistry:
    registry = FixtureRegistry(run_id=RUN_ID, prefix="MERCURY-V021")
    registry.register(
        handle=CONTACT_HANDLE,
        provider_id="provider-contact-private-123",
        action_ref=(CONTACT_ACTION, CONTACT_VERSION),
        cleanup_action_ref=(DELETE_CONTACT_ACTION, DELETE_CONTACT_VERSION),
    )
    registry.register(
        handle=INVOICE_HANDLE,
        provider_id="provider-invoice-private-456",
        action_ref=(INVOICE_ACTION, INVOICE_VERSION),
        cleanup_action_ref=(VOID_INVOICE_ACTION, VOID_INVOICE_VERSION),
        depends_on=(CONTACT_HANDLE,),
    )
    return registry


def _state_payload(root: Path) -> dict[str, object]:
    path = root / ".mercury" / "validation" / RUN_ID / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_cleanup_runs_once_in_reverse_dependency_order_and_persists_opaque_state(
    tmp_path: Path,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    adapter = RecordingCleanupAdapter()

    report = asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())

    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE, CONTACT_HANDLE]
    assert report.attempted_handles == (INVOICE_HANDLE, CONTACT_HANDLE)
    assert report.cleaned_handles == (INVOICE_HANDLE, CONTACT_HANDLE)
    assert report.failed_handles == ()
    assert report.outcome_unknown_handles == ()
    assert report.run_state is QualificationRunState.COMPLETED
    assert report.publication_allowed is True

    repeated = asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())
    assert repeated.attempted_handles == ()
    assert len(adapter.calls) == 2

    state_path = tmp_path / ".mercury" / "validation" / RUN_ID / "state.json"
    assert tuple(path.name for path in state_path.parent.iterdir()) == ("state.json",)
    payload = _state_payload(tmp_path)
    assert set(payload) == {
        "created_at",
        "fixtures",
        "publication_allowed",
        "quarantine_reason",
        "run_id",
        "state",
        "updated_at",
    }
    assert payload["state"] == "completed"
    assert payload["publication_allowed"] is True
    assert [fixture["handle"] for fixture in payload["fixtures"]] == [
        CONTACT_HANDLE,
        INVOICE_HANDLE,
    ]
    assert all(
        set(fixture)
        == {
            "action_id",
            "cleanup_action_id",
            "cleanup_status",
            "cleanup_updated_at",
            "cleanup_version_id",
            "depends_on",
            "handle",
            "registered_at",
            "version_id",
        }
        for fixture in payload["fixtures"]
    )

    serialized = state_path.read_text(encoding="utf-8")
    for private_value in (
        "provider-contact-private-123",
        "provider-invoice-private-456",
        "MERCURY-V021",
        "company",
        "credential",
        "token",
        "https://",
        str(tmp_path),
        "@",
    ):
        assert private_value not in serialized


def test_registry_public_references_and_cleanup_report_never_expose_provider_ids(
    tmp_path: Path,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    adapter = RecordingCleanupAdapter()

    report = asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())
    public = json.dumps(
        {
            "fixtures": [item.model_dump(mode="json") for item in registry.references],
            "report": report.model_dump(mode="json"),
        },
        sort_keys=True,
    )

    assert "provider-contact-private-123" not in public
    assert "provider-invoice-private-456" not in public
    assert "provider_id" not in public
    assert "raw" not in public


def test_cleanup_failure_quarantines_run_and_keeps_report_payload_free(
    tmp_path: Path,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    adapter = RecordingCleanupAdapter(failures=frozenset({INVOICE_HANDLE}))

    report = asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())

    assert report.attempted_handles == (INVOICE_HANDLE, CONTACT_HANDLE)
    assert report.failed_handles == (INVOICE_HANDLE,)
    assert report.cleaned_handles == (CONTACT_HANDLE,)
    assert report.run_state is QualificationRunState.QUARANTINED
    assert report.publication_allowed is False
    assert store.publication_allowed is False
    assert _state_payload(tmp_path)["quarantine_reason"] == "cleanup_failed"
    assert "provider-private-999" not in report.model_dump_json()
    assert "provider-private-999" not in json.dumps(_state_payload(tmp_path))


def test_unknown_cleanup_outcome_quarantines_and_is_never_retried(
    tmp_path: Path,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    adapter = RecordingCleanupAdapter(outcomes={INVOICE_HANDLE: CleanupOutcome.OUTCOME_UNKNOWN})
    coordinator = CleanupCoordinator(registry, adapter, store)

    first = asyncio.run(coordinator.cleanup())
    second = asyncio.run(coordinator.cleanup())

    assert first.outcome_unknown_handles == (INVOICE_HANDLE,)
    assert first.run_state is QualificationRunState.QUARANTINED
    assert first.publication_allowed is False
    assert second.attempted_handles == ()
    assert [call[0] for call in adapter.calls].count(INVOICE_HANDLE) == 1
    assert store.cleanup_status(INVOICE_HANDLE) is CleanupStatus.OUTCOME_UNKNOWN
    assert _state_payload(tmp_path)["quarantine_reason"] == "outcome_unknown"


def test_explicit_unknown_mutation_quarantines_without_cleanup_dispatch(
    tmp_path: Path,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    store.record_fixtures(registry.references)
    store.quarantine("outcome_unknown")
    adapter = RecordingCleanupAdapter()

    report = asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())

    assert adapter.calls == []
    assert report.attempted_handles == ()
    assert report.run_state is QualificationRunState.QUARANTINED
    assert report.publication_allowed is False


def test_process_loss_quarantines_pending_state_instead_of_resuming_cleanup(
    tmp_path: Path,
) -> None:
    registry = _registry()
    original = QualificationRunStore(tmp_path, RUN_ID)
    original.record_fixtures(registry.references)

    recovered = QualificationRunStore(tmp_path, RUN_ID)

    assert recovered.state is QualificationRunState.QUARANTINED
    assert recovered.publication_allowed is False
    assert _state_payload(tmp_path)["quarantine_reason"] == "process_lost"


@pytest.mark.parametrize(
    "run_id",
    [
        "run_demo",
        "../run_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "run_01ARZ3NDEKTSV4RRFFQ69G5FAI",
        "run_01ARZ3NDEKTSV4RRFFQ69G5FAV/child",
    ],
)
def test_run_store_rejects_invalid_run_ids_before_writing(
    tmp_path: Path,
    run_id: str,
) -> None:
    with pytest.raises(ValueError, match="^qualification_run_id_invalid$"):
        QualificationRunStore(tmp_path, run_id)

    assert not (tmp_path / ".mercury").exists()


@pytest.mark.skipif(os.name != "posix", reason="symlink confinement requires POSIX")
@pytest.mark.parametrize(
    "unsafe_kind",
    ["mercury_symlink", "run_symlink", "state_symlink", "state_dir"],
)
def test_run_store_rejects_symlinked_or_non_regular_state_paths_without_escape(
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_text("external sentinel", encoding="utf-8")

    if unsafe_kind == "mercury_symlink":
        (tmp_path / ".mercury").symlink_to(outside, target_is_directory=True)
    else:
        validation = tmp_path / ".mercury" / "validation"
        validation.mkdir(parents=True)
        run_dir = validation / RUN_ID
        if unsafe_kind == "run_symlink":
            run_dir.symlink_to(outside, target_is_directory=True)
        else:
            run_dir.mkdir()
            state_path = run_dir / "state.json"
            if unsafe_kind == "state_symlink":
                state_path.symlink_to(sentinel)
            else:
                state_path.mkdir()

    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        QualificationRunStore(tmp_path, RUN_ID)

    assert sentinel.read_text(encoding="utf-8") == "external sentinel"
    assert not (outside / "validation" / RUN_ID / "state.json").exists()


def test_atomic_write_failure_keeps_previous_fail_closed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = QualificationRunStore(tmp_path, RUN_ID)
    previous = _state_payload(tmp_path)

    def fail_rename(*args, **kwargs):
        raise OSError("simulated atomic rename failure")

    monkeypatch.setattr(run_store_module.os, "rename", fail_rename)

    with pytest.raises(ValueError, match="^qualification_state_write_failed$"):
        store.complete()

    assert store.state is QualificationRunState.FAILED
    assert store.publication_allowed is False
    assert _state_payload(tmp_path) == previous
    run_dir = tmp_path / ".mercury" / "validation" / RUN_ID
    assert not tuple(run_dir.glob(".state-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="dir_fd checks require POSIX")
@pytest.mark.parametrize("missing_capability", ["rename_dir_fd", "stat_nofollow"])
def test_missing_atomic_capability_fails_before_any_runtime_state_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_capability: str,
) -> None:
    if missing_capability == "stat_nofollow":
        monkeypatch.setattr(
            run_store_module.os,
            "supports_follow_symlinks",
            run_store_module.os.supports_follow_symlinks - {run_store_module.os.stat},
        )
    else:
        monkeypatch.setattr(
            run_store_module.os,
            "supports_dir_fd",
            run_store_module.os.supports_dir_fd - {run_store_module.os.rename},
        )

    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        QualificationRunStore(tmp_path, RUN_ID)

    assert not (tmp_path / ".mercury").exists()


@pytest.mark.parametrize(
    ("dependencies", "error"),
    [
        (("fx_01ARZ3NDEKTSV4RRFFQ69G5FAC",), "fixture_dependency_missing"),
        ((INVOICE_HANDLE,), "fixture_dependency_cycle"),
    ],
)
def test_registry_dependency_errors_are_exact_and_payload_free(
    dependencies: tuple[str, ...],
    error: str,
) -> None:
    registry = FixtureRegistry(run_id=RUN_ID)
    registry.register(
        handle=CONTACT_HANDLE,
        provider_id="provider-contact-private-123",
        action_ref=(CONTACT_ACTION, CONTACT_VERSION),
        cleanup_action_ref=(DELETE_CONTACT_ACTION, DELETE_CONTACT_VERSION),
        depends_on=dependencies,
    )
    if error == "fixture_dependency_cycle":
        registry.register(
            handle=INVOICE_HANDLE,
            provider_id="provider-invoice-private-456",
            action_ref=(INVOICE_ACTION, INVOICE_VERSION),
            cleanup_action_ref=(VOID_INVOICE_ACTION, VOID_INVOICE_VERSION),
            depends_on=(CONTACT_HANDLE,),
        )

    with pytest.raises(ValueError, match=f"^{error}$"):
        registry.dependency_order()


def test_registry_rejects_duplicate_opaque_handles() -> None:
    registry = _registry()

    with pytest.raises(ValueError, match="^fixture_handle_duplicate$"):
        registry.register(
            handle=CONTACT_HANDLE,
            provider_id="another-private-provider-id",
            action_ref=(CONTACT_ACTION, CONTACT_VERSION),
            cleanup_action_ref=(DELETE_CONTACT_ACTION, DELETE_CONTACT_VERSION),
        )
