create table if not exists public.mercury_peak_setup_sessions (
  id pg_catalog.uuid primary key,
  setup_attempt_id pg_catalog.uuid not null unique
    references public.mercury_provider_setup_attempts(id) on delete cascade,
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  provider pg_catalog.text not null,
  environment pg_catalog.text not null,
  session_hash pg_catalog.text not null unique,
  csrf_hash pg_catalog.text not null unique,
  expires_at pg_catalog.timestamptz not null,
  consumed_at pg_catalog.timestamptz,
  created_at pg_catalog.timestamptz not null default pg_catalog.now(),
  constraint mercury_peak_setup_sessions_provider_check
    check (provider = 'peak'),
  constraint mercury_peak_setup_sessions_environment_check
    check (
      environment ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(environment) <= 64
    ),
  constraint mercury_peak_setup_sessions_session_hash_check
    check (session_hash ~ '^[0-9a-f]{64}$'),
  constraint mercury_peak_setup_sessions_csrf_hash_check
    check (csrf_hash ~ '^[0-9a-f]{64}$'),
  constraint mercury_peak_setup_sessions_expiry_check
    check (
      expires_at > created_at
      and expires_at <= created_at + pg_catalog.make_interval(mins => 10)
    ),
  constraint mercury_peak_setup_sessions_consumed_check
    check (consumed_at is null or consumed_at >= created_at)
);

create index if not exists mercury_peak_setup_sessions_binding_idx
  on public.mercury_peak_setup_sessions (
    tenant_id,
    workspace_id,
    auth_user_id,
    provider,
    environment
  );

alter table public.mercury_peak_setup_sessions enable row level security;

revoke all on table public.mercury_peak_setup_sessions
  from public, anon, authenticated;
grant all on table public.mercury_peak_setup_sessions to service_role;

create or replace function public.mercury_peak_hash_matches(
  p_left pg_catalog.text,
  p_right pg_catalog.text
)
returns pg_catalog.bool
language plpgsql
immutable
parallel safe
set search_path = ''
as $$
declare
  v_difference pg_catalog.int4 := 0;
  v_index pg_catalog.int4;
begin
  if p_left is null
    or p_right is null
    or p_left !~ '^[0-9a-f]{64}$'
    or p_right !~ '^[0-9a-f]{64}$'
  then
    return false;
  end if;

  for v_index in 1..64 loop
    v_difference := v_difference | (
      pg_catalog.ascii(pg_catalog.substr(p_left, v_index, 1))
      # pg_catalog.ascii(pg_catalog.substr(p_right, v_index, 1))
    );
  end loop;
  return v_difference = 0;
end;
$$;

create or replace function public.exchange_mercury_peak_setup_attempt(
  p_session_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_token_hash pg_catalog.text,
  p_session_hash pg_catalog.text,
  p_csrf_hash pg_catalog.text
)
returns table (
  session_id pg_catalog.uuid,
  setup_attempt_id pg_catalog.uuid,
  tenant_id pg_catalog.uuid,
  workspace_id pg_catalog.uuid,
  auth_user_id pg_catalog.uuid,
  provider pg_catalog.text,
  environment pg_catalog.text,
  expires_at pg_catalog.timestamptz,
  consumed_at pg_catalog.timestamptz,
  created_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_setup_attempts%rowtype;
  v_session public.mercury_peak_setup_sessions%rowtype;
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_session_id is null
    or p_auth_user_id is null
    or p_session_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_token_hash !~ '^[0-9a-f]{64}$'
    or p_session_hash !~ '^[0-9a-f]{64}$'
    or p_csrf_hash !~ '^[0-9a-f]{64}$'
    or p_token_hash = p_session_hash
    or p_token_hash = p_csrf_hash
    or p_session_hash = p_csrf_hash
    or auth.uid() is distinct from p_auth_user_id
  then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  select attempt.*
  into v_attempt
  from public.mercury_provider_setup_attempts as attempt
  where attempt.auth_user_id = p_auth_user_id
    and attempt.provider = 'peak'
    and attempt.consumed_at is null
    and attempt.expires_at > v_now
    and public.mercury_peak_hash_matches(
      attempt.token_hash,
      p_token_hash
    )
  for update;

  if not found then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  perform public.mercury_assert_provider_workspace_access(
    v_attempt.tenant_id,
    v_attempt.workspace_id,
    v_attempt.auth_user_id
  );

  begin
    insert into public.mercury_peak_setup_sessions (
      id,
      setup_attempt_id,
      tenant_id,
      workspace_id,
      auth_user_id,
      provider,
      environment,
      session_hash,
      csrf_hash,
      expires_at,
      created_at
    )
    values (
      p_session_id,
      v_attempt.id,
      v_attempt.tenant_id,
      v_attempt.workspace_id,
      v_attempt.auth_user_id,
      v_attempt.provider,
      v_attempt.environment,
      p_session_hash,
      p_csrf_hash,
      v_attempt.expires_at,
      v_now
    )
    returning * into v_session;
  exception
    when unique_violation
      or not_null_violation
      or check_violation
      or foreign_key_violation
      or invalid_text_representation
      or datetime_field_overflow
    then
      raise invalid_parameter_value
        using message = 'peak_setup_state_invalid';
  end;

  return query
  select
    v_session.id,
    v_session.setup_attempt_id,
    v_session.tenant_id,
    v_session.workspace_id,
    v_session.auth_user_id,
    v_session.provider,
    v_session.environment,
    v_session.expires_at,
    v_session.consumed_at,
    v_session.created_at;
end;
$$;

create or replace function public.peek_mercury_peak_setup_session(
  p_auth_user_id pg_catalog.uuid,
  p_session_hash pg_catalog.text
)
returns table (
  session_id pg_catalog.uuid,
  setup_attempt_id pg_catalog.uuid,
  tenant_id pg_catalog.uuid,
  workspace_id pg_catalog.uuid,
  auth_user_id pg_catalog.uuid,
  provider pg_catalog.text,
  environment pg_catalog.text,
  expires_at pg_catalog.timestamptz,
  consumed_at pg_catalog.timestamptz,
  created_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_session public.mercury_peak_setup_sessions%rowtype;
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_auth_user_id is null
    or p_auth_user_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_session_hash !~ '^[0-9a-f]{64}$'
  then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  select session.*
  into v_session
  from public.mercury_peak_setup_sessions as session
  where session.auth_user_id = p_auth_user_id
    and session.provider = 'peak'
    and session.consumed_at is null
    and session.expires_at > v_now
    and public.mercury_peak_hash_matches(
      session.session_hash,
      p_session_hash
    );

  if not found then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  perform public.mercury_assert_provider_backend_workspace_access(
    v_session.tenant_id,
    v_session.workspace_id,
    v_session.auth_user_id
  );

  return query
  select
    v_session.id,
    v_session.setup_attempt_id,
    v_session.tenant_id,
    v_session.workspace_id,
    v_session.auth_user_id,
    v_session.provider,
    v_session.environment,
    v_session.expires_at,
    v_session.consumed_at,
    v_session.created_at;
end;
$$;

create or replace function public.list_mercury_provider_connections_backend(
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
    or p_tenant_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id
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
    and connection.provider_account_id
      <> 'oauth-pending-' || connection.id::pg_catalog.text
    and (
      (
        connection.oauth_generation_id is null
        and not exists (
          select 1
          from public.mercury_provider_oauth_attempts as history
          where history.target_connection_id = connection.id
        )
      )
      or exists (
        select 1
        from public.mercury_provider_oauth_attempts as attempt
        where attempt.id = connection.oauth_generation_id
          and attempt.target_connection_id = connection.id
          and attempt.status = 'finalized'
          and attempt.acknowledged_at is not null
      )
    )
  order by
    connection.provider,
    connection.environment,
    connection.account_display_name,
    connection.id;
end;
$$;

create or replace function public.finalize_mercury_peak_setup(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_session_hash pg_catalog.text,
  p_csrf_hash pg_catalog.text,
  p_connection_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_provider_account_id pg_catalog.text,
  p_account_display_name pg_catalog.text,
  p_granted_permissions pg_catalog.jsonb,
  p_revision pg_catalog.int8,
  p_last_validated_at pg_catalog.timestamptz,
  p_envelopes pg_catalog.jsonb
)
returns table (
  connection_id pg_catalog.uuid,
  revision pg_catalog.int8,
  created_at pg_catalog.timestamptz,
  updated_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_session public.mercury_peak_setup_sessions%rowtype;
  v_attempt public.mercury_provider_setup_attempts%rowtype;
  v_connection record;
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
  v_updated pg_catalog.int4;
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_connection_id is null
    or p_tenant_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_connection_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_session_hash !~ '^[0-9a-f]{64}$'
    or p_csrf_hash !~ '^[0-9a-f]{64}$'
    or p_session_hash = p_csrf_hash
    or p_provider is distinct from 'peak'
    or p_environment is null
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or p_provider_account_id is null
    or pg_catalog.length(p_provider_account_id) not between 1 and 512
    or p_provider_account_id ~ '[[:cntrl:]]'
    or p_account_display_name is null
    or pg_catalog.length(p_account_display_name) not between 1 and 200
    or p_account_display_name ~ '[[:cntrl:]]'
    or p_granted_permissions is distinct from '["profile.read"]'::pg_catalog.jsonb
    or p_revision is null
    or p_revision < 1
    or p_last_validated_at is null
    or p_envelopes is null
    or pg_catalog.jsonb_typeof(p_envelopes) <> 'array'
    or pg_catalog.jsonb_array_length(p_envelopes) <> 3
    or (
      select pg_catalog.count(distinct envelope.item ->> 'credential_type')
      from pg_catalog.jsonb_array_elements(p_envelopes) as envelope(item)
      where envelope.item ->> 'credential_type'
        in ('user_token', 'connect_id', 'connect_key')
    ) <> 3
  then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  select session.*
  into v_session
  from public.mercury_peak_setup_sessions as session
  where session.provider = 'peak'
    and session.consumed_at is null
    and session.expires_at > v_now
    and public.mercury_peak_hash_matches(
      session.session_hash,
      p_session_hash
    )
  for update;

  if not found
    or v_session.tenant_id <> p_tenant_id
    or v_session.workspace_id <> p_workspace_id
    or v_session.auth_user_id <> p_auth_user_id
    or v_session.provider <> p_provider
    or v_session.environment <> p_environment
    or not public.mercury_peak_hash_matches(
      v_session.csrf_hash,
      p_csrf_hash
    )
  then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  select attempt.*
  into v_attempt
  from public.mercury_provider_setup_attempts as attempt
  where attempt.id = v_session.setup_attempt_id
  for update;

  if not found
    or v_attempt.tenant_id <> v_session.tenant_id
    or v_attempt.workspace_id <> v_session.workspace_id
    or v_attempt.auth_user_id <> v_session.auth_user_id
    or v_attempt.provider <> v_session.provider
    or v_attempt.environment <> v_session.environment
    or v_attempt.consumed_at is not null
    or v_attempt.expires_at <= v_now
  then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  perform public.mercury_assert_provider_backend_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  select saved.*
  into v_connection
  from public.save_mercury_provider_connection(
    p_connection_id,
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id,
    'peak',
    p_environment,
    p_provider_account_id,
    p_account_display_name,
    'provider_credentials',
    p_granted_permissions,
    'ready',
    p_revision,
    p_last_validated_at,
    p_envelopes
  ) as saved;

  if not found
    or v_connection.connection_id <> p_connection_id
    or v_connection.provider <> 'peak'
    or v_connection.environment <> p_environment
    or v_connection.authorization_method <> 'provider_credentials'
    or v_connection.granted_permissions <> '["profile.read"]'::pg_catalog.jsonb
    or v_connection.readiness <> 'ready'
    or v_connection.revision <> p_revision
    or v_connection.provider_revocation_required
  then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  update public.mercury_peak_setup_sessions as session
  set consumed_at = v_now
  where session.id = v_session.id
    and session.consumed_at is null;
  get diagnostics v_updated = row_count;
  if v_updated <> 1 then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  update public.mercury_provider_setup_attempts as attempt
  set consumed_at = v_now
  where attempt.id = v_attempt.id
    and attempt.consumed_at is null;
  get diagnostics v_updated = row_count;
  if v_updated <> 1 then
    raise invalid_parameter_value
      using message = 'peak_setup_state_invalid';
  end if;

  return query
  select
    v_connection.connection_id,
    v_connection.revision,
    v_connection.created_at,
    v_connection.updated_at;
end;
$$;

revoke all on function public.mercury_peak_hash_matches(
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.exchange_mercury_peak_setup_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.peek_mercury_peak_setup_session(
  pg_catalog.uuid,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.list_mercury_provider_connections_backend(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;
revoke all on function public.finalize_mercury_peak_setup(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb,
  pg_catalog.int8,
  pg_catalog.timestamptz,
  pg_catalog.jsonb
) from public, anon, authenticated;

grant execute on function public.exchange_mercury_peak_setup_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) to authenticated;
grant execute on function public.peek_mercury_peak_setup_session(
  pg_catalog.uuid,
  pg_catalog.text
) to service_role;
grant execute on function public.list_mercury_provider_connections_backend(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;
grant execute on function public.finalize_mercury_peak_setup(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.jsonb,
  pg_catalog.int8,
  pg_catalog.timestamptz,
  pg_catalog.jsonb
) to service_role;
