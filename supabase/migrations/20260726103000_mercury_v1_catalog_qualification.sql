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
    'discovered_unreviewed', 'schema_validated', 'nonproduction_qualified',
    'enabled', 'disabled', 'superseded'
  )),
  company_sha256 text check (company_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_revision_sha256 text check (evidence_revision_sha256 ~ '^[0-9a-f]{64}$'),
  qualification_evidence_uri text,
  evidence_evaluated_at timestamptz,
  evidence_expires_at timestamptz,
  nonproduction_evidence_revision_sha256 text check (
    nonproduction_evidence_revision_sha256 ~ '^[0-9a-f]{64}$'
  ),
  nonproduction_company_sha256 text check (nonproduction_company_sha256 ~ '^[0-9a-f]{64}$'),
  production_canary_at timestamptz,
  owner_authorized_by text,
  disable_reason text,
  created_at timestamptz not null default statement_timestamp(),
  updated_at timestamptz not null default statement_timestamp(),
  unique (provider, capability_version_sha256, evidence_revision_sha256)
);

create index if not exists mercury_provider_capability_qualification_lookup_idx
  on public.mercury_provider_capability_qualifications (
    provider, environment, normalized_capability, provider_tool_name,
    capability_version_sha256, qualification_state, evidence_expires_at
  );

create or replace function public.mercury_canonical_jsonb(value jsonb)
returns text
language plpgsql
immutable
strict
set search_path = pg_catalog, public, pg_temp
as $$
begin
  case jsonb_typeof(value)
    when 'object' then
      return '{' || coalesce((
        select string_agg(to_jsonb(key)::text || ':' || public.mercury_canonical_jsonb(item), ',' order by key)
        from jsonb_each(value) as entry(key, item)
      ), '') || '}';
    when 'array' then
      return '[' || coalesce((
        select string_agg(public.mercury_canonical_jsonb(item), ',' order by ordinal)
        from jsonb_array_elements(value) with ordinality as entry(item, ordinal)
      ), '') || ']';
    else
      return value::text;
  end case;
end;
$$;

revoke all on function public.mercury_canonical_jsonb(jsonb)
  from public, anon, authenticated;

create or replace function public.publish_mercury_provider_capability_qualification(
  p_qualification jsonb,
  p_artifact jsonb default null
)
returns uuid
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  candidate public.mercury_provider_capability_qualifications%rowtype;
  existing public.mercury_provider_capability_qualifications%rowtype;
  now_at timestamptz := statement_timestamp();
  expected_schema_hash text;
  expected_version_hash text;
  expected_artifact_revision text;
  permission_values text[];
  sorted_permissions text[];
  reference_count integer;
begin
  if jsonb_typeof(p_qualification) <> 'object'
    or exists (
      select 1 from jsonb_object_keys(p_qualification) as key
      where key not in (
        'id', 'provider', 'environment', 'provider_tool_name', 'normalized_capability',
        'input_schema', 'output_schema', 'schema_hash', 'response_shape_hash',
        'required_permissions', 'capability_version_sha256', 'qualification_state',
        'company_sha256', 'evidence_revision_sha256', 'qualification_evidence_uri',
        'evidence_evaluated_at', 'evidence_expires_at',
        'nonproduction_evidence_revision_sha256', 'nonproduction_company_sha256',
        'production_canary_at', 'owner_authorized_by', 'disable_reason',
        'created_at', 'updated_at'
      )
    ) then
    raise exception 'mercury_provider_capability_payload_invalid';
  end if;

  select * into candidate
  from jsonb_populate_record(
    null::public.mercury_provider_capability_qualifications,
    p_qualification
  );
  candidate.id := coalesce(candidate.id, gen_random_uuid());
  candidate.created_at := coalesce(candidate.created_at, now_at);
  candidate.updated_at := now_at;

  if jsonb_typeof(candidate.required_permissions) <> 'array'
    or jsonb_array_length(candidate.required_permissions) = 0
    or exists (
      select 1 from jsonb_array_elements(candidate.required_permissions) as item
      where jsonb_typeof(item) <> 'string'
    ) then
    raise exception 'mercury_provider_capability_permissions_invalid';
  end if;
  select array_agg(value), array_agg(value order by value)
    into permission_values, sorted_permissions
  from jsonb_array_elements_text(candidate.required_permissions) with ordinality as item(value, ordinal);
  if permission_values is distinct from sorted_permissions
    or cardinality(permission_values) <> cardinality(array(select distinct unnest(permission_values)))
    or exists (
      select 1 from unnest(permission_values) as value
      where value !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ) then
    raise exception 'mercury_provider_capability_permissions_invalid';
  end if;

  expected_schema_hash := encode(digest(
    public.mercury_canonical_jsonb(jsonb_build_object(
      'input_schema', candidate.input_schema,
      'output_schema', candidate.output_schema
    )), 'sha256'
  ), 'hex');
  if candidate.schema_hash is distinct from expected_schema_hash then
    raise exception 'mercury_provider_capability_schema_hash_invalid';
  end if;
  expected_version_hash := encode(digest(
    public.mercury_canonical_jsonb(jsonb_build_object(
      'provider', candidate.provider,
      'environment', candidate.environment,
      'provider_tool_name', candidate.provider_tool_name,
      'normalized_capability', candidate.normalized_capability,
      'input_schema', candidate.input_schema,
      'output_schema', candidate.output_schema,
      'schema_hash', candidate.schema_hash,
      'response_shape_hash', candidate.response_shape_hash,
      'required_permissions', candidate.required_permissions
    )), 'sha256'
  ), 'hex');
  if candidate.capability_version_sha256 is distinct from expected_version_hash then
    raise exception 'mercury_provider_capability_version_invalid';
  end if;

  select * into existing
  from public.mercury_provider_capability_qualifications
  where id = candidate.id;

  if not found then
    if candidate.qualification_state <> 'discovered_unreviewed'
      or candidate.company_sha256 is not null
      or candidate.evidence_revision_sha256 is not null
      or candidate.qualification_evidence_uri is not null
      or candidate.evidence_evaluated_at is not null
      or candidate.evidence_expires_at is not null
      or candidate.nonproduction_evidence_revision_sha256 is not null
      or candidate.nonproduction_company_sha256 is not null
      or candidate.production_canary_at is not null
      or candidate.owner_authorized_by is not null
      or candidate.disable_reason is not null then
      raise exception 'mercury_provider_capability_initial_state_invalid';
    end if;
    insert into public.mercury_provider_capability_qualifications values (candidate.*);
    return candidate.id;
  end if;

  if p_qualification ? 'created_at'
    and candidate.created_at is distinct from existing.created_at then
    raise exception 'mercury_provider_capability_versions_are_immutable';
  end if;
  candidate.created_at := existing.created_at;

  if (
    candidate.provider, candidate.environment, candidate.provider_tool_name,
    candidate.normalized_capability, candidate.input_schema, candidate.output_schema,
    candidate.schema_hash, candidate.response_shape_hash, candidate.required_permissions,
    candidate.capability_version_sha256, candidate.created_at
  ) is distinct from (
    existing.provider, existing.environment, existing.provider_tool_name,
    existing.normalized_capability, existing.input_schema, existing.output_schema,
    existing.schema_hash, existing.response_shape_hash, existing.required_permissions,
    existing.capability_version_sha256, existing.created_at
  ) then
    raise exception 'mercury_provider_capability_versions_are_immutable';
  end if;

  if not (
    (existing.qualification_state = 'discovered_unreviewed' and candidate.qualification_state = 'schema_validated')
    or (existing.qualification_state = 'schema_validated' and candidate.qualification_state = 'nonproduction_qualified')
    or (existing.qualification_state = 'nonproduction_qualified' and candidate.qualification_state = 'enabled')
    or (existing.qualification_state = 'enabled' and candidate.qualification_state in ('disabled', 'superseded'))
  ) then
    raise exception 'mercury_provider_capability_transition_invalid';
  end if;

  if existing.qualification_state = 'nonproduction_qualified'
    and candidate.qualification_state = 'enabled'
    and (
      candidate.company_sha256 is distinct from existing.company_sha256
      or candidate.evidence_revision_sha256 is distinct from existing.evidence_revision_sha256
      or candidate.qualification_evidence_uri is distinct from existing.qualification_evidence_uri
      or candidate.evidence_evaluated_at is distinct from existing.evidence_evaluated_at
      or candidate.evidence_expires_at is distinct from existing.evidence_expires_at
      or candidate.nonproduction_evidence_revision_sha256 is distinct from existing.nonproduction_evidence_revision_sha256
      or candidate.nonproduction_company_sha256 is distinct from existing.nonproduction_company_sha256
    ) then
    raise exception 'mercury_provider_capability_evidence_identity_mismatch';
  end if;

  if candidate.qualification_state in ('nonproduction_qualified', 'enabled') then
    if jsonb_typeof(p_artifact) <> 'object'
      or candidate.company_sha256 is null
      or candidate.evidence_revision_sha256 is null
      or candidate.qualification_evidence_uri <> (
        'catalog://global/' || candidate.provider || '/qualifications/'
        || candidate.capability_version_sha256 || '-' || candidate.evidence_revision_sha256 || '.json'
      )
      or candidate.evidence_evaluated_at is null
      or candidate.evidence_expires_at is null
      or candidate.evidence_evaluated_at > now_at
      or candidate.evidence_expires_at <= now_at then
      raise exception 'mercury_provider_capability_evidence_invalid';
    end if;
    expected_artifact_revision := encode(digest(
      public.mercury_canonical_jsonb(p_artifact - 'evidence_revision_sha256'), 'sha256'
    ), 'hex');
    if p_artifact->>'evidence_revision_sha256' is distinct from expected_artifact_revision
      or candidate.evidence_revision_sha256 is distinct from expected_artifact_revision
      or p_artifact->>'provider' is distinct from candidate.provider
      or p_artifact->>'environment' is distinct from candidate.environment
      or p_artifact->>'company_sha256' is distinct from candidate.company_sha256
      or p_artifact->>'normalized_capability' is distinct from candidate.normalized_capability
      or p_artifact->>'provider_tool_name' is distinct from candidate.provider_tool_name
      or p_artifact->>'capability_version_sha256' is distinct from candidate.capability_version_sha256
      or p_artifact->>'schema_hash' is distinct from candidate.schema_hash
      or p_artifact->>'response_shape_hash' is distinct from candidate.response_shape_hash
      or (p_artifact->>'evaluated_at')::timestamptz is distinct from candidate.evidence_evaluated_at
      or (p_artifact->>'evidence_expires_at')::timestamptz is distinct from candidate.evidence_expires_at
      or coalesce((p_artifact->>'passed')::boolean, false) is not true then
      raise exception 'mercury_provider_capability_evidence_invalid';
    end if;
  elsif candidate.qualification_state = 'schema_validated' then
    if candidate.company_sha256 is not null or candidate.evidence_revision_sha256 is not null
      or candidate.qualification_evidence_uri is not null or candidate.evidence_evaluated_at is not null
      or candidate.evidence_expires_at is not null or candidate.nonproduction_evidence_revision_sha256 is not null
      or candidate.nonproduction_company_sha256 is not null or candidate.production_canary_at is not null
      or candidate.owner_authorized_by is not null or candidate.disable_reason is not null then
      raise exception 'mercury_provider_capability_evidence_unexpected';
    end if;
  elsif candidate.qualification_state in ('disabled', 'superseded') then
    if candidate.disable_reason is null
      or candidate.disable_reason !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
      or candidate.company_sha256 is distinct from existing.company_sha256
      or candidate.evidence_revision_sha256 is distinct from existing.evidence_revision_sha256
      or candidate.qualification_evidence_uri is distinct from existing.qualification_evidence_uri
      or candidate.evidence_evaluated_at is distinct from existing.evidence_evaluated_at
      or candidate.evidence_expires_at is distinct from existing.evidence_expires_at
      or candidate.nonproduction_evidence_revision_sha256 is distinct from existing.nonproduction_evidence_revision_sha256
      or candidate.nonproduction_company_sha256 is distinct from existing.nonproduction_company_sha256
      or candidate.production_canary_at is distinct from existing.production_canary_at
      or candidate.owner_authorized_by is distinct from existing.owner_authorized_by then
      raise exception 'mercury_provider_capability_terminal_invalid';
    end if;
  end if;

  if candidate.environment = 'production' and candidate.qualification_state in ('nonproduction_qualified', 'enabled', 'disabled', 'superseded') then
    if candidate.nonproduction_evidence_revision_sha256 is null
      or candidate.nonproduction_company_sha256 is null then
      raise exception 'mercury_provider_capability_nonproduction_evidence_required';
    end if;
    select count(*) into reference_count
    from public.mercury_provider_capability_qualifications as evidence
    where evidence.provider = candidate.provider
      and evidence.environment <> 'production'
      and evidence.provider_tool_name = candidate.provider_tool_name
      and evidence.normalized_capability = candidate.normalized_capability
      and evidence.input_schema = candidate.input_schema
      and evidence.output_schema = candidate.output_schema
      and evidence.schema_hash = candidate.schema_hash
      and evidence.response_shape_hash = candidate.response_shape_hash
      and evidence.required_permissions = candidate.required_permissions
      and evidence.evidence_revision_sha256 = candidate.nonproduction_evidence_revision_sha256
      and evidence.company_sha256 = candidate.nonproduction_company_sha256
      and evidence.qualification_state in ('nonproduction_qualified', 'enabled')
      and evidence.evidence_evaluated_at <= now_at
      and evidence.evidence_expires_at > now_at;
    if reference_count <> 1 then
      raise exception 'mercury_provider_capability_nonproduction_evidence_required';
    end if;
  elsif candidate.nonproduction_evidence_revision_sha256 is not null
    or candidate.nonproduction_company_sha256 is not null then
    raise exception 'mercury_provider_capability_nonproduction_reference_unexpected';
  end if;

  if candidate.qualification_state = 'enabled' then
    if not (
      candidate.normalized_capability ~ '\.(get|list)$'
      or candidate.normalized_capability ~ '^documents\.[a-z][a-z0-9_]*\.create$'
    ) then
      raise exception 'mercury_provider_capability_operation_not_allowed';
    end if;
    if candidate.environment = 'production' and (
      candidate.production_canary_at is null
      or candidate.production_canary_at > now_at
      or candidate.owner_authorized_by !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ) then
      raise exception 'mercury_provider_capability_canary_required';
    elsif candidate.environment <> 'production' and (
      candidate.production_canary_at is not null or candidate.owner_authorized_by is not null
    ) then
      raise exception 'mercury_provider_capability_canary_invalid';
    end if;
    if exists (
      select 1 from public.mercury_provider_capability_qualifications as active
      where active.id <> candidate.id
        and active.provider = candidate.provider
        and active.environment = candidate.environment
        and active.provider_tool_name = candidate.provider_tool_name
        and active.normalized_capability = candidate.normalized_capability
        and active.capability_version_sha256 = candidate.capability_version_sha256
        and active.qualification_state = 'enabled'
        and active.evidence_evaluated_at <= now_at
        and active.evidence_expires_at > now_at
    ) then
      raise exception 'mercury_provider_capability_current_revision_ambiguous';
    end if;
  end if;

  update public.mercury_provider_capability_qualifications
  set qualification_state = candidate.qualification_state,
      company_sha256 = candidate.company_sha256,
      evidence_revision_sha256 = candidate.evidence_revision_sha256,
      qualification_evidence_uri = candidate.qualification_evidence_uri,
      evidence_evaluated_at = candidate.evidence_evaluated_at,
      evidence_expires_at = candidate.evidence_expires_at,
      nonproduction_evidence_revision_sha256 = candidate.nonproduction_evidence_revision_sha256,
      nonproduction_company_sha256 = candidate.nonproduction_company_sha256,
      production_canary_at = candidate.production_canary_at,
      owner_authorized_by = candidate.owner_authorized_by,
      disable_reason = candidate.disable_reason,
      updated_at = candidate.updated_at
  where id = candidate.id;
  return candidate.id;
end;
$$;

revoke all on function public.publish_mercury_provider_capability_qualification(jsonb, jsonb)
  from public, anon, authenticated;
grant execute on function public.publish_mercury_provider_capability_qualification(jsonb, jsonb)
  to service_role;

alter table public.mercury_provider_capability_qualifications enable row level security;
alter table public.mercury_provider_capability_qualifications force row level security;

revoke all on table public.mercury_provider_capability_qualifications
  from public, anon, authenticated, service_role;
grant select on table public.mercury_provider_capability_qualifications to service_role;

commit;
