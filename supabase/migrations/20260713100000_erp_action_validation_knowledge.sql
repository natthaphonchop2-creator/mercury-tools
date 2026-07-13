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

create or replace function public.mercury_validation_test_guard_matches(
  expected_marker text
)
returns boolean
language sql
stable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select coalesce(
    expected_marker ~ '^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$'
    and current_setting('app.mercury_validation_test_guard', true) = expected_marker,
    false
  );
$function$;

revoke all on function public.mercury_validation_test_guard_matches(text)
  from public, anon, authenticated;
grant execute on function public.mercury_validation_test_guard_matches(text)
  to service_role;

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

create or replace function public.validation_text_has_forbidden_value(value text)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select
    value is null
    or char_length(value) > 512
    or value ~ '[[:cntrl:]]'
    or strpos(value, '@') > 0
    or strpos(lower(value), '://') > 0
    or strpos(value, '../') > 0
    or strpos(value, './') > 0
    or strpos(value, '~/') > 0
    or strpos(value, chr(92)) > 0
    or strpos(value, chr(8725)) > 0
    or strpos(value, chr(65295)) > 0
    or regexp_replace(
      regexp_replace(
        lower(value),
        '(debit/credit|input/output)',
        '',
        'g'
      ),
      '[0-9]{1,4}/[0-9]{1,2}(/[0-9]{1,4})?',
      '',
      'g'
    ) ~ '/'
    or exists (
      select 1
      from unnest(array[
        'bearer ',
        'basic ',
        'digest ',
        'gho_',
        'ghp_',
        'github_pat_',
        'pk_live_',
        'rk_live_',
        'raw_payload',
        'raw payload',
        'raw_response',
        'raw response',
        'request_body',
        'request body',
        'request_payload',
        'request payload',
        'response_body',
        'response body',
        'response_payload',
        'response payload',
        'provider_response',
        'provider response',
        'sk-',
        'sk_',
        'xoxb-',
        'xoxp-',
        'ya29.'
      ]) as forbidden(fragment)
      where strpos(lower(value), forbidden.fragment) > 0
    )
    or exists (
      select 1
      from regexp_matches(
        lower(value),
        '(^|[^a-z0-9])(password[ _-]+value|token[ _-]+value|secret[ _-]+value|credential[ _-]+value|api[ _-]+keys?|client[ _-]+secrets?|passwords?|tokens?|secrets?|credentials?)([[:space:]]*[:=][[:space:]]*|[[:space:]]+)([^[:space:]].*)',
        'g'
      ) as labelled_sensitive(parts)
      where labelled_sensitive.parts[4] !~ '^((absent|available|configured|disabled|included|known|missing|needed|omitted|present|provided|redacted|required|stored|supported|unavailable|unknown)|(are[[:space:]]+not[[:space:]]+available[[:space:]]+for[[:space:]]+live[[:space:]]+validation)|(are|is|remain|remains|was|were)[[:space:]]+(not[[:space:]]+)?(absent|available|configured|disabled|included|known|missing|needed|omitted|present|provided|redacted|required|stored|supported|unavailable|unknown)|(cannot|must|should)[[:space:]]+be[[:space:]]+(absent|available|configured|disabled|included|known|missing|needed|omitted|present|provided|redacted|required|stored|supported|unavailable|unknown))[.]?$'
    )
    or exists (
      select 1
      from regexp_matches(
        lower(value),
        '(^|[^a-z0-9])(provider|source)[ _-]+(record|document)([ _-]+id)?([[:space:]]*[:=][[:space:]]*|[[:space:]]+)([^[:space:]].*)',
        'g'
      ) as labelled_reference(parts)
      where labelled_reference.parts[6] !~ '^((absent|available|configured|disabled|included|known|missing|needed|omitted|present|provided|redacted|required|stored|supported|unavailable|unknown|field|id|identifier|key|number|schema|string)|(are|is|remain|remains|was|were)[[:space:]]+(not[[:space:]]+)?(absent|available|configured|disabled|included|known|missing|needed|omitted|present|provided|redacted|required|stored|supported|unavailable|unknown|field|id|identifier|key|number|schema|string)|(cannot|must|should)[[:space:]]+be[[:space:]]+(absent|available|configured|disabled|included|known|missing|needed|omitted|present|provided|redacted|required|stored|supported|unavailable|unknown|field|id|identifier|key|number|schema|string))[.]?$'
    )
    or value ~ '[A-Za-z0-9_-]{8,}[.][A-Za-z0-9_-]{8,}[.][A-Za-z0-9_-]{4,}'
    or value ~ '([[:digit:]][^[:alnum:]]*){9}'
    or btrim(value) ~ '^[[:digit:]]+$'
    or lower(value) ~ (
      '(^|[^a-z0-9])[a-z][a-z0-9]*[-_][a-z0-9_-]*'
      '[[:digit:]][a-z0-9_-]*($|[^a-z0-9])'
    )
    or (
      lower(value) ~ (
        '(^|[^a-z0-9])([a-z]+[0-9]+|[0-9]+[a-z]+)'
        '[a-z0-9]*($|[^a-z0-9])'
      )
      and btrim(lower(value)) !~ '^[1-5]xx$'
    )
    or left(btrim(value), 1) in ('{', '[')
    or value ~ '"[^"]+"[[:space:]]*:';
$function$;

revoke all on function public.validation_text_has_forbidden_value(text)
  from public, anon, authenticated;
grant execute on function public.validation_text_has_forbidden_value(text)
  to service_role;

create or replace function public.jsonb_has_forbidden_validation_value(value jsonb)
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
      'lax $.**'
    ) as nodes(item)
    where jsonb_typeof(nodes.item) in ('number', 'boolean', 'null')
      or (
        jsonb_typeof(nodes.item) = 'string'
        and (
          (
            nodes.item #>> '{}' !~ '^act_[0-9a-f]{24}$'
            and nodes.item #>> '{}' !~ '^av_[0-9a-f]{64}$'
            and nodes.item #>> '{}' !~ '^ev_[a-z0-9_]{8,128}$'
            and nodes.item #>> '{}' !~ '^run_[a-z0-9_]{8,128}$'
            and public.validation_text_has_forbidden_value(nodes.item #>> '{}')
          )
        )
      )
  );
$function$;

revoke all on function public.jsonb_has_forbidden_validation_value(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_has_forbidden_validation_value(jsonb)
  to service_role;

create or replace function public.jsonb_is_safe_validation_string_array(value jsonb)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select
    jsonb_typeof(value) = 'array'
    and not exists (
      select 1
      from jsonb_array_elements(
        case
          when jsonb_typeof(value) = 'array' then value
          else '[]'::jsonb
        end
      ) as elements(item)
      where jsonb_typeof(elements.item) <> 'string'
        or public.jsonb_has_forbidden_validation_value(elements.item)
    );
$function$;

revoke all on function public.jsonb_is_safe_validation_string_array(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_is_safe_validation_string_array(jsonb)
  to service_role;

create or replace function public.jsonb_is_safe_validation_response_shape(value jsonb)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select
    coalesce(jsonb_typeof(value) = 'object', false)
    and not public.jsonb_has_forbidden_validation_key(value)
    and not public.jsonb_has_forbidden_validation_value(value)
    and not exists (
      select 1
      from jsonb_path_query(value, 'lax $.**') as nodes(item)
      where jsonb_typeof(nodes.item) not in ('object', 'string')
        or (
          jsonb_typeof(nodes.item) = 'string'
          and nodes.item #>> '{}' not in (
            'boolean', 'integer', 'null', 'number', 'string', 'truncated', 'unknown', 'array'
          )
        )
    )
    and not exists (
      select 1
      from jsonb_path_query(value, 'lax $.**.keyvalue()') as entries(item)
      where char_length(entries.item->>'key') > 64
        or entries.item->>'key' !~ '^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)*$'
        or entries.item->>'key' ~ '[[:digit:]]{6,}'
    );
$function$;

revoke all on function public.jsonb_is_safe_validation_response_shape(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_is_safe_validation_response_shape(jsonb)
  to service_role;

create or replace function public.jsonb_is_safe_validation_semantic_contract(value jsonb)
returns boolean
language plpgsql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
declare
  array_field text;
  array_item jsonb;
  semantic_entry record;
begin
  if value is null or jsonb_typeof(value) <> 'object' then
    return false;
  end if;

  if public.jsonb_has_forbidden_validation_key(value)
    or public.jsonb_has_forbidden_validation_value(value)
    or not value ? 'business_object'
    or not value ? 'operation'
    or value - array[
      'business_object',
      'operation',
      'accounting_uses',
      'output_semantics',
      'join_keys',
      'next_action_ids',
      'required_external_capabilities',
      'optional_external_capabilities',
      'fallbacks'
    ] <> '{}'::jsonb
  then
    return false;
  end if;

  if jsonb_typeof(value->'business_object') <> 'string'
    or value->>'business_object' !~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'
    or jsonb_typeof(value->'operation') <> 'string'
    or value->>'operation' !~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'
  then
    return false;
  end if;

  foreach array_field in array array[
    'accounting_uses',
    'join_keys',
    'fallbacks'
  ] loop
    if value ? array_field then
      if jsonb_typeof(value->array_field) <> 'array' then
        return false;
      end if;
      for array_item in
        select elements.item
        from jsonb_array_elements(value->array_field) as elements(item)
      loop
        if jsonb_typeof(array_item) <> 'string'
          or array_item #>> '{}' !~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'
        then
          return false;
        end if;
      end loop;
    end if;
  end loop;

  if value ? 'next_action_ids' then
    if jsonb_typeof(value->'next_action_ids') <> 'array' then
      return false;
    end if;
    for array_item in
      select elements.item
      from jsonb_array_elements(value->'next_action_ids') as elements(item)
    loop
      if jsonb_typeof(array_item) <> 'string'
        or array_item #>> '{}' !~ '^act_[0-9a-f]{24}$'
      then
        return false;
      end if;
    end loop;
  end if;

  foreach array_field in array array[
    'required_external_capabilities',
    'optional_external_capabilities'
  ] loop
    if value ? array_field then
      if jsonb_typeof(value->array_field) <> 'array' then
        return false;
      end if;
      for array_item in
        select elements.item
        from jsonb_array_elements(value->array_field) as elements(item)
      loop
        if jsonb_typeof(array_item) <> 'string'
          or array_item #>> '{}' !~ (
            '^[a-z][a-z0-9_]{0,31}'
            '([.][a-z][a-z0-9_]{0,31}){2,5}$'
          )
        then
          return false;
        end if;
      end loop;
    end if;
  end loop;

  if value ? 'output_semantics' then
    if jsonb_typeof(value->'output_semantics') <> 'object' then
      return false;
    end if;
    for semantic_entry in
      select entries.key, entries.item
      from jsonb_each(value->'output_semantics') as entries(key, item)
    loop
      if semantic_entry.key !~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'
        or jsonb_typeof(semantic_entry.item) <> 'string'
        or semantic_entry.item #>> '{}' !~ '^[a-z]+( [a-z]+)+$'
        or char_length(semantic_entry.item #>> '{}') > 128
      then
        return false;
      end if;
    end loop;
  end if;

  return true;
end;
$function$;

revoke all on function public.jsonb_is_safe_validation_semantic_contract(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_is_safe_validation_semantic_contract(jsonb)
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
  ),
  constraint erp_validation_public_value_safe check (
    not public.validation_text_has_forbidden_value(summary_th)
    and not public.validation_text_has_forbidden_value(summary_en)
    and not public.validation_text_has_forbidden_value(recommended_next_step)
    and not public.validation_text_has_forbidden_value(status_class)
    and not public.validation_text_has_forbidden_value(reviewed_by)
    and not public.validation_text_has_forbidden_value(runner_version)
    and not public.jsonb_has_forbidden_validation_value(prerequisites)
    and not public.jsonb_has_forbidden_validation_value(limitations)
    and not public.jsonb_has_forbidden_validation_value(response_shape)
    and not public.jsonb_has_forbidden_validation_value(semantic_contract)
    and public.jsonb_is_safe_validation_string_array(prerequisites)
    and public.jsonb_is_safe_validation_string_array(limitations)
    and public.jsonb_is_safe_validation_response_shape(response_shape)
    and public.jsonb_is_safe_validation_semantic_contract(semantic_contract)
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
