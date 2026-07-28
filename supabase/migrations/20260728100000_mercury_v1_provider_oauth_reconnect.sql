-- Forward-only OAuth reconnect finalization and durable revocation obligations.

create or replace function public.stage_mercury_provider_connection(
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
begin
  if p_readiness <> 'requires_validation'
    or p_revision <> 1
    or p_last_validated_at is not null
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;

  perform 1
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

  update public.mercury_provider_connections as connection
  set provider_revocation_required = true
  where connection.id = p_connection_id
    and connection.tenant_id = p_tenant_id
    and connection.workspace_id = p_workspace_id
    and connection.auth_user_id = p_auth_user_id
  returning connection.* into v_connection;

  if not found then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;

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

create or replace function public.resolve_mercury_provider_connection_target(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_provider_account_id pg_catalog.text,
  p_proposed_connection_id pg_catalog.uuid
)
returns table (
  connection_id pg_catalog.uuid,
  revision pg_catalog.int8,
  reuses_existing pg_catalog.bool
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_connection public.mercury_provider_connections%rowtype;
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_proposed_connection_id is null
    or p_provider is null
    or p_environment is null
    or p_provider_account_id is null
    or p_provider not in ('flowaccount', 'peak')
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or pg_catalog.length(p_provider_account_id) not between 1 and 512
    or p_provider_account_id ~ '[[:cntrl:]]'
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
  where connection.tenant_id = p_tenant_id
    and connection.workspace_id = p_workspace_id
    and connection.auth_user_id = p_auth_user_id
    and connection.provider = p_provider
    and connection.environment = p_environment
    and connection.provider_account_id = p_provider_account_id
  for update;

  if found then
    if v_connection.readiness <> 'disconnected'
      or v_connection.credential_envelope_ids <> '{}'::pg_catalog.uuid[]
      or v_connection.provider_revocation_required
      or exists (
        select 1
        from public.mercury_provider_credential_envelopes as envelope
        where envelope.connection_id = v_connection.id
      )
    then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;
    return query
    select v_connection.id, v_connection.revision + 1, true;
    return;
  end if;

  perform 1
  from public.mercury_provider_connections as connection
  where connection.id = p_proposed_connection_id
  for update;
  if found then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  return query
  select p_proposed_connection_id, 1::pg_catalog.int8, false;
end;
$$;

create or replace function public.finalize_mercury_provider_connection(
  p_staged_connection_id pg_catalog.uuid,
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
  v_staged public.mercury_provider_connections%rowtype;
  v_target public.mercury_provider_connections%rowtype;
  v_saved record;
begin
  if p_staged_connection_id is null
    or p_staged_connection_id = p_connection_id
    or p_readiness <> 'ready'
    or p_last_validated_at is null
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
  into v_staged
  from public.mercury_provider_connections as connection
  where connection.id = p_staged_connection_id
    and connection.tenant_id = p_tenant_id
    and connection.workspace_id = p_workspace_id
    and connection.auth_user_id = p_auth_user_id
    and connection.provider = p_provider
    and connection.environment = p_environment
  for update;

  if not found
    or v_staged.readiness = 'disconnected'
    or not v_staged.provider_revocation_required
    or v_staged.credential_envelope_ids = '{}'::pg_catalog.uuid[]
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;

  select connection.*
  into v_target
  from public.mercury_provider_connections as connection
  where connection.id = p_connection_id
  for update;
  if found and v_target.provider_revocation_required then
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

  perform 1
  from public.disconnect_mercury_provider_connection(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    p_staged_connection_id,
    true
  );
  perform 1
  from public.complete_mercury_provider_revocation(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    p_staged_connection_id
  );

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

create or replace function public.record_mercury_provider_revocation_obligation(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_connection_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_provider_account_id pg_catalog.text,
  p_account_display_name pg_catalog.text,
  p_authorization_method pg_catalog.text,
  p_granted_permissions pg_catalog.jsonb
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
  v_deleted pg_catalog.int4 := 0;
  v_already_disconnected pg_catalog.bool;
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_connection_id is null
    or p_provider is null
    or p_environment is null
    or p_provider_account_id is null
    or p_account_display_name is null
    or p_authorization_method is null
    or p_granted_permissions is null
    or p_provider not in ('flowaccount', 'peak')
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or pg_catalog.length(p_provider_account_id) not between 1 and 512
    or p_provider_account_id ~ '[[:cntrl:]]'
    or pg_catalog.length(p_account_display_name) not between 1 and 200
    or p_account_display_name ~ '[[:cntrl:]]'
    or p_authorization_method not in ('oauth2_pkce', 'provider_credentials')
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

  select connection.*
  into v_connection
  from public.mercury_provider_connections as connection
  where connection.id = p_connection_id
  for update;

  if found then
    if v_connection.tenant_id <> p_tenant_id
      or v_connection.workspace_id <> p_workspace_id
      or v_connection.auth_user_id <> p_auth_user_id
      or v_connection.provider <> p_provider
      or v_connection.environment <> p_environment
      or v_connection.provider_account_id <> p_provider_account_id
    then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;
    v_already_disconnected := v_connection.readiness = 'disconnected';

    delete from public.mercury_provider_credential_envelopes as envelope
    where envelope.connection_id = p_connection_id
      and envelope.tenant_id = p_tenant_id
      and envelope.workspace_id = p_workspace_id
      and envelope.auth_user_id = p_auth_user_id;
    get diagnostics v_deleted = row_count;

    update public.mercury_provider_connections as connection
    set account_display_name = p_account_display_name,
        authorization_method = p_authorization_method,
        granted_permissions = p_granted_permissions,
        readiness = 'disconnected',
        revision = case
          when v_already_disconnected then connection.revision
          else connection.revision + 1
        end,
        last_validated_at = null,
        credential_envelope_ids = '{}'::pg_catalog.uuid[],
        provider_revocation_required = true,
        disconnected_at = coalesce(connection.disconnected_at, v_now),
        updated_at = v_now
    where connection.id = p_connection_id
    returning connection.* into v_connection;
  else
    v_already_disconnected := true;
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
        credential_envelope_ids,
        provider_revocation_required,
        disconnected_at,
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
        'disconnected',
        1,
        null,
        '{}'::pg_catalog.uuid[],
        true,
        v_now,
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

revoke all on function public.stage_mercury_provider_connection(
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
revoke all on function public.resolve_mercury_provider_connection_target(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.finalize_mercury_provider_connection(
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
revoke all on function public.record_mercury_provider_revocation_obligation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb
) from public, anon, authenticated;

grant execute on function public.stage_mercury_provider_connection(
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
grant execute on function public.resolve_mercury_provider_connection_target(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.uuid
) to service_role;
grant execute on function public.finalize_mercury_provider_connection(
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
grant execute on function public.record_mercury_provider_revocation_obligation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb
) to service_role;
