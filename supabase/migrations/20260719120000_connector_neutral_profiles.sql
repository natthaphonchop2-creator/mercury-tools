begin;

alter table public.mercury_connector_profiles
  add column if not exists connection_mode text not null default 'api_driver',
  add column if not exists company_ref text,
  add column if not exists external_server_name text,
  add column if not exists capability_states jsonb not null default '{}'::jsonb,
  add column if not exists evidence_source text,
  add column if not exists validated_at timestamptz;

alter table public.mercury_skill_catalog
  add column if not exists required_capabilities jsonb not null default '[]'::jsonb;

update public.mercury_connector_profiles
set
  connection_mode = 'api_driver',
  status = case
    when connection_mode = 'local_bridge' then 'requires_local_setup'
    else 'needs_validation'
  end,
  updated_at = now()
where connection_mode is distinct from 'api_driver'
   or status not in ('requires_authorization', 'requires_local_setup', 'needs_validation');

alter table public.mercury_connector_profiles
  drop constraint if exists mercury_connector_profiles_connection_mode_check,
  add constraint mercury_connector_profiles_connection_mode_check
    check (connection_mode in ('native_mcp', 'api_driver', 'local_bridge')),
  drop constraint if exists mercury_connector_profiles_capability_states_object_check,
  add constraint mercury_connector_profiles_capability_states_object_check
    check (jsonb_typeof(capability_states) = 'object'),
  drop constraint if exists mercury_connector_profiles_capability_states_safe_keys_check,
  add constraint mercury_connector_profiles_capability_states_safe_keys_check
    check (
      not capability_states ?| array[
        'access_token',
        'api_key',
        'authorization',
        'client_secret',
        'credential',
        'credentials',
        'email',
        'password',
        'secret',
        'token'
      ]
    ),
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
