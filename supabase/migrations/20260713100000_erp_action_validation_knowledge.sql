begin;

do $migration$
begin
  if not exists (
    select 1
    from pg_catalog.pg_constraint
    where conrelid = 'public.erp_action_versions'::regclass
      and conname = 'erp_action_versions_connector_identity_unique'
  ) then
    alter table public.erp_action_versions
      add constraint erp_action_versions_connector_identity_unique
      unique (connector_id, action_id, version_id);
  end if;
end;
$migration$;

create or replace function public.jsonb_has_forbidden_validation_key(value jsonb)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select exists (
    select 1
    from jsonb_path_query(
      coalesce(value, 'null'::jsonb),
      'lax $.**.keyvalue()'
    ) as keys(item)
    cross join lateral (
      select trim(
        both '_' from lower(
          regexp_replace(
            regexp_replace(
              regexp_replace(
                keys.item->>'key',
                '([[:upper:]]+)([[:upper:]][[:lower:]])',
                '\1_\2',
                'g'
              ),
              '([[:lower:][:digit:]])([[:upper:]])',
              '\1_\2',
              'g'
            ),
            '[^[:alnum:]]+',
            '_',
            'g'
          )
        )
      ) as normalized_key
    ) as normalized
    cross join unnest(array[
      'authorization',
      'token',
      'secret',
      'password',
      'api_key',
      'apikey',
      'client_id',
      'clientid',
      'client_secret',
      'clientsecret',
      'path',
      'uri',
      'raw',
      'payload',
      'response'
    ]) as forbidden(fragment)
    where normalized.normalized_key ~ (
      '(^|_)' || forbidden.fragment || '(_|$)'
    )
  );
$function$;

revoke all on function public.jsonb_has_forbidden_validation_key(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_has_forbidden_validation_key(jsonb)
  to service_role;

create table public.erp_action_validation_knowledge (
  id uuid primary key default gen_random_uuid(),
  opaque_evidence_id text not null unique check (
    opaque_evidence_id ~ '^ev_[a-z0-9_]{8,128}$'
  ),
  run_id text not null check (run_id ~ '^run_[a-z0-9_]{8,128}$'),
  action_id text not null check (char_length(btrim(action_id)) > 0),
  version_id text not null check (char_length(btrim(version_id)) > 0),
  connector_id text not null check (char_length(btrim(connector_id)) > 0),
  environment text not null check (
    environment in ('sandbox', 'test', 'uat', 'production')
  ),
  validation_status text not null check (
    validation_status in (
      'live_success',
      'live_failed',
      'contract_validated',
      'blocked_missing_credentials',
      'blocked_missing_prerequisite',
      'blocked_external_effect',
      'unsupported_by_sandbox',
      'outcome_unknown'
    )
  ),
  evidence_level text not null check (
    evidence_level in (
      'documented',
      'contract_validated',
      'sandbox_observed',
      'accountant_reviewed'
    )
  ),
  execution_eligibility text not null check (
    execution_eligibility in (
      'discovery_only',
      'sandbox_read',
      'sandbox_write_with_approval',
      'production_pending_validation',
      'blocked'
    )
  ),
  run_state text not null check (run_state in ('completed', 'quarantined', 'failed')),
  approved_public boolean not null default false,
  summary_th text not null check (char_length(btrim(summary_th)) > 0),
  summary_en text not null check (char_length(btrim(summary_en)) > 0),
  prerequisites jsonb not null default '[]'::jsonb check (
    jsonb_typeof(prerequisites) = 'array'
  ),
  limitations jsonb not null default '[]'::jsonb check (
    jsonb_typeof(limitations) = 'array'
  ),
  recommended_next_step text not null check (
    char_length(btrim(recommended_next_step)) > 0
  ),
  response_shape jsonb not null default '{}'::jsonb check (
    jsonb_typeof(response_shape) = 'object'
  ),
  status_class text not null check (char_length(btrim(status_class)) > 0),
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  semantic_contract jsonb not null check (jsonb_typeof(semantic_contract) = 'object'),
  evidence_sha256 text not null check (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  reviewed_by text not null check (char_length(btrim(reviewed_by)) > 0),
  runner_version text not null check (char_length(btrim(runner_version)) > 0),
  evaluated_at timestamptz not null,
  expires_at timestamptz check (expires_at is null or expires_at > evaluated_at),
  created_at timestamptz not null default now(),
  unique (connector_id, action_id, version_id, environment, run_id),
  foreign key (connector_id, action_id, version_id)
    references public.erp_action_versions(connector_id, action_id, version_id)
    on delete restrict,
  constraint erp_validation_public_json_safe check (
    not public.jsonb_has_forbidden_validation_key(prerequisites)
    and not public.jsonb_has_forbidden_validation_key(limitations)
    and not public.jsonb_has_forbidden_validation_key(response_shape)
    and not public.jsonb_has_forbidden_validation_key(semantic_contract)
  )
);

create or replace function public.reject_validation_evidence_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $function$
begin
  raise exception using
    errcode = '55000',
    message = 'erp_validation_evidence_is_append_only';
end;
$function$;

revoke all on function public.reject_validation_evidence_mutation()
  from public, anon, authenticated;
grant execute on function public.reject_validation_evidence_mutation()
  to service_role;

drop trigger if exists erp_action_validation_knowledge_is_append_only
  on public.erp_action_validation_knowledge;
create trigger erp_action_validation_knowledge_is_append_only
before update or delete on public.erp_action_validation_knowledge
for each row execute function public.reject_validation_evidence_mutation();

drop trigger if exists erp_action_observations_are_append_only
  on public.erp_action_observations;
create trigger erp_action_observations_are_append_only
before update or delete on public.erp_action_observations
for each row execute function public.reject_validation_evidence_mutation();

create index if not exists erp_validation_exact_lookup_idx
  on public.erp_action_validation_knowledge
  (connector_id, action_id, version_id, environment, evaluated_at desc);

create index if not exists erp_validation_coverage_idx
  on public.erp_action_validation_knowledge
  (connector_id, environment, validation_status, approved_public);

alter table public.erp_action_validation_knowledge enable row level security;

revoke all on table public.erp_action_validation_knowledge
  from public, anon, authenticated;
grant all on table public.erp_action_validation_knowledge to service_role;

-- Row triggers cannot protect TRUNCATE, so the writer role does not receive it.
revoke truncate on table public.erp_action_validation_knowledge from service_role;
revoke truncate on table public.erp_action_observations from service_role;

commit;
