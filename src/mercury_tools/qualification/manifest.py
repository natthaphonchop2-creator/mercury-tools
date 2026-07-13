"""Reviewed, deterministic FlowAccount sandbox execution policy."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from mercury_tools.catalog.models import (
    CatalogAction,
    HttpMethod,
    RiskTier,
    revalidate_catalog_action,
)
from mercury_tools.qualification.models import StrictSafeModel
from mercury_tools.qualification.semantics import load_actions

FLOWACCOUNT_ACTION_COUNT = 190
FLOWACCOUNT_METHOD_COUNTS: dict[HttpMethod, int] = {
    HttpMethod.GET: 36,
    HttpMethod.POST: 119,
    HttpMethod.PUT: 22,
    HttpMethod.DELETE: 13,
}
MAX_MANIFEST_BYTES = 8 * 1024 * 1024

LIVE_READS = frozenset(
    {
        (
            "act_cfda9281facf4a5e94129392",
            "av_c7e2e7bc876b02cb4c437066f4c7208436be307e1ef55ad1620b19d2ad47e99e",
        ),
        (
            "act_28a40ff500382918e7dc1ccb",
            "av_1ff16226fa09d2e131537507ed092e8a1a2f138399c98b4a4e49c7234b079137",
        ),
        (
            "act_4e0873e60b60925fa10dd30f",
            "av_ca1ef6ba45aeef5ba0a806707aac6bf9d5278b29f108ac61e4dc13f23f8bf64e",
        ),
        (
            "act_9a77991a6742a48906bbeca5",
            "av_b3090d992e0a927a73bb61fd6af1505ea3d9eeaebd740bb1b47c5483780577a8",
        ),
    }
)

_BLOCKED_EFFECT_SEGMENTS = frozenset({"email", "share", "payment", "void", "invite"})


class SandboxDisposition(StrEnum):
    SANDBOX_EXECUTABLE = "sandbox_executable"
    CONTRACT_ONLY = "contract_only"
    BLOCKED_EXTERNAL_EFFECT = "blocked_external_effect"
    UNSUPPORTED_BY_SANDBOX = "unsupported_by_sandbox"


class SandboxActionPolicy(StrictSafeModel):
    """One reviewed disposition for one immutable action version."""

    action_id: str
    version_id: str
    disposition: SandboxDisposition
    prerequisites: tuple[str, ...] = ()
    fixture_builder: str | None = None
    ownership_predicate: str | None = None
    cleanup_action_id: str | None = None
    external_effects: tuple[str, ...] = ()
    controlled_destination: bool = False
    max_attempts: int = Field(default=0, ge=0, le=2)
    request_budget: int = Field(default=0, ge=0, le=5)
    blocked_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_execution_contract(self) -> SandboxActionPolicy:
        if self.disposition is SandboxDisposition.SANDBOX_EXECUTABLE:
            if self.max_attempts != 1 or self.request_budget < 1:
                raise ValueError("sandbox_execution_budget_invalid")
            if self.external_effects and not self.controlled_destination:
                raise ValueError("controlled_destination_required")
            if self.blocked_reasons:
                raise ValueError("sandbox_execution_blocked_reasons_invalid")
            return self

        if self.max_attempts != 0 or self.request_budget != 0:
            raise ValueError("sandbox_non_executable_budget_invalid")
        if self.controlled_destination:
            raise ValueError("sandbox_non_executable_destination_invalid")
        if self.disposition is SandboxDisposition.BLOCKED_EXTERNAL_EFFECT:
            if not self.blocked_reasons:
                raise ValueError("sandbox_blocked_reasons_missing")
        elif self.blocked_reasons:
            raise ValueError("sandbox_non_blocked_reasons_invalid")
        return self

    def validate_against(
        self,
        action: CatalogAction,
        *,
        environment: str,
    ) -> SandboxActionPolicy:
        """Bind this policy to the action's exact reviewed execution contract."""
        checked = _validated_action(action)
        if (self.action_id, self.version_id) != (checked.action_id, checked.version_id):
            raise ValueError("sandbox_action_identity_mismatch")
        if environment != "sandbox" or "sandbox" not in checked.environments:
            raise ValueError("sandbox_action_environment_invalid")

        if self.disposition is not SandboxDisposition.SANDBOX_EXECUTABLE:
            return self
        if tuple(self.external_effects) != tuple(checked.side_effects):
            raise ValueError("sandbox_execution_side_effects_mismatch")
        if checked.method is HttpMethod.GET:
            if checked.risk_tier is not RiskTier.SAFE_READ or checked.side_effects:
                raise ValueError("sandbox_read_contract_invalid")
            return self
        if checked.method is HttpMethod.DELETE:
            raise ValueError("sandbox_mutation_method_invalid")
        if not (self.fixture_builder and self.ownership_predicate and self.cleanup_action_id):
            raise ValueError("sandbox_mutation_fixture_requirements")
        return self


class SandboxExecutionManifest(StrictSafeModel):
    """A complete FlowAccount policy sidecar bound to catalog bytes."""

    environment: Literal["sandbox"]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actions: tuple[SandboxActionPolicy, ...]

    @model_validator(mode="after")
    def validate_distinct_policies(self) -> SandboxExecutionManifest:
        identities = [(policy.action_id, policy.version_id) for policy in self.actions]
        if len(identities) != len(set(identities)):
            raise ValueError("sandbox_manifest_policy_duplicate")
        return self

    def require_policy(self, action_id: str, version_id: str) -> SandboxActionPolicy:
        for policy in self.actions:
            if (policy.action_id, policy.version_id) == (action_id, version_id):
                return policy
        raise LookupError("sandbox_action_not_reviewed")

    def require_executable(self, action: CatalogAction) -> SandboxActionPolicy:
        checked = _validated_action(action)
        policy = self.require_policy(checked.action_id, checked.version_id)
        if policy.disposition is not SandboxDisposition.SANDBOX_EXECUTABLE:
            raise PermissionError("sandbox_action_not_executable")
        return policy.validate_against(checked, environment=self.environment)


def is_multipart_attachment_upload(action: CatalogAction) -> bool:
    """Recognize uploads only from exact media type plus the files schema."""
    media_type = action.content_type.split(";", 1)[0].strip().casefold()
    files = action.input_schema.get("files")
    return media_type == "multipart/form-data" and isinstance(files, Mapping) and bool(files)


def classify_blocked_reasons(action: CatalogAction) -> tuple[str, ...]:
    """Return deterministic, explicit reasons that prohibit sandbox execution."""
    checked = _validated_action(action)
    reasons: set[str] = set()
    capability_segments = tuple(segment.casefold() for segment in checked.capability.split("."))
    effects = {effect.casefold() for effect in checked.side_effects}

    if checked.method is HttpMethod.DELETE:
        reasons.add("delete")
    for effect in _BLOCKED_EFFECT_SEGMENTS:
        if effect in capability_segments or effect in effects:
            reasons.add(effect)
    if len(capability_segments) >= 2 and capability_segments[-2:] == ("attachment", "upload"):
        reasons.add("attachment_upload")
    if is_multipart_attachment_upload(checked):
        reasons.add("multipart_upload")
    if checked.method is not HttpMethod.GET and capability_segments[:1] == ("company",):
        reasons.add("company_mutation")
    return tuple(sorted(reasons))


def reviewed_policy_for(action: CatalogAction) -> SandboxActionPolicy:
    """Apply the reviewed block rules before the narrow immutable read allowlist."""
    checked = _validated_action(action)
    identity = (checked.action_id, checked.version_id)
    blocked_reasons = classify_blocked_reasons(checked)
    if blocked_reasons and identity in LIVE_READS:
        raise ValueError("sandbox_manifest_disposition_overlap")
    if blocked_reasons:
        return SandboxActionPolicy(
            action_id=checked.action_id,
            version_id=checked.version_id,
            disposition=SandboxDisposition.BLOCKED_EXTERNAL_EFFECT,
            external_effects=checked.side_effects,
            blocked_reasons=blocked_reasons,
        )
    if identity in LIVE_READS:
        if (
            checked.method is not HttpMethod.GET
            or checked.risk_tier is not RiskTier.SAFE_READ
            or checked.side_effects
            or "sandbox" not in checked.environments
        ):
            raise ValueError("sandbox_live_read_contract_invalid")
        return SandboxActionPolicy(
            action_id=checked.action_id,
            version_id=checked.version_id,
            disposition=SandboxDisposition.SANDBOX_EXECUTABLE,
            external_effects=checked.side_effects,
            max_attempts=1,
            request_budget=1,
        )
    return SandboxActionPolicy(
        action_id=checked.action_id,
        version_id=checked.version_id,
        disposition=SandboxDisposition.CONTRACT_ONLY,
        external_effects=checked.side_effects,
    )


def sha256_file(path: Path) -> str:
    """Hash one regular catalog file without following a symlink."""
    data = _read_regular_file(path, error="sandbox_manifest_catalog_file_unsafe")
    return hashlib.sha256(data).hexdigest()


def build_sandbox_execution_manifest(catalog_path: Path) -> SandboxExecutionManifest:
    """Build the complete reviewed manifest from immutable FlowAccount actions."""
    actions = _validated_flowaccount_actions(load_actions(catalog_path))
    policies = tuple(
        reviewed_policy_for(action)
        for action in sorted(actions, key=lambda item: (item.action_id, item.version_id))
    )
    return SandboxExecutionManifest(
        environment="sandbox",
        catalog_sha256=sha256_file(catalog_path),
        actions=policies,
    )


def serialize_sandbox_execution_manifest(manifest: SandboxExecutionManifest) -> bytes:
    """Return canonical, newline-terminated JSON bytes for reviewed output."""
    payload = manifest.model_dump(mode="json")
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    )
    return f"{serialized}\n".encode("ascii")


def write_sandbox_execution_manifest(
    catalog_path: Path,
    output_path: Path,
) -> SandboxExecutionManifest:
    """Generate one atomic manifest sidecar without following output symlinks."""
    manifest = build_sandbox_execution_manifest(catalog_path)
    _atomic_write_regular_file(output_path, serialize_sandbox_execution_manifest(manifest))
    return manifest


def load_sandbox_execution_manifest(
    path: Path,
    actions: Sequence[CatalogAction],
    catalog_path: Path | None = None,
) -> SandboxExecutionManifest:
    """Load a complete policy sidecar and bind it to explicit catalog bytes.

    When ``catalog_path`` is omitted, the only inferred relationship is the
    ``actions.json`` sibling of the supplied manifest path. Callers may pass a
    catalog path explicitly to avoid that convenience relationship.
    """
    manifest_path = Path(path)
    payload = _load_manifest_payload(manifest_path)
    try:
        manifest = SandboxExecutionManifest.model_validate(payload)
    except ValidationError as error:
        if _has_validation_code(error, "sandbox_manifest_policy_duplicate"):
            raise ValueError("sandbox_manifest_policy_duplicate") from None
        raise ValueError("sandbox_manifest_invalid") from None
    except (TypeError, ValueError):
        raise ValueError("sandbox_manifest_invalid") from None

    checked_actions = _validated_flowaccount_actions(actions)
    resolved_catalog_path = (
        Path(catalog_path) if catalog_path is not None else manifest_path.with_name("actions.json")
    )
    if manifest.catalog_sha256 != sha256_file(resolved_catalog_path):
        raise ValueError("sandbox_manifest_catalog_mismatch")

    expected = {(action.action_id, action.version_id) for action in checked_actions}
    versions_by_action: dict[str, set[str]] = {}
    for action_id, version_id in expected:
        versions_by_action.setdefault(action_id, set()).add(version_id)

    identities = [(policy.action_id, policy.version_id) for policy in manifest.actions]
    for action_id, version_id in identities:
        if action_id not in versions_by_action:
            raise ValueError("sandbox_manifest_policy_unknown")
        if version_id not in versions_by_action[action_id]:
            raise ValueError("sandbox_manifest_policy_version_drift")
    if len(identities) != len(expected) or set(identities) != expected:
        raise ValueError("sandbox_manifest_coverage_incomplete")
    if identities != sorted(identities):
        raise ValueError("sandbox_manifest_policy_order_invalid")

    by_identity = {(action.action_id, action.version_id): action for action in checked_actions}
    for policy in manifest.actions:
        action = by_identity[(policy.action_id, policy.version_id)]
        try:
            policy.validate_against(action, environment=manifest.environment)
        except ValueError:
            raise ValueError("sandbox_manifest_policy_contract_invalid") from None
        if policy != reviewed_policy_for(action):
            raise ValueError("sandbox_manifest_policy_review_mismatch")
    return manifest


def _validated_flowaccount_actions(actions: Sequence[CatalogAction]) -> tuple[CatalogAction, ...]:
    checked_actions: list[CatalogAction] = []
    identities: set[tuple[str, str]] = set()
    for action in actions:
        checked = _validated_action(action)
        if checked.connector_id != "flowaccount":
            raise ValueError("sandbox_manifest_catalog_connector_invalid")
        identity = (checked.action_id, checked.version_id)
        if identity in identities:
            raise ValueError("sandbox_manifest_catalog_identity_duplicate")
        identities.add(identity)
        checked_actions.append(checked)
    if (
        len(checked_actions) != FLOWACCOUNT_ACTION_COUNT
        or Counter(action.method for action in checked_actions) != FLOWACCOUNT_METHOD_COUNTS
    ):
        raise ValueError("sandbox_manifest_catalog_shape_invalid")
    return tuple(checked_actions)


def _validated_action(action: CatalogAction) -> CatalogAction:
    if not isinstance(action, CatalogAction):
        raise ValueError("sandbox_manifest_catalog_action_invalid")
    try:
        return revalidate_catalog_action(action)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("sandbox_manifest_catalog_action_invalid") from None


def _load_manifest_payload(path: Path) -> Mapping[str, Any]:
    raw = _read_regular_file(path, error="sandbox_manifest_file_unsafe")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except _DuplicateJsonKey:
        raise ValueError("sandbox_manifest_json_duplicate_key") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("sandbox_manifest_invalid") from None
    if not isinstance(payload, Mapping):
        raise ValueError("sandbox_manifest_invalid")
    return payload


class _DuplicateJsonKey(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey
        value[key] = item
    return value


def _has_validation_code(error: ValidationError, expected: str) -> bool:
    """Preserve reviewed model codes without formatting untrusted input values."""
    for detail in error.errors():
        context = detail.get("ctx")
        nested = context.get("error") if isinstance(context, Mapping) else None
        if isinstance(nested, ValueError) and str(nested) == expected:
            return True
    return False


def _read_regular_file(path: Path, *, error: str) -> bytes:
    try:
        candidate = Path(path)
        before = os.lstat(candidate)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(error)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(candidate, flags)
    except ValueError:
        raise
    except (OSError, TypeError):
        raise ValueError(error) from None

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise ValueError(error)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MANIFEST_BYTES:
                raise ValueError(error)
            chunks.append(chunk)
        return b"".join(chunks)
    except ValueError:
        raise
    except OSError:
        raise ValueError(error) from None
    finally:
        os.close(descriptor)


def _atomic_write_regular_file(path: Path, data: bytes) -> None:
    candidate = Path(path)
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
            raise ValueError("sandbox_manifest_output_unsafe")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".sandbox-execution-manifest-",
            suffix=".tmp",
            dir=candidate.parent,
        )
    except ValueError:
        raise
    except OSError:
        raise ValueError("sandbox_manifest_output_unsafe") from None

    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, candidate)
    except OSError:
        raise ValueError("sandbox_manifest_output_unsafe") from None
    finally:
        temporary.unlink(missing_ok=True)
