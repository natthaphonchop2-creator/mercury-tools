begin;

create or replace function public.mercury_jsonb_has_exact_keys(
  value pg_catalog.jsonb,
  expected_keys pg_catalog.text[]
)
returns pg_catalog.bool
language sql
immutable
set search_path = ''
as $$
  select coalesce(pg_catalog.jsonb_typeof(value) = 'object'
    and value ?& expected_keys
    and not exists (
      select 1
      from pg_catalog.jsonb_object_keys(value) as supplied(key)
      where not supplied.key = any(expected_keys)
    ), false)
$$;

create or replace function public.mercury_public_text(value pg_catalog.text)
returns pg_catalog.text
language plpgsql
immutable
set search_path = ''
as $$
declare
  projected pg_catalog.text := coalesce(value, '');
begin
  projected := pg_catalog.regexp_replace(
    projected,
    '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
    '[REDACTED_EMAIL]',
    'gi'
  );
  projected := pg_catalog.regexp_replace(
    projected,
    '(^|[^0-9])([0-9][ -]?){13}([^0-9]|$)',
    '\1[REDACTED_TAX_ID]\3',
    'g'
  );
  projected := pg_catalog.regexp_replace(
    projected,
    '(^|[^0-9])(0[689][0-9][ -]?[0-9]{3}[ -]?[0-9]{4})([^0-9]|$)',
    '\1[REDACTED_PHONE]\3',
    'g'
  );
  projected := pg_catalog.regexp_replace(
    projected,
    'sk-[A-Za-z0-9_-]{12,}',
    '[REDACTED_TOKEN]',
    'gi'
  );
  return projected;
end;
$$;

create or replace function public.mercury_public_identifier_is_safe(value pg_catalog.text)
returns pg_catalog.bool
language sql
immutable
set search_path = ''
as $$
  select value is not null
    and value ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    and public.mercury_public_text(value) = value
$$;

create or replace function public.mercury_review_codes_are_safe(value pg_catalog.jsonb)
returns pg_catalog.bool
language sql
immutable
set search_path = ''
as $$
  select coalesce(
    pg_catalog.jsonb_typeof(value) = 'array'
    and pg_catalog.jsonb_array_length(value) <= 2500
    and not exists (
      select 1
      from pg_catalog.jsonb_array_elements(value) as item(value)
      where pg_catalog.jsonb_typeof(item.value) <> 'string'
        or not public.mercury_public_identifier_is_safe(item.value #>> '{}')
    )
    and (
      select pg_catalog.count(*)
      from pg_catalog.jsonb_array_elements_text(value)
    ) = (
      select pg_catalog.count(distinct item.value)
      from pg_catalog.jsonb_array_elements_text(value) as item(value)
    ), false)
$$;

create or replace function public.mercury_create_schema_is_closed(
  value pg_catalog.jsonb,
  is_root pg_catalog.bool default true
)
returns pg_catalog.bool
language plpgsql
immutable
set search_path = ''
as $$
declare
  schema_type pg_catalog.text;
  properties pg_catalog.jsonb;
  required_keys pg_catalog.jsonb;
  child pg_catalog.jsonb;
  key pg_catalog.text;
begin
  if pg_catalog.jsonb_typeof(value) <> 'object' or value = '{}'::pg_catalog.jsonb
    or value ? '$ref'
    or value ? 'allOf'
    or value ? 'anyOf'
    or value ? 'oneOf'
    or value ? 'not'
    or value ? 'if'
    or value ? 'then'
    or value ? 'else'
  then
    return false;
  end if;
  schema_type := value->>'type';
  if schema_type is null or (is_root and schema_type <> 'object') then
    return false;
  end if;
  if schema_type = 'object' then
    properties := value->'properties';
    required_keys := value->'required';
    if pg_catalog.jsonb_typeof(properties) <> 'object'
      or properties = '{}'::pg_catalog.jsonb
      or pg_catalog.jsonb_typeof(required_keys) <> 'array'
      or pg_catalog.jsonb_array_length(required_keys) = 0
      or value->'additionalProperties' <> 'false'::pg_catalog.jsonb
      or (value ? 'unevaluatedProperties'
        and value->'unevaluatedProperties' <> 'false'::pg_catalog.jsonb)
      or value ? 'patternProperties'
    then
      return false;
    end if;
    for key in select pg_catalog.jsonb_array_elements_text(required_keys) loop
      if not properties ? key then
        return false;
      end if;
    end loop;
    for child in select entry.value from pg_catalog.jsonb_each(properties) as entry(key, value) loop
      if not public.mercury_create_schema_is_closed(child, false) then
        return false;
      end if;
    end loop;
    return true;
  end if;
  if schema_type = 'array' then
    child := value->'items';
    if pg_catalog.jsonb_typeof(child) <> 'object'
      or child = '{}'::pg_catalog.jsonb
      or pg_catalog.jsonb_typeof(value->'maxItems') <> 'number'
      or (value->>'maxItems')::pg_catalog.int4 < 1
    then
      return false;
    end if;
    return public.mercury_create_schema_is_closed(child, false);
  end if;
  return schema_type in ('string', 'integer', 'boolean', 'null');
end;
$$;

create or replace function public.mercury_parent_operation_transition_is_allowed(
  current_state pg_catalog.text,
  target_state pg_catalog.text,
  child_states pg_catalog.text[]
)
returns pg_catalog.bool
language plpgsql
immutable
set search_path = ''
as $$
declare
  child_count pg_catalog.int4 := coalesce(pg_catalog.array_length(child_states, 1), 0);
  all_terminal pg_catalog.bool;
begin
  if current_state = 'prepared' then
    return target_state in ('awaiting_confirmation', 'expired', 'cancelled');
  end if;
  if current_state = 'awaiting_confirmation' then
    if target_state = 'dispatching' then
      return not exists (
        select 1
        from pg_catalog.unnest(child_states) as state(value)
        where state.value not in ('awaiting_confirmation', 'failed_pre_dispatch')
      );
    end if;
    return target_state in ('failed_pre_dispatch', 'expired', 'cancelled');
  end if;
  if current_state = 'failed_pre_dispatch' then
    if target_state = 'dispatching' then
      return not exists (
        select 1
        from pg_catalog.unnest(child_states) as state(value)
        where state.value not in ('awaiting_confirmation', 'failed_pre_dispatch')
      );
    end if;
    return target_state = 'cancelled';
  end if;
  if current_state = 'outcome_unknown' then
    if target_state = 'succeeded' then
      return child_count > 0 and not exists (
        select 1 from pg_catalog.unnest(child_states) as state(value)
        where state.value <> 'succeeded'
      );
    end if;
    if target_state = 'needs_manual_review' then
      return child_count > 0
        and not exists (
          select 1 from pg_catalog.unnest(child_states) as state(value)
          where state.value not in (
            'succeeded', 'provider_rejected', 'outcome_unknown', 'needs_manual_review',
            'not_dispatched', 'expired', 'cancelled'
          )
        )
        and (
          'needs_manual_review' = any(child_states)
          or 'outcome_unknown' = any(child_states)
        );
    end if;
    return false;
  end if;
  if current_state <> 'dispatching' then
    return false;
  end if;
  select child_count > 0 and not exists (
    select 1
    from pg_catalog.unnest(child_states) as state(value)
    where state.value not in (
      'succeeded', 'provider_rejected', 'outcome_unknown', 'needs_manual_review',
      'not_dispatched', 'expired', 'cancelled'
    )
  ) into all_terminal;
  if target_state = 'succeeded' then
    return child_count > 0 and not exists (
      select 1 from pg_catalog.unnest(child_states) as state(value)
      where state.value <> 'succeeded'
    );
  end if;
  if target_state = 'provider_rejected' then
    return all_terminal and 'provider_rejected' = any(child_states);
  end if;
  if target_state = 'outcome_unknown' then
    return all_terminal and 'outcome_unknown' = any(child_states);
  end if;
  if target_state = 'needs_manual_review' then
    return all_terminal and (
      'needs_manual_review' = any(child_states) or 'outcome_unknown' = any(child_states)
    );
  end if;
  return false;
end;
$$;

create or replace function public.mercury_item_operation_transition_is_allowed(
  current_state pg_catalog.text,
  target_state pg_catalog.text,
  parent_state pg_catalog.text
)
returns pg_catalog.bool
language sql
immutable
set search_path = ''
as $$
  select parent_state = 'dispatching' and (
    (current_state = 'prepared'
      and target_state in ('awaiting_confirmation', 'expired', 'cancelled'))
    or (current_state = 'awaiting_confirmation'
      and target_state in ('dispatching', 'failed_pre_dispatch', 'not_dispatched', 'cancelled', 'expired'))
    or (current_state = 'failed_pre_dispatch'
      and target_state in ('dispatching', 'cancelled'))
    or (current_state = 'dispatching'
      and target_state in ('succeeded', 'provider_rejected', 'outcome_unknown'))
    or (current_state = 'outcome_unknown'
      and target_state in ('succeeded', 'needs_manual_review'))
  )
$$;

create table if not exists public.mercury_document_previews (
  id pg_catalog.uuid primary key,
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  connection_id pg_catalog.uuid not null
    references public.mercury_provider_connections(id) on delete restrict,
  provider pg_catalog.text not null check (provider in ('flowaccount', 'peak')),
  provider_account_sha256 pg_catalog.text not null
    check (provider_account_sha256 ~ '^[0-9a-f]{64}$'),
  account_display_name pg_catalog.text not null
    check (pg_catalog.length(account_display_name) between 1 and 200),
  environment pg_catalog.text not null
    check (environment ~ '^[a-z][a-z0-9_-]{0,63}$'),
  qualification_id pg_catalog.uuid not null
    references public.mercury_provider_capability_qualifications(id) on delete restrict,
  provider_tool_name pg_catalog.text not null
    check (provider_tool_name ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  capability_id pg_catalog.text not null
    check (capability_id ~ '^documents\.[a-z][a-z0-9_]*\.create$'),
  capability_version pg_catalog.text not null
    check (capability_version ~ '^[0-9a-f]{64}$'),
  schema_hash pg_catalog.text not null check (schema_hash ~ '^[0-9a-f]{64}$'),
  response_shape_hash pg_catalog.text not null check (response_shape_hash ~ '^[0-9a-f]{64}$'),
  evidence_revision_sha256 pg_catalog.text not null
    check (evidence_revision_sha256 ~ '^[0-9a-f]{64}$'),
  projector_id pg_catalog.text not null
    check (projector_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  projector_version pg_catalog.text not null
    check (projector_version ~ '^[0-9a-f]{64}$'),
  connection_revision pg_catalog.int8 not null check (connection_revision >= 1),
  connection_readiness pg_catalog.text not null check (connection_readiness = 'ready'),
  provider_call_hash pg_catalog.text not null check (provider_call_hash ~ '^[0-9a-f]{64}$'),
  preview_integrity_hash pg_catalog.text not null
    check (preview_integrity_hash ~ '^[0-9a-f]{64}$'),
  status pg_catalog.text not null
    check (status in ('prepared', 'awaiting_confirmation', 'confirmed', 'expired', 'cancelled')),
  state_version pg_catalog.int8 not null default 1 check (state_version >= 1),
  document_count pg_catalog.int4 not null check (document_count between 1 and 25),
  currency pg_catalog.text not null check (currency ~ '^[A-Z]{3}$'),
  subtotal pg_catalog.text not null
    check (subtotal ~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'),
  discount_total pg_catalog.text not null
    check (discount_total ~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'),
  vat_total pg_catalog.text not null
    check (vat_total ~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'),
  withholding_tax_total pg_catalog.text not null
    check (withholding_tax_total ~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'),
  grand_total pg_catalog.text not null
    check (grand_total ~ '^-?(0|[1-9][0-9]*)(\.[0-9]{1,4})?$'),
  warning_count pg_catalog.int4 not null default 0 check (warning_count >= 0),
  sanitized_summary pg_catalog.jsonb not null
    check (pg_catalog.jsonb_typeof(sanitized_summary) = 'object'),
  warnings pg_catalog.jsonb not null default '[]'::pg_catalog.jsonb,
  accountant_review_points pg_catalog.jsonb not null default '[]'::pg_catalog.jsonb,
  supersedes_preview_id pg_catalog.uuid
    references public.mercury_document_previews(id) on delete restrict,
  created_at pg_catalog.timestamptz not null,
  expires_at pg_catalog.timestamptz not null,
  payload_purge_after pg_catalog.timestamptz not null,
  confirmed_at pg_catalog.timestamptz,
  cancelled_at pg_catalog.timestamptz,
  check (expires_at = created_at + pg_catalog.make_interval(secs => 1800)),
  check (
    (status <> 'confirmed'
      and payload_purge_after = expires_at + pg_catalog.make_interval(hours => 24))
    or (status = 'confirmed'
      and confirmed_at is not null
      and payload_purge_after >= expires_at + pg_catalog.make_interval(hours => 24)
      and payload_purge_after <= confirmed_at + pg_catalog.make_interval(days => 30))
  ),
  check (
    (status in ('prepared', 'awaiting_confirmation')
      and confirmed_at is null and cancelled_at is null)
    or (status = 'confirmed' and confirmed_at is not null and cancelled_at is null)
    or (status = 'cancelled' and confirmed_at is null and cancelled_at is not null)
    or (status = 'expired' and confirmed_at is null and cancelled_at is null)
  ),
  check (
    (status in ('prepared', 'awaiting_confirmation') and state_version = 1)
    or (status in ('confirmed', 'expired', 'cancelled') and state_version = 2)
  ),
  unique (workspace_id, connection_id, provider_call_hash),
  unique (id, tenant_id, auth_user_id, workspace_id, connection_id)
);

create table if not exists public.mercury_preview_items (
  id pg_catalog.uuid primary key,
  preview_id pg_catalog.uuid not null,
  tenant_id pg_catalog.uuid not null,
  auth_user_id pg_catalog.uuid not null,
  workspace_id pg_catalog.uuid not null,
  connection_id pg_catalog.uuid not null,
  item_index pg_catalog.int4 not null check (item_index between 0 and 24),
  client_item_id pg_catalog.text not null
    check (public.mercury_public_identifier_is_safe(client_item_id)),
  provider_call_hash pg_catalog.text not null
    check (provider_call_hash ~ '^[0-9a-f]{64}$'),
  preview_integrity_hash pg_catalog.text not null
    check (preview_integrity_hash ~ '^[0-9a-f]{64}$'),
  document_type pg_catalog.text not null
    check (document_type ~ '^[a-z][a-z0-9_]{0,63}$'),
  sanitized_summary pg_catalog.jsonb not null
    check (pg_catalog.jsonb_typeof(sanitized_summary) = 'object'),
  payload_envelope_id pg_catalog.uuid not null unique,
  payload_key_version pg_catalog.text not null
    check (payload_key_version ~ '^[a-z][a-z0-9]*(\.[a-z0-9]+|_[a-z0-9]+|-[a-z0-9]+)*$'),
  payload_nonce pg_catalog.bytea not null check (pg_catalog.octet_length(payload_nonce) = 12),
  payload_ciphertext pg_catalog.bytea not null
    check (pg_catalog.octet_length(payload_ciphertext) >= 16),
  payload_aad_hash pg_catalog.bytea not null
    check (pg_catalog.octet_length(payload_aad_hash) = 32),
  payload_envelope_created_at pg_catalog.timestamptz not null,
  created_at pg_catalog.timestamptz not null,
  payload_purge_after pg_catalog.timestamptz not null,
  check (
    payload_purge_after >= created_at + pg_catalog.make_interval(secs => 1800)
      + pg_catalog.make_interval(hours => 24)
    and payload_purge_after <= created_at + pg_catalog.make_interval(secs => 1800)
      + pg_catalog.make_interval(days => 30)
  ),
  foreign key (preview_id, tenant_id, auth_user_id, workspace_id, connection_id)
    references public.mercury_document_previews (
      id, tenant_id, auth_user_id, workspace_id, connection_id
    ) on delete cascade,
  unique (preview_id, item_index),
  unique (preview_id, client_item_id),
  unique (preview_id, provider_call_hash),
  unique (workspace_id, connection_id, provider_call_hash),
  unique (
    id, preview_id, tenant_id, auth_user_id, workspace_id, connection_id,
    item_index, client_item_id, provider_call_hash, preview_integrity_hash
  )
);

create table if not exists public.mercury_operations (
  id pg_catalog.uuid primary key,
  preview_id pg_catalog.uuid not null,
  tenant_id pg_catalog.uuid not null references public.mercury_tenants(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  workspace_id pg_catalog.uuid not null references public.mercury_workspaces(id) on delete cascade,
  connection_id pg_catalog.uuid not null
    references public.mercury_provider_connections(id) on delete restrict,
  provider pg_catalog.text not null check (provider in ('flowaccount', 'peak')),
  environment pg_catalog.text not null
    check (environment ~ '^[a-z][a-z0-9_-]{0,63}$'),
  capability_id pg_catalog.text not null
    check (capability_id ~ '^documents\.[a-z][a-z0-9_]*\.create$'),
  capability_version pg_catalog.text not null check (capability_version ~ '^[0-9a-f]{64}$'),
  connection_revision pg_catalog.int8 not null check (connection_revision >= 1),
  provider_call_hash pg_catalog.text not null check (provider_call_hash ~ '^[0-9a-f]{64}$'),
  preview_integrity_hash pg_catalog.text not null
    check (preview_integrity_hash ~ '^[0-9a-f]{64}$'),
  status pg_catalog.text not null check (status in (
    'prepared', 'awaiting_confirmation', 'dispatching', 'succeeded',
    'failed_pre_dispatch', 'provider_rejected', 'outcome_unknown',
    'needs_manual_review', 'expired', 'cancelled'
  )),
  state_version pg_catalog.int8 not null default 1 check (state_version >= 1),
  created_at pg_catalog.timestamptz not null,
  updated_at pg_catalog.timestamptz not null,
  payload_purge_after pg_catalog.timestamptz not null,
  check (updated_at >= created_at),
  check (
    payload_purge_after > created_at
    and payload_purge_after <= created_at + pg_catalog.make_interval(days => 30)
  ),
  foreign key (preview_id, tenant_id, auth_user_id, workspace_id, connection_id)
    references public.mercury_document_previews (
      id, tenant_id, auth_user_id, workspace_id, connection_id
    ) on delete restrict,
  unique (preview_id),
  unique (workspace_id, connection_id, provider_call_hash),
  unique (id, tenant_id, auth_user_id, workspace_id, connection_id)
);

create table if not exists public.mercury_operation_items (
  id pg_catalog.uuid primary key,
  operation_id pg_catalog.uuid not null,
  preview_item_id pg_catalog.uuid not null,
  preview_id pg_catalog.uuid not null,
  tenant_id pg_catalog.uuid not null,
  auth_user_id pg_catalog.uuid not null,
  workspace_id pg_catalog.uuid not null,
  connection_id pg_catalog.uuid not null,
  item_index pg_catalog.int4 not null check (item_index between 0 and 24),
  client_item_id pg_catalog.text not null
    check (public.mercury_public_identifier_is_safe(client_item_id)),
  provider_call_hash pg_catalog.text not null
    check (provider_call_hash ~ '^[0-9a-f]{64}$'),
  preview_integrity_hash pg_catalog.text not null
    check (preview_integrity_hash ~ '^[0-9a-f]{64}$'),
  status pg_catalog.text not null check (status in (
    'prepared', 'awaiting_confirmation', 'dispatching', 'succeeded',
    'failed_pre_dispatch', 'provider_rejected', 'outcome_unknown',
    'needs_manual_review', 'not_dispatched', 'expired', 'cancelled'
  )),
  state_version pg_catalog.int8 not null default 1 check (state_version >= 1),
  provider_result_identifier pg_catalog.text
    check (provider_result_identifier is null or pg_catalog.length(provider_result_identifier) between 1 and 200),
  created_at pg_catalog.timestamptz not null,
  updated_at pg_catalog.timestamptz not null,
  check (updated_at >= created_at),
  foreign key (
    operation_id, tenant_id, auth_user_id, workspace_id, connection_id
  ) references public.mercury_operations (
    id, tenant_id, auth_user_id, workspace_id, connection_id
  ) on delete cascade,
  foreign key (
    preview_item_id, preview_id, tenant_id, auth_user_id, workspace_id,
    connection_id, item_index, client_item_id, provider_call_hash, preview_integrity_hash
  ) references public.mercury_preview_items (
    id, preview_id, tenant_id, auth_user_id, workspace_id,
    connection_id, item_index, client_item_id, provider_call_hash, preview_integrity_hash
  ) on delete restrict,
  unique (operation_id, item_index),
  unique (operation_id, preview_item_id),
  unique (operation_id, client_item_id),
  unique (operation_id, provider_call_hash),
  unique (id, operation_id, tenant_id, auth_user_id, workspace_id, connection_id)
);

create table if not exists public.mercury_operation_events (
  id pg_catalog.uuid primary key,
  operation_id pg_catalog.uuid not null,
  operation_item_id pg_catalog.uuid,
  tenant_id pg_catalog.uuid not null,
  auth_user_id pg_catalog.uuid not null,
  workspace_id pg_catalog.uuid not null,
  connection_id pg_catalog.uuid not null,
  from_state pg_catalog.text,
  to_state pg_catalog.text not null,
  state_version pg_catalog.int8 not null check (state_version >= 1),
  sanitized_reason pg_catalog.text not null
    check (public.mercury_public_identifier_is_safe(sanitized_reason)),
  occurred_at pg_catalog.timestamptz not null,
  check (from_state is null or from_state in (
    'prepared', 'awaiting_confirmation', 'dispatching', 'succeeded',
    'failed_pre_dispatch', 'provider_rejected', 'outcome_unknown',
    'needs_manual_review', 'not_dispatched', 'expired', 'cancelled'
  )),
  check (to_state in (
    'prepared', 'awaiting_confirmation', 'dispatching', 'succeeded',
    'failed_pre_dispatch', 'provider_rejected', 'outcome_unknown',
    'needs_manual_review', 'not_dispatched', 'expired', 'cancelled'
  )),
  foreign key (
    operation_id, tenant_id, auth_user_id, workspace_id, connection_id
  ) references public.mercury_operations (
    id, tenant_id, auth_user_id, workspace_id, connection_id
  ) on delete cascade,
  foreign key (
    operation_item_id, operation_id, tenant_id, auth_user_id,
    workspace_id, connection_id
  ) references public.mercury_operation_items (
    id, operation_id, tenant_id, auth_user_id, workspace_id, connection_id
  ) on delete cascade
);

create unique index if not exists mercury_operation_parent_event_version_uidx
  on public.mercury_operation_events (operation_id, state_version)
  where operation_item_id is null;
create unique index if not exists mercury_operation_item_event_version_uidx
  on public.mercury_operation_events (operation_id, operation_item_id, state_version)
  where operation_item_id is not null;
create index if not exists mercury_preview_lookup_idx
  on public.mercury_document_previews (tenant_id, auth_user_id, workspace_id, connection_id, id);
create index if not exists mercury_preview_payload_purge_idx
  on public.mercury_document_previews (payload_purge_after, id)
  where status in ('prepared', 'awaiting_confirmation', 'expired', 'cancelled');
create index if not exists mercury_preview_item_purge_idx
  on public.mercury_preview_items (payload_purge_after, preview_id, id);
create index if not exists mercury_operation_lookup_idx
  on public.mercury_operations (tenant_id, auth_user_id, workspace_id, connection_id, id);
create index if not exists mercury_operation_payload_purge_idx
  on public.mercury_operations (payload_purge_after, id);
create index if not exists mercury_operation_event_timeline_idx
  on public.mercury_operation_events (operation_id, occurred_at, id);

alter table public.mercury_document_previews enable row level security;
alter table public.mercury_preview_items enable row level security;
alter table public.mercury_operations enable row level security;
alter table public.mercury_operation_items enable row level security;
alter table public.mercury_operation_events enable row level security;

revoke all on table public.mercury_document_previews,
  public.mercury_preview_items,
  public.mercury_operations,
  public.mercury_operation_items,
  public.mercury_operation_events
  from public, anon, authenticated;
grant all on table public.mercury_document_previews to service_role;
grant all on table public.mercury_preview_items to service_role;
grant all on table public.mercury_operations to service_role;
grant all on table public.mercury_operation_items to service_role;
grant all on table public.mercury_operation_events to service_role;

do $$
declare
  table_name pg_catalog.text;
  policy_name pg_catalog.text;
begin
  foreach table_name in array array[
    'mercury_document_previews',
    'mercury_preview_items',
    'mercury_operations',
    'mercury_operation_items',
    'mercury_operation_events'
  ] loop
    policy_name := table_name || '_tenant_member';
    if not exists (
      select 1 from pg_catalog.pg_policy
      where polname = policy_name
        and polrelid = ('public.' || table_name)::pg_catalog.regclass
    ) then
      execute pg_catalog.format(
        'create policy %1$I on public.%2$I for select to authenticated using ('
        || 'exists (select 1 from public.mercury_workspace_members as member '
        || 'join public.mercury_workspaces as workspace '
        || 'on workspace.id = member.workspace_id and workspace.tenant_id = member.tenant_id '
        || 'where member.tenant_id = %2$I.tenant_id and member.workspace_id = %2$I.workspace_id '
        || 'and member.auth_user_id = (select auth.uid()) and member.status = ''active'' '
        || 'and workspace.status = ''active''))',
        policy_name,
        table_name
      );
    end if;
  end loop;
end;
$$;

create or replace function public.mercury_preview_authority_is_current(
  candidate public.mercury_document_previews,
  now_at pg_catalog.timestamptz
)
returns pg_catalog.void
language plpgsql
security definer
set search_path = ''
as $$
declare
  connection public.mercury_provider_connections%rowtype;
  qualification public.mercury_provider_capability_qualifications%rowtype;
begin
  select * into connection
  from public.mercury_provider_connections as stored
  where stored.id = candidate.connection_id
    and stored.tenant_id = candidate.tenant_id
    and stored.auth_user_id = candidate.auth_user_id
    and stored.workspace_id = candidate.workspace_id
  for share;
  if not found
    or connection.provider <> candidate.provider
    or connection.environment <> candidate.environment
    or connection.readiness <> 'ready'
    or connection.revision <> candidate.connection_revision
    or pg_catalog.encode(public.digest(
      pg_catalog.convert_to(connection.provider_account_id, 'UTF8'), 'sha256'
    ), 'hex') <> candidate.provider_account_sha256
  then
    raise invalid_parameter_value using message = 'preview_binding_changed';
  end if;
  select * into qualification
  from public.mercury_provider_capability_qualifications as stored
  where stored.id = candidate.qualification_id
  for share;
  if not found
    or qualification.provider <> candidate.provider
    or qualification.environment <> candidate.environment
    or qualification.provider_tool_name <> candidate.provider_tool_name
    or qualification.normalized_capability <> candidate.capability_id
    or qualification.capability_version_sha256 <> candidate.capability_version
    or qualification.schema_hash <> candidate.schema_hash
    or qualification.response_shape_hash <> candidate.response_shape_hash
    or qualification.evidence_revision_sha256 <> candidate.evidence_revision_sha256
    or qualification.company_sha256 <> candidate.provider_account_sha256
  then
    raise invalid_parameter_value using message = 'preview_binding_changed';
  end if;
  if qualification.qualification_state <> 'enabled'
    or qualification.evidence_evaluated_at is null
    or qualification.evidence_evaluated_at > now_at
    or qualification.evidence_expires_at is null
    or qualification.evidence_expires_at <= now_at
    or not public.mercury_create_schema_is_closed(qualification.input_schema, true)
    or exists (
      select 1
      from pg_catalog.jsonb_array_elements_text(qualification.required_permissions) as required(value)
      where not connection.granted_permissions ? required.value
    )
  then
    raise invalid_parameter_value using message = 'capability_unavailable';
  end if;
end;
$$;

create or replace function public.mercury_reject_open_create_qualification()
returns pg_catalog.trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.normalized_capability ~ '^documents\.[a-z][a-z0-9_]*\.create$'
    and not public.mercury_create_schema_is_closed(new.input_schema, true)
  then
    raise invalid_parameter_value using message = 'capability_unavailable';
  end if;
  return new;
end;
$$;

do $$
begin
  if not exists (
    select 1 from pg_catalog.pg_trigger
    where tgname = 'mercury_reject_open_create_qualification_trigger'
      and tgrelid = 'public.mercury_provider_capability_qualifications'::pg_catalog.regclass
  ) then
    create trigger mercury_reject_open_create_qualification_trigger
      before insert or update of normalized_capability, input_schema
      on public.mercury_provider_capability_qualifications
      for each row execute function public.mercury_reject_open_create_qualification();
  end if;
end;
$$;

create or replace function public.save_mercury_document_preview(
  p_preview pg_catalog.jsonb,
  p_items pg_catalog.jsonb
)
returns table (preview pg_catalog.jsonb, items pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
declare
  candidate public.mercury_document_previews%rowtype;
  candidate_item public.mercury_preview_items%rowtype;
  existing_preview public.mercury_document_previews%rowtype;
  item pg_catalog.jsonb;
  item_summary pg_catalog.jsonb;
  item_count pg_catalog.int4 := 0;
begin
  if not public.mercury_jsonb_has_exact_keys(p_preview, array[
    'id', 'tenant_id', 'auth_user_id', 'workspace_id', 'connection_id',
    'provider', 'provider_account_sha256', 'account_display_name', 'environment',
    'qualification_id', 'provider_tool_name', 'capability_id', 'capability_version',
    'schema_hash', 'response_shape_hash', 'evidence_revision_sha256',
    'projector_id', 'projector_version', 'connection_revision', 'connection_readiness',
    'provider_call_hash', 'preview_integrity_hash', 'status', 'state_version',
    'document_count', 'currency', 'subtotal', 'discount_total', 'vat_total',
    'withholding_tax_total', 'grand_total', 'warning_count', 'sanitized_summary',
    'warnings', 'accountant_review_points', 'supersedes_preview_id', 'created_at',
    'expires_at', 'payload_purge_after', 'confirmed_at', 'cancelled_at'
  ]::pg_catalog.text[]) or pg_catalog.jsonb_typeof(p_items) <> 'array' then
    raise invalid_parameter_value using message = 'preview_conflict';
  end if;
  select * into candidate
  from pg_catalog.jsonb_populate_record(null::public.mercury_document_previews, p_preview);
  candidate.account_display_name := public.mercury_public_text(candidate.account_display_name);
  candidate.sanitized_summary := pg_catalog.jsonb_set(
    candidate.sanitized_summary,
    '{company_display_name}',
    pg_catalog.to_jsonb(candidate.account_display_name),
    true
  );
  perform public.mercury_assert_provider_backend_workspace_access(
    candidate.tenant_id, candidate.workspace_id, candidate.auth_user_id
  );
  if candidate.status <> 'awaiting_confirmation'
    or candidate.state_version <> 1
    or candidate.confirmed_at is not null
    or candidate.cancelled_at is not null
    or candidate.created_at > pg_catalog.statement_timestamp()
    or pg_catalog.jsonb_array_length(p_items) <> candidate.document_count
    or not public.mercury_review_codes_are_safe(candidate.warnings)
    or not public.mercury_review_codes_are_safe(candidate.accountant_review_points)
    or candidate.warning_count <> pg_catalog.jsonb_array_length(candidate.warnings)
    or not public.mercury_jsonb_has_exact_keys(candidate.sanitized_summary, array[
      'workspace_id', 'preview_id', 'state_version', 'status', 'provider',
      'company_display_name', 'environment', 'capability_id', 'capability_version',
      'document_count', 'currency', 'subtotal', 'discount_total', 'vat_total',
      'withholding_tax_total', 'grand_total', 'warning_count', 'expires_at'
    ]::pg_catalog.text[])
    or candidate.sanitized_summary->>'preview_id' <> candidate.id::pg_catalog.text
    or candidate.sanitized_summary->>'workspace_id' <> candidate.workspace_id::pg_catalog.text
    or candidate.sanitized_summary->>'status' <> candidate.status
    or candidate.sanitized_summary->>'provider' <> candidate.provider
    or candidate.sanitized_summary->>'company_display_name' <> candidate.account_display_name
    or candidate.sanitized_summary->>'capability_id' <> candidate.capability_id
    or candidate.sanitized_summary->>'capability_version' <> candidate.capability_version
  then
    raise invalid_parameter_value using message = 'preview_conflict';
  end if;
  perform public.mercury_preview_authority_is_current(candidate, pg_catalog.statement_timestamp());
  select * into existing_preview
  from public.mercury_document_previews as stored
  where stored.workspace_id = candidate.workspace_id
    and stored.connection_id = candidate.connection_id
    and stored.provider_call_hash = candidate.provider_call_hash
  for share;
  if found then
    if existing_preview.preview_integrity_hash <> candidate.preview_integrity_hash then
      raise unique_violation using message = 'duplicate_provider_call';
    end if;
    return query select * from public.load_mercury_document_preview(
      candidate.tenant_id, candidate.workspace_id, candidate.auth_user_id, existing_preview.id
    );
    return;
  end if;
  begin
    insert into public.mercury_document_previews values (candidate.*);
  exception when unique_violation then
    raise unique_violation using message = 'duplicate_provider_call';
  end;
  for item in select value from pg_catalog.jsonb_array_elements(p_items) loop
    item_count := item_count + 1;
    if not public.mercury_jsonb_has_exact_keys(item, array[
      'id', 'preview_id', 'tenant_id', 'auth_user_id', 'workspace_id', 'connection_id',
      'item_index', 'client_item_id', 'provider_call_hash', 'preview_integrity_hash',
      'document_type', 'sanitized_summary', 'payload_envelope_id', 'payload_key_version',
      'payload_nonce', 'payload_ciphertext', 'payload_aad_hash',
      'payload_envelope_created_at', 'created_at', 'payload_purge_after'
    ]::pg_catalog.text[]) then
      raise invalid_parameter_value using message = 'preview_conflict';
    end if;
    select * into candidate_item
    from pg_catalog.jsonb_populate_record(null::public.mercury_preview_items, item);
    if not public.mercury_public_identifier_is_safe(candidate_item.client_item_id)
      or candidate_item.preview_id <> candidate.id
      or candidate_item.tenant_id <> candidate.tenant_id
      or candidate_item.auth_user_id <> candidate.auth_user_id
      or candidate_item.workspace_id <> candidate.workspace_id
      or candidate_item.connection_id <> candidate.connection_id
      or candidate_item.created_at <> candidate.created_at
      or candidate_item.payload_purge_after <> candidate.payload_purge_after
      or pg_catalog.jsonb_typeof(candidate_item.sanitized_summary->'counterparty_display') <> 'string'
      or not public.mercury_jsonb_has_exact_keys(candidate_item.sanitized_summary, array[
        'client_item_id', 'provider_call_hash', 'preview_integrity_hash', 'document_type',
        'counterparty_display', 'issue_date', 'due_date', 'financials', 'warnings',
        'accountant_review_points'
      ]::pg_catalog.text[])
      or candidate_item.sanitized_summary->>'client_item_id' <> candidate_item.client_item_id
      or candidate_item.sanitized_summary->>'provider_call_hash' <> candidate_item.provider_call_hash
      or candidate_item.sanitized_summary->>'preview_integrity_hash' <> candidate_item.preview_integrity_hash
      or candidate_item.sanitized_summary->>'document_type' <> candidate_item.document_type
      or pg_catalog.jsonb_typeof(candidate_item.sanitized_summary->'financials') <> 'object'
      or not public.mercury_review_codes_are_safe(candidate_item.sanitized_summary->'warnings')
      or not public.mercury_review_codes_are_safe(
        candidate_item.sanitized_summary->'accountant_review_points'
      )
    then
      raise invalid_parameter_value using message = 'preview_conflict';
    end if;
    item_summary := pg_catalog.jsonb_set(
      candidate_item.sanitized_summary,
      '{counterparty_display}',
      pg_catalog.to_jsonb(public.mercury_public_text(
        candidate_item.sanitized_summary->>'counterparty_display'
      )),
      true
    );
    candidate_item.sanitized_summary := item_summary;
    begin
      insert into public.mercury_preview_items values (candidate_item.*);
    exception when unique_violation then
      raise unique_violation using message = 'duplicate_provider_call';
    end;
  end loop;
  if item_count <> candidate.document_count then
    raise invalid_parameter_value using message = 'preview_conflict';
  end if;
  return query select * from public.load_mercury_document_preview(
    candidate.tenant_id, candidate.workspace_id, candidate.auth_user_id, candidate.id
  );
end;
$$;

create or replace function public.load_mercury_document_preview(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_preview_id pg_catalog.uuid
)
returns table (preview pg_catalog.jsonb, items pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id, p_workspace_id, p_auth_user_id
  );
  return query
  select pg_catalog.to_jsonb(stored), coalesce((
    select pg_catalog.jsonb_agg(pg_catalog.to_jsonb(item) order by item.item_index)
    from public.mercury_preview_items as item
    where item.preview_id = stored.id
      and item.tenant_id = p_tenant_id
      and item.auth_user_id = p_auth_user_id
      and item.workspace_id = p_workspace_id
  ), '[]'::pg_catalog.jsonb)
  from public.mercury_document_previews as stored
  where stored.id = p_preview_id
    and stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id;
end;
$$;

create or replace function public.find_mercury_document_preview_by_provider_call(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_connection_id pg_catalog.uuid,
  p_provider_call_hash pg_catalog.text
)
returns table (preview pg_catalog.jsonb, items pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
declare
  preview_id pg_catalog.uuid;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id, p_workspace_id, p_auth_user_id
  );
  select stored.id into preview_id
  from public.mercury_document_previews as stored
  where stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id
    and stored.connection_id = p_connection_id
    and stored.provider_call_hash = p_provider_call_hash;
  if found then
    return query select * from public.load_mercury_document_preview(
      p_tenant_id, p_workspace_id, p_auth_user_id, preview_id
    );
  end if;
end;
$$;

create or replace function public.transition_mercury_document_preview(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_preview_id pg_catalog.uuid,
  p_expected_state_version pg_catalog.int8,
  p_target_status pg_catalog.text,
  p_occurred_at pg_catalog.timestamptz
)
returns table (preview pg_catalog.jsonb, items pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_preview public.mercury_document_previews%rowtype;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id, p_workspace_id, p_auth_user_id
  );
  select * into current_preview
  from public.mercury_document_previews as stored
  where stored.id = p_preview_id
    and stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id
  for update;
  if not found then
    raise no_data_found using message = 'preview_not_found';
  end if;
  if current_preview.state_version <> p_expected_state_version then
    raise serialization_failure using message = 'preview_state_stale';
  end if;
  if p_target_status = 'confirmed'
    or p_occurred_at < current_preview.created_at
    or not (
      (current_preview.status = 'prepared'
        and p_target_status in ('awaiting_confirmation', 'expired', 'cancelled'))
      or (current_preview.status = 'awaiting_confirmation'
        and p_target_status in ('expired', 'cancelled'))
    )
    or (p_target_status = 'expired' and p_occurred_at < current_preview.expires_at)
  then
    raise invalid_parameter_value using message = 'preview_state_invalid';
  end if;
  update public.mercury_document_previews
  set status = p_target_status,
      state_version = state_version + 1,
      cancelled_at = case when p_target_status = 'cancelled' then p_occurred_at else null end
  where id = current_preview.id;
  return query select * from public.load_mercury_document_preview(
    p_tenant_id, p_workspace_id, p_auth_user_id, p_preview_id
  );
end;
$$;

create or replace function public.load_mercury_operation(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_operation_id pg_catalog.uuid
)
returns table (operation pg_catalog.jsonb, items pg_catalog.jsonb, events pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id, p_workspace_id, p_auth_user_id
  );
  return query
  select
    pg_catalog.to_jsonb(stored),
    coalesce((
      select pg_catalog.jsonb_agg(pg_catalog.to_jsonb(item) order by item.item_index)
      from public.mercury_operation_items as item
      where item.operation_id = stored.id
        and item.tenant_id = p_tenant_id
        and item.auth_user_id = p_auth_user_id
        and item.workspace_id = p_workspace_id
    ), '[]'::pg_catalog.jsonb),
    coalesce((
      select pg_catalog.jsonb_agg(pg_catalog.to_jsonb(event) order by event.occurred_at, event.id)
      from public.mercury_operation_events as event
      where event.operation_id = stored.id
        and event.tenant_id = p_tenant_id
        and event.auth_user_id = p_auth_user_id
        and event.workspace_id = p_workspace_id
    ), '[]'::pg_catalog.jsonb)
  from public.mercury_operations as stored
  where stored.id = p_operation_id
    and stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id;
end;
$$;

create or replace function public.save_mercury_operation(
  p_operation pg_catalog.jsonb,
  p_items pg_catalog.jsonb,
  p_events pg_catalog.jsonb,
  p_expected_preview_state_version pg_catalog.int8
)
returns table (operation pg_catalog.jsonb, items pg_catalog.jsonb, events pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
declare
  candidate public.mercury_operations%rowtype;
  source_preview public.mercury_document_previews%rowtype;
  existing_operation_id pg_catalog.uuid;
  item pg_catalog.jsonb;
  event pg_catalog.jsonb;
begin
  if not public.mercury_jsonb_has_exact_keys(p_operation, array[
    'id', 'preview_id', 'tenant_id', 'auth_user_id', 'workspace_id', 'connection_id',
    'provider', 'environment', 'capability_id', 'capability_version',
    'connection_revision', 'provider_call_hash', 'preview_integrity_hash', 'status',
    'state_version', 'created_at', 'updated_at', 'payload_purge_after'
  ]::pg_catalog.text[])
    or pg_catalog.jsonb_typeof(p_items) <> 'array'
    or pg_catalog.jsonb_typeof(p_events) <> 'array'
  then
    raise invalid_parameter_value using message = 'operation_conflict';
  end if;
  select * into candidate
  from pg_catalog.jsonb_populate_record(null::public.mercury_operations, p_operation);
  perform public.mercury_assert_provider_backend_workspace_access(
    candidate.tenant_id, candidate.workspace_id, candidate.auth_user_id
  );
  select * into source_preview
  from public.mercury_document_previews as stored
  where stored.id = candidate.preview_id
    and stored.tenant_id = candidate.tenant_id
    and stored.auth_user_id = candidate.auth_user_id
    and stored.workspace_id = candidate.workspace_id
    and stored.connection_id = candidate.connection_id
  for update;
  if not found
    or source_preview.provider_call_hash <> candidate.provider_call_hash
    or source_preview.preview_integrity_hash <> candidate.preview_integrity_hash
    or source_preview.provider <> candidate.provider
    or source_preview.environment <> candidate.environment
    or source_preview.capability_id <> candidate.capability_id
    or source_preview.capability_version <> candidate.capability_version
    or source_preview.connection_revision <> candidate.connection_revision
    or candidate.status <> 'awaiting_confirmation'
    or candidate.state_version <> 1
    or candidate.updated_at <> candidate.created_at
    or pg_catalog.jsonb_array_length(p_items) <> source_preview.document_count
    or pg_catalog.jsonb_array_length(p_events) <> 1
  then
    raise invalid_parameter_value using message = 'operation_conflict';
  end if;
  select stored.id into existing_operation_id
  from public.mercury_operations as stored
  where stored.preview_id = source_preview.id
    and stored.tenant_id = source_preview.tenant_id
    and stored.auth_user_id = source_preview.auth_user_id
    and stored.workspace_id = source_preview.workspace_id;
  if found then
    return query select * from public.load_mercury_operation(
      candidate.tenant_id, candidate.workspace_id, candidate.auth_user_id, existing_operation_id
    );
    return;
  end if;
  perform public.mercury_preview_authority_is_current(source_preview, pg_catalog.statement_timestamp());
  if source_preview.state_version <> p_expected_preview_state_version then
    raise serialization_failure using message = 'preview_state_stale';
  end if;
  if source_preview.status not in ('prepared', 'awaiting_confirmation') then
    raise invalid_parameter_value using message = 'preview_state_invalid';
  end if;
  if source_preview.expires_at <= pg_catalog.statement_timestamp()
    or candidate.created_at < source_preview.created_at
    or candidate.created_at >= source_preview.expires_at
    or candidate.payload_purge_after < source_preview.payload_purge_after
    or candidate.payload_purge_after > candidate.created_at + pg_catalog.make_interval(days => 30)
  then
    raise invalid_parameter_value using message = 'preview_expired';
  end if;
  for item in select value from pg_catalog.jsonb_array_elements(p_items) loop
    if not public.mercury_jsonb_has_exact_keys(item, array[
      'id', 'operation_id', 'preview_item_id', 'preview_id', 'tenant_id', 'auth_user_id',
      'workspace_id', 'connection_id', 'item_index', 'client_item_id',
      'provider_call_hash', 'preview_integrity_hash', 'status', 'state_version',
      'provider_result_identifier', 'created_at', 'updated_at'
    ]::pg_catalog.text[])
      or (item->>'operation_id')::pg_catalog.uuid <> candidate.id
      or (item->>'preview_id')::pg_catalog.uuid <> candidate.preview_id
      or (item->>'tenant_id')::pg_catalog.uuid <> candidate.tenant_id
      or (item->>'auth_user_id')::pg_catalog.uuid <> candidate.auth_user_id
      or (item->>'workspace_id')::pg_catalog.uuid <> candidate.workspace_id
      or (item->>'connection_id')::pg_catalog.uuid <> candidate.connection_id
      or item->>'status' <> 'awaiting_confirmation'
      or (item->>'state_version')::pg_catalog.int8 <> 1
      or item->>'provider_result_identifier' is not null
      or (item->>'created_at')::pg_catalog.timestamptz <> candidate.created_at
      or (item->>'updated_at')::pg_catalog.timestamptz <> candidate.created_at
      or not exists (
        select 1
        from public.mercury_preview_items as source_item
        where source_item.id = (item->>'preview_item_id')::pg_catalog.uuid
          and source_item.preview_id = source_preview.id
          and source_item.tenant_id = source_preview.tenant_id
          and source_item.auth_user_id = source_preview.auth_user_id
          and source_item.workspace_id = source_preview.workspace_id
          and source_item.connection_id = source_preview.connection_id
          and source_item.item_index = (item->>'item_index')::pg_catalog.int4
          and source_item.client_item_id = item->>'client_item_id'
          and source_item.provider_call_hash = item->>'provider_call_hash'
          and source_item.preview_integrity_hash = item->>'preview_integrity_hash'
      )
    then
      raise invalid_parameter_value using message = 'operation_conflict';
    end if;
  end loop;
  event := p_events->0;
  if not public.mercury_jsonb_has_exact_keys(event, array[
    'id', 'operation_id', 'operation_item_id', 'tenant_id', 'auth_user_id',
    'workspace_id', 'connection_id', 'from_state', 'to_state', 'state_version',
    'sanitized_reason', 'occurred_at'
  ]::pg_catalog.text[])
    or (event->>'operation_id')::pg_catalog.uuid <> candidate.id
    or event->>'operation_item_id' is not null
    or (event->>'tenant_id')::pg_catalog.uuid <> candidate.tenant_id
    or (event->>'auth_user_id')::pg_catalog.uuid <> candidate.auth_user_id
    or (event->>'workspace_id')::pg_catalog.uuid <> candidate.workspace_id
    or (event->>'connection_id')::pg_catalog.uuid <> candidate.connection_id
    or event->>'from_state' is not null
    or event->>'to_state' <> 'awaiting_confirmation'
    or (event->>'state_version')::pg_catalog.int8 <> 1
    or (event->>'occurred_at')::pg_catalog.timestamptz <> candidate.created_at
    or not public.mercury_public_identifier_is_safe(event->>'sanitized_reason')
  then
    raise invalid_parameter_value using message = 'operation_conflict';
  end if;
  begin
    update public.mercury_document_previews
    set status = 'confirmed', state_version = state_version + 1,
        confirmed_at = candidate.created_at, payload_purge_after = candidate.payload_purge_after
    where id = source_preview.id;
    update public.mercury_preview_items
    set payload_purge_after = candidate.payload_purge_after
    where preview_id = source_preview.id;
    insert into public.mercury_operations values (candidate.*);
    insert into public.mercury_operation_items (
      id, operation_id, preview_item_id, preview_id, tenant_id, auth_user_id,
      workspace_id, connection_id, item_index, client_item_id, provider_call_hash,
      preview_integrity_hash, status, state_version, provider_result_identifier,
      created_at, updated_at
    )
    select
      (element.payload->>'id')::pg_catalog.uuid,
      (element.payload->>'operation_id')::pg_catalog.uuid,
      (element.payload->>'preview_item_id')::pg_catalog.uuid,
      (element.payload->>'preview_id')::pg_catalog.uuid,
      (element.payload->>'tenant_id')::pg_catalog.uuid,
      (element.payload->>'auth_user_id')::pg_catalog.uuid,
      (element.payload->>'workspace_id')::pg_catalog.uuid,
      (element.payload->>'connection_id')::pg_catalog.uuid,
      (element.payload->>'item_index')::pg_catalog.int4,
      element.payload->>'client_item_id',
      element.payload->>'provider_call_hash',
      element.payload->>'preview_integrity_hash',
      element.payload->>'status',
      (element.payload->>'state_version')::pg_catalog.int8,
      element.payload->>'provider_result_identifier',
      (element.payload->>'created_at')::pg_catalog.timestamptz,
      (element.payload->>'updated_at')::pg_catalog.timestamptz
    from pg_catalog.jsonb_array_elements(p_items) as element(payload);
    insert into public.mercury_operation_events (
      id, operation_id, operation_item_id, tenant_id, auth_user_id, workspace_id,
      connection_id, from_state, to_state, state_version, sanitized_reason, occurred_at
    ) values (
      (event->>'id')::pg_catalog.uuid, (event->>'operation_id')::pg_catalog.uuid, null,
      (event->>'tenant_id')::pg_catalog.uuid, (event->>'auth_user_id')::pg_catalog.uuid,
      (event->>'workspace_id')::pg_catalog.uuid, (event->>'connection_id')::pg_catalog.uuid,
      null, event->>'to_state', (event->>'state_version')::pg_catalog.int8,
      event->>'sanitized_reason', (event->>'occurred_at')::pg_catalog.timestamptz
    );
  exception when unique_violation then
    raise unique_violation using message = 'operation_conflict';
  end;
  return query select * from public.load_mercury_operation(
    candidate.tenant_id, candidate.workspace_id, candidate.auth_user_id, candidate.id
  );
end;
$$;

create or replace function public.transition_mercury_operation(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_operation_id pg_catalog.uuid,
  p_expected_state_version pg_catalog.int8,
  p_target_state pg_catalog.text,
  p_event_id pg_catalog.uuid,
  p_occurred_at pg_catalog.timestamptz,
  p_sanitized_reason pg_catalog.text
)
returns table (operation pg_catalog.jsonb, items pg_catalog.jsonb, events pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_operation public.mercury_operations%rowtype;
  source_preview public.mercury_document_previews%rowtype;
  child_states pg_catalog.text[];
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id, p_workspace_id, p_auth_user_id
  );
  select * into current_operation
  from public.mercury_operations as stored
  where stored.id = p_operation_id
    and stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id
  for update;
  if not found then
    raise no_data_found using message = 'operation_not_found';
  end if;
  if current_operation.state_version <> p_expected_state_version then
    raise serialization_failure using message = 'operation_state_stale';
  end if;
  if p_target_state = 'dispatching' then
    select * into source_preview
    from public.mercury_document_previews as stored
    where stored.id = current_operation.preview_id
      and stored.tenant_id = current_operation.tenant_id
      and stored.auth_user_id = current_operation.auth_user_id
      and stored.workspace_id = current_operation.workspace_id
      and stored.connection_id = current_operation.connection_id
    for share;
    if not found then
      raise no_data_found using message = 'operation_not_found';
    end if;
    perform public.mercury_preview_authority_is_current(
      source_preview, pg_catalog.statement_timestamp()
    );
  end if;
  select pg_catalog.array_agg(item.status order by item.item_index) into child_states
  from public.mercury_operation_items as item
  where item.operation_id = current_operation.id;
  if p_occurred_at < current_operation.updated_at
    or not public.mercury_public_identifier_is_safe(p_sanitized_reason)
    or not public.mercury_parent_operation_transition_is_allowed(
      current_operation.status, p_target_state, child_states
    )
  then
    raise invalid_parameter_value using message = 'operation_transition_invalid';
  end if;
  update public.mercury_operations
  set status = p_target_state, state_version = state_version + 1, updated_at = p_occurred_at
  where id = p_operation_id;
  insert into public.mercury_operation_events (
    id, operation_id, operation_item_id, tenant_id, auth_user_id, workspace_id,
    connection_id, from_state, to_state, state_version, sanitized_reason, occurred_at
  ) values (
    p_event_id, p_operation_id, null, p_tenant_id, p_auth_user_id, p_workspace_id,
    current_operation.connection_id, current_operation.status, p_target_state,
    current_operation.state_version + 1, p_sanitized_reason, p_occurred_at
  );
  return query select * from public.load_mercury_operation(
    p_tenant_id, p_workspace_id, p_auth_user_id, p_operation_id
  );
end;
$$;

create or replace function public.transition_mercury_operation_item(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_operation_id pg_catalog.uuid,
  p_operation_item_id pg_catalog.uuid,
  p_expected_state_version pg_catalog.int8,
  p_target_state pg_catalog.text,
  p_event_id pg_catalog.uuid,
  p_occurred_at pg_catalog.timestamptz,
  p_sanitized_reason pg_catalog.text,
  p_provider_result_identifier pg_catalog.text default null
)
returns table (operation pg_catalog.jsonb, items pg_catalog.jsonb, events pg_catalog.jsonb)
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_operation public.mercury_operations%rowtype;
  current_item public.mercury_operation_items%rowtype;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id, p_workspace_id, p_auth_user_id
  );
  select * into current_operation
  from public.mercury_operations as stored
  where stored.id = p_operation_id
    and stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id
  for update;
  if not found then
    raise no_data_found using message = 'operation_not_found';
  end if;
  select * into current_item
  from public.mercury_operation_items as stored
  where stored.id = p_operation_item_id
    and stored.operation_id = p_operation_id
    and stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id
  for update;
  if not found then
    raise no_data_found using message = 'operation_not_found';
  end if;
  if current_item.state_version <> p_expected_state_version then
    raise serialization_failure using message = 'operation_state_stale';
  end if;
  if p_occurred_at < current_item.updated_at
    or p_occurred_at < current_operation.updated_at
    or not public.mercury_public_identifier_is_safe(p_sanitized_reason)
    or (p_target_state = 'succeeded' and p_provider_result_identifier is null)
    or (p_target_state <> 'succeeded' and p_provider_result_identifier is not null)
    or not public.mercury_item_operation_transition_is_allowed(
      current_item.status, p_target_state, current_operation.status
    )
  then
    raise invalid_parameter_value using message = 'operation_transition_invalid';
  end if;
  update public.mercury_operation_items
  set status = p_target_state,
      state_version = state_version + 1,
      provider_result_identifier = p_provider_result_identifier,
      updated_at = p_occurred_at
  where id = p_operation_item_id;
  update public.mercury_operations
  set updated_at = p_occurred_at
  where id = p_operation_id;
  insert into public.mercury_operation_events (
    id, operation_id, operation_item_id, tenant_id, auth_user_id, workspace_id,
    connection_id, from_state, to_state, state_version, sanitized_reason, occurred_at
  ) values (
    p_event_id, p_operation_id, p_operation_item_id, p_tenant_id, p_auth_user_id,
    p_workspace_id, current_operation.connection_id, current_item.status, p_target_state,
    current_item.state_version + 1, p_sanitized_reason, p_occurred_at
  );
  return query select * from public.load_mercury_operation(
    p_tenant_id, p_workspace_id, p_auth_user_id, p_operation_id
  );
end;
$$;

revoke all on function public.mercury_jsonb_has_exact_keys(pg_catalog.jsonb, pg_catalog.text[])
  from public, anon, authenticated;
revoke all on function public.mercury_public_text(pg_catalog.text)
  from public, anon, authenticated;
revoke all on function public.mercury_public_identifier_is_safe(pg_catalog.text)
  from public, anon, authenticated;
revoke all on function public.mercury_review_codes_are_safe(pg_catalog.jsonb)
  from public, anon, authenticated;
revoke all on function public.mercury_create_schema_is_closed(pg_catalog.jsonb, pg_catalog.bool)
  from public, anon, authenticated;
revoke all on function public.mercury_parent_operation_transition_is_allowed(
  pg_catalog.text, pg_catalog.text, pg_catalog.text[]
) from public, anon, authenticated;
revoke all on function public.mercury_item_operation_transition_is_allowed(
  pg_catalog.text, pg_catalog.text, pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.mercury_preview_authority_is_current(
  public.mercury_document_previews, pg_catalog.timestamptz
) from public, anon, authenticated;
revoke all on function public.mercury_reject_open_create_qualification()
  from public, anon, authenticated;
revoke all on function public.save_mercury_document_preview(pg_catalog.jsonb, pg_catalog.jsonb)
  from public, anon, authenticated;
revoke all on function public.load_mercury_document_preview(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.find_mercury_document_preview_by_provider_call(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.transition_mercury_document_preview(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid,
  pg_catalog.int8, pg_catalog.text, pg_catalog.timestamptz
) from public, anon, authenticated;
revoke all on function public.save_mercury_operation(
  pg_catalog.jsonb, pg_catalog.jsonb, pg_catalog.jsonb, pg_catalog.int8
) from public, anon, authenticated;
revoke all on function public.load_mercury_operation(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.transition_mercury_operation(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid,
  pg_catalog.int8, pg_catalog.text, pg_catalog.uuid, pg_catalog.timestamptz, pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.transition_mercury_operation_item(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid,
  pg_catalog.int8, pg_catalog.text, pg_catalog.uuid, pg_catalog.timestamptz,
  pg_catalog.text, pg_catalog.text
) from public, anon, authenticated;

grant execute on function public.mercury_jsonb_has_exact_keys(pg_catalog.jsonb, pg_catalog.text[])
  to service_role;
grant execute on function public.mercury_public_text(pg_catalog.text) to service_role;
grant execute on function public.mercury_public_identifier_is_safe(pg_catalog.text) to service_role;
grant execute on function public.mercury_review_codes_are_safe(pg_catalog.jsonb) to service_role;
grant execute on function public.mercury_create_schema_is_closed(pg_catalog.jsonb, pg_catalog.bool)
  to service_role;
grant execute on function public.mercury_parent_operation_transition_is_allowed(
  pg_catalog.text, pg_catalog.text, pg_catalog.text[]
) to service_role;
grant execute on function public.mercury_item_operation_transition_is_allowed(
  pg_catalog.text, pg_catalog.text, pg_catalog.text
) to service_role;
grant execute on function public.mercury_preview_authority_is_current(
  public.mercury_document_previews, pg_catalog.timestamptz
) to service_role;
grant execute on function public.save_mercury_document_preview(pg_catalog.jsonb, pg_catalog.jsonb)
  to service_role;
grant execute on function public.load_mercury_document_preview(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid
) to service_role;
grant execute on function public.find_mercury_document_preview_by_provider_call(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.text
) to service_role;
grant execute on function public.transition_mercury_document_preview(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid,
  pg_catalog.int8, pg_catalog.text, pg_catalog.timestamptz
) to service_role;
grant execute on function public.save_mercury_operation(
  pg_catalog.jsonb, pg_catalog.jsonb, pg_catalog.jsonb, pg_catalog.int8
) to service_role;
grant execute on function public.load_mercury_operation(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid
) to service_role;
grant execute on function public.transition_mercury_operation(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid,
  pg_catalog.int8, pg_catalog.text, pg_catalog.uuid, pg_catalog.timestamptz, pg_catalog.text
) to service_role;
grant execute on function public.transition_mercury_operation_item(
  pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid, pg_catalog.uuid,
  pg_catalog.int8, pg_catalog.text, pg_catalog.uuid, pg_catalog.timestamptz,
  pg_catalog.text, pg_catalog.text
) to service_role;

commit;
