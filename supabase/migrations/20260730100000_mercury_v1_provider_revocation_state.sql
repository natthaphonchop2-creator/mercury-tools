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
      provider_revocation_required = case
        when v_already_disconnected then connection.provider_revocation_required
        else connection.provider_revocation_required or p_provider_revocation_required
      end,
      disconnected_at = coalesce(connection.disconnected_at, v_now),
      updated_at = case
        when v_already_disconnected then connection.updated_at
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

revoke all on function public.disconnect_mercury_provider_connection(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.bool
) from public, anon, authenticated;

grant execute on function public.disconnect_mercury_provider_connection(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.bool
) to service_role;
