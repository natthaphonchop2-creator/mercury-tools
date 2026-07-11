create extension if not exists pgcrypto;

create table if not exists public.erp_spec_sources (
  source_id text primary key check (char_length(source_id) > 0),
  connector_id text not null check (char_length(connector_id) > 0),
  source_type text not null check (
    source_type in ('openapi3', 'swagger2', 'postman2.1', 'documentation')
  ),
  source_uri text not null check (char_length(source_uri) > 0),
  source_hash text not null check (char_length(source_hash) = 64),
  imported_version text not null check (char_length(imported_version) > 0),
  sanitization jsonb not null check (jsonb_typeof(sanitization) = 'object'),
  metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
  imported_at timestamptz not null,
  created_at timestamptz not null default now(),
  unique (connector_id, source_uri, source_hash)
);

create table if not exists public.erp_action_versions (
  id uuid primary key default gen_random_uuid(),
  action_id text not null check (char_length(action_id) > 0),
  version_id text not null check (char_length(version_id) > 0),
  connector_id text not null check (char_length(connector_id) > 0),
  method text not null check (method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),
  path_template text not null check (char_length(path_template) > 0),
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  source_id text not null references public.erp_spec_sources(source_id) on delete restrict,
  created_at timestamptz not null default now(),
  unique (action_id, version_id)
);

create table if not exists public.erp_action_catalog (
  action_id text primary key check (char_length(action_id) > 0),
  connector_id text not null check (char_length(connector_id) > 0),
  capability text not null check (char_length(capability) > 0),
  active_version_id text not null check (char_length(active_version_id) > 0),
  updated_at timestamptz not null default now(),
  foreign key (action_id, active_version_id)
    references public.erp_action_versions(action_id, version_id)
    deferrable initially deferred
);

create table if not exists public.erp_action_observations (
  id uuid primary key default gen_random_uuid(),
  opaque_event_id text not null unique check (char_length(opaque_event_id) > 0),
  action_id text not null check (char_length(action_id) > 0),
  version_id text not null check (char_length(version_id) > 0),
  connector_id text not null check (char_length(connector_id) > 0),
  method text not null check (method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')),
  observed_state text not null check (
    observed_state in ('success', 'failed', 'outcome_unknown')
  ),
  status_class text not null check (char_length(status_class) > 0),
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  metadata jsonb not null default '{}'::jsonb check (
    jsonb_typeof(metadata) = 'object'
    and metadata - 'source' - 'reviewed_by' - 'note' = '{}'::jsonb
  ),
  created_at timestamptz not null default now(),
  foreign key (action_id, version_id)
    references public.erp_action_versions(action_id, version_id)
    on delete restrict
);

create index if not exists erp_action_versions_connector_method_idx
  on public.erp_action_versions (connector_id, method);

create index if not exists erp_action_versions_source_idx
  on public.erp_action_versions (source_id);

create index if not exists erp_action_catalog_connector_capability_idx
  on public.erp_action_catalog (connector_id, capability);

create index if not exists erp_action_observations_action_created_idx
  on public.erp_action_observations (action_id, created_at desc);

create index if not exists erp_action_observations_version_idx
  on public.erp_action_observations (action_id, version_id);

create or replace function public.reject_erp_action_version_mutation()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  raise exception 'erp_action_versions_are_immutable';
end;
$$;

revoke all on function public.reject_erp_action_version_mutation()
  from public, anon, authenticated;

drop trigger if exists erp_action_versions_are_immutable on public.erp_action_versions;
create trigger erp_action_versions_are_immutable
before update or delete on public.erp_action_versions
for each row execute function public.reject_erp_action_version_mutation();

alter table public.erp_spec_sources enable row level security;
alter table public.erp_action_catalog enable row level security;
alter table public.erp_action_versions enable row level security;
alter table public.erp_action_observations enable row level security;

revoke all on table public.erp_spec_sources from anon, authenticated;
revoke all on table public.erp_action_catalog from anon, authenticated;
revoke all on table public.erp_action_versions from anon, authenticated;
revoke all on table public.erp_action_observations from anon, authenticated;

grant all on table public.erp_spec_sources to service_role;
grant all on table public.erp_action_catalog to service_role;
grant all on table public.erp_action_versions to service_role;
grant all on table public.erp_action_observations to service_role;
