create or replace function public.load_mercury_provider_connection_backend(
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_connection_id pg_catalog.uuid
)
returns table (
  connection_id pg_catalog.uuid,
  provider pg_catalog.text,
  environment pg_catalog.text,
  provider_account_id pg_catalog.text,
  account_display_name pg_catalog.text,
  authorization_method pg_catalog.text,
  granted_permissions pg_catalog.jsonb,
  readiness pg_catalog.text,
  revision pg_catalog.int8,
  last_validated_at pg_catalog.timestamptz,
  credential_envelope_ids pg_catalog.uuid[],
  provider_revocation_required pg_catalog.bool,
  disconnected_at pg_catalog.timestamptz,
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
  if p_tenant_id is null
    or p_workspace_id is null
    or p_auth_user_id is null
    or p_connection_id is null
    or p_tenant_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_connection_id = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
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
    );

  if not found then
    raise invalid_parameter_value
      using message = 'provider_connection_not_found';
  end if;

  return query
  select
    v_connection.id,
    v_connection.provider,
    v_connection.environment,
    v_connection.provider_account_id,
    v_connection.account_display_name,
    v_connection.authorization_method,
    v_connection.granted_permissions,
    v_connection.readiness,
    v_connection.revision,
    v_connection.last_validated_at,
    v_connection.credential_envelope_ids,
    v_connection.provider_revocation_required,
    v_connection.disconnected_at,
    v_connection.created_at,
    v_connection.updated_at;
end;
$$;

revoke all on function public.load_mercury_provider_connection_backend(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) from public, anon, authenticated;

grant execute on function public.load_mercury_provider_connection_backend(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid
) to service_role;
