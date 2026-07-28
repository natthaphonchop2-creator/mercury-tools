-- Internal OAuth recovery attempts. Provider material never stages in public connections.

create table if not exists public.mercury_provider_oauth_attempts (
  id pg_catalog.uuid primary key,
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  provider pg_catalog.text not null,
  environment pg_catalog.text not null,
  granted_permissions pg_catalog.jsonb not null,
  status pg_catalog.text not null default 'exchange_pending',
  provider_account_id pg_catalog.text,
  account_display_name pg_catalog.text,
  authorization_method pg_catalog.text,
  credential_envelopes pg_catalog.jsonb not null default '[]'::pg_catalog.jsonb,
  target_connection_id pg_catalog.uuid,
  target_revision pg_catalog.int8,
  provider_revocation_required pg_catalog.bool not null default true,
  created_at pg_catalog.timestamptz not null default pg_catalog.now(),
  updated_at pg_catalog.timestamptz not null default pg_catalog.now(),
  constraint mercury_provider_oauth_attempts_provider_check
    check (provider = 'flowaccount'),
  constraint mercury_provider_oauth_attempts_environment_check
    check (
      environment ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(environment) <= 64
    ),
  constraint mercury_provider_oauth_attempts_permissions_check
    check (public.mercury_provider_permissions_are_safe(granted_permissions)),
  constraint mercury_provider_oauth_attempts_status_check
    check (
      status in (
        'exchange_pending',
        'material_attached',
        'finalized',
        'failed',
        'revoked'
      )
    ),
  constraint mercury_provider_oauth_attempts_target_check
    check (
      (target_connection_id is null and target_revision is null)
      or (
        target_connection_id is not null
        and target_revision is not null
        and target_revision >= 1
      )
    ),
  constraint mercury_provider_oauth_attempts_material_check
    check (
      pg_catalog.jsonb_typeof(credential_envelopes) = 'array'
      and (
        (
          status = 'exchange_pending'
          and provider_account_id is null
          and account_display_name is null
          and authorization_method is null
          and credential_envelopes = '[]'::pg_catalog.jsonb
          and target_connection_id is null
          and provider_revocation_required
        )
        or (
          status = 'material_attached'
          and provider_account_id is not null
          and account_display_name is not null
          and authorization_method = 'oauth2_pkce'
          and pg_catalog.jsonb_array_length(credential_envelopes) between 1 and 16
          and target_connection_id is null
          and provider_revocation_required
        )
        or (
          status = 'finalized'
          and credential_envelopes = '[]'::pg_catalog.jsonb
          and target_connection_id is not null
          and not provider_revocation_required
        )
        or (
          status = 'failed'
          and (
            credential_envelopes = '[]'::pg_catalog.jsonb
            or pg_catalog.jsonb_array_length(credential_envelopes)
              between 1 and 16
          )
          and provider_revocation_required
        )
        or (
          status = 'revoked'
          and credential_envelopes = '[]'::pg_catalog.jsonb
          and not provider_revocation_required
        )
      )
    )
);

create index if not exists mercury_provider_oauth_attempts_remediation_idx
  on public.mercury_provider_oauth_attempts (
    provider_revocation_required,
    status,
    updated_at
  );

alter table public.mercury_provider_oauth_attempts enable row level security;

revoke all on table public.mercury_provider_oauth_attempts
  from public, anon, authenticated;
grant all on table public.mercury_provider_oauth_attempts to service_role;

create or replace function public.mercury_provider_oauth_envelopes_are_safe(
  p_envelopes pg_catalog.jsonb
)
returns pg_catalog.bool
language plpgsql
immutable
set search_path = ''
as $$
declare
  v_envelope pg_catalog.jsonb;
  v_envelope_id pg_catalog.uuid;
  v_credential_type pg_catalog.text;
  v_key_version pg_catalog.text;
  v_created_at pg_catalog.timestamptz;
  v_rotated_at pg_catalog.timestamptz;
  v_envelope_ids pg_catalog.uuid[] := '{}'::pg_catalog.uuid[];
  v_credential_types pg_catalog.text[] := '{}'::pg_catalog.text[];
begin
  if p_envelopes is null
    or pg_catalog.jsonb_typeof(p_envelopes) <> 'array'
    or pg_catalog.jsonb_array_length(p_envelopes) not between 1 and 16
  then
    return false;
  end if;

  for v_envelope in
    select item.value
    from pg_catalog.jsonb_array_elements(p_envelopes) as item(value)
  loop
    begin
      if pg_catalog.jsonb_typeof(v_envelope) <> 'object'
        or v_envelope - array[
          'id',
          'credential_type',
          'key_version',
          'nonce',
          'ciphertext',
          'aad_hash',
          'created_at',
          'rotated_at',
          'revoked_at'
        ] <> '{}'::pg_catalog.jsonb
        or not (
          v_envelope ? 'id'
          and v_envelope ? 'credential_type'
          and v_envelope ? 'key_version'
          and v_envelope ? 'nonce'
          and v_envelope ? 'ciphertext'
          and v_envelope ? 'aad_hash'
          and v_envelope ? 'created_at'
        )
        or v_envelope ->> 'credential_type'
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        or pg_catalog.length(v_envelope ->> 'credential_type') > 64
        or v_envelope ->> 'key_version'
          !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        or pg_catalog.length(v_envelope ->> 'key_version') > 64
        or v_envelope ->> 'nonce' !~ '^[0-9a-f]{24}$'
        or v_envelope ->> 'ciphertext' !~ '^[0-9a-f]{32,}$'
        or pg_catalog.length(v_envelope ->> 'ciphertext') % 2 <> 0
        or v_envelope ->> 'aad_hash' !~ '^[0-9a-f]{64}$'
        or nullif(v_envelope ->> 'revoked_at', '') is not null
      then
        return false;
      end if;

      v_envelope_id := (v_envelope ->> 'id')::pg_catalog.uuid;
      v_credential_type := v_envelope ->> 'credential_type';
      v_key_version := v_envelope ->> 'key_version';
      v_created_at := (v_envelope ->> 'created_at')::pg_catalog.timestamptz;
      v_rotated_at := nullif(
        v_envelope ->> 'rotated_at',
        ''
      )::pg_catalog.timestamptz;

      if v_envelope_id is null
        or v_envelope_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
        or v_key_version is null
        or v_created_at is null
        or (v_rotated_at is not null and v_rotated_at < v_created_at)
        or v_envelope_id = any(v_envelope_ids)
        or v_credential_type = any(v_credential_types)
      then
        return false;
      end if;
    exception
      when others then
        return false;
    end;

    v_envelope_ids := pg_catalog.array_append(v_envelope_ids, v_envelope_id);
    v_credential_types := pg_catalog.array_append(
      v_credential_types,
      v_credential_type
    );
  end loop;
  return true;
end;
$$;

revoke all on function public.mercury_provider_oauth_envelopes_are_safe(
  pg_catalog.jsonb
) from public, anon, authenticated;
grant execute on function public.mercury_provider_oauth_envelopes_are_safe(
  pg_catalog.jsonb
) to service_role;

create or replace function public.begin_mercury_provider_oauth_attempt(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_granted_permissions pg_catalog.jsonb
)
returns table (
  attempt_id pg_catalog.uuid,
  status pg_catalog.text,
  target_connection_id pg_catalog.uuid,
  target_revision pg_catalog.int8,
  provider_revocation_required pg_catalog.bool,
  created_at pg_catalog.timestamptz,
  updated_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
begin
  if p_attempt_id is null
    or p_attempt_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_provider <> 'flowaccount'
    or p_environment is null
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or not public.mercury_provider_permissions_are_safe(p_granted_permissions)
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  if exists (
    select 1
    from public.mercury_provider_connections as connection
    where connection.id = p_attempt_id
  ) then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  insert into public.mercury_provider_oauth_attempts (
    id,
    tenant_id,
    workspace_id,
    auth_user_id,
    provider,
    environment,
    granted_permissions
  )
  values (
    p_attempt_id,
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    p_provider,
    p_environment,
    p_granted_permissions
  )
  on conflict (id) do nothing;

  select attempt.*
  into v_attempt
  from public.mercury_provider_oauth_attempts as attempt
  where attempt.id = p_attempt_id
  for update;

  if not found
    or v_attempt.tenant_id <> p_tenant_id
    or v_attempt.workspace_id <> p_workspace_id
    or v_attempt.auth_user_id <> p_auth_user_id
    or v_attempt.provider <> p_provider
    or v_attempt.environment <> p_environment
    or v_attempt.granted_permissions <> p_granted_permissions
    or v_attempt.status <> 'exchange_pending'
  then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  return query
  select
    v_attempt.id,
    v_attempt.status,
    v_attempt.target_connection_id,
    v_attempt.target_revision,
    v_attempt.provider_revocation_required,
    v_attempt.created_at,
    v_attempt.updated_at;
end;
$$;

create or replace function public.attach_mercury_provider_oauth_attempt(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_provider_account_id pg_catalog.text,
  p_account_display_name pg_catalog.text,
  p_authorization_method pg_catalog.text,
  p_granted_permissions pg_catalog.jsonb,
  p_readiness pg_catalog.text,
  p_revision pg_catalog.int8,
  p_last_validated_at pg_catalog.timestamptz,
  p_envelopes pg_catalog.jsonb
)
returns table (
  attempt_id pg_catalog.uuid,
  status pg_catalog.text,
  target_connection_id pg_catalog.uuid,
  target_revision pg_catalog.int8,
  provider_revocation_required pg_catalog.bool,
  created_at pg_catalog.timestamptz,
  updated_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
begin
  if p_attempt_id is null
    or p_provider <> 'flowaccount'
    or p_provider_account_id <> 'oauth-pending-' || p_attempt_id::pg_catalog.text
    or pg_catalog.length(p_account_display_name) not between 1 and 200
    or p_account_display_name ~ '[[:cntrl:]]'
    or p_authorization_method <> 'oauth2_pkce'
    or not public.mercury_provider_permissions_are_safe(p_granted_permissions)
    or p_readiness <> 'requires_validation'
    or p_revision <> 1
    or p_last_validated_at is not null
    or not public.mercury_provider_oauth_envelopes_are_safe(p_envelopes)
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  select attempt.*
  into v_attempt
  from public.mercury_provider_oauth_attempts as attempt
  where attempt.id = p_attempt_id
    and attempt.tenant_id = p_tenant_id
    and attempt.workspace_id = p_workspace_id
    and attempt.auth_user_id = p_auth_user_id
    and attempt.provider = p_provider
    and attempt.environment = p_environment
  for update;

  if not found or v_attempt.granted_permissions <> p_granted_permissions then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;
  if v_attempt.status = 'material_attached' then
    if v_attempt.provider_account_id <> p_provider_account_id
      or v_attempt.account_display_name <> p_account_display_name
      or v_attempt.authorization_method <> p_authorization_method
      or v_attempt.credential_envelopes <> p_envelopes
    then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;
  elsif v_attempt.status = 'exchange_pending' then
    update public.mercury_provider_oauth_attempts as attempt
    set status = 'material_attached',
        provider_account_id = p_provider_account_id,
        account_display_name = p_account_display_name,
        authorization_method = p_authorization_method,
        credential_envelopes = p_envelopes,
        updated_at = pg_catalog.statement_timestamp()
    where attempt.id = p_attempt_id
    returning attempt.* into v_attempt;
  else
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  return query
  select
    v_attempt.id,
    v_attempt.status,
    v_attempt.target_connection_id,
    v_attempt.target_revision,
    v_attempt.provider_revocation_required,
    v_attempt.created_at,
    v_attempt.updated_at;
end;
$$;

create or replace function public.load_mercury_provider_oauth_attempt_envelopes(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text
)
returns table (
  id pg_catalog.uuid,
  tenant_id pg_catalog.uuid,
  workspace_id pg_catalog.uuid,
  auth_user_id pg_catalog.uuid,
  connection_id pg_catalog.uuid,
  provider pg_catalog.text,
  environment pg_catalog.text,
  credential_type pg_catalog.text,
  key_version pg_catalog.text,
  nonce pg_catalog.bytea,
  ciphertext pg_catalog.bytea,
  aad_hash pg_catalog.bytea,
  created_at pg_catalog.timestamptz,
  rotated_at pg_catalog.timestamptz,
  revoked_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  select attempt.*
  into v_attempt
  from public.mercury_provider_oauth_attempts as attempt
  where attempt.id = p_attempt_id
    and attempt.tenant_id = p_tenant_id
    and attempt.workspace_id = p_workspace_id
    and attempt.auth_user_id = p_auth_user_id
    and attempt.provider = p_provider
    and attempt.environment = p_environment;

  if not found
    or v_attempt.status not in ('material_attached', 'failed')
    or not v_attempt.provider_revocation_required
    or v_attempt.credential_envelopes = '[]'::pg_catalog.jsonb
  then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;

  return query
  select
    (item.value ->> 'id')::pg_catalog.uuid,
    v_attempt.tenant_id,
    v_attempt.workspace_id,
    v_attempt.auth_user_id,
    coalesce(v_attempt.target_connection_id, v_attempt.id),
    v_attempt.provider,
    v_attempt.environment,
    item.value ->> 'credential_type',
    item.value ->> 'key_version',
    pg_catalog.decode(item.value ->> 'nonce', 'hex'),
    pg_catalog.decode(item.value ->> 'ciphertext', 'hex'),
    pg_catalog.decode(item.value ->> 'aad_hash', 'hex'),
    (item.value ->> 'created_at')::pg_catalog.timestamptz,
    nullif(item.value ->> 'rotated_at', '')::pg_catalog.timestamptz,
    null::pg_catalog.timestamptz
  from pg_catalog.jsonb_array_elements(
    v_attempt.credential_envelopes
  ) as item(value)
  order by item.value ->> 'credential_type', item.value ->> 'id';
end;
$$;

create or replace function public.finalize_mercury_provider_oauth_attempt(
  p_attempt_id pg_catalog.uuid,
  p_connection_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_provider_account_id pg_catalog.text,
  p_account_display_name pg_catalog.text,
  p_authorization_method pg_catalog.text,
  p_granted_permissions pg_catalog.jsonb,
  p_readiness pg_catalog.text,
  p_revision pg_catalog.int8,
  p_last_validated_at pg_catalog.timestamptz,
  p_envelopes pg_catalog.jsonb
)
returns table (
  connection_id pg_catalog.uuid,
  provider pg_catalog.text,
  environment pg_catalog.text,
  account_display_name pg_catalog.text,
  authorization_method pg_catalog.text,
  granted_permissions pg_catalog.jsonb,
  readiness pg_catalog.text,
  revision pg_catalog.int8,
  last_validated_at pg_catalog.timestamptz,
  provider_revocation_required pg_catalog.bool,
  created_at pg_catalog.timestamptz,
  updated_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
  v_target public.mercury_provider_connections%rowtype;
  v_saved record;
  v_envelope_ids pg_catalog.uuid[];
begin
  if p_attempt_id is null
    or p_attempt_id = p_connection_id
    or p_readiness <> 'ready'
    or p_last_validated_at is null
    or not public.mercury_provider_oauth_envelopes_are_safe(p_envelopes)
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  select pg_catalog.array_agg(
    (item.value ->> 'id')::pg_catalog.uuid
    order by item.ordinality
  )
  into v_envelope_ids
  from pg_catalog.jsonb_array_elements(p_envelopes)
    with ordinality as item(value, ordinality);

  select attempt.*
  into v_attempt
  from public.mercury_provider_oauth_attempts as attempt
  where attempt.id = p_attempt_id
    and attempt.tenant_id = p_tenant_id
    and attempt.workspace_id = p_workspace_id
    and attempt.auth_user_id = p_auth_user_id
    and attempt.provider = p_provider
    and attempt.environment = p_environment
  for update;

  if not found or v_attempt.granted_permissions <> p_granted_permissions then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;
  if v_attempt.status = 'finalized' then
    select connection.*
    into v_target
    from public.mercury_provider_connections as connection
    where connection.id = p_connection_id
    for update;
    if not found
      or v_attempt.target_connection_id <> p_connection_id
      or v_attempt.target_revision <> p_revision
      or v_attempt.provider_revocation_required
      or v_target.tenant_id <> p_tenant_id
      or v_target.workspace_id <> p_workspace_id
      or v_target.auth_user_id <> p_auth_user_id
      or v_target.provider <> p_provider
      or v_target.environment <> p_environment
      or v_target.provider_account_id <> p_provider_account_id
      or v_target.account_display_name <> p_account_display_name
      or v_target.authorization_method <> p_authorization_method
      or v_target.granted_permissions <> p_granted_permissions
      or v_target.readiness <> 'ready'
      or v_target.revision <> p_revision
      or v_target.last_validated_at <> p_last_validated_at
      or v_target.provider_revocation_required
      or v_target.credential_envelope_ids <> v_envelope_ids
    then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;
    return query
    select
      v_target.id,
      v_target.provider,
      v_target.environment,
      v_target.account_display_name,
      v_target.authorization_method,
      v_target.granted_permissions,
      v_target.readiness,
      v_target.revision,
      v_target.last_validated_at,
      v_target.provider_revocation_required,
      v_target.created_at,
      v_target.updated_at;
    return;
  end if;
  if v_attempt.status <> 'material_attached'
    or not v_attempt.provider_revocation_required
  then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  select *
  into v_saved
  from public.save_mercury_provider_connection(
    p_connection_id,
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    p_provider,
    p_environment,
    p_provider_account_id,
    p_account_display_name,
    p_authorization_method,
    p_granted_permissions,
    p_readiness,
    p_revision,
    p_last_validated_at,
    p_envelopes
  );

  update public.mercury_provider_oauth_attempts as attempt
  set status = 'finalized',
      credential_envelopes = '[]'::pg_catalog.jsonb,
      target_connection_id = p_connection_id,
      target_revision = p_revision,
      provider_revocation_required = false,
      updated_at = pg_catalog.statement_timestamp()
  where attempt.id = p_attempt_id;

  return query
  select
    v_saved.connection_id,
    v_saved.provider,
    v_saved.environment,
    v_saved.account_display_name,
    v_saved.authorization_method,
    v_saved.granted_permissions,
    v_saved.readiness,
    v_saved.revision,
    v_saved.last_validated_at,
    v_saved.provider_revocation_required,
    v_saved.created_at,
    v_saved.updated_at;
end;
$$;

create or replace function public.fail_mercury_provider_oauth_attempt(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text
)
returns table (
  attempt_id pg_catalog.uuid,
  status pg_catalog.text,
  target_connection_id pg_catalog.uuid,
  target_revision pg_catalog.int8,
  provider_revocation_required pg_catalog.bool,
  created_at pg_catalog.timestamptz,
  updated_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
  v_target public.mercury_provider_connections%rowtype;
  v_disconnected record;
  v_recovery_envelopes pg_catalog.jsonb;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  select attempt.*
  into v_attempt
  from public.mercury_provider_oauth_attempts as attempt
  where attempt.id = p_attempt_id
    and attempt.tenant_id = p_tenant_id
    and attempt.workspace_id = p_workspace_id
    and attempt.auth_user_id = p_auth_user_id
    and attempt.provider = p_provider
    and attempt.environment = p_environment
  for update;

  if not found then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;
  if v_attempt.status = 'revoked' then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;
  if v_attempt.status = 'failed' then
    return query
    select
      v_attempt.id,
      v_attempt.status,
      v_attempt.target_connection_id,
      v_attempt.target_revision,
      v_attempt.provider_revocation_required,
      v_attempt.created_at,
      v_attempt.updated_at;
    return;
  end if;

  if v_attempt.target_connection_id is not null then
    select connection.*
    into v_target
    from public.mercury_provider_connections as connection
    where connection.id = v_attempt.target_connection_id
    for update;

    if not found
      or v_attempt.status <> 'finalized'
      or v_target.tenant_id <> p_tenant_id
      or v_target.workspace_id <> p_workspace_id
      or v_target.auth_user_id <> p_auth_user_id
      or v_target.provider <> p_provider
      or v_target.environment <> p_environment
      or v_target.readiness <> 'ready'
      or v_target.revision <> v_attempt.target_revision
      or v_target.provider_revocation_required
      or v_target.credential_envelope_ids = '{}'::pg_catalog.uuid[]
    then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;

    select coalesce(
      pg_catalog.jsonb_agg(
        pg_catalog.jsonb_build_object(
          'id', envelope.id,
          'credential_type', envelope.credential_type,
          'key_version', envelope.key_version,
          'nonce', pg_catalog.encode(envelope.nonce, 'hex'),
          'ciphertext', pg_catalog.encode(envelope.ciphertext, 'hex'),
          'aad_hash', pg_catalog.encode(envelope.aad_hash, 'hex'),
          'created_at', envelope.created_at,
          'rotated_at', envelope.rotated_at,
          'revoked_at', envelope.revoked_at
        )
        order by envelope.credential_type, envelope.id
      ),
      '[]'::pg_catalog.jsonb
    )
    into v_recovery_envelopes
    from public.mercury_provider_credential_envelopes as envelope
    where envelope.connection_id = v_target.id
      and envelope.tenant_id = p_tenant_id
      and envelope.workspace_id = p_workspace_id
      and envelope.auth_user_id = p_auth_user_id;

    if not public.mercury_provider_oauth_envelopes_are_safe(
      v_recovery_envelopes
    ) then
      raise unique_violation
        using message = 'provider_credential_binding_invalid';
    end if;

    select *
    into v_disconnected
    from public.disconnect_mercury_provider_connection(
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id,
      v_attempt.target_connection_id,
      true
    );
    v_attempt.target_revision := v_disconnected.revision;
  else
    v_recovery_envelopes := v_attempt.credential_envelopes;
  end if;

  update public.mercury_provider_oauth_attempts as attempt
  set status = 'failed',
      provider_account_id = coalesce(
        v_target.provider_account_id,
        attempt.provider_account_id
      ),
      account_display_name = coalesce(
        v_target.account_display_name,
        attempt.account_display_name
      ),
      authorization_method = coalesce(
        v_target.authorization_method,
        attempt.authorization_method
      ),
      credential_envelopes = v_recovery_envelopes,
      target_revision = v_attempt.target_revision,
      provider_revocation_required = true,
      updated_at = pg_catalog.statement_timestamp()
  where attempt.id = p_attempt_id
  returning attempt.* into v_attempt;

  return query
  select
    v_attempt.id,
    v_attempt.status,
    v_attempt.target_connection_id,
    v_attempt.target_revision,
    v_attempt.provider_revocation_required,
    v_attempt.created_at,
    v_attempt.updated_at;
end;
$$;

create or replace function
public.complete_mercury_provider_oauth_attempt_revocation(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text
)
returns table (
  attempt_id pg_catalog.uuid,
  status pg_catalog.text,
  target_connection_id pg_catalog.uuid,
  target_revision pg_catalog.int8,
  provider_revocation_required pg_catalog.bool,
  created_at pg_catalog.timestamptz,
  updated_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
begin
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  select attempt.*
  into v_attempt
  from public.mercury_provider_oauth_attempts as attempt
  where attempt.id = p_attempt_id
    and attempt.tenant_id = p_tenant_id
    and attempt.workspace_id = p_workspace_id
    and attempt.auth_user_id = p_auth_user_id
    and attempt.provider = p_provider
    and attempt.environment = p_environment
  for update;

  if not found then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;
  if v_attempt.status = 'revoked' then
    return query
    select
      v_attempt.id,
      v_attempt.status,
      v_attempt.target_connection_id,
      v_attempt.target_revision,
      v_attempt.provider_revocation_required,
      v_attempt.created_at,
      v_attempt.updated_at;
    return;
  end if;
  if v_attempt.status <> 'failed'
    or not v_attempt.provider_revocation_required
  then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  if v_attempt.target_connection_id is not null then
    perform 1
    from public.complete_mercury_provider_revocation(
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id,
      v_attempt.target_connection_id
    );
  end if;

  update public.mercury_provider_oauth_attempts as attempt
  set status = 'revoked',
      credential_envelopes = '[]'::pg_catalog.jsonb,
      provider_revocation_required = false,
      updated_at = pg_catalog.statement_timestamp()
  where attempt.id = p_attempt_id
  returning attempt.* into v_attempt;

  return query
  select
    v_attempt.id,
    v_attempt.status,
    v_attempt.target_connection_id,
    v_attempt.target_revision,
    v_attempt.provider_revocation_required,
    v_attempt.created_at,
    v_attempt.updated_at;
end;
$$;

revoke all on function public.begin_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb
) from public, anon, authenticated;
revoke all on function public.attach_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb,
  pg_catalog.text,
  pg_catalog.int8,
  pg_catalog.timestamptz,
  pg_catalog.jsonb
) from public, anon, authenticated;
revoke all on function public.load_mercury_provider_oauth_attempt_envelopes(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.finalize_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb,
  pg_catalog.text,
  pg_catalog.int8,
  pg_catalog.timestamptz,
  pg_catalog.jsonb
) from public, anon, authenticated;
revoke all on function public.fail_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function
public.complete_mercury_provider_oauth_attempt_revocation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;

grant execute on function public.begin_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb
) to service_role;
grant execute on function public.attach_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb,
  pg_catalog.text,
  pg_catalog.int8,
  pg_catalog.timestamptz,
  pg_catalog.jsonb
) to service_role;
grant execute on function public.load_mercury_provider_oauth_attempt_envelopes(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) to service_role;
grant execute on function public.finalize_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb,
  pg_catalog.text,
  pg_catalog.int8,
  pg_catalog.timestamptz,
  pg_catalog.jsonb
) to service_role;
grant execute on function public.fail_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) to service_role;
grant execute on function
public.complete_mercury_provider_oauth_attempt_revocation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) to service_role;
