begin;

create table if not exists public.mercury_provider_capability_qualifications (
  id uuid primary key default gen_random_uuid(),
  provider text not null check (provider in ('flowaccount', 'peak')),
  environment text not null check (environment ~ '^[a-z][a-z0-9_-]{0,63}$'),
  provider_tool_name text not null check (
    provider_tool_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
  ),
  normalized_capability text not null check (
    normalized_capability ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'
  ),
  input_schema jsonb not null check (jsonb_typeof(input_schema) = 'object'),
  output_schema jsonb not null check (jsonb_typeof(output_schema) = 'object'),
  schema_hash text not null check (schema_hash ~ '^[0-9a-f]{64}$'),
  response_shape_hash text not null check (response_shape_hash ~ '^[0-9a-f]{64}$'),
  required_permissions jsonb not null check (
    jsonb_typeof(required_permissions) = 'array'
    and jsonb_array_length(required_permissions) > 0
  ),
  capability_version_sha256 text not null check (
    capability_version_sha256 ~ '^[0-9a-f]{64}$'
  ),
  qualification_state text not null check (qualification_state in (
    'discovered_unreviewed',
    'schema_validated',
    'nonproduction_qualified',
    'enabled',
    'disabled',
    'superseded'
  )),
  qualification_evidence_uri text,
  evidence_expires_at timestamptz,
  production_canary_at timestamptz,
  owner_authorized_by text,
  disable_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (
    provider,
    environment,
    normalized_capability,
    provider_tool_name,
    capability_version_sha256
  ),
  check (
    (
      qualification_state in ('discovered_unreviewed', 'schema_validated')
      and qualification_evidence_uri is null
      and evidence_expires_at is null
      and production_canary_at is null
      and owner_authorized_by is null
      and disable_reason is null
    )
    or (
      qualification_state = 'nonproduction_qualified'
      and qualification_evidence_uri ~ '^catalog://global/(flowaccount|peak)/qualifications/[0-9a-f]{64}\.json$'
      and qualification_evidence_uri like ('catalog://global/' || provider || '/qualifications/%')
      and evidence_expires_at is not null
      and production_canary_at is null
      and owner_authorized_by is null
      and disable_reason is null
    )
    or (
      qualification_state = 'enabled'
      and qualification_evidence_uri ~ '^catalog://global/(flowaccount|peak)/qualifications/[0-9a-f]{64}\.json$'
      and qualification_evidence_uri like ('catalog://global/' || provider || '/qualifications/%')
      and evidence_expires_at is not null
      and disable_reason is null
      and (
        (
          environment = 'production'
          and production_canary_at is not null
          and owner_authorized_by ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
        )
        or (
          environment <> 'production'
          and production_canary_at is null
          and owner_authorized_by is null
        )
      )
    )
    or (
      qualification_state in ('disabled', 'superseded')
      and qualification_evidence_uri is null
      and evidence_expires_at is null
      and production_canary_at is null
      and owner_authorized_by is null
      and disable_reason ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    )
  )
);

create index if not exists mercury_provider_capability_qualification_lookup_idx
  on public.mercury_provider_capability_qualifications (
    provider,
    environment,
    normalized_capability,
    provider_tool_name,
    capability_version_sha256,
    qualification_state,
    evidence_expires_at
  );

create or replace function public.mercury_provider_capability_qualification_guard()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  if tg_op = 'INSERT' then
    if new.qualification_state <> 'discovered_unreviewed' then
      raise exception 'mercury_provider_capability_initial_state_invalid';
    end if;
    return new;
  end if;

  if tg_op = 'DELETE' then
    raise exception 'mercury_provider_capability_versions_are_immutable';
  end if;

  if (
    new.provider,
    new.environment,
    new.provider_tool_name,
    new.normalized_capability,
    new.input_schema,
    new.output_schema,
    new.schema_hash,
    new.response_shape_hash,
    new.required_permissions,
    new.capability_version_sha256,
    new.created_at
  ) is distinct from (
    old.provider,
    old.environment,
    old.provider_tool_name,
    old.normalized_capability,
    old.input_schema,
    old.output_schema,
    old.schema_hash,
    old.response_shape_hash,
    old.required_permissions,
    old.capability_version_sha256,
    old.created_at
  ) then
    raise exception 'mercury_provider_capability_versions_are_immutable';
  end if;

  if not (
    (old.qualification_state = 'discovered_unreviewed' and new.qualification_state = 'schema_validated')
    or (old.qualification_state = 'schema_validated' and new.qualification_state = 'nonproduction_qualified')
    or (old.qualification_state = 'nonproduction_qualified' and new.qualification_state = 'enabled')
    or (old.qualification_state = 'enabled' and new.qualification_state in ('disabled', 'superseded'))
  ) then
    raise exception 'mercury_provider_capability_transition_invalid';
  end if;

  if new.qualification_state = 'nonproduction_qualified'
    and new.environment = 'production'
    and not exists (
      select 1
      from public.mercury_provider_capability_qualifications as evidence
      where evidence.provider = new.provider
        and evidence.environment <> 'production'
        and evidence.provider_tool_name = new.provider_tool_name
        and evidence.normalized_capability = new.normalized_capability
        and evidence.input_schema = new.input_schema
        and evidence.output_schema = new.output_schema
        and evidence.schema_hash = new.schema_hash
        and evidence.response_shape_hash = new.response_shape_hash
        and evidence.required_permissions = new.required_permissions
        and evidence.qualification_state in ('nonproduction_qualified', 'enabled')
        and evidence.evidence_expires_at > statement_timestamp()
    ) then
    raise exception 'mercury_provider_capability_nonproduction_evidence_required';
  end if;

  if new.qualification_state = 'enabled' then
    if not (
      new.normalized_capability ~ '\.(get|list)$'
      or new.normalized_capability ~ '^documents\.[a-z][a-z0-9_]*\.create$'
    ) then
      raise exception 'mercury_provider_capability_operation_not_allowed';
    end if;
    if new.evidence_expires_at <= statement_timestamp() then
      raise exception 'mercury_provider_capability_evidence_expired';
    end if;
    if new.environment = 'production' and not exists (
      select 1
      from public.mercury_provider_capability_qualifications as evidence
      where evidence.provider = new.provider
        and evidence.environment <> 'production'
        and evidence.provider_tool_name = new.provider_tool_name
        and evidence.normalized_capability = new.normalized_capability
        and evidence.input_schema = new.input_schema
        and evidence.output_schema = new.output_schema
        and evidence.schema_hash = new.schema_hash
        and evidence.response_shape_hash = new.response_shape_hash
        and evidence.required_permissions = new.required_permissions
        and evidence.qualification_state in ('nonproduction_qualified', 'enabled')
        and evidence.evidence_expires_at > statement_timestamp()
    ) then
      raise exception 'mercury_provider_capability_nonproduction_evidence_required';
    end if;
  end if;

  new.updated_at = statement_timestamp();
  return new;
end;
$$;

revoke all on function public.mercury_provider_capability_qualification_guard()
  from public, anon, authenticated;

drop trigger if exists mercury_provider_capability_qualification_guard
  on public.mercury_provider_capability_qualifications;
create trigger mercury_provider_capability_qualification_guard
before insert or update or delete on public.mercury_provider_capability_qualifications
for each row execute function public.mercury_provider_capability_qualification_guard();

alter table public.mercury_provider_capability_qualifications enable row level security;
alter table public.mercury_provider_capability_qualifications force row level security;

revoke all on table public.mercury_provider_capability_qualifications
  from public, anon, authenticated;
grant select, insert, update on table public.mercury_provider_capability_qualifications
  to service_role;

commit;
