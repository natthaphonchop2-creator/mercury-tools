"""Tests for the reviewed, version-bound FlowAccount sandbox manifest."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from mercury_tools.catalog.identity import build_version_id
from mercury_tools.catalog.models import CatalogAction, HttpMethod, RiskTier
from mercury_tools.qualification import manifest as manifest_module
from mercury_tools.qualification.manifest import (
    LIVE_READS,
    SandboxActionPolicy,
    SandboxDisposition,
    SandboxExecutionManifest,
    classify_blocked_reasons,
    is_multipart_attachment_upload,
    load_sandbox_execution_manifest,
    reviewed_policy_for,
    write_sandbox_execution_manifest,
)
from mercury_tools.qualification.semantics import load_actions

ROOT = Path(__file__).resolve().parents[1]
FLOWACCOUNT_ACTIONS = ROOT / "catalog/global/flowaccount/actions.json"
FLOWACCOUNT_MANIFEST = ROOT / "catalog/global/flowaccount/sandbox-execution-manifest.json"
BUILD_SCRIPT = ROOT / "scripts/build_sandbox_manifest.py"


@pytest.fixture(scope="module")
def actions():
    return load_actions(FLOWACCOUNT_ACTIONS)


@pytest.fixture(scope="module")
def manifest(actions):
    return load_sandbox_execution_manifest(FLOWACCOUNT_MANIFEST, FLOWACCOUNT_ACTIONS)


def _manifest_payload() -> dict[str, object]:
    value = json.loads(FLOWACCOUNT_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "sandbox-execution-manifest.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def test_flowaccount_catalog_has_190_unique_method_bound_identities(actions) -> None:
    assert len(actions) == 190
    assert Counter(action.method for action in actions) == {
        HttpMethod.GET: 36,
        HttpMethod.POST: 119,
        HttpMethod.PUT: 22,
        HttpMethod.DELETE: 13,
    }
    assert len({(action.action_id, action.version_id) for action in actions}) == 190


def test_manifest_covers_every_flowaccount_action_exactly_once(actions, manifest) -> None:
    catalog_identities = [(action.action_id, action.version_id) for action in actions]
    manifest_identities = [(policy.action_id, policy.version_id) for policy in manifest.actions]

    assert len(manifest.actions) == 190
    assert len(manifest_identities) == 190
    assert len(set(manifest_identities)) == 190
    assert set(manifest_identities) == set(catalog_identities)
    assert manifest_identities == sorted(manifest_identities)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda payload: payload.update({"catalog_sha256": "0" * 64}),
            "sandbox_manifest_catalog_mismatch",
        ),
        (
            lambda payload: payload["actions"].__setitem__(1, payload["actions"][0]),
            "sandbox_manifest_policy_duplicate",
        ),
        (lambda payload: payload["actions"].pop(), "sandbox_manifest_coverage_incomplete"),
        (
            lambda payload: payload["actions"].__setitem__(
                0,
                {
                    **payload["actions"][0],
                    "action_id": "act_" + "f" * 24,
                    "version_id": "av_" + "f" * 64,
                },
            ),
            "sandbox_manifest_policy_unknown",
        ),
        (
            lambda payload: payload["actions"].__setitem__(
                0,
                {
                    **payload["actions"][0],
                    "version_id": "av_" + "0" * 64,
                },
            ),
            "sandbox_manifest_policy_version_drift",
        ),
    ],
)
def test_loader_rejects_manifest_hash_and_identity_tricks(
    tmp_path: Path,
    actions,
    mutation,
    error: str,
) -> None:
    payload = _manifest_payload()
    mutation(payload)

    with pytest.raises(ValueError, match=error):
        load_sandbox_execution_manifest(
            _write_manifest(tmp_path, payload),
            FLOWACCOUNT_ACTIONS,
        )


def test_loader_rejects_duplicate_json_keys_without_echoing_input(
    tmp_path: Path,
    actions,
) -> None:
    duplicate = tmp_path / "sandbox-execution-manifest.json"
    duplicate.write_text('{"catalog_sha256":"a", "catalog_sha256":"b"}', encoding="utf-8")

    with pytest.raises(ValueError, match="sandbox_manifest_json_duplicate_key") as raised:
        load_sandbox_execution_manifest(duplicate, FLOWACCOUNT_ACTIONS)

    assert '"catalog_sha256":"a"' not in str(raised.value)


def _write_version_drifted_catalog(tmp_path: Path) -> tuple[Path, str]:
    rows = json.loads(FLOWACCOUNT_ACTIONS.read_text(encoding="utf-8"))
    changed = CatalogAction.model_validate(
        {**rows[0], "description": "structurally different catalog snapshot"}
    )
    changed = changed.model_copy(update={"version_id": build_version_id(changed)})
    rows[0] = changed.model_dump(mode="json")
    data = json.dumps(
        rows,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    ).encode("utf-8")
    path = tmp_path / "actions.json"
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def test_loader_cannot_accept_stale_actions_separate_from_hashed_catalog(
    tmp_path: Path,
    actions,
) -> None:
    catalog_path, catalog_sha256 = _write_version_drifted_catalog(tmp_path)
    payload = _manifest_payload()
    payload["catalog_sha256"] = catalog_sha256
    manifest_path = _write_manifest(tmp_path, payload)

    with pytest.raises(TypeError):
        load_sandbox_execution_manifest(
            manifest_path,
            actions,
            catalog_path=catalog_path,
        )
    with pytest.raises(ValueError, match="sandbox_manifest_policy_version_drift"):
        load_sandbox_execution_manifest(manifest_path, catalog_path)


def test_exact_lookup_rejects_unknown_versions_and_non_executable_actions(
    actions, manifest
) -> None:
    executable = next(
        action for action in actions if (action.action_id, action.version_id) in LIVE_READS
    )
    contract_only = next(
        action
        for action in actions
        if (action.action_id, action.version_id) not in LIVE_READS
        and not classify_blocked_reasons(action)
    )

    with pytest.raises(LookupError, match="sandbox_action_not_reviewed"):
        manifest.require_policy(executable.action_id, "av_" + "0" * 64)
    with pytest.raises(PermissionError, match="sandbox_action_not_executable"):
        manifest.require_executable(contract_only)


def _direct_executable_manifest(action) -> SandboxExecutionManifest:
    policy = SandboxActionPolicy(
        action_id=action.action_id,
        version_id=action.version_id,
        disposition=SandboxDisposition.SANDBOX_EXECUTABLE,
        fixture_builder="fixture_builder",
        ownership_predicate="owned_fixture",
        cleanup_action_id=action.action_id,
        external_effects=action.side_effects,
        controlled_destination=bool(action.side_effects),
        max_attempts=1,
        request_budget=1,
    )
    return SandboxExecutionManifest(
        environment="sandbox",
        catalog_sha256="0" * 64,
        actions=(policy,),
    )


@pytest.mark.parametrize(
    "selector",
    [
        lambda action: (
            action.method is HttpMethod.GET
            and (action.action_id, action.version_id) not in LIVE_READS
        ),
        lambda action: "payment" in action.side_effects,
        lambda action: action.method is HttpMethod.DELETE,
        lambda action: action.capability == "company.update",
        is_multipart_attachment_upload,
    ],
    ids=("nonallowlisted_get", "payment", "delete", "company_update", "multipart"),
)
def test_direct_models_cannot_bypass_the_exact_live_read_allowlist(
    actions,
    selector,
) -> None:
    action = next(action for action in actions if selector(action))
    fabricated = _direct_executable_manifest(action)

    with pytest.raises(PermissionError, match="sandbox_action_not_executable"):
        fabricated.require_executable(action)


def test_only_the_exact_four_safe_reads_are_executable(actions, manifest) -> None:
    by_identity = {(action.action_id, action.version_id): action for action in actions}
    executable = [
        policy
        for policy in manifest.actions
        if policy.disposition is SandboxDisposition.SANDBOX_EXECUTABLE
    ]

    assert {(policy.action_id, policy.version_id) for policy in executable} == LIVE_READS
    assert len(executable) == 4
    for policy in executable:
        action = by_identity[(policy.action_id, policy.version_id)]
        assert action.method is HttpMethod.GET
        assert action.risk_tier is RiskTier.SAFE_READ
        assert action.side_effects == ()
        assert "sandbox" in action.environments
        assert policy.external_effects == ()
        assert policy.max_attempts == 1
        assert policy.request_budget == 1
        assert policy.controlled_destination is False


def test_blocked_actions_use_explicit_precise_classifications(actions, manifest) -> None:
    policies = {(policy.action_id, policy.version_id): policy for policy in manifest.actions}
    classified = {
        (action.action_id, action.version_id): classify_blocked_reasons(action)
        for action in actions
    }

    assert any("email" in reasons for reasons in classified.values())
    assert any("share" in reasons for reasons in classified.values())
    assert any("payment" in reasons for reasons in classified.values())
    assert any("void" in reasons for reasons in classified.values())
    assert any("delete" in reasons for reasons in classified.values())
    assert any("company_mutation" in reasons for reasons in classified.values())
    assert any("attachment_upload" in reasons for reasons in classified.values())

    for identity, reasons in classified.items():
        policy = policies[identity]
        if reasons:
            assert policy.disposition is SandboxDisposition.BLOCKED_EXTERNAL_EFFECT
            assert policy.blocked_reasons == reasons


def _independent_semantic_tokens(action) -> set[str]:
    values = (
        action.operation_id,
        action.capability,
        action.path_template,
        *action.aliases_en,
        *action.aliases_th,
        *action.side_effects,
    )
    tokens: set[str] = set()
    for value in values:
        split_acronyms = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
        split_camel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", split_acronyms)
        tokens.update(token.casefold() for token in re.findall(r"[^\W\d_]+", split_camel))
    return tokens


def test_every_structural_payment_action_is_explicitly_blocked(actions, manifest) -> None:
    policies = {(policy.action_id, policy.version_id): policy for policy in manifest.actions}
    payment_actions = [
        action for action in actions if "payment" in _independent_semantic_tokens(action)
    ]

    assert "act_5d14ee0467a696707d4bc137" in {action.action_id for action in payment_actions}
    for action in payment_actions:
        policy = policies[(action.action_id, action.version_id)]
        assert policy.disposition is SandboxDisposition.BLOCKED_EXTERNAL_EFFECT
        assert "payment" in policy.blocked_reasons


def test_payment_predicate_does_not_match_larger_alphabetic_tokens(action_factory) -> None:
    repayment = action_factory(
        operation_id="createRepaymentPlan",
        capability="documents.repayment.create",
        path_template="/repayment-plans",
        aliases_en=("create repayment plan",),
    )

    assert "payment" not in classify_blocked_reasons(repayment)


def test_multipart_detection_requires_precise_media_type_and_file_schema(actions) -> None:
    action = actions[0]
    attachment_schema = {
        **action.input_schema,
        "files": {"attachment": {"type": "string", "required": True}},
    }
    multipart = action.model_copy(
        update={
            "content_type": "multipart/form-data; charset=utf-8",
            "input_schema": attachment_schema,
        }
    )
    loose_text = action.model_copy(
        update={
            "content_type": "application/json; profile=multipart/form-data",
            "input_schema": attachment_schema,
        }
    )
    no_file_schema = action.model_copy(
        update={
            "content_type": "multipart/form-data",
            "input_schema": {**action.input_schema, "files": {}},
        }
    )

    assert is_multipart_attachment_upload(multipart)
    assert not is_multipart_attachment_upload(loose_text)
    assert not is_multipart_attachment_upload(no_file_schema)


def test_executable_policy_enforces_budgets_and_controlled_destination() -> None:
    values = {
        "action_id": "act_share",
        "version_id": "av_share",
        "disposition": SandboxDisposition.SANDBOX_EXECUTABLE,
        "external_effects": ("share",),
        "max_attempts": 1,
        "request_budget": 1,
    }
    with pytest.raises(ValidationError, match="controlled_destination_required"):
        SandboxActionPolicy.model_validate(values)
    with pytest.raises(ValidationError, match="sandbox_execution_budget_invalid"):
        SandboxActionPolicy.model_validate(
            {**values, "controlled_destination": True, "max_attempts": 2}
        )


def test_executable_mutations_require_task8_fixture_ownership_and_cleanup(actions) -> None:
    mutation = next(
        action for action in actions if action.method not in {HttpMethod.GET, HttpMethod.DELETE}
    )
    policy = SandboxActionPolicy(
        action_id=mutation.action_id,
        version_id=mutation.version_id,
        disposition=SandboxDisposition.SANDBOX_EXECUTABLE,
        external_effects=mutation.side_effects,
        controlled_destination=bool(mutation.side_effects),
        max_attempts=1,
        request_budget=1,
    )

    with pytest.raises(ValueError, match="sandbox_mutation_fixture_requirements"):
        policy.validate_against(mutation, environment="sandbox")


def test_policy_models_are_strict_frozen_and_credential_safe_without_echoing() -> None:
    with pytest.raises(ValidationError, match="catalog_credentials_unsafe") as raised:
        SandboxActionPolicy(
            action_id="Bearer private-value-must-not-echo",
            version_id="av_safe",
            disposition=SandboxDisposition.CONTRACT_ONLY,
        )
    assert "private-value-must-not-echo" not in str(raised.value)

    policy = SandboxActionPolicy(
        action_id="act_safe",
        version_id="av_safe",
        disposition=SandboxDisposition.CONTRACT_ONLY,
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        policy.request_budget = 1
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SandboxActionPolicy.model_validate({**policy.model_dump(), "unexpected": True})


@pytest.mark.skipif(os.name != "posix", reason="symlink checks require POSIX")
def test_loader_rejects_symlinked_or_non_regular_files(tmp_path: Path, actions) -> None:
    linked_manifest = tmp_path / "linked-manifest.json"
    linked_manifest.symlink_to(FLOWACCOUNT_MANIFEST)
    with pytest.raises(ValueError, match="sandbox_manifest_file_unsafe"):
        load_sandbox_execution_manifest(
            linked_manifest,
            FLOWACCOUNT_ACTIONS,
        )

    directory = tmp_path / "not-a-file"
    directory.mkdir()
    with pytest.raises(ValueError, match="sandbox_manifest_file_unsafe"):
        load_sandbox_execution_manifest(directory, FLOWACCOUNT_ACTIONS)

    linked_catalog = tmp_path / "linked-actions.json"
    linked_catalog.symlink_to(FLOWACCOUNT_ACTIONS)
    with pytest.raises(ValueError, match="sandbox_manifest_catalog_file_unsafe"):
        load_sandbox_execution_manifest(FLOWACCOUNT_MANIFEST, linked_catalog)


@pytest.mark.skipif(os.name != "posix", reason="dir_fd checks require POSIX")
def test_writer_rejects_symlinked_output_ancestor_without_external_write(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    (outside / "nested").mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    output = linked / "nested/sandbox-execution-manifest.json"

    with pytest.raises(ValueError, match="sandbox_manifest_output_unsafe"):
        write_sandbox_execution_manifest(FLOWACCOUNT_ACTIONS, output)

    assert not (outside / "nested/sandbox-execution-manifest.json").exists()


def test_writer_requires_an_existing_output_parent(tmp_path: Path) -> None:
    missing_parent = tmp_path / "missing"

    with pytest.raises(ValueError, match="sandbox_manifest_output_unsafe"):
        write_sandbox_execution_manifest(
            FLOWACCOUNT_ACTIONS,
            missing_parent / "sandbox-execution-manifest.json",
        )

    assert not missing_parent.exists()


@pytest.mark.skipif(os.name != "posix", reason="dir_fd checks require POSIX")
def test_writer_rechecks_parent_binding_before_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    outside = tmp_path / "outside"
    outside.mkdir()
    output = parent / "sandbox-execution-manifest.json"
    real_open = manifest_module.os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        name = Path(os.fsdecode(path)).name
        if not swapped and name.startswith(".sandbox-execution-manifest-"):
            parent.rename(moved_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(manifest_module.os, "open", racing_open)

    with pytest.raises(ValueError, match="sandbox_manifest_output_unsafe"):
        write_sandbox_execution_manifest(FLOWACCOUNT_ACTIONS, output)

    assert swapped
    assert not (outside / output.name).exists()
    assert not (moved_parent / output.name).exists()


@pytest.mark.skipif(os.name != "posix", reason="symlink checks require POSIX")
def test_writer_rejects_symlinked_destination_without_touching_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text("external sentinel", encoding="utf-8")
    output = tmp_path / "sandbox-execution-manifest.json"
    output.symlink_to(target)

    with pytest.raises(ValueError, match="sandbox_manifest_output_unsafe"):
        write_sandbox_execution_manifest(FLOWACCOUNT_ACTIONS, output)

    assert target.read_text(encoding="utf-8") == "external sentinel"


@pytest.mark.skipif(os.name != "posix", reason="dir_fd checks require POSIX")
def test_destination_swap_cannot_redirect_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.json"
    target.write_text("external sentinel", encoding="utf-8")
    output = tmp_path / "sandbox-execution-manifest.json"
    output.write_text("old manifest", encoding="utf-8")
    real_replace = manifest_module.os.replace

    def racing_replace(source, destination, *args, **kwargs):
        output.unlink()
        output.symlink_to(target)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(manifest_module.os, "replace", racing_replace)

    write_sandbox_execution_manifest(FLOWACCOUNT_ACTIONS, output)

    assert target.read_text(encoding="utf-8") == "external sentinel"
    assert output.is_file()
    assert not output.is_symlink()


def test_generator_is_import_safe_helpful_and_byte_deterministic(tmp_path: Path, actions) -> None:
    import_result = subprocess.run(
        [sys.executable, "-c", f"import runpy; runpy.run_path({str(BUILD_SCRIPT)!r})"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert import_result.returncode == 0, import_result.stderr

    help_result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "usage:" in help_result.stdout.casefold()

    output = tmp_path / "sandbox-execution-manifest.json"
    command = [
        sys.executable,
        str(BUILD_SCRIPT),
        "--catalog",
        str(FLOWACCOUNT_ACTIONS),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    first_bytes = output.read_bytes()
    second = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr

    assert output.read_bytes() == first_bytes
    generated = load_sandbox_execution_manifest(output, FLOWACCOUNT_ACTIONS)
    assert len(generated.actions) == 190
    assert "total=190" in first.stdout
    assert "sandbox_executable=4" in first.stdout
    assert "missing=0" in first.stdout


def test_generated_manifest_is_secret_safe_and_path_safe() -> None:
    serialized = FLOWACCOUNT_MANIFEST.read_text(encoding="utf-8").casefold()

    for unsafe in (
        "/users/",
        str(Path.home()).casefold(),
        "authorization",
        "client_secret",
        "bearer ",
        "access_token",
        ".mercury/",
    ):
        assert unsafe not in serialized


def test_generator_policy_matches_the_reviewed_manifest(actions, manifest) -> None:
    expected = tuple(
        reviewed_policy_for(action)
        for action in sorted(actions, key=lambda action: (action.action_id, action.version_id))
    )
    assert manifest.actions == expected
