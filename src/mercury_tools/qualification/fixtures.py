"""In-memory fixture identities and deterministic cleanup coordination."""

from __future__ import annotations

import heapq
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import model_validator

from mercury_tools.qualification.models import QualificationRunState, StrictSafeModel
from mercury_tools.qualification.run_store import (
    CleanupStatus,
    FixtureReference,
    QualificationRunStore,
    validate_action_ref,
    validate_fixture_handle,
    validate_run_id,
)

_OPAQUE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class CleanupOutcome(StrEnum):
    CLEANED = "cleaned"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True, repr=False)
class FixtureCleanupTarget:
    """Process-memory-only cleanup binding; provider_id is never serialized."""

    handle: str
    provider_id: str = field(repr=False)
    action_ref: tuple[str, str]
    cleanup_action_ref: tuple[str, str]
    depends_on: tuple[str, ...]

    def __repr__(self) -> str:
        return (
            "FixtureCleanupTarget("
            f"handle={self.handle!r}, action_ref={self.action_ref!r}, "
            f"cleanup_action_ref={self.cleanup_action_ref!r}, depends_on={self.depends_on!r})"
        )


@runtime_checkable
class CleanupAdapter(Protocol):
    async def cleanup(self, fixture: FixtureCleanupTarget) -> CleanupOutcome:
        """Clean one exact in-memory provider fixture without retrying."""


class CleanupReport(StrictSafeModel):
    attempted_handles: tuple[str, ...] = ()
    cleaned_handles: tuple[str, ...] = ()
    failed_handles: tuple[str, ...] = ()
    outcome_unknown_handles: tuple[str, ...] = ()
    run_state: QualificationRunState
    publication_allowed: bool

    @model_validator(mode="after")
    def validate_handles(self) -> CleanupReport:
        for handles in (
            self.attempted_handles,
            self.cleaned_handles,
            self.failed_handles,
            self.outcome_unknown_handles,
        ):
            if len(handles) != len(set(handles)):
                raise ValueError("cleanup_report_handle_duplicate")
            for handle in handles:
                validate_fixture_handle(handle)
        attempted = set(self.attempted_handles)
        outcomes = (
            set(self.cleaned_handles) | set(self.failed_handles) | set(self.outcome_unknown_handles)
        )
        if not outcomes <= attempted:
            raise ValueError("cleanup_report_handle_invalid")
        if (
            set(self.cleaned_handles) & set(self.failed_handles)
            or set(self.cleaned_handles) & set(self.outcome_unknown_handles)
            or set(self.failed_handles) & set(self.outcome_unknown_handles)
        ):
            raise ValueError("cleanup_report_handle_invalid")
        if self.publication_allowed != (self.run_state is QualificationRunState.COMPLETED):
            raise ValueError("cleanup_report_state_invalid")
        return self


class FixtureRegistry:
    """Hold provider identifiers only for the lifetime of this process."""

    def __init__(self, *, run_id: str) -> None:
        self._run_id = validate_run_id(run_id)
        self._fixtures: dict[str, FixtureCleanupTarget] = {}
        self._claimed_handles: set[str] = set()
        self._cleanup_halted = False

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def references(self) -> tuple[FixtureReference, ...]:
        return tuple(_public_reference(self._fixtures[handle]) for handle in sorted(self._fixtures))

    def register(
        self,
        *,
        provider_id: str,
        action_ref: tuple[str, str],
        cleanup_action_ref: tuple[str, str],
        depends_on: tuple[str, ...] = (),
        handle: str | None = None,
    ) -> str:
        checked_handle = validate_fixture_handle(handle or _new_fixture_handle())
        if checked_handle in self._fixtures:
            raise ValueError("fixture_handle_duplicate")
        if (
            not isinstance(provider_id, str)
            or not provider_id
            or len(provider_id) > 2048
            or "\x00" in provider_id
        ):
            raise ValueError("fixture_provider_id_invalid")
        checked_action = validate_action_ref(action_ref)
        checked_cleanup_action = validate_action_ref(cleanup_action_ref)
        if not isinstance(depends_on, tuple):
            raise ValueError("fixture_dependency_invalid")
        try:
            checked_dependencies = tuple(
                validate_fixture_handle(dependency) for dependency in depends_on
            )
        except ValueError:
            raise ValueError("fixture_dependency_invalid") from None
        if len(checked_dependencies) != len(set(checked_dependencies)):
            raise ValueError("fixture_dependency_duplicate")

        self._fixtures[checked_handle] = FixtureCleanupTarget(
            handle=checked_handle,
            provider_id=provider_id,
            action_ref=checked_action,
            cleanup_action_ref=checked_cleanup_action,
            depends_on=checked_dependencies,
        )
        return checked_handle

    def dependency_order(self) -> tuple[FixtureCleanupTarget, ...]:
        handles = set(self._fixtures)
        successors = {handle: set() for handle in handles}
        indegree = {handle: 0 for handle in handles}
        for fixture in self._fixtures.values():
            for dependency in fixture.depends_on:
                if dependency not in handles:
                    raise ValueError("fixture_dependency_missing")
                if fixture.handle not in successors[dependency]:
                    successors[dependency].add(fixture.handle)
                    indegree[fixture.handle] += 1

        ready = [handle for handle, count in indegree.items() if count == 0]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            handle = heapq.heappop(ready)
            ordered.append(handle)
            for dependent in sorted(successors[handle]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, dependent)
        if len(ordered) != len(handles):
            raise ValueError("fixture_dependency_cycle")
        return tuple(self._fixtures[handle] for handle in ordered)

    def claim_cleanup(self, handle: str) -> bool:
        checked_handle = validate_fixture_handle(handle)
        if checked_handle not in self._fixtures:
            raise ValueError("fixture_handle_missing")
        if checked_handle in self._claimed_handles:
            return False
        self._claimed_handles.add(checked_handle)
        return True

    @property
    def cleanup_halted(self) -> bool:
        return self._cleanup_halted

    def halt_cleanup(self) -> None:
        self._cleanup_halted = True

    def __repr__(self) -> str:
        return f"FixtureRegistry(run_id={self.run_id!r}, handles={tuple(sorted(self._fixtures))!r})"


class CleanupCoordinator:
    def __init__(
        self,
        registry: FixtureRegistry,
        adapter: CleanupAdapter,
        run_store: QualificationRunStore,
    ) -> None:
        if not isinstance(registry, FixtureRegistry):
            raise ValueError("fixture_registry_invalid")
        if not isinstance(run_store, QualificationRunStore):
            raise ValueError("qualification_run_store_invalid")
        if registry.run_id != run_store.run_id:
            raise ValueError("qualification_run_store_mismatch")
        if not isinstance(adapter, CleanupAdapter):
            raise ValueError("cleanup_adapter_invalid")
        self.registry = registry
        self.adapter = adapter
        self.run_store = run_store

    async def cleanup(self) -> CleanupReport:
        if self.registry.cleanup_halted:
            return self._report()
        if self.run_store.state is QualificationRunState.QUARANTINED:
            return self._report()

        dependency_order = self.registry.dependency_order()
        if self.run_store.state is QualificationRunState.FAILED:
            self.run_store.record_fixtures(self.registry.references)
        if self.run_store.state is QualificationRunState.COMPLETED:
            return self._report()

        attempted: list[str] = []
        cleaned: list[str] = []
        failed: list[str] = []
        outcome_unknown: list[str] = []
        for fixture in reversed(dependency_order):
            if self.run_store.cleanup_status(fixture.handle) is not CleanupStatus.PENDING:
                continue
            if not self.registry.claim_cleanup(fixture.handle):
                continue
            attempted.append(fixture.handle)
            try:
                raw_outcome = await self.adapter.cleanup(fixture)
                outcome = CleanupOutcome(raw_outcome)
            except Exception:
                outcome = CleanupOutcome.FAILED

            if outcome is CleanupOutcome.CLEANED:
                self.run_store.mark_cleanup(fixture.handle, CleanupStatus.CLEANED)
                cleaned.append(fixture.handle)
            elif outcome is CleanupOutcome.FAILED:
                self.registry.halt_cleanup()
                self.run_store.quarantine_cleanup(fixture.handle, CleanupStatus.FAILED)
                failed.append(fixture.handle)
                break
            else:
                self.registry.halt_cleanup()
                self.run_store.quarantine_cleanup(
                    fixture.handle,
                    CleanupStatus.OUTCOME_UNKNOWN,
                )
                outcome_unknown.append(fixture.handle)
                break

        if not failed and not outcome_unknown:
            self.run_store.complete()
        return self._report(
            attempted=tuple(attempted),
            cleaned=tuple(cleaned),
            failed=tuple(failed),
            outcome_unknown=tuple(outcome_unknown),
        )

    def _report(
        self,
        *,
        attempted: tuple[str, ...] = (),
        cleaned: tuple[str, ...] = (),
        failed: tuple[str, ...] = (),
        outcome_unknown: tuple[str, ...] = (),
    ) -> CleanupReport:
        return CleanupReport(
            attempted_handles=attempted,
            cleaned_handles=cleaned,
            failed_handles=failed,
            outcome_unknown_handles=outcome_unknown,
            run_state=self.run_store.state,
            publication_allowed=self.run_store.publication_allowed,
        )


def _public_reference(fixture: FixtureCleanupTarget) -> FixtureReference:
    return FixtureReference(
        handle=fixture.handle,
        action_id=fixture.action_ref[0],
        version_id=fixture.action_ref[1],
        cleanup_action_id=fixture.cleanup_action_ref[0],
        cleanup_version_id=fixture.cleanup_action_ref[1],
        depends_on=fixture.depends_on,
    )


def _new_fixture_handle() -> str:
    return "fx_" + "".join(secrets.choice(_OPAQUE_ALPHABET) for _ in range(26))


__all__ = [
    "CleanupAdapter",
    "CleanupCoordinator",
    "CleanupOutcome",
    "CleanupReport",
    "FixtureCleanupTarget",
    "FixtureRegistry",
]
