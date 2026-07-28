create or replace function public.cancel_mercury_provider_oauth_state(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_state_hash pg_catalog.text
)
returns table (
  oauth_state_id pg_catalog.uuid,
  callback_state pg_catalog.jsonb,
  consumed_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_state public.mercury_provider_oauth_states%rowtype;
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_provider <> 'flowaccount'
    or p_environment is null
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or p_state_hash is null
    or p_state_hash !~ '^[0-9a-f]{64}$'
  then
    raise invalid_parameter_value
      using message = 'provider_oauth_state_invalid';
  end if;
  perform public.mercury_assert_provider_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  select state.*
  into v_state
  from public.mercury_provider_oauth_states as state
  where state.tenant_id = p_tenant_id
    and state.workspace_id = p_workspace_id
    and state.auth_user_id = p_auth_user_id
    and state.provider = p_provider
    and state.environment = p_environment
    and state.state_hash = p_state_hash
    and state.consumed_at is null
    and state.expires_at > v_now
  for update;

  if not found then
    raise invalid_parameter_value
      using message = 'provider_oauth_state_invalid';
  end if;

  update public.mercury_provider_oauth_states as state
  set consumed_at = v_now,
      pkce_verifier_ciphertext = null,
      pkce_key_version = null,
      pkce_nonce = null,
      pkce_aad_hash = null
  where state.id = v_state.id;

  return query
  select v_state.id, v_state.callback_state, v_now;
end;
$$;

create or replace function public.cleanup_expired_mercury_provider_oauth_states(
  p_limit pg_catalog.int4 default 100
)
returns table (
  cleaned_count pg_catalog.int4
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_cleaned pg_catalog.int4;
begin
  if p_limit is null or p_limit not between 1 and 1000 then
    raise invalid_parameter_value
      using message = 'provider_oauth_cleanup_invalid';
  end if;

  with expired as (
    select state.id
    from public.mercury_provider_oauth_states as state
    where state.consumed_at is null
      and state.expires_at <= pg_catalog.statement_timestamp()
    order by state.expires_at, state.id
    limit p_limit
    for update skip locked
  ),
  cleared as (
    update public.mercury_provider_oauth_states as state
    set consumed_at = pg_catalog.statement_timestamp(),
        pkce_verifier_ciphertext = null,
        pkce_key_version = null,
        pkce_nonce = null,
        pkce_aad_hash = null
    from expired
    where state.id = expired.id
    returning state.id
  )
  select pg_catalog.count(*)::pg_catalog.int4
  into v_cleaned
  from cleared;

  return query select v_cleaned;
end;
$$;

create or replace function public.complete_mercury_provider_revocation(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_connection_id pg_catalog.uuid
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
  if v_connection.readiness <> 'disconnected'
    or v_connection.credential_envelope_ids <> '{}'::pg_catalog.uuid[]
    or exists (
      select 1
      from public.mercury_provider_credential_envelopes as envelope
      where envelope.connection_id = p_connection_id
        and envelope.tenant_id = p_tenant_id
        and envelope.workspace_id = p_workspace_id
        and envelope.auth_user_id = p_auth_user_id
    )
  then
    raise invalid_parameter_value
      using message = 'provider_connection_invalid';
  end if;

  update public.mercury_provider_connections as connection
  set provider_revocation_required = false,
      updated_at = case
        when connection.provider_revocation_required
          then pg_catalog.statement_timestamp()
        else connection.updated_at
      end
  where connection.id = p_connection_id
  returning connection.* into v_connection;

  return query
  select
    v_connection.id,
    'disconnected'::pg_catalog.text,
    0::pg_catalog.int4,
    true,
    v_connection.provider_revocation_required,
    v_connection.revision;
end;
$$;

revoke all on function public.cancel_mercury_provider_oauth_state(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.cleanup_expired_mercury_provider_oauth_states(
  pg_catalog.int4
) from public, anon, authenticated;
revoke all on function public.complete_mercury_provider_revocation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;

grant execute on function public.cancel_mercury_provider_oauth_state(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) to authenticated;
grant execute on function public.cleanup_expired_mercury_provider_oauth_states(
  pg_catalog.int4
) to service_role;
grant execute on function public.complete_mercury_provider_revocation(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;
