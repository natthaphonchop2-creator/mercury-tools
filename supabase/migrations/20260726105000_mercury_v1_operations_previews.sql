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
    check (
      pg_catalog.length(account_display_name) between 1 and 200
      and account_display_name !~ '[[:cntrl:]]'
    ),
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
  response_shape_hash pg_catalog.text not null
    check (response_shape_hash ~ '^[0-9a-f]{64}$'),
  evidence_revision_sha256 pg_catalog.text not null
    check (evidence_revision_sha256 ~ '^[0-9a-f]{64}$'),
  connection_revision pg_catalog.int8 not null check (connection_revision >= 1),
  connection_readiness pg_catalog.text not null check (connection_readiness = 'ready'),
  payload_hash pg_catalog.text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
  status pg_catalog.text not null
    check (status in (
      'prepared', 'awaiting_confirmation', 'confirmed', 'expired', 'cancelled'
    )),
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
  warnings pg_catalog.jsonb not null default '[]'::pg_catalog.jsonb
    check (pg_catalog.jsonb_typeof(warnings) = 'array'),
  accountant_review_points pg_catalog.jsonb not null default '[]'::pg_catalog.jsonb
    check (pg_catalog.jsonb_typeof(accountant_review_points) = 'array'),
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
      and payload_purge_after
        <= confirmed_at + pg_catalog.make_interval(days => 30))
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
  unique (workspace_id, connection_id, payload_hash),
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
    check (client_item_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  payload_hash pg_catalog.text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
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
    payload_purge_after >= created_at
      + pg_catalog.make_interval(secs => 1800)
      + pg_catalog.make_interval(hours => 24)
    and payload_purge_after <= created_at
      + pg_catalog.make_interval(secs => 1800)
      + pg_catalog.make_interval(days => 30)
  ),
  foreign key (preview_id, tenant_id, auth_user_id, workspace_id, connection_id)
    references public.mercury_document_previews (
      id, tenant_id, auth_user_id, workspace_id, connection_id
    ) on delete cascade,
  unique (preview_id, item_index),
  unique (preview_id, client_item_id),
  unique (preview_id, payload_hash),
  unique (workspace_id, connection_id, payload_hash),
  unique (
    id, preview_id, tenant_id, auth_user_id, workspace_id, connection_id,
    item_index, client_item_id, payload_hash
  )
);

create table if not exists public.mercury_operations (
  id pg_catalog.uuid primary key,
  preview_id pg_catalog.uuid not null,
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  connection_id pg_catalog.uuid not null
    references public.mercury_provider_connections(id) on delete restrict,
  provider pg_catalog.text not null check (provider in ('flowaccount', 'peak')),
  environment pg_catalog.text not null
    check (environment ~ '^[a-z][a-z0-9_-]{0,63}$'),
  capability_id pg_catalog.text not null
    check (capability_id ~ '^documents\.[a-z][a-z0-9_]*\.create$'),
  capability_version pg_catalog.text not null
    check (capability_version ~ '^[0-9a-f]{64}$'),
  connection_revision pg_catalog.int8 not null check (connection_revision >= 1),
  payload_hash pg_catalog.text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
  status pg_catalog.text not null check (status in (
    'awaiting_confirmation', 'dispatching', 'succeeded', 'failed_pre_dispatch',
    'provider_rejected', 'outcome_unknown', 'needs_manual_review',
    'not_dispatched', 'cancelled'
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
    check (client_item_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  payload_hash pg_catalog.text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
  status pg_catalog.text not null check (status in (
    'awaiting_confirmation', 'dispatching', 'succeeded', 'failed_pre_dispatch',
    'provider_rejected', 'outcome_unknown', 'needs_manual_review',
    'not_dispatched', 'cancelled'
  )),
  state_version pg_catalog.int8 not null default 1 check (state_version >= 1),
  provider_result_identifier pg_catalog.text
    check (
      provider_result_identifier is null
      or (
        pg_catalog.length(provider_result_identifier) between 1 and 200
        and provider_result_identifier !~ '[[:cntrl:]]'
      )
    ),
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
    connection_id, item_index, client_item_id, payload_hash
  ) references public.mercury_preview_items (
    id, preview_id, tenant_id, auth_user_id, workspace_id,
    connection_id, item_index, client_item_id, payload_hash
  ) on delete restrict,
  unique (operation_id, item_index),
  unique (operation_id, preview_item_id),
  unique (operation_id, client_item_id),
  unique (operation_id, payload_hash),
  unique (
    id, operation_id, tenant_id, auth_user_id, workspace_id, connection_id
  )
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
    check (sanitized_reason ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'),
  occurred_at pg_catalog.timestamptz not null,
  check (from_state is null or from_state in (
    'awaiting_confirmation', 'dispatching', 'succeeded', 'failed_pre_dispatch',
    'provider_rejected', 'outcome_unknown', 'needs_manual_review',
    'not_dispatched', 'cancelled'
  )),
  check (to_state in (
    'awaiting_confirmation', 'dispatching', 'succeeded', 'failed_pre_dispatch',
    'provider_rejected', 'outcome_unknown', 'needs_manual_review',
    'not_dispatched', 'cancelled'
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
  on public.mercury_operation_events (
    operation_id, operation_item_id, state_version
  ) where operation_item_id is not null;
create index if not exists mercury_preview_lookup_idx
  on public.mercury_document_previews (
    tenant_id, auth_user_id, workspace_id, connection_id, id
  );
create index if not exists mercury_preview_payload_purge_idx
  on public.mercury_document_previews (payload_purge_after, id)
  where status in ('prepared', 'awaiting_confirmation', 'expired', 'cancelled');
create index if not exists mercury_preview_item_purge_idx
  on public.mercury_preview_items (payload_purge_after, preview_id, id);
create index if not exists mercury_operation_lookup_idx
  on public.mercury_operations (
    tenant_id, auth_user_id, workspace_id, connection_id, id
  );
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
      select 1
      from pg_catalog.pg_policy
      where polname = policy_name
        and polrelid = ('public.' || table_name)::pg_catalog.regclass
    ) then
      execute pg_catalog.format(
        'create policy %1$I on public.%2$I for select to authenticated using ('
        || 'exists (select 1 from public.mercury_workspace_members as member '
        || 'join public.mercury_workspaces as workspace '
        || 'on workspace.id = member.workspace_id '
        || 'and workspace.tenant_id = member.tenant_id '
        || 'where member.tenant_id = %2$I.tenant_id '
        || 'and member.workspace_id = %2$I.workspace_id '
        || 'and member.auth_user_id = (select auth.uid()) '
        || 'and member.status = ''active'' and workspace.status = ''active''))',
        policy_name,
        table_name
      );
    end if;
  end loop;
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
  connection public.mercury_provider_connections%rowtype;
  qualification public.mercury_provider_capability_qualifications%rowtype;
  item pg_catalog.jsonb;
  line pg_catalog.jsonb;
  item_count pg_catalog.int4;
begin
  if not public.mercury_jsonb_has_exact_keys(p_preview, array[
    'id', 'tenant_id', 'auth_user_id', 'workspace_id', 'connection_id',
    'provider', 'provider_account_sha256', 'account_display_name', 'environment',
    'qualification_id', 'provider_tool_name', 'capability_id',
    'capability_version', 'schema_hash', 'response_shape_hash',
    'evidence_revision_sha256', 'connection_revision', 'connection_readiness',
    'payload_hash', 'status',
    'state_version', 'document_count', 'currency', 'subtotal', 'discount_total',
    'vat_total', 'withholding_tax_total', 'grand_total', 'warning_count',
    'sanitized_summary', 'warnings', 'accountant_review_points',
    'supersedes_preview_id', 'created_at', 'expires_at', 'payload_purge_after',
    'confirmed_at', 'cancelled_at'
  ]::pg_catalog.text[])
    or pg_catalog.jsonb_typeof(p_items) <> 'array'
  then
    raise invalid_parameter_value using message = 'preview_conflict';
  end if;

  select * into candidate
  from pg_catalog.jsonb_populate_record(
    null::public.mercury_document_previews,
    p_preview
  );
  perform public.mercury_assert_provider_backend_workspace_access(
    candidate.tenant_id,
    candidate.workspace_id,
    candidate.auth_user_id
  );
  if candidate.status <> 'awaiting_confirmation'
    or candidate.state_version <> 1
    or candidate.confirmed_at is not null
    or candidate.cancelled_at is not null
    or candidate.created_at > pg_catalog.statement_timestamp()
    or not public.mercury_jsonb_has_exact_keys(
      candidate.sanitized_summary,
      array[
        'workspace_id', 'preview_id', 'state_version', 'status', 'provider',
        'company_display_name', 'environment', 'capability_id',
        'capability_version', 'document_count', 'currency', 'subtotal',
        'discount_total', 'vat_total', 'withholding_tax_total', 'grand_total',
        'warning_count', 'expires_at'
      ]::pg_catalog.text[]
    )
    or candidate.sanitized_summary->>'preview_id' <> candidate.id::pg_catalog.text
    or candidate.sanitized_summary->>'workspace_id'
      <> candidate.workspace_id::pg_catalog.text
    or candidate.sanitized_summary->>'status' <> candidate.status
    or candidate.sanitized_summary->>'capability_id' <> candidate.capability_id
    or candidate.sanitized_summary->>'capability_version'
      <> candidate.capability_version
  then
    raise invalid_parameter_value using message = 'preview_conflict';
  end if;

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
    or connection.revision <> candidate.connection_revision
    or connection.readiness <> 'ready'
    or connection.readiness <> candidate.connection_readiness
    or pg_catalog.encode(
      public.digest(
        pg_catalog.convert_to(connection.provider_account_id, 'UTF8'),
        'sha256'
      ),
      'hex'
    ) <> candidate.provider_account_sha256
  then
    raise insufficient_privilege using message = 'preview_binding_changed';
  end if;

  select * into qualification
  from public.mercury_provider_capability_qualifications as stored
  where stored.id = candidate.qualification_id
    and stored.provider = candidate.provider
    and stored.environment = candidate.environment
    and stored.provider_tool_name = candidate.provider_tool_name
    and stored.normalized_capability = candidate.capability_id
    and stored.capability_version_sha256 = candidate.capability_version
    and stored.schema_hash = candidate.schema_hash
    and stored.response_shape_hash = candidate.response_shape_hash
    and stored.evidence_revision_sha256 = candidate.evidence_revision_sha256
    and stored.company_sha256 = candidate.provider_account_sha256
    and stored.qualification_state = 'enabled'
    and stored.evidence_expires_at > pg_catalog.statement_timestamp()
    and stored.required_permissions <@ connection.granted_permissions
  for share;
  if not found then
    raise insufficient_privilege using message = 'capability_unavailable';
  end if;

  if candidate.supersedes_preview_id is not null and not exists (
    select 1
    from public.mercury_document_previews as superseded
    where superseded.id = candidate.supersedes_preview_id
      and superseded.id <> candidate.id
      and superseded.tenant_id = candidate.tenant_id
      and superseded.auth_user_id = candidate.auth_user_id
      and superseded.workspace_id = candidate.workspace_id
      and superseded.connection_id = candidate.connection_id
      and superseded.provider = candidate.provider
      and superseded.environment = candidate.environment
      and superseded.capability_id = candidate.capability_id
      and superseded.capability_version = candidate.capability_version
  ) then
    raise invalid_parameter_value using message = 'preview_binding_changed';
  end if;

  item_count := pg_catalog.jsonb_array_length(p_items);
  if item_count <> candidate.document_count or item_count not between 1 and 25 then
    raise invalid_parameter_value using message = 'preview_conflict';
  end if;

  begin
    insert into public.mercury_document_previews values (candidate.*);
    for item in select value from pg_catalog.jsonb_array_elements(p_items) loop
      if not public.mercury_jsonb_has_exact_keys(item, array[
        'id', 'preview_id', 'tenant_id', 'auth_user_id', 'workspace_id',
        'connection_id', 'item_index', 'client_item_id', 'payload_hash',
        'document_type', 'sanitized_summary', 'payload_envelope_id',
        'payload_key_version', 'payload_nonce', 'payload_ciphertext',
        'payload_aad_hash', 'payload_envelope_created_at', 'created_at',
        'payload_purge_after'
      ]::pg_catalog.text[])
        or (item->>'preview_id')::pg_catalog.uuid <> candidate.id
        or (item->>'tenant_id')::pg_catalog.uuid <> candidate.tenant_id
        or (item->>'auth_user_id')::pg_catalog.uuid <> candidate.auth_user_id
        or (item->>'workspace_id')::pg_catalog.uuid <> candidate.workspace_id
        or (item->>'connection_id')::pg_catalog.uuid <> candidate.connection_id
        or (item->>'created_at')::pg_catalog.timestamptz <> candidate.created_at
        or (item->>'payload_purge_after')::pg_catalog.timestamptz
          <> candidate.payload_purge_after
        or not public.mercury_jsonb_has_exact_keys(
          item->'sanitized_summary',
          array[
            'client_item_id', 'document_type', 'counterparty_display',
            'issue_date', 'due_date', 'financials', 'warnings',
            'accountant_review_points'
          ]::pg_catalog.text[]
        )
        or item->'sanitized_summary'->>'client_item_id' <> item->>'client_item_id'
        or item->'sanitized_summary'->>'document_type' <> item->>'document_type'
        or not public.mercury_jsonb_has_exact_keys(
          item->'sanitized_summary'->'financials',
          array[
            'currency', 'lines', 'subtotal', 'discount_total', 'vat_total',
            'withholding_tax_total', 'grand_total'
          ]::pg_catalog.text[]
        )
        or pg_catalog.jsonb_typeof(
          item->'sanitized_summary'->'financials'->'lines'
        ) <> 'array'
        or pg_catalog.jsonb_array_length(
          item->'sanitized_summary'->'financials'->'lines'
        ) not between 1 and 2500
      then
        raise invalid_parameter_value using message = 'preview_conflict';
      end if;
      for line in select value from pg_catalog.jsonb_array_elements(
        item->'sanitized_summary'->'financials'->'lines'
      ) loop
        if not public.mercury_jsonb_has_exact_keys(line, array[
          'currency', 'quantity', 'unit_price', 'discount_amount', 'vat_rate',
          'vat_amount', 'withholding_rate', 'withholding_amount', 'line_total'
        ]::pg_catalog.text[]) then
          raise invalid_parameter_value using message = 'preview_conflict';
        end if;
      end loop;
      insert into public.mercury_preview_items (
        id, preview_id, tenant_id, auth_user_id, workspace_id, connection_id,
        item_index, client_item_id, payload_hash, document_type,
        sanitized_summary, payload_envelope_id, payload_key_version,
        payload_nonce, payload_ciphertext, payload_aad_hash,
        payload_envelope_created_at, created_at, payload_purge_after
      ) values (
        (item->>'id')::pg_catalog.uuid,
        (item->>'preview_id')::pg_catalog.uuid,
        (item->>'tenant_id')::pg_catalog.uuid,
        (item->>'auth_user_id')::pg_catalog.uuid,
        (item->>'workspace_id')::pg_catalog.uuid,
        (item->>'connection_id')::pg_catalog.uuid,
        (item->>'item_index')::pg_catalog.int4,
        item->>'client_item_id',
        item->>'payload_hash',
        item->>'document_type',
        item->'sanitized_summary',
        (item->>'payload_envelope_id')::pg_catalog.uuid,
        item->>'payload_key_version',
        pg_catalog.decode(item->>'payload_nonce', 'hex'),
        pg_catalog.decode(item->>'payload_ciphertext', 'hex'),
        pg_catalog.decode(item->>'payload_aad_hash', 'hex'),
        (item->>'payload_envelope_created_at')::pg_catalog.timestamptz,
        (item->>'created_at')::pg_catalog.timestamptz,
        (item->>'payload_purge_after')::pg_catalog.timestamptz
      );
    end loop;
  exception
    when unique_violation then
      raise unique_violation using message = 'preview_conflict';
  end;

  return query
  select
    pg_catalog.to_jsonb(stored),
    coalesce(
      (
        select pg_catalog.jsonb_agg(
          pg_catalog.to_jsonb(stored_item) order by stored_item.item_index
        )
        from public.mercury_preview_items as stored_item
        where stored_item.preview_id = stored.id
      ),
      '[]'::pg_catalog.jsonb
    )
  from public.mercury_document_previews as stored
  where stored.id = candidate.id;
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
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  return query
  select
    pg_catalog.to_jsonb(stored),
    coalesce(
      (
        select pg_catalog.jsonb_agg(
          pg_catalog.to_jsonb(stored_item) order by stored_item.item_index
        )
        from public.mercury_preview_items as stored_item
        where stored_item.preview_id = stored.id
          and stored_item.tenant_id = p_tenant_id
          and stored_item.auth_user_id = p_auth_user_id
          and stored_item.workspace_id = p_workspace_id
      ),
      '[]'::pg_catalog.jsonb
    )
  from public.mercury_document_previews as stored
  where stored.id = p_preview_id
    and stored.tenant_id = p_tenant_id
    and stored.auth_user_id = p_auth_user_id
    and stored.workspace_id = p_workspace_id;
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
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
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
  if current_preview.status not in ('prepared', 'awaiting_confirmation')
    or p_target_status not in ('expired', 'cancelled')
    or p_occurred_at < current_preview.created_at
    or (p_target_status = 'expired' and p_occurred_at < current_preview.expires_at)
  then
    raise invalid_parameter_value using message = 'preview_state_invalid';
  end if;
  update public.mercury_document_previews
  set status = p_target_status,
      state_version = state_version + 1,
      cancelled_at = case when p_target_status = 'cancelled' then p_occurred_at end
  where id = p_preview_id;
  return query
  select * from public.load_mercury_document_preview(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    p_preview_id
  );
end;
$$;

create or replace function public.load_mercury_operation(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_operation_id pg_catalog.uuid
)
returns table (
  operation pg_catalog.jsonb,
  items pg_catalog.jsonb,
  events pg_catalog.jsonb
)
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  return query
  select
    pg_catalog.to_jsonb(stored),
    coalesce(
      (
        select pg_catalog.jsonb_agg(
          pg_catalog.to_jsonb(item) order by item.item_index
        )
        from public.mercury_operation_items as item
        where item.operation_id = stored.id
          and item.tenant_id = p_tenant_id
          and item.auth_user_id = p_auth_user_id
          and item.workspace_id = p_workspace_id
      ),
      '[]'::pg_catalog.jsonb
    ),
    coalesce(
      (
        select pg_catalog.jsonb_agg(
          pg_catalog.to_jsonb(event) order by event.occurred_at, event.id
        )
        from public.mercury_operation_events as event
        where event.operation_id = stored.id
          and event.tenant_id = p_tenant_id
          and event.auth_user_id = p_auth_user_id
          and event.workspace_id = p_workspace_id
      ),
      '[]'::pg_catalog.jsonb
    )
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
returns table (
  operation pg_catalog.jsonb,
  items pg_catalog.jsonb,
  events pg_catalog.jsonb
)
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
    'id', 'preview_id', 'tenant_id', 'auth_user_id', 'workspace_id',
    'connection_id', 'provider', 'environment', 'capability_id',
    'capability_version', 'connection_revision', 'payload_hash', 'status',
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
    candidate.tenant_id,
    candidate.workspace_id,
    candidate.auth_user_id
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
    or source_preview.payload_hash <> candidate.payload_hash
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

  for item in select value from pg_catalog.jsonb_array_elements(p_items) loop
    if not public.mercury_jsonb_has_exact_keys(item, array[
      'id', 'operation_id', 'preview_item_id', 'preview_id', 'tenant_id', 'auth_user_id',
      'workspace_id', 'connection_id', 'item_index', 'client_item_id',
      'payload_hash', 'status', 'state_version', 'provider_result_identifier',
      'created_at', 'updated_at'
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
          and source_item.payload_hash = item->>'payload_hash'
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
    return query
    select * from public.load_mercury_operation(
      candidate.tenant_id,
      candidate.workspace_id,
      candidate.auth_user_id,
      existing_operation_id
    );
    return;
  end if;

  if source_preview.state_version <> p_expected_preview_state_version then
    raise serialization_failure using message = 'preview_state_stale';
  end if;
  if source_preview.status not in ('prepared', 'awaiting_confirmation') then
    raise invalid_parameter_value using message = 'preview_state_invalid';
  end if;
  if source_preview.expires_at <= pg_catalog.statement_timestamp()
    or candidate.created_at < source_preview.created_at
    or candidate.created_at >= source_preview.expires_at
  then
    raise invalid_parameter_value using message = 'preview_expired';
  end if;
  if candidate.payload_purge_after < source_preview.payload_purge_after
    or candidate.payload_purge_after
      > candidate.created_at + pg_catalog.make_interval(days => 30)
  then
    raise invalid_parameter_value using message = 'operation_conflict';
  end if;

  begin
    update public.mercury_document_previews
    set status = 'confirmed',
        state_version = state_version + 1,
        confirmed_at = candidate.created_at,
        payload_purge_after = candidate.payload_purge_after
    where id = source_preview.id;
    update public.mercury_preview_items
    set payload_purge_after = candidate.payload_purge_after
    where preview_id = source_preview.id;

    insert into public.mercury_operations values (candidate.*);
    for item in select value from pg_catalog.jsonb_array_elements(p_items) loop
      insert into public.mercury_operation_items (
        id, operation_id, preview_item_id, preview_id, tenant_id, auth_user_id,
        workspace_id, connection_id, item_index, client_item_id, payload_hash, status,
        state_version, provider_result_identifier, created_at, updated_at
      ) values (
        (item->>'id')::pg_catalog.uuid,
        (item->>'operation_id')::pg_catalog.uuid,
        (item->>'preview_item_id')::pg_catalog.uuid,
        (item->>'preview_id')::pg_catalog.uuid,
        (item->>'tenant_id')::pg_catalog.uuid,
        (item->>'auth_user_id')::pg_catalog.uuid,
        (item->>'workspace_id')::pg_catalog.uuid,
        (item->>'connection_id')::pg_catalog.uuid,
        (item->>'item_index')::pg_catalog.int4,
        item->>'client_item_id',
        item->>'payload_hash',
        item->>'status',
        (item->>'state_version')::pg_catalog.int8,
        item->>'provider_result_identifier',
        (item->>'created_at')::pg_catalog.timestamptz,
        (item->>'updated_at')::pg_catalog.timestamptz
      );
    end loop;
    insert into public.mercury_operation_events (
      id, operation_id, operation_item_id, tenant_id, auth_user_id, workspace_id,
      connection_id, from_state, to_state, state_version, sanitized_reason,
      occurred_at
    ) values (
      (event->>'id')::pg_catalog.uuid,
      (event->>'operation_id')::pg_catalog.uuid,
      null,
      (event->>'tenant_id')::pg_catalog.uuid,
      (event->>'auth_user_id')::pg_catalog.uuid,
      (event->>'workspace_id')::pg_catalog.uuid,
      (event->>'connection_id')::pg_catalog.uuid,
      null,
      event->>'to_state',
      (event->>'state_version')::pg_catalog.int8,
      event->>'sanitized_reason',
      (event->>'occurred_at')::pg_catalog.timestamptz
    );
  exception
    when unique_violation then
      raise unique_violation using message = 'operation_conflict';
  end;

  return query
  select * from public.load_mercury_operation(
    candidate.tenant_id,
    candidate.workspace_id,
    candidate.auth_user_id,
    candidate.id
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
returns table (
  operation pg_catalog.jsonb,
  items pg_catalog.jsonb,
  events pg_catalog.jsonb
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_operation public.mercury_operations%rowtype;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
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
  if p_occurred_at < current_operation.updated_at
    or not (
      (current_operation.status = 'awaiting_confirmation'
        and p_target_state in (
          'dispatching', 'failed_pre_dispatch', 'not_dispatched', 'cancelled'
        ))
      or (current_operation.status = 'failed_pre_dispatch'
        and p_target_state in ('dispatching', 'cancelled'))
      or (current_operation.status = 'dispatching'
        and p_target_state in (
          'succeeded', 'provider_rejected', 'outcome_unknown'
        ))
      or (current_operation.status = 'outcome_unknown'
        and p_target_state in ('succeeded', 'needs_manual_review'))
    )
  then
    raise invalid_parameter_value using message = 'operation_transition_invalid';
  end if;
  update public.mercury_operations
  set status = p_target_state,
      state_version = state_version + 1,
      updated_at = p_occurred_at
  where id = p_operation_id;
  insert into public.mercury_operation_events (
    id, operation_id, operation_item_id, tenant_id, auth_user_id, workspace_id,
    connection_id, from_state, to_state, state_version, sanitized_reason,
    occurred_at
  ) values (
    p_event_id, p_operation_id, null, p_tenant_id, p_auth_user_id, p_workspace_id,
    current_operation.connection_id, current_operation.status, p_target_state,
    current_operation.state_version + 1, p_sanitized_reason, p_occurred_at
  );
  return query
  select * from public.load_mercury_operation(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    p_operation_id
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
returns table (
  operation pg_catalog.jsonb,
  items pg_catalog.jsonb,
  events pg_catalog.jsonb
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_operation public.mercury_operations%rowtype;
  current_item public.mercury_operation_items%rowtype;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
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
    or (p_target_state = 'succeeded' and p_provider_result_identifier is null)
    or (p_target_state <> 'succeeded' and p_provider_result_identifier is not null)
    or not (
      (current_item.status = 'awaiting_confirmation'
        and p_target_state in (
          'dispatching', 'failed_pre_dispatch', 'not_dispatched', 'cancelled'
        ))
      or (current_item.status = 'failed_pre_dispatch'
        and p_target_state in ('dispatching', 'cancelled'))
      or (current_item.status = 'dispatching'
        and p_target_state in (
          'succeeded', 'provider_rejected', 'outcome_unknown'
        ))
      or (current_item.status = 'outcome_unknown'
        and p_target_state in ('succeeded', 'needs_manual_review'))
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
    connection_id, from_state, to_state, state_version, sanitized_reason,
    occurred_at
  ) values (
    p_event_id, p_operation_id, p_operation_item_id, p_tenant_id, p_auth_user_id,
    p_workspace_id, current_operation.connection_id, current_item.status,
    p_target_state, current_item.state_version + 1, p_sanitized_reason,
    p_occurred_at
  );
  return query
  select * from public.load_mercury_operation(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    p_operation_id
  );
end;
$$;

revoke all on function public.mercury_jsonb_has_exact_keys(
  pg_catalog.jsonb,
  pg_catalog.text[]
) from public, anon, authenticated;
revoke all on function public.save_mercury_document_preview(
  pg_catalog.jsonb,
  pg_catalog.jsonb
) from public, anon, authenticated;
revoke all on function public.load_mercury_document_preview(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.transition_mercury_document_preview(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.int8,
  pg_catalog.text,
  pg_catalog.timestamptz
) from public, anon, authenticated;
revoke all on function public.save_mercury_operation(
  pg_catalog.jsonb,
  pg_catalog.jsonb,
  pg_catalog.jsonb,
  pg_catalog.int8
) from public, anon, authenticated;
revoke all on function public.load_mercury_operation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.transition_mercury_operation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.int8,
  pg_catalog.text,
  pg_catalog.uuid,
  pg_catalog.timestamptz,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.transition_mercury_operation_item(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.int8,
  pg_catalog.text,
  pg_catalog.uuid,
  pg_catalog.timestamptz,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;

grant execute on function public.mercury_jsonb_has_exact_keys(
  pg_catalog.jsonb,
  pg_catalog.text[]
) to service_role;
grant execute on function public.save_mercury_document_preview(
  pg_catalog.jsonb,
  pg_catalog.jsonb
) to service_role;
grant execute on function public.load_mercury_document_preview(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;
grant execute on function public.transition_mercury_document_preview(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.int8,
  pg_catalog.text,
  pg_catalog.timestamptz
) to service_role;
grant execute on function public.save_mercury_operation(
  pg_catalog.jsonb,
  pg_catalog.jsonb,
  pg_catalog.jsonb,
  pg_catalog.int8
) to service_role;
grant execute on function public.load_mercury_operation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;
grant execute on function public.transition_mercury_operation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.int8,
  pg_catalog.text,
  pg_catalog.uuid,
  pg_catalog.timestamptz,
  pg_catalog.text
) to service_role;
grant execute on function public.transition_mercury_operation_item(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.int8,
  pg_catalog.text,
  pg_catalog.uuid,
  pg_catalog.timestamptz,
  pg_catalog.text,
  pg_catalog.text
) to service_role;

commit;
