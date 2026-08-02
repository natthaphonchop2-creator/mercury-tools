# Wave D Broad-Review Remediation Report

Implementation commit: `68ebb70967215b678543ed9735ecf7622f323ee0`

## Finding Map

| # | Implementation | Regression coverage | Commit |
|---|---|---|---|
| 1 | Added qualification-authoritative `public_output_field_paths`, bound explicit classifications into the immutable capability version, persisted and validated them in PostgreSQL, rejected legacy unreviewed classifications at publication and dispatch, and used one classifier for both public schemas and runtime projection. Unknown aliases, nested fields, array fields, non-English keys, and conditional fields now default to private. | `test_public_output_field_classification_is_explicit_and_version_bound`; `test_public_projection_prunes_nested_sensitive_fields_across_refs_and_applicators`; `test_public_projection_preserves_hidden_optional_conditional_false_path`; PostgreSQL Task 8 qualification suite. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 2 | Preserved complete closed Draft 2020-12 input schemas and injected Mercury routing fields into every object-bearing root composition, reference, and conditional branch. | `test_generated_input_preserves_root_one_of_and_local_references`; `test_generated_input_preserves_root_if_then_else_runtime_parity`. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 3 | Replaced the unbounded wire-model map with a locked 128-entry LRU cache, pruned it after committed publication, and cleared it during publisher shutdown without invalidating held model references. | `test_many_immutable_versions_keep_wire_model_cache_bounded_and_clearable`. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 4 | Classified exact response-model and JSON Schema rejection as `ProviderSchemaChanged` with the strongest known dispatch certainty. Hosted reads now preserve dispatched certainty, and generated tools reuse the existing exact-version quarantine, durable demotion, refresh, and terminal audit path. | `test_hosted_read_retains_dispatch_certainty_after_response_validation_failure`; `test_runtime_schema_drift_persists_then_refreshes_the_exact_version`; `test_schema_drift_persistence_alert_retains_dispatch_certainty`; runtime response-contract tests. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 5 | Registered the active V1 MCP session from both `tools/list` and stable-tool calls, while retaining the existing bounded idle/session notification lifecycle. | `test_stable_core_only_session_receives_background_tool_list_changed`; protected-resource lifecycle tests. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 6 | Derived the cumulative hosted read deadline from the exact selected driver's manifest `READ` timeout while preserving the separate connect timeout. | `test_hosted_read_uses_exact_manifest_read_deadline_beyond_five_seconds`. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 7 | Serialized catalog load, reconcile, and swap under a projection authority lock so an older delayed load cannot commit after a newer publication. | `test_older_delayed_catalog_load_cannot_replace_newer_publication`. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 8 | Removed list-summary selection from `connector_status`; qualification, response, and audit now use one exact loaded connection rechecked against tenant, workspace, user, and connection identity. | `test_connector_status_writes_only_a_sanitized_local_audit_event`, including stale-summary/current-load regression coverage. | `68ebb70967215b678543ed9735ecf7622f323ee0` |
| 9 | Added Git-canonical exact read mappings, executed every required binding through `HostedReadService`, rechecked exact connection and qualification versions, combined typed host facts, explicitly public provider results, and reviewed knowledge citations, validated exact `facts`/`citations` output, and audited actual read versions and all terminal outcomes. No create mapping or dispatch path was added. Regenerated the deterministic first-party Skill publication migration. | `test_each_published_read_backed_skill_executes_exact_required_reads`; `test_read_backed_skill_missing_or_failed_read_returns_closed_error`; `test_git_read_mapping_rejects_noncanonical_request_or_result_name`; `test_checked_in_publication_migration_is_exact_deterministic_projection`; PostgreSQL Task 11 publication suite. | `68ebb70967215b678543ed9735ecf7622f323ee0` |

## Verification

- Required focused suite: `126 passed`.
- Server and lifecycle suite: `170 passed`, with one pre-existing Starlette/httpx deprecation warning.
- Deterministic Skill publication suite: `3 passed`.
- Disposable PostgreSQL qualification suite: `31 passed`.
- Disposable PostgreSQL knowledge and Skill publication suite: `9 passed`.
- Ruff check passed for every changed Python file.
- Ruff format check passed for every changed Python file (`20 files already formatted`).
- `git diff --check` and staged diff checks passed.
- Self-review confirmed closed public schemas, default-private projection, exact identity/version rechecks, sanitized audit records, and no create dispatch.
- No live provider, Supabase, or Render calls were made. No deploy, push, tag, or history rewrite was performed.

## Concerns

None.
