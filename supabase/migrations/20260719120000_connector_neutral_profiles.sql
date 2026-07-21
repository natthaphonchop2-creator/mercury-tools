begin;

-- A pre-v0.3 profile has no connection_mode. Clear its unproven legacy JSON once,
-- before the new profile columns make this migration distinguishable on rerun.
do $$
declare
  profile_mode_column_existed boolean;
begin
  select exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'mercury_connector_profiles'
      and column_name = 'connection_mode'
  )
  into profile_mode_column_existed;

  if not profile_mode_column_existed then
    update public.mercury_connector_profiles
    set metadata = '{}'::jsonb,
        updated_at = now()
    where metadata is distinct from '{}'::jsonb;
  end if;
end
$$;

alter table public.mercury_connector_profiles
  add column if not exists connection_mode text not null default 'api_driver',
  add column if not exists company_ref text,
  add column if not exists external_server_name text,
  add column if not exists capability_states jsonb not null default '{}'::jsonb,
  add column if not exists evidence_source text,
  add column if not exists validated_at timestamptz;

alter table public.mercury_skill_catalog
  add column if not exists required_capabilities jsonb not null default '[]'::jsonb;

-- Translate only the legacy status. New neutral statuses are preserved on rerun.
update public.mercury_connector_profiles
set status = 'needs_validation',
    updated_at = now()
where status = 'requires_credentials';

-- Reject every unsafe capability name, including embedded forms such as
-- provider_access_token, and allow only reviewed state values.
create or replace function public.mercury_capability_states_are_safe(capability_states jsonb)
returns boolean
language sql
immutable
set search_path = pg_catalog
as $$
  select
    jsonb_typeof(capability_states) = 'object'
    and not exists (
      select 1
      from jsonb_each(capability_states) as state_entry(capability, capability_state)
      where capability !~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$'
        or jsonb_typeof(capability_state) <> 'string'
        or capability_state #>> '{}' not in (
          'observed',
          'provider_unavailable',
          'not_authorized',
          'validation_failed',
          'environment_mismatch'
        )
        or regexp_replace(lower(capability), '[^a-z0-9]+', '_', 'g') ~
          '(api[_-]?key|access[_-]?token|auth|bearer|credential|email|password|secret|tax[_-]?id|taxid|token|response[_-]?body|payload)'
    );
$$;

revoke execute on function public.mercury_capability_states_are_safe(jsonb) from public;
revoke execute on function public.mercury_capability_states_are_safe(jsonb) from anon, authenticated;
grant execute on function public.mercury_capability_states_are_safe(jsonb) to service_role;

alter table public.mercury_connector_profiles
  drop constraint if exists mercury_connector_profiles_connection_mode_check,
  add constraint mercury_connector_profiles_connection_mode_check
    check (connection_mode in ('native_mcp', 'api_driver', 'local_bridge')),
  drop constraint if exists mercury_connector_profiles_capability_states_object_check,
  add constraint mercury_connector_profiles_capability_states_object_check
    check (jsonb_typeof(capability_states) = 'object'),
  drop constraint if exists mercury_connector_profiles_capability_states_safe_keys_check,
  add constraint mercury_connector_profiles_capability_states_safe_keys_check
    check (public.mercury_capability_states_are_safe(capability_states)),
  drop constraint if exists mercury_connector_profiles_company_ref_safe_check,
  add constraint mercury_connector_profiles_company_ref_safe_check
    check (company_ref is null or company_ref ~ '^[A-Za-z0-9._ -]{1,200}$'),
  drop constraint if exists mercury_connector_profiles_external_server_name_safe_check,
  add constraint mercury_connector_profiles_external_server_name_safe_check
    check (external_server_name is null or external_server_name ~ '^[A-Za-z0-9._-]{1,200}$'),
  drop constraint if exists mercury_connector_profiles_evidence_source_safe_check,
  add constraint mercury_connector_profiles_evidence_source_safe_check
    check (
      evidence_source is null
      or evidence_source in (
        'native_mcp_safe_read',
        'api_driver_safe_probe',
        'local_bridge_safe_probe'
      )
    );

alter table public.mercury_skill_catalog
  drop constraint if exists mercury_skill_catalog_required_capabilities_array_check,
  add constraint mercury_skill_catalog_required_capabilities_array_check
    check (jsonb_typeof(required_capabilities) = 'array');

alter table public.mercury_connector_profiles
  drop constraint if exists mercury_connector_profiles_workspace_id_connector_id_environment_key,
  drop constraint if exists mercury_connector_profiles_workspace_connector_mode_environment_key,
  add constraint mercury_connector_profiles_workspace_connector_mode_environment_key
    unique (workspace_id, connector_id, connection_mode, environment);

drop index if exists public.mercury_connector_profiles_workspace_idx;
create index if not exists mercury_connector_profiles_workspace_mode_idx
  on public.mercury_connector_profiles (workspace_id, connector_id, connection_mode, environment);

alter table public.mercury_connector_profiles enable row level security;
alter table public.mercury_skill_catalog enable row level security;

revoke all on table public.mercury_connector_profiles from anon, authenticated;
revoke all on table public.mercury_skill_catalog from anon, authenticated;
grant all on table public.mercury_connector_profiles to service_role;
grant all on table public.mercury_skill_catalog to service_role;

commit;
