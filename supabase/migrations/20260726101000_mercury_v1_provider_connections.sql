create or replace function public.mercury_provider_permissions_are_safe(
  value pg_catalog.jsonb
)
returns pg_catalog.bool
language sql
immutable
set search_path = ''
as $$
  select case
    when value is null then false
    when pg_catalog.jsonb_typeof(value) <> 'array' then false
    when pg_catalog.jsonb_array_length(value) > 100 then false
    else
      not exists (
        select 1
        from pg_catalog.jsonb_array_elements(value) as permission(element)
        where pg_catalog.jsonb_typeof(permission.element) is distinct from 'string'
          or pg_catalog.length(permission.element #>> '{}') < 1
          or pg_catalog.length(permission.element #>> '{}') > 200
          or permission.element #>> '{}'
            !~ '^[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}$'
      )
      and not exists (
        select 1
        from (
          select
            permission.element #>> '{}' as value,
            pg_catalog.lag(permission.element #>> '{}') over (
              order by permission.ordinality
            ) as previous_value
          from pg_catalog.jsonb_array_elements(value)
            with ordinality as permission(element, ordinality)
        ) as ordered_permission
        where ordered_permission.previous_value is not null
          and ordered_permission.value <= ordered_permission.previous_value
      )
  end
$$;

create or replace function public.mercury_provider_callback_state_is_safe(
  value pg_catalog.jsonb
)
returns pg_catalog.bool
language sql
immutable
set search_path = ''
as $$
  select case
    when value is null then false
    when pg_catalog.jsonb_typeof(value) <> 'object' then false
    when value - array[
      'return_path',
      'requested_permissions',
      'connection_attempt_id'
    ] <> '{}'::pg_catalog.jsonb then false
    when value ? 'return_path'
      and (
        pg_catalog.jsonb_typeof(value -> 'return_path') <> 'string'
        or value ->> 'return_path' !~ '^/[A-Za-z0-9/_-]{0,255}$'
      )
      then false
    when value ? 'requested_permissions'
      and not public.mercury_provider_permissions_are_safe(
        value -> 'requested_permissions'
      )
      then false
    when value ? 'connection_attempt_id'
      and (
        pg_catalog.jsonb_typeof(value -> 'connection_attempt_id') <> 'string'
        or value ->> 'connection_attempt_id'
          !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
      )
      then false
    else true
  end
$$;

revoke all on function public.mercury_provider_permissions_are_safe(
  pg_catalog.jsonb
) from public, anon, authenticated;
revoke all on function public.mercury_provider_callback_state_is_safe(
  pg_catalog.jsonb
) from public, anon, authenticated;

grant execute on function public.mercury_provider_permissions_are_safe(
  pg_catalog.jsonb
) to service_role;
grant execute on function public.mercury_provider_callback_state_is_safe(
  pg_catalog.jsonb
) to service_role;

create table if not exists public.mercury_provider_connections (
  id pg_catalog.uuid primary key default gen_random_uuid(),
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  provider pg_catalog.text not null,
  environment pg_catalog.text not null,
  provider_account_id pg_catalog.text not null,
  account_display_name pg_catalog.text not null,
  authorization_method pg_catalog.text not null,
  granted_permissions pg_catalog.jsonb not null default '[]'::pg_catalog.jsonb,
  readiness pg_catalog.text not null default 'requires_validation',
  revision pg_catalog.int8 not null default 1,
  last_validated_at pg_catalog.timestamptz,
  provider_revocation_required pg_catalog.bool not null default false,
  disconnected_at pg_catalog.timestamptz,
  created_at pg_catalog.timestamptz not null default pg_catalog.now(),
  updated_at pg_catalog.timestamptz not null default pg_catalog.now(),
  constraint mercury_provider_connections_provider_check
    check (provider in ('flowaccount', 'peak')),
  constraint mercury_provider_connections_environment_check
    check (
      environment ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(environment) <= 64
    ),
  constraint mercury_provider_connections_account_id_check
    check (
      pg_catalog.length(provider_account_id) between 1 and 512
      and provider_account_id !~ '[[:cntrl:]]'
    ),
  constraint mercury_provider_connections_display_name_check
    check (
      pg_catalog.length(account_display_name) between 1 and 200
      and account_display_name !~ '[[:cntrl:]]'
    ),
  constraint mercury_provider_connections_authorization_method_check
    check (authorization_method in ('oauth2_pkce', 'provider_credentials')),
  constraint mercury_provider_connections_permissions_check
    check (public.mercury_provider_permissions_are_safe(granted_permissions)),
  constraint mercury_provider_connections_readiness_check
    check (
      readiness in (
        'requires_validation',
        'ready',
        'validation_failed',
        'requires_reauthorization',
        'disconnected'
      )
    ),
  constraint mercury_provider_connections_revision_check
    check (revision >= 1),
  constraint mercury_provider_connections_ready_check
    check (readiness <> 'ready' or last_validated_at is not null),
  constraint mercury_provider_connections_disconnect_check
    check (
      (readiness = 'disconnected' and disconnected_at is not null)
      or (readiness <> 'disconnected' and disconnected_at is null)
    ),
  unique (
    tenant_id,
    workspace_id,
    auth_user_id,
    provider,
    environment,
    provider_account_id
  )
);

create table if not exists public.mercury_provider_setup_attempts (
  id pg_catalog.uuid primary key,
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  provider pg_catalog.text not null,
  environment pg_catalog.text not null,
  token_hash pg_catalog.text not null unique,
  expires_at pg_catalog.timestamptz not null,
  consumed_at pg_catalog.timestamptz,
  created_at pg_catalog.timestamptz not null default pg_catalog.now(),
  constraint mercury_provider_setup_attempts_provider_check
    check (provider in ('flowaccount', 'peak')),
  constraint mercury_provider_setup_attempts_environment_check
    check (
      environment ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(environment) <= 64
    ),
  constraint mercury_provider_setup_attempts_token_hash_check
    check (token_hash ~ '^[0-9a-f]{64}$'),
  constraint mercury_provider_setup_attempts_expiry_check
    check (
      expires_at > created_at
      and expires_at <= created_at + pg_catalog.make_interval(mins => 10)
    ),
  constraint mercury_provider_setup_attempts_consumed_check
    check (consumed_at is null or consumed_at >= created_at)
);

create table if not exists public.mercury_provider_oauth_states (
  id pg_catalog.uuid primary key,
  setup_attempt_id pg_catalog.uuid not null
    references public.mercury_provider_setup_attempts(id) on delete cascade,
  tenant_id pg_catalog.uuid not null
    references public.mercury_tenants(id) on delete cascade,
  workspace_id pg_catalog.uuid not null
    references public.mercury_workspaces(id) on delete cascade,
  auth_user_id pg_catalog.uuid not null,
  provider pg_catalog.text not null,
  environment pg_catalog.text not null,
  state_hash pg_catalog.text not null unique,
  pkce_verifier_ciphertext pg_catalog.bytea,
  pkce_key_version pg_catalog.text,
  pkce_nonce pg_catalog.bytea,
  pkce_aad_hash pg_catalog.bytea,
  callback_state pg_catalog.jsonb not null default '{}'::pg_catalog.jsonb,
  expires_at pg_catalog.timestamptz not null,
  consumed_at pg_catalog.timestamptz,
  created_at pg_catalog.timestamptz not null default pg_catalog.now(),
  constraint mercury_provider_oauth_states_provider_check
    check (provider = 'flowaccount'),
  constraint mercury_provider_oauth_states_environment_check
    check (
      environment ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      and pg_catalog.length(environment) <= 64
    ),
  constraint mercury_provider_oauth_states_state_hash_check
    check (state_hash ~ '^[0-9a-f]{64}$'),
  constraint mercury_provider_oauth_states_pkce_check
    check (
      (
        pkce_verifier_ciphertext is null
        and pkce_key_version is null
        and pkce_nonce is null
        and pkce_aad_hash is null
      )
      or (
        pg_catalog.octet_length(pkce_verifier_ciphertext) >= 16
        and pkce_key_version is not null
        and pkce_key_version ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        and pg_catalog.length(pkce_key_version) <= 64
        and pg_catalog.octet_length(pkce_nonce) = 12
        and pg_catalog.octet_length(pkce_aad_hash) = 32
      )
    ),
  constraint mercury_provider_oauth_states_callback_check
    check (public.mercury_provider_callback_state_is_safe(callback_state)),
  constraint mercury_provider_oauth_states_expiry_check
    check (
      expires_at > created_at
      and expires_at <= created_at + pg_catalog.make_interval(mins => 10)
    ),
  constraint mercury_provider_oauth_states_consumed_check
    check (consumed_at is null or consumed_at >= created_at)
);

create index if not exists mercury_provider_connections_workspace_idx
  on public.mercury_provider_connections (
    tenant_id,
    workspace_id,
    auth_user_id,
    provider,
    environment
  );

create index if not exists mercury_provider_setup_attempts_binding_idx
  on public.mercury_provider_setup_attempts (
    tenant_id,
    workspace_id,
    auth_user_id,
    expires_at
  );

create index if not exists mercury_provider_oauth_states_binding_idx
  on public.mercury_provider_oauth_states (
    tenant_id,
    workspace_id,
    auth_user_id,
    expires_at
  );

create unique index if not exists
  mercury_provider_oauth_states_setup_attempt_uidx
  on public.mercury_provider_oauth_states (setup_attempt_id);

alter table public.mercury_provider_connections enable row level security;
alter table public.mercury_provider_setup_attempts enable row level security;
alter table public.mercury_provider_oauth_states enable row level security;

revoke all on table public.mercury_provider_connections
  from public, anon, authenticated;
revoke all on table public.mercury_provider_setup_attempts
  from public, anon, authenticated;
revoke all on table public.mercury_provider_oauth_states
  from public, anon, authenticated;

grant all on table public.mercury_provider_connections to service_role;
grant all on table public.mercury_provider_setup_attempts to service_role;
grant all on table public.mercury_provider_oauth_states to service_role;

create or replace function public.mercury_assert_provider_workspace_access(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid
)
returns pg_catalog.void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_auth_user_id pg_catalog.uuid;
begin
  v_auth_user_id := auth.uid();
  if v_auth_user_id is null then
    raise insufficient_privilege
      using message = 'mercury_auth_required';
  end if;
  if p_auth_user_id is distinct from v_auth_user_id then
    raise insufficient_privilege
      using message = 'workspace_access_denied';
  end if;
  if not exists (
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
  ) then
    raise insufficient_privilege
      using message = 'workspace_access_denied';
  end if;
end;
$$;

revoke all on function public.mercury_assert_provider_workspace_access(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;
grant execute on function public.mercury_assert_provider_workspace_access(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;

create or replace function public.create_mercury_provider_setup_attempt(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_token_hash pg_catalog.text,
  p_expires_at pg_catalog.timestamptz
)
returns table (
  attempt_id pg_catalog.uuid,
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
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_attempt_id is null
    or p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_attempt_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_provider is null
    or p_environment is null
    or p_token_hash is null
    or p_expires_at is null
    or p_provider not in ('flowaccount', 'peak')
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or p_token_hash !~ '^[0-9a-f]{64}$'
    or p_expires_at <= v_now
    or p_expires_at > v_now + pg_catalog.make_interval(mins => 10)
  then
    raise invalid_parameter_value
      using message = 'provider_setup_attempt_invalid';
  end if;
  perform public.mercury_assert_provider_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  begin
    insert into public.mercury_provider_setup_attempts (
      id,
      tenant_id,
      workspace_id,
      auth_user_id,
      provider,
      environment,
      token_hash,
      expires_at,
      created_at
    )
    values (
      p_attempt_id,
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id,
      p_provider,
      p_environment,
      p_token_hash,
      p_expires_at,
      v_now
    )
    returning * into v_attempt;
  exception
    when unique_violation then
      raise unique_violation
        using message = 'provider_setup_attempt_conflict';
    when not_null_violation
      or check_violation
      or foreign_key_violation
      or invalid_text_representation
      or datetime_field_overflow
    then
      raise invalid_parameter_value
        using message = 'provider_setup_attempt_invalid';
  end;

  return query
  select
    v_attempt.id,
    v_attempt.tenant_id,
    v_attempt.workspace_id,
    v_attempt.auth_user_id,
    v_attempt.provider,
    v_attempt.environment,
    v_attempt.expires_at,
    v_attempt.consumed_at,
    v_attempt.created_at;
end;
$$;

create or replace function public.consume_mercury_provider_setup_attempt(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_token_hash pg_catalog.text
)
returns table (
  attempt_id pg_catalog.uuid,
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
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_provider is null
    or p_environment is null
    or p_token_hash is null
    or p_provider not in ('flowaccount', 'peak')
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or p_token_hash !~ '^[0-9a-f]{64}$'
  then
    raise invalid_parameter_value
      using message = 'provider_setup_attempt_invalid';
  end if;
  perform public.mercury_assert_provider_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  update public.mercury_provider_setup_attempts as attempt
  set consumed_at = v_now
  where attempt.tenant_id = p_tenant_id
    and attempt.workspace_id = p_workspace_id
    and attempt.auth_user_id = p_auth_user_id
    and attempt.provider = p_provider
    and attempt.environment = p_environment
    and attempt.token_hash = p_token_hash
    and attempt.consumed_at is null
    and attempt.expires_at > v_now
  returning attempt.* into v_attempt;

  if not found then
    raise invalid_parameter_value
      using message = 'provider_setup_attempt_invalid';
  end if;

  return query
  select
    v_attempt.id,
    v_attempt.tenant_id,
    v_attempt.workspace_id,
    v_attempt.auth_user_id,
    v_attempt.provider,
    v_attempt.environment,
    v_attempt.expires_at,
    v_attempt.consumed_at,
    v_attempt.created_at;
end;
$$;

create or replace function public.create_mercury_provider_oauth_state(
  p_state_id pg_catalog.uuid,
  p_setup_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_state_hash pg_catalog.text,
  p_pkce_verifier_ciphertext pg_catalog.bytea,
  p_pkce_key_version pg_catalog.text,
  p_pkce_nonce pg_catalog.bytea,
  p_pkce_aad_hash pg_catalog.bytea,
  p_callback_state pg_catalog.jsonb,
  p_expires_at pg_catalog.timestamptz
)
returns table (
  oauth_state_id pg_catalog.uuid,
  setup_attempt_id pg_catalog.uuid,
  expires_at pg_catalog.timestamptz,
  created_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_state public.mercury_provider_oauth_states%rowtype;
  v_attempt public.mercury_provider_setup_attempts%rowtype;
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
begin
  if p_state_id is null
    or p_setup_attempt_id is null
    or p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_state_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_setup_attempt_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_provider is null
    or p_environment is null
    or p_state_hash is null
    or p_pkce_verifier_ciphertext is null
    or p_pkce_key_version is null
    or p_pkce_nonce is null
    or p_pkce_aad_hash is null
    or p_callback_state is null
    or p_expires_at is null
    or p_provider <> 'flowaccount'
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
    or p_state_hash !~ '^[0-9a-f]{64}$'
    or not public.mercury_provider_callback_state_is_safe(p_callback_state)
    or p_expires_at <= v_now
    or p_expires_at > v_now + pg_catalog.make_interval(mins => 10)
    or (
      pg_catalog.octet_length(p_pkce_verifier_ciphertext) < 16
      or p_pkce_key_version !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
      or pg_catalog.length(p_pkce_key_version) > 64
      or pg_catalog.octet_length(p_pkce_nonce) <> 12
      or pg_catalog.octet_length(p_pkce_aad_hash) <> 32
    )
  then
    raise invalid_parameter_value
      using message = 'provider_oauth_state_invalid';
  end if;
  perform public.mercury_assert_provider_workspace_access(
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  );

  update public.mercury_provider_setup_attempts as attempt
  set consumed_at = v_now
  where attempt.id = p_setup_attempt_id
    and attempt.tenant_id = p_tenant_id
    and attempt.workspace_id = p_workspace_id
    and attempt.auth_user_id = p_auth_user_id
    and attempt.provider = p_provider
    and attempt.environment = p_environment
    and attempt.consumed_at is null
    and attempt.expires_at > v_now
  returning attempt.* into v_attempt;

  if not found then
    raise invalid_parameter_value
      using message = 'provider_oauth_state_invalid';
  end if;

  begin
    insert into public.mercury_provider_oauth_states (
      id,
      setup_attempt_id,
      tenant_id,
      workspace_id,
      auth_user_id,
      provider,
      environment,
      state_hash,
      pkce_verifier_ciphertext,
      pkce_key_version,
      pkce_nonce,
      pkce_aad_hash,
      callback_state,
      expires_at,
      created_at
    )
    values (
      p_state_id,
      p_setup_attempt_id,
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id,
      p_provider,
      p_environment,
      p_state_hash,
      p_pkce_verifier_ciphertext,
      p_pkce_key_version,
      p_pkce_nonce,
      p_pkce_aad_hash,
      p_callback_state,
      p_expires_at,
      v_now
    )
    returning * into v_state;
  exception
    when unique_violation then
      raise unique_violation
        using message = 'provider_oauth_state_conflict';
    when not_null_violation
      or check_violation
      or foreign_key_violation
      or invalid_text_representation
      or datetime_field_overflow
    then
      raise invalid_parameter_value
        using message = 'provider_oauth_state_invalid';
  end;

  return query
  select
    v_state.id,
    v_state.setup_attempt_id,
    v_state.expires_at,
    v_state.created_at;
end;
$$;

create or replace function public.consume_mercury_provider_oauth_state(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_state_hash pg_catalog.text
)
returns table (
  oauth_state_id pg_catalog.uuid,
  setup_attempt_id pg_catalog.uuid,
  pkce_verifier_ciphertext pg_catalog.bytea,
  pkce_key_version pg_catalog.text,
  pkce_nonce pg_catalog.bytea,
  pkce_aad_hash pg_catalog.bytea,
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
    or p_provider is null
    or p_environment is null
    or p_state_hash is null
    or p_provider <> 'flowaccount'
    or p_environment !~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
    or pg_catalog.length(p_environment) > 64
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
  select
    v_state.id,
    v_state.setup_attempt_id,
    v_state.pkce_verifier_ciphertext,
    v_state.pkce_key_version,
    v_state.pkce_nonce,
    v_state.pkce_aad_hash,
    v_state.callback_state,
    v_now;
end;
$$;

revoke all on function public.create_mercury_provider_setup_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.timestamptz
) from public, anon, authenticated;
revoke all on function public.consume_mercury_provider_setup_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;
revoke all on function public.create_mercury_provider_oauth_state(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.bytea,
  pg_catalog.text,
  pg_catalog.bytea,
  pg_catalog.bytea,
  pg_catalog.jsonb,
  pg_catalog.timestamptz
) from public, anon, authenticated;
revoke all on function public.consume_mercury_provider_oauth_state(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;

grant execute on function public.create_mercury_provider_setup_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.timestamptz
) to authenticated;
grant execute on function public.consume_mercury_provider_setup_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) to authenticated;
grant execute on function public.create_mercury_provider_oauth_state(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.bytea,
  pg_catalog.text,
  pg_catalog.bytea,
  pg_catalog.bytea,
  pg_catalog.jsonb,
  pg_catalog.timestamptz
) to authenticated;
grant execute on function public.consume_mercury_provider_oauth_state(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.text
) to authenticated;
