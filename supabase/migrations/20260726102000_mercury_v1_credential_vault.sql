create table if not exists public.mercury_provider_credential_envelopes (
  id pg_catalog.uuid primary key,
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  connection_id pg_catalog.uuid not null
    references public.mercury_provider_connections(id) on delete cascade,
  provider pg_catalog.text not null,
  environment pg_catalog.text not null,
  credential_type pg_catalog.text not null,
  key_version pg_catalog.text not null,
  nonce pg_catalog.bytea not null,
  ciphertext pg_catalog.bytea not null,
  aad_hash pg_catalog.bytea not null,
  created_at pg_catalog.timestamptz not null,
  rotated_at pg_catalog.timestamptz,
  revoked_at pg_catalog.timestamptz,
  constraint mercury_provider_credential_envelopes_provider_check
    check (provider in ('flowaccount', 'peak')),
  constraint mercury_provider_credential_envelopes_environment_check
    check (
      environment ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(environment) <= 64
    ),
  constraint mercury_provider_credential_envelopes_type_check
    check (
      credential_type ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(credential_type) <= 64
    ),
  constraint mercury_provider_credential_envelopes_key_check
    check (
      key_version ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(key_version) <= 64
    ),
  constraint mercury_provider_credential_envelopes_nonce_check
    check (pg_catalog.octet_length(nonce) = 12),
  constraint mercury_provider_credential_envelopes_ciphertext_check
    check (pg_catalog.octet_length(ciphertext) >= 16),
  constraint mercury_provider_credential_envelopes_aad_check
    check (pg_catalog.octet_length(aad_hash) = 32),
  constraint mercury_provider_credential_envelopes_rotation_check
    check (rotated_at is null or rotated_at >= created_at),
  constraint mercury_provider_credential_envelopes_revocation_check
    check (revoked_at is null or revoked_at >= created_at),
  unique (connection_id, credential_type)
);

alter table public.mercury_provider_connections
  add column if not exists credential_envelope_ids pg_catalog.uuid[]
    not null default '{}'::pg_catalog.uuid[];

create index if not exists mercury_provider_credential_envelopes_binding_idx
  on public.mercury_provider_credential_envelopes (
    tenant_id,
    workspace_id,
    auth_user_id,
    connection_id
  );

alter table public.mercury_provider_credential_envelopes enable row level security;

revoke all on table public.mercury_provider_credential_envelopes
  from public, anon, authenticated;
grant all on table public.mercury_provider_credential_envelopes to service_role;

create or replace function
public.mercury_assert_provider_backend_workspace_access(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid
)
returns pg_catalog.void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or not exists (
      select 1
      from public.mercury_workspace_members as member
      join public.mercury_workspaces as workspace
        on workspace.id = member.workspace_id
        and workspace.tenant_id = member.tenant_id
      where member.tenant_id = p_tenant_id
        and member.workspace_id = p_workspace_id
        and member.auth_user_id = p_auth_user_id
        and member.status = 'active'
        and workspace.status = 'active'
    )
  then
    raise insufficient_privilege
      using message = 'workspace_access_denied';
  end if;
end;
$$;

revoke all on function
public.mercury_assert_provider_backend_workspace_access(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;
grant execute on function
public.mercury_assert_provider_backend_workspace_access(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;

create or replace function public.save_mercury_provider_connection(
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
  v_connection public.mercury_provider_connections%rowtype;
  v_existing public.mercury_provider_connections%rowtype;
  v_envelope pg_catalog.jsonb;
  v_envelope_id pg_catalog.uuid;
  v_credential_type pg_catalog.text;
  v_key_version pg_catalog.text;
  v_nonce pg_catalog.bytea;
  v_ciphertext pg_catalog.bytea;
  v_aad_hash pg_catalog.bytea;
  v_created_at pg_catalog.timestamptz;
  v_rotated_at pg_catalog.timestamptz;
  v_envelope_ids pg_catalog.uuid[] := '{}'::pg_catalog.uuid[];
  v_credential_types pg_catalog.text[] := '{}'::pg_catalog.text[];
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  if p_connection_id is null
    or p_connection_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_provider is null
    or p_environment is null
    or p_provider_account_id is null
    or p_account_display_name is null
    or p_authorization_method is null
    or p_granted_permissions is null
    or p_readiness is null
    or p_revision is null
    or p_envelopes is null
    or p_provider not in ('flowaccount', 'peak')
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or pg_catalog.length(p_provider_account_id) not between 1 and 512
    or p_provider_account_id ~ '[[:cntrl:]]'
    or pg_catalog.length(p_account_display_name) not between 1 and 200
    or p_account_display_name ~ '[[:cntrl:]]'
    or p_authorization_method not in ('oauth2_pkce', 'provider_credentials')
    or not public.mercury_provider_permissions_are_safe(p_granted_permissions)
    or p_readiness not in (
      'requires_validation',
      'ready',
      'validation_failed',
      'requires_reauthorization'
    )
    or p_revision < 1
    or (p_readiness = 'ready' and p_last_validated_at is null)
    or pg_catalog.jsonb_typeof(p_envelopes) <> 'array'
    or pg_catalog.jsonb_array_length(p_envelopes) not between 1 and 16
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  select connection.*
  into v_existing
  from public.mercury_provider_connections as connection
  where connection.id = p_connection_id
  for update;

  if found then
    if v_existing.tenant_id <> p_tenant_id
      or v_existing.workspace_id <> p_workspace_id
      or v_existing.auth_user_id <> p_auth_user_id
      or v_existing.provider <> p_provider
      or v_existing.environment <> p_environment
      or v_existing.provider_account_id <> p_provider_account_id
      or p_revision <> v_existing.revision + 1
    then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;

    delete from public.mercury_provider_credential_envelopes as envelope
    where envelope.connection_id = p_connection_id
      and envelope.tenant_id = p_tenant_id
      and envelope.workspace_id = p_workspace_id
      and envelope.auth_user_id = p_auth_user_id;

    update public.mercury_provider_connections as connection
    set account_display_name = p_account_display_name,
        authorization_method = p_authorization_method,
        granted_permissions = p_granted_permissions,
        readiness = p_readiness,
        revision = p_revision,
        last_validated_at = p_last_validated_at,
        provider_revocation_required = false,
        disconnected_at = null,
        credential_envelope_ids = '{}'::pg_catalog.uuid[],
        updated_at = v_now
    where connection.id = p_connection_id
    returning connection.* into v_connection;
  else
    if p_revision <> 1 then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;
    begin
      insert into public.mercury_provider_connections (
        id,
        tenant_id,
        workspace_id,
        auth_user_id,
        provider,
        environment,
        provider_account_id,
        account_display_name,
        authorization_method,
        granted_permissions,
        readiness,
        revision,
        last_validated_at,
        provider_revocation_required,
        disconnected_at,
        credential_envelope_ids,
        created_at,
        updated_at
      )
      values (
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
        false,
        null,
        '{}'::pg_catalog.uuid[],
        v_now,
        v_now
      )
      returning * into v_connection;
    exception
      when unique_violation then
        raise unique_violation
          using message = 'provider_connection_conflict';
    end;
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
        or v_envelope ->> 'nonce' !~ '^[0-9a-f]{24}$'
        or v_envelope ->> 'ciphertext' !~ '^[0-9a-f]{32,}$'
        or pg_catalog.length(v_envelope ->> 'ciphertext') % 2 <> 0
        or v_envelope ->> 'aad_hash' !~ '^[0-9a-f]{64}$'
        or nullif(v_envelope ->> 'revoked_at', '') is not null
      then
        raise invalid_parameter_value;
      end if;

      v_envelope_id := (v_envelope ->> 'id')::pg_catalog.uuid;
      v_credential_type := v_envelope ->> 'credential_type';
      v_key_version := v_envelope ->> 'key_version';
      v_nonce := pg_catalog.decode(v_envelope ->> 'nonce', 'hex');
      v_ciphertext := pg_catalog.decode(v_envelope ->> 'ciphertext', 'hex');
      v_aad_hash := pg_catalog.decode(v_envelope ->> 'aad_hash', 'hex');
      v_created_at := (v_envelope ->> 'created_at')::pg_catalog.timestamptz;
      v_rotated_at := nullif(
        v_envelope ->> 'rotated_at',
        ''
      )::pg_catalog.timestamptz;

      if v_envelope_id is null
        or v_key_version !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        or pg_catalog.length(v_key_version) > 64
        or pg_catalog.octet_length(v_nonce) <> 12
        or pg_catalog.octet_length(v_ciphertext) < 16
        or pg_catalog.octet_length(v_aad_hash) <> 32
        or v_created_at is null
        or (v_rotated_at is not null and v_rotated_at < v_created_at)
        or v_envelope_id = any(v_envelope_ids)
        or v_credential_type = any(v_credential_types)
      then
        raise invalid_parameter_value;
      end if;

      insert into public.mercury_provider_credential_envelopes (
        id,
        tenant_id,
        workspace_id,
        auth_user_id,
        connection_id,
        provider,
        environment,
        credential_type,
        key_version,
        nonce,
        ciphertext,
        aad_hash,
        created_at,
        rotated_at,
        revoked_at
      )
      values (
        v_envelope_id,
        p_tenant_id,
        p_workspace_id,
        p_auth_user_id,
        p_connection_id,
        p_provider,
        p_environment,
        v_credential_type,
        v_key_version,
        v_nonce,
        v_ciphertext,
        v_aad_hash,
        v_created_at,
        v_rotated_at,
        null
      );
    exception
      when others then
        raise invalid_parameter_value
          using message = 'provider_credential_envelope_invalid';
    end;

    v_envelope_ids := pg_catalog.array_append(
      v_envelope_ids,
      v_envelope_id
    );
    v_credential_types := pg_catalog.array_append(
      v_credential_types,
      v_credential_type
    );
  end loop;

  update public.mercury_provider_connections as connection
  set credential_envelope_ids = v_envelope_ids
  where connection.id = p_connection_id
  returning connection.* into v_connection;

  return query
  select
    v_connection.id,
    v_connection.provider,
    v_connection.environment,
    v_connection.account_display_name,
    v_connection.authorization_method,
    v_connection.granted_permissions,
    v_connection.readiness,
    v_connection.revision,
    v_connection.last_validated_at,
    v_connection.provider_revocation_required,
    v_connection.created_at,
    v_connection.updated_at;
end;
$$;

create or replace function public.list_mercury_provider_connections(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid
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
  provider_revocation_required pg_catalog.bool
)
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  perform public.mercury_assert_provider_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  return query
  select
    connection.id,
    connection.provider,
    connection.environment,
    connection.account_display_name,
    connection.authorization_method,
    connection.granted_permissions,
    connection.readiness,
    connection.revision,
    connection.last_validated_at,
    connection.provider_revocation_required
  from public.mercury_provider_connections as connection
  where connection.tenant_id = p_tenant_id
    and connection.workspace_id = p_workspace_id
    and connection.auth_user_id = p_auth_user_id
  order by
    connection.provider,
    connection.environment,
    connection.account_display_name,
    connection.id;
end;
$$;

create or replace function public.load_mercury_provider_credential_envelopes(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_connection_id pg_catalog.uuid
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
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_connection_id is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_connection_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );
  if not exists (
    select 1
    from public.mercury_provider_connections as connection
    where connection.id = p_connection_id
      and connection.tenant_id = p_tenant_id
      and connection.workspace_id = p_workspace_id
      and connection.auth_user_id = p_auth_user_id
      and connection.readiness <> 'disconnected'
  ) then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;

  return query
  select
    envelope.id,
    envelope.tenant_id,
    envelope.workspace_id,
    envelope.auth_user_id,
    envelope.connection_id,
    envelope.provider,
    envelope.environment,
    envelope.credential_type,
    envelope.key_version,
    envelope.nonce,
    envelope.ciphertext,
    envelope.aad_hash,
    envelope.created_at,
    envelope.rotated_at,
    envelope.revoked_at
  from public.mercury_provider_credential_envelopes as envelope
  where envelope.tenant_id = p_tenant_id
    and envelope.workspace_id = p_workspace_id
    and envelope.auth_user_id = p_auth_user_id
    and envelope.connection_id = p_connection_id
    and envelope.revoked_at is null
  order by envelope.credential_type, envelope.id;
end;
$$;

create or replace function public.disconnect_mercury_provider_connection(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_connection_id pg_catalog.uuid,
  p_provider_revocation_required pg_catalog.bool default false
)
returns table (
  connection_id pg_catalog.uuid,
  status pg_catalog.text,
  deleted_envelope_count pg_catalog.int4,
  already_disconnected pg_catalog.bool,
  provider_revocation_required pg_catalog.bool,
  revision pg_catalog.int8
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_connection public.mercury_provider_connections%rowtype;
  v_deleted pg_catalog.int4;
  v_already_disconnected pg_catalog.bool;
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_connection_id is null
    or p_provider_revocation_required is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_connection_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;
  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  select connection.*
  into v_connection
  from public.mercury_provider_connections as connection
  where connection.id = p_connection_id
    and connection.tenant_id = p_tenant_id
    and connection.workspace_id = p_workspace_id
    and connection.auth_user_id = p_auth_user_id
  for update;

  if not found then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;

  v_already_disconnected := v_connection.readiness = 'disconnected';

  delete from public.mercury_provider_credential_envelopes as envelope
  where envelope.connection_id = p_connection_id
    and envelope.tenant_id = p_tenant_id
    and envelope.workspace_id = p_workspace_id
    and envelope.auth_user_id = p_auth_user_id;
  get diagnostics v_deleted = row_count;

  update public.mercury_provider_connections as connection
  set readiness = 'disconnected',
      revision = case
        when v_already_disconnected then connection.revision
        else connection.revision + 1
      end,
      credential_envelope_ids = '{}'::pg_catalog.uuid[],
      provider_revocation_required = (
        connection.provider_revocation_required
        or p_provider_revocation_required
      ),
      disconnected_at = coalesce(connection.disconnected_at, v_now),
      updated_at = case
        when v_already_disconnected
          and connection.provider_revocation_required
            = (
              connection.provider_revocation_required
              or p_provider_revocation_required
            )
          then connection.updated_at
        else v_now
      end
  where connection.id = p_connection_id
  returning connection.* into v_connection;

  return query
  select
    v_connection.id,
    'disconnected'::pg_catalog.text,
    v_deleted,
    v_already_disconnected,
    v_connection.provider_revocation_required,
    v_connection.revision;
end;
$$;

revoke all on function public.save_mercury_provider_connection(
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
revoke all on function public.list_mercury_provider_connections(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.load_mercury_provider_credential_envelopes(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.disconnect_mercury_provider_connection(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.bool
) from public, anon, authenticated;

grant execute on function public.save_mercury_provider_connection(
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
grant execute on function public.list_mercury_provider_connections(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to authenticated;
grant execute on function public.load_mercury_provider_credential_envelopes(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;
grant execute on function public.disconnect_mercury_provider_connection(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.bool
) to service_role;
