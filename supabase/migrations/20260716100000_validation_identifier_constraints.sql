begin;

-- Align validation identifier checks with the runtime Pydantic safe-token rules.
-- This migration only replaces the weaker checks created by the original schema.

alter table public.erp_action_validation_knowledge
  drop constraint if exists erp_action_validation_knowledge_opaque_evidence_id_check;

alter table public.erp_action_validation_knowledge
  drop constraint if exists erp_action_validation_knowledge_run_id_check;

alter table public.erp_action_validation_knowledge
  drop constraint if exists erp_action_validation_knowledge_action_id_check;

alter table public.erp_action_validation_knowledge
  drop constraint if exists erp_action_validation_knowledge_version_id_check;

alter table public.erp_action_validation_knowledge
  drop constraint if exists erp_action_validation_knowledge_connector_id_check;

alter table public.erp_action_observations
  drop constraint if exists erp_action_observations_opaque_event_id_check;

alter table public.erp_action_observations
  drop constraint if exists erp_action_observations_action_id_check;

alter table public.erp_action_observations
  drop constraint if exists erp_action_observations_version_id_check;

alter table public.erp_action_observations
  drop constraint if exists erp_action_observations_connector_id_check;

do $migration$
begin
  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_validation_knowledge'::regclass
      and conname = 'erp_action_validation_knowledge_opaque_evidence_id_safe_check'
  ) then
    alter table public.erp_action_validation_knowledge
      add constraint erp_action_validation_knowledge_opaque_evidence_id_safe_check
      check (
        opaque_evidence_id ~ '^[A-Za-z0-9._:-]{1,200}$'
        and (
          not approved_public
          or opaque_evidence_id ~* '^ev_[0-9A-HJKMNP-TV-Z]{26}$'
        )
      );
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_validation_knowledge'::regclass
      and conname = 'erp_action_validation_knowledge_action_id_safe_check'
  ) then
    alter table public.erp_action_validation_knowledge
      add constraint erp_action_validation_knowledge_action_id_safe_check
      check (action_id ~ '^[A-Za-z0-9._:-]{1,200}$');
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_validation_knowledge'::regclass
      and conname = 'erp_action_validation_knowledge_version_id_safe_check'
  ) then
    alter table public.erp_action_validation_knowledge
      add constraint erp_action_validation_knowledge_version_id_safe_check
      check (version_id ~ '^[A-Za-z0-9._:-]{1,200}$');
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_validation_knowledge'::regclass
      and conname = 'erp_action_validation_knowledge_connector_id_safe_check'
  ) then
    alter table public.erp_action_validation_knowledge
      add constraint erp_action_validation_knowledge_connector_id_safe_check
      check (connector_id ~ '^[A-Za-z0-9._:-]{1,200}$');
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_validation_knowledge'::regclass
      and conname = 'erp_action_validation_knowledge_run_id_safe_check'
  ) then
    alter table public.erp_action_validation_knowledge
      add constraint erp_action_validation_knowledge_run_id_safe_check
      check (
        run_id ~ '^[A-Za-z0-9._:-]{1,200}$'
        and (
          not approved_public
          or run_id ~* '^run_[0-9A-HJKMNP-TV-Z]{26}$'
        )
      );
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_observations'::regclass
      and conname = 'erp_action_observations_opaque_event_id_safe_check'
  ) then
    alter table public.erp_action_observations
      add constraint erp_action_observations_opaque_event_id_safe_check
      check (opaque_event_id ~ '^[A-Za-z0-9._:-]{1,200}$');
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_observations'::regclass
      and conname = 'erp_action_observations_action_id_safe_check'
  ) then
    alter table public.erp_action_observations
      add constraint erp_action_observations_action_id_safe_check
      check (action_id ~ '^[A-Za-z0-9._:-]{1,200}$');
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_observations'::regclass
      and conname = 'erp_action_observations_version_id_safe_check'
  ) then
    alter table public.erp_action_observations
      add constraint erp_action_observations_version_id_safe_check
      check (version_id ~ '^[A-Za-z0-9._:-]{1,200}$');
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_observations'::regclass
      and conname = 'erp_action_observations_connector_id_safe_check'
  ) then
    alter table public.erp_action_observations
      add constraint erp_action_observations_connector_id_safe_check
      check (connector_id ~ '^[A-Za-z0-9._:-]{1,200}$');
  end if;
end;
$migration$;

commit;
