from __future__ import annotations

import asyncio
import errno
import json
import os
import stat
import traceback
from datetime import UTC, datetime, timedelta
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


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        if not self.values:
            raise AssertionError("clock sequence exhausted")
        return self.values.pop(0)


def _registry() -> FixtureRegistry:
    registry = FixtureRegistry(run_id=RUN_ID)
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
    assert store.publication_allowed is True

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
    assert payload["publication_allowed"] is False
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
        "company",
        "credential",
        "token",
        "https://",
        str(tmp_path),
        "@",
    ):
        assert private_value not in serialized

    reopened = QualificationRunStore(tmp_path, RUN_ID)
    assert reopened.state is QualificationRunState.COMPLETED
    assert reopened.publication_allowed is False
    assert _state_payload(tmp_path)["publication_allowed"] is False


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

    assert report.attempted_handles == (INVOICE_HANDLE,)
    assert report.failed_handles == (INVOICE_HANDLE,)
    assert report.cleaned_handles == ()
    assert report.run_state is QualificationRunState.QUARANTINED
    assert report.publication_allowed is False
    assert store.publication_allowed is False
    assert store.cleanup_status(CONTACT_HANDLE) is CleanupStatus.PENDING
    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE]
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
    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE]
    assert store.cleanup_status(INVOICE_HANDLE) is CleanupStatus.OUTCOME_UNKNOWN
    assert store.cleanup_status(CONTACT_HANDLE) is CleanupStatus.PENDING
    assert _state_payload(tmp_path)["quarantine_reason"] == "outcome_unknown"


@pytest.mark.parametrize(
    ("outcome", "expected_status", "reason"),
    [
        (CleanupOutcome.FAILED, CleanupStatus.FAILED, "cleanup_failed"),
        (
            CleanupOutcome.OUTCOME_UNKNOWN,
            CleanupStatus.OUTCOME_UNKNOWN,
            "outcome_unknown",
        ),
    ],
)
def test_terminal_cleanup_and_quarantine_use_one_atomic_transition(
    tmp_path: Path,
    outcome: CleanupOutcome,
    expected_status: CleanupStatus,
    reason: str,
) -> None:
    created_at = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    registered_at = created_at + timedelta(seconds=1)
    terminal_at = created_at + timedelta(seconds=3)
    rollback_at = created_at + timedelta(seconds=2)
    clock = SequenceClock(created_at, registered_at, terminal_at, rollback_at)
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID, clock=clock)
    adapter = RecordingCleanupAdapter(outcomes={INVOICE_HANDLE: outcome})
    coordinator = CleanupCoordinator(registry, adapter, store)

    report = asyncio.run(coordinator.cleanup())
    same_coordinator_retry = asyncio.run(coordinator.cleanup())
    retry_adapter = RecordingCleanupAdapter()
    new_coordinator_retry = asyncio.run(
        CleanupCoordinator(registry, retry_adapter, store).cleanup()
    )

    assert report.run_state is QualificationRunState.QUARANTINED
    assert report.publication_allowed is False
    assert store.cleanup_status(INVOICE_HANDLE) is expected_status
    assert store.cleanup_status(CONTACT_HANDLE) is CleanupStatus.PENDING
    assert store.quarantine_reason == reason
    persisted = _state_payload(tmp_path)
    persisted_fixtures = {fixture["handle"]: fixture for fixture in persisted["fixtures"]}
    assert persisted["state"] == "quarantined"
    assert persisted["publication_allowed"] is False
    assert persisted["quarantine_reason"] == reason
    assert persisted_fixtures[INVOICE_HANDLE]["cleanup_status"] == expected_status
    assert persisted_fixtures[CONTACT_HANDLE]["cleanup_status"] == "pending"
    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE]
    assert same_coordinator_retry.attempted_handles == ()
    assert new_coordinator_retry.attempted_handles == ()
    assert retry_adapter.calls == []
    assert clock.calls == 3


@pytest.mark.parametrize(
    "status",
    [
        CleanupStatus.PENDING,
        CleanupStatus.FAILED,
        CleanupStatus.OUTCOME_UNKNOWN,
    ],
)
def test_mark_cleanup_rejects_non_cleaned_status_without_skipping_fixture(
    tmp_path: Path,
    status: CleanupStatus,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    store.record_fixtures(registry.references)
    before = store.state_path.read_bytes()

    with pytest.raises(ValueError) as raised:
        store.mark_cleanup(INVOICE_HANDLE, status)

    assert raised.value.args == ("qualification_cleanup_status_invalid",)
    assert store.cleanup_status(INVOICE_HANDLE) is CleanupStatus.PENDING
    assert store.state_path.read_bytes() == before

    adapter = RecordingCleanupAdapter()
    report = asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())

    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE, CONTACT_HANDLE]
    assert report.run_state is QualificationRunState.COMPLETED
    assert report.publication_allowed is True


def test_terminal_transition_error_stops_before_prerequisite_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    adapter = RecordingCleanupAdapter(outcomes={INVOICE_HANDLE: CleanupOutcome.OUTCOME_UNKNOWN})

    def fail_terminal_transition(handle: str, status: CleanupStatus) -> None:
        raise ValueError("qualification_state_write_failed")

    monkeypatch.setattr(store, "quarantine_cleanup", fail_terminal_transition)

    with pytest.raises(ValueError, match="^qualification_state_write_failed$"):
        asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())

    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE]
    assert store.cleanup_status(INVOICE_HANDLE) is CleanupStatus.PENDING
    assert store.cleanup_status(CONTACT_HANDLE) is CleanupStatus.PENDING
    assert store.publication_allowed is False


@pytest.mark.parametrize(
    "outcome",
    [CleanupOutcome.FAILED, CleanupOutcome.OUTCOME_UNKNOWN],
)
def test_terminal_write_failure_halts_live_registry_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: CleanupOutcome,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    store.record_fixtures(registry.references)
    adapter = RecordingCleanupAdapter(outcomes={INVOICE_HANDLE: outcome})

    def fail_rename(*args, **kwargs):
        raise OSError("simulated terminal state rename failure")

    monkeypatch.setattr(run_store_module.os, "rename", fail_rename)

    with pytest.raises(ValueError, match="^qualification_state_write_failed$"):
        asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())

    retry_adapter = RecordingCleanupAdapter()
    retry_report = asyncio.run(CleanupCoordinator(registry, retry_adapter, store).cleanup())

    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE]
    assert retry_adapter.calls == []
    assert retry_report.attempted_handles == ()
    assert retry_report.run_state is QualificationRunState.FAILED
    assert retry_report.publication_allowed is False
    assert store.cleanup_status(INVOICE_HANDLE) is CleanupStatus.PENDING
    assert store.cleanup_status(CONTACT_HANDLE) is CleanupStatus.PENDING
    assert "provider-contact-private-123" not in repr(registry)
    assert "provider-invoice-private-456" not in repr(registry)


def test_cleaned_write_failure_halts_registry_and_all_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    store = QualificationRunStore(tmp_path, RUN_ID)
    store.record_fixtures(registry.references)
    adapter = RecordingCleanupAdapter()
    coordinator = CleanupCoordinator(registry, adapter, store)

    def fail_rename(*args, **kwargs):
        raise OSError("simulated cleaned state rename failure")

    monkeypatch.setattr(run_store_module.os, "rename", fail_rename)

    with pytest.raises(ValueError, match="^qualification_state_write_failed$"):
        asyncio.run(coordinator.cleanup())

    same_coordinator_retry = asyncio.run(coordinator.cleanup())
    retry_adapter = RecordingCleanupAdapter()
    new_coordinator_retry = asyncio.run(
        CleanupCoordinator(registry, retry_adapter, store).cleanup()
    )

    assert registry.cleanup_halted is True
    assert [call[0] for call in adapter.calls] == [INVOICE_HANDLE]
    assert retry_adapter.calls == []
    assert same_coordinator_retry.attempted_handles == ()
    assert new_coordinator_retry.attempted_handles == ()
    assert store.state is QualificationRunState.FAILED
    assert store.cleanup_status(INVOICE_HANDLE) is CleanupStatus.PENDING
    assert store.cleanup_status(CONTACT_HANDLE) is CleanupStatus.PENDING
    assert store.publication_allowed is False


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


@pytest.mark.parametrize("transition", ["record", "cleanup", "quarantine", "complete"])
def test_state_transitions_reject_backwards_clock_before_write(
    tmp_path: Path,
    transition: str,
) -> None:
    created_at = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)
    later = created_at + timedelta(seconds=2)
    backwards = created_at + timedelta(seconds=1)
    clock = MutableClock(created_at)
    store = QualificationRunStore(tmp_path, RUN_ID, clock=clock)
    registry = _registry()

    clock.value = later
    if transition == "record":
        store.record_fixtures((registry.references[0],))
    else:
        store.record_fixtures(registry.references)
        if transition == "complete":
            store.mark_cleanup(CONTACT_HANDLE, CleanupStatus.CLEANED)
            store.mark_cleanup(INVOICE_HANDLE, CleanupStatus.CLEANED)

    before = store.state_path.read_bytes()
    clock.value = backwards

    with pytest.raises(ValueError) as raised:
        if transition == "record":
            store.record_fixtures(registry.references)
        elif transition == "cleanup":
            store.mark_cleanup(INVOICE_HANDLE, CleanupStatus.CLEANED)
        elif transition == "quarantine":
            store.quarantine("cleanup_failed")
        else:
            store.complete()

    assert raised.value.args == ("qualification_clock_regressed",)
    assert store.state_path.read_bytes() == before


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
@pytest.mark.parametrize(
    ("failure_phase", "expected_error"),
    [
        ("post_rename_binding", "qualification_state_path_unsafe"),
        ("directory_fsync", "qualification_state_write_failed"),
    ],
)
def test_failed_completion_and_quarantine_never_persist_publication_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
    expected_error: str,
) -> None:
    store = QualificationRunStore(tmp_path, RUN_ID)
    real_validate = run_store_module._validate_run_directory_binding
    real_fsync = run_store_module.os.fsync
    binding_checks = 0
    directory_fsync_failed = False

    def fail_after_rename_binding(root: Path, run_id: str, run_fd: int) -> None:
        nonlocal binding_checks
        real_validate(root, run_id, run_fd)
        binding_checks += 1
        if binding_checks >= 2:
            raise ValueError("qualification_state_path_unsafe")

    def fail_directory_fsync_then_quarantine_write(descriptor: int) -> None:
        nonlocal directory_fsync_failed
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if is_directory and not directory_fsync_failed:
            directory_fsync_failed = True
            raise OSError(errno.EIO, "simulated directory fsync failure")
        if directory_fsync_failed and not is_directory:
            raise OSError(errno.EIO, "simulated quarantine write failure")
        real_fsync(descriptor)

    with monkeypatch.context() as faults:
        if failure_phase == "post_rename_binding":
            faults.setattr(
                run_store_module,
                "_validate_run_directory_binding",
                fail_after_rename_binding,
            )
        else:
            faults.setattr(run_store_module.os, "fsync", fail_directory_fsync_then_quarantine_write)

        with pytest.raises(ValueError, match=f"^{expected_error}$"):
            store.complete()

        after_completion = _state_payload(tmp_path)
        assert after_completion["state"] == QualificationRunState.COMPLETED.value
        assert after_completion["publication_allowed"] is False

        with pytest.raises(ValueError, match=f"^{expected_error}$"):
            store.quarantine("cleanup_failed")

        assert _state_payload(tmp_path) == after_completion

    assert store.state is QualificationRunState.FAILED
    assert store.publication_allowed is False
    reopened = QualificationRunStore(tmp_path, RUN_ID)
    assert reopened.state is QualificationRunState.COMPLETED
    assert reopened.publication_allowed is False
    assert _state_payload(tmp_path)["publication_allowed"] is False
    run_dir = tmp_path / ".mercury" / "validation" / RUN_ID
    assert not tuple(run_dir.glob(".state-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="dir_fd checks require POSIX")
def test_run_directory_swap_before_rename_fails_closed_without_touching_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = QualificationRunStore(tmp_path, RUN_ID)
    run_dir = tmp_path / ".mercury" / "validation" / RUN_ID
    moved_run_dir = tmp_path / "moved-run"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "state.json"
    sentinel.write_text("outside sentinel", encoding="utf-8")
    real_rename = run_store_module.os.rename
    swapped = False

    def racing_rename(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and destination == "state.json":
            real_rename(run_dir, moved_run_dir)
            run_dir.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(run_store_module.os, "rename", racing_rename)

    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        store.complete()

    assert swapped is True
    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        _ = store.state
    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        _ = store.publication_allowed
    assert sentinel.read_text(encoding="utf-8") == "outside sentinel"
    assert not tuple(moved_run_dir.glob(".state-*.tmp"))
    assert tuple(outside.iterdir()) == (sentinel,)


@pytest.mark.skipif(os.name != "posix", reason="dir_fd checks require POSIX")
def test_run_directory_swap_during_final_fsync_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = QualificationRunStore(tmp_path, RUN_ID)
    run_dir = tmp_path / ".mercury" / "validation" / RUN_ID
    moved_run_dir = tmp_path / "moved-during-fsync"
    outside = tmp_path / "outside-fsync"
    outside.mkdir()
    sentinel = outside / "state.json"
    sentinel.write_text("outside fsync sentinel", encoding="utf-8")
    real_fsync = run_store_module.os.fsync
    real_rename = run_store_module.os.rename
    swapped = False

    def racing_fsync(descriptor: int) -> None:
        nonlocal swapped
        if not swapped and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            real_rename(run_dir, moved_run_dir)
            run_dir.symlink_to(outside, target_is_directory=True)
            swapped = True
        real_fsync(descriptor)

    monkeypatch.setattr(run_store_module.os, "fsync", racing_fsync)

    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        store.complete()

    assert swapped is True
    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        _ = store.state
    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        _ = store.publication_allowed
    assert sentinel.read_text(encoding="utf-8") == "outside fsync sentinel"
    assert not tuple(moved_run_dir.glob(".state-*.tmp"))
    assert tuple(outside.iterdir()) == (sentinel,)


@pytest.mark.skipif(os.name != "posix", reason="dir_fd checks require POSIX")
def test_run_directory_swap_after_final_check_never_authorizes_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = QualificationRunStore(tmp_path, RUN_ID)
    run_dir = tmp_path / ".mercury" / "validation" / RUN_ID
    moved_run_dir = tmp_path / "moved-after-final-check"
    outside = tmp_path / "outside-after-final-check"
    outside.mkdir()
    sentinel = outside / "state.json"
    sentinel.write_text("outside post-check sentinel", encoding="utf-8")
    real_validate = run_store_module._validate_run_directory_binding
    real_rename = run_store_module.os.rename
    validation_calls = 0

    def racing_validate(root: Path, run_id: str, run_fd: int) -> None:
        nonlocal validation_calls
        real_validate(root, run_id, run_fd)
        validation_calls += 1
        if validation_calls == 4:
            real_rename(run_dir, moved_run_dir)
            run_dir.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        run_store_module,
        "_validate_run_directory_binding",
        racing_validate,
    )

    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        store.complete()

    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        _ = store.state
    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        _ = store.publication_allowed

    registry = FixtureRegistry(run_id=RUN_ID)
    adapter = RecordingCleanupAdapter()
    with pytest.raises(ValueError, match="^qualification_state_path_unsafe$"):
        asyncio.run(CleanupCoordinator(registry, adapter, store).cleanup())

    assert validation_calls == 4
    assert adapter.calls == []
    assert sentinel.read_text(encoding="utf-8") == "outside post-check sentinel"
    assert tuple(outside.iterdir()) == (sentinel,)


@pytest.mark.skipif(os.name != "posix", reason="filesystem error probe requires POSIX")
def test_external_path_errors_have_no_cause_or_traceback_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = QualificationRunStore(tmp_path, RUN_ID)
    real_open = run_store_module.os.open
    private_path = tmp_path / "private-review-path"

    def failing_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None and os.fspath(path) == os.fspath(tmp_path):
            raise FileNotFoundError(errno.ENOENT, "review probe", private_path)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(run_store_module.os, "open", failing_open)

    with pytest.raises(ValueError) as raised:
        store.complete()

    assert raised.value.args == ("qualification_state_path_unsafe",)
    assert raised.value.__cause__ is None
    rendered = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    assert str(tmp_path) not in rendered
    assert str(private_path) not in rendered


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
