-- Durable OAuth credential generations stay held until callback acknowledgement.

alter table public.mercury_provider_connections
  add column if not exists oauth_generation_id pg_catalog.uuid;

alter table public.mercury_provider_oauth_attempts
  add column if not exists material_revision pg_catalog.int8 not null default 0;

alter table public.mercury_provider_oauth_attempts
  add column if not exists acknowledged_at pg_catalog.timestamptz;

create index if not exists mercury_provider_connections_oauth_generation_idx
  on public.mercury_provider_connections (oauth_generation_id)
  where oauth_generation_id is not null;

create index if not exists mercury_provider_oauth_attempts_target_connection_idx
  on public.mercury_provider_oauth_attempts (target_connection_id)
  where target_connection_id is not null;

update public.mercury_provider_oauth_attempts as attempt
set material_revision = case
      when attempt.status = 'exchange_pending' then 0
      when attempt.material_revision < 1 then 1
      else attempt.material_revision
    end;

-- Select one proven legacy owner before acknowledging or assigning anything.
-- The UUID tie-breaker makes evaluation stable but never resolves two attempts
-- at the same ownership revision; equal latest revisions remain ambiguous.
with upgrade_targets as materialized (
  select connection.*
  from public.mercury_provider_connections as connection
  where connection.oauth_generation_id is null
    and exists (
      select 1
      from public.mercury_provider_oauth_attempts as history
      where history.target_connection_id = connection.id
    )
),
binding_compatible_candidates as materialized (
  select
    target.id as connection_id,
    attempt.id as attempt_id,
    attempt.status,
    attempt.target_revision,
    attempt.updated_at,
    attempt.created_at
  from upgrade_targets as target
  join public.mercury_provider_oauth_attempts as attempt
    on attempt.target_connection_id = target.id
   and attempt.tenant_id = target.tenant_id
   and attempt.workspace_id = target.workspace_id
   and attempt.auth_user_id = target.auth_user_id
   and attempt.provider = target.provider
   and attempt.environment = target.environment
   and (
     attempt.provider_account_id = target.provider_account_id
     or (
       attempt.status = 'finalized'
       and attempt.provider_account_id
         = 'oauth-pending-' || attempt.id::pg_catalog.text
     )
   )
   and attempt.authorization_method = target.authorization_method
   and attempt.granted_permissions = target.granted_permissions
  where (
      target.readiness = 'ready'
      and not target.provider_revocation_required
      and target.credential_envelope_ids <> '{}'::pg_catalog.uuid[]
      and attempt.status = 'finalized'
      and not attempt.provider_revocation_required
      and attempt.target_revision <= target.revision
    )
    or (
      target.readiness = 'disconnected'
      and target.provider_revocation_required
      and attempt.status = 'failed'
      and attempt.provider_revocation_required
      and attempt.target_revision = target.revision
    )
),
ranked_candidates as materialized (
  select
    candidate.*,
    pg_catalog.dense_rank() over (
      partition by candidate.connection_id
      order by candidate.target_revision desc
    ) as ownership_rank,
    pg_catalog.count(*) over (
      partition by candidate.connection_id, candidate.target_revision
    ) as ownership_rank_size,
    pg_catalog.row_number() over (
      partition by candidate.connection_id
      order by
        candidate.target_revision desc,
        candidate.updated_at desc,
        candidate.created_at desc,
        candidate.attempt_id
    ) as stable_order
  from binding_compatible_candidates as candidate
),
selected_candidates as materialized (
  select candidate.*
  from ranked_candidates as candidate
  where candidate.ownership_rank = 1
    and candidate.ownership_rank_size = 1
    and candidate.stable_order = 1
),
acknowledged_candidates as (
  update public.mercury_provider_oauth_attempts as attempt
  set acknowledged_at = coalesce(
        attempt.acknowledged_at,
        attempt.updated_at
      )
  from selected_candidates as selected
  where selected.status = 'finalized'
    and attempt.id = selected.attempt_id
  returning attempt.id
)
update public.mercury_provider_connections as connection
set oauth_generation_id = selected.attempt_id,
    readiness = case
      when target.readiness = 'ready'
        and selected.attempt_id is null
      then 'requires_validation'
      else connection.readiness
    end,
    updated_at = case
      when target.readiness = 'ready'
        and selected.attempt_id is null
      then pg_catalog.statement_timestamp()
      else connection.updated_at
    end
from upgrade_targets as target
left join selected_candidates as selected
  on selected.connection_id = target.id
where connection.id = target.id
  and connection.oauth_generation_id is null
  and (
    selected.attempt_id is not null
    or target.readiness = 'ready'
  )
  and (
    selected.status = 'failed'
    or selected.attempt_id is null
    or exists (
      select 1
      from acknowledged_candidates as acknowledged
      where acknowledged.id = selected.attempt_id
    )
  );

do $$
begin
  if not exists (
    select 1
    from pg_catalog.pg_constraint as constraint_record
    where constraint_record.conname
      = 'mercury_provider_oauth_attempts_generation_check'
      and constraint_record.conrelid
        = 'public.mercury_provider_oauth_attempts'::pg_catalog.regclass
  ) then
    alter table public.mercury_provider_oauth_attempts
      add constraint mercury_provider_oauth_attempts_generation_check
      check (
        material_revision >= 0
        and (
          status <> 'exchange_pending'
          or material_revision = 0
        )
        and (
          status not in ('material_attached', 'finalized')
          or material_revision >= 1
        )
        and (
          acknowledged_at is null
          or target_connection_id is not null
        )
      );
  end if;
end;
$$;

-- Convert only the exact legacy staging discriminator. Similar customer values
-- remain ordinary provider accounts.
do $$
declare
  v_connection public.mercury_provider_connections%rowtype;
  v_envelopes pg_catalog.jsonb;
  v_persisted_envelope_ids pg_catalog.uuid[];
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
begin
  for v_connection in
    select connection.*
    from public.mercury_provider_connections as connection
    where connection.provider = 'flowaccount'
      and connection.authorization_method = 'oauth2_pkce'
      and connection.provider_account_id
        = 'oauth-pending-' || connection.id::pg_catalog.text
    for update
  loop
    select
      coalesce(
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
      ),
      coalesce(
        pg_catalog.array_agg(
          envelope.id
          order by envelope.credential_type, envelope.id
        ) filter (where envelope.id is not null),
        '{}'::pg_catalog.uuid[]
      )
    into v_envelopes, v_persisted_envelope_ids
    from public.mercury_provider_credential_envelopes as envelope
    where envelope.connection_id = v_connection.id
      and envelope.tenant_id = v_connection.tenant_id
      and envelope.workspace_id = v_connection.workspace_id
      and envelope.auth_user_id = v_connection.auth_user_id;

    if pg_catalog.cardinality(v_connection.credential_envelope_ids)
      <> pg_catalog.cardinality(v_persisted_envelope_ids)
      or not (
        v_connection.credential_envelope_ids
        @> v_persisted_envelope_ids
        and v_connection.credential_envelope_ids
        <@ v_persisted_envelope_ids
      )
    then
      raise integrity_constraint_violation
        using message = 'provider_credential_binding_invalid';
    end if;

    if v_connection.provider_revocation_required
      or v_envelopes <> '[]'::pg_catalog.jsonb
    then
      insert into public.mercury_provider_oauth_attempts (
        id,
        tenant_id,
        workspace_id,
        auth_user_id,
        provider,
        environment,
        granted_permissions,
        status,
        provider_account_id,
        account_display_name,
        authorization_method,
        credential_envelopes,
        material_revision,
        target_connection_id,
        target_revision,
        acknowledged_at,
        provider_revocation_required,
        created_at,
        updated_at
      )
      values (
        v_connection.id,
        v_connection.tenant_id,
        v_connection.workspace_id,
        v_connection.auth_user_id,
        v_connection.provider,
        v_connection.environment,
        v_connection.granted_permissions,
        'failed',
        v_connection.provider_account_id,
        v_connection.account_display_name,
        v_connection.authorization_method,
        v_envelopes,
        case
          when v_envelopes = '[]'::pg_catalog.jsonb then 0
          when v_connection.revision < 1 then 1::pg_catalog.int8
          else v_connection.revision
        end,
        null,
        null,
        null,
        true,
        v_connection.created_at,
        v_connection.updated_at
      )
      on conflict (id) do nothing;

      select attempt.*
      into v_attempt
      from public.mercury_provider_oauth_attempts as attempt
      where attempt.id = v_connection.id
      for update;

      if not found
        or v_attempt.tenant_id <> v_connection.tenant_id
        or v_attempt.workspace_id <> v_connection.workspace_id
        or v_attempt.auth_user_id <> v_connection.auth_user_id
        or v_attempt.provider <> v_connection.provider
        or v_attempt.environment <> v_connection.environment
        or v_attempt.granted_permissions <> v_connection.granted_permissions
        or v_attempt.status <> 'failed'
        or v_attempt.provider_account_id <> v_connection.provider_account_id
        or v_attempt.credential_envelopes <> v_envelopes
        or not v_attempt.provider_revocation_required
      then
        raise unique_violation
          using message = 'provider_connection_conflict';
      end if;
    end if;

    delete from public.mercury_provider_credential_envelopes as envelope
    where envelope.connection_id = v_connection.id
      and envelope.tenant_id = v_connection.tenant_id
      and envelope.workspace_id = v_connection.workspace_id
      and envelope.auth_user_id = v_connection.auth_user_id;

    delete from public.mercury_provider_connections as connection
    where connection.id = v_connection.id
      and connection.provider_account_id
        = 'oauth-pending-' || connection.id::pg_catalog.text;
  end loop;
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
    or p_provider_account_id
      <> 'oauth-pending-' || p_attempt_id::pg_catalog.text
    or pg_catalog.length(p_account_display_name) not between 1 and 200
    or p_account_display_name ~ '[[:cntrl:]]'
    or p_authorization_method <> 'oauth2_pkce'
    or not public.mercury_provider_permissions_are_safe(
      p_granted_permissions
    )
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
      or v_attempt.material_revision <> 1
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
        material_revision = 1,
        acknowledged_at = null,
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

create or replace function
public.replace_mercury_provider_oauth_attempt_envelopes(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text,
  p_expected_revision pg_catalog.int8,
  p_envelopes pg_catalog.jsonb
)
returns table (
  attempt_id pg_catalog.uuid,
  material_revision pg_catalog.int8,
  credential_envelope_ids pg_catalog.uuid[],
  created_at pg_catalog.timestamptz,
  updated_at pg_catalog.timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_attempt public.mercury_provider_oauth_attempts%rowtype;
  v_envelope_ids pg_catalog.uuid[];
begin
  if p_attempt_id is null
    or p_expected_revision is null
    or p_expected_revision < 1
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

  if not found
    or v_attempt.status <> 'material_attached'
    or v_attempt.provider_account_id
      <> 'oauth-pending-' || p_attempt_id::pg_catalog.text
    or not v_attempt.provider_revocation_required
  then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;
  if v_attempt.material_revision = p_expected_revision + 1
    and v_attempt.credential_envelopes = p_envelopes
  then
    return query
    select
      v_attempt.id,
      v_attempt.material_revision,
      v_envelope_ids,
      v_attempt.created_at,
      v_attempt.updated_at;
    return;
  end if;
  if v_attempt.material_revision <> p_expected_revision then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  update public.mercury_provider_oauth_attempts as attempt
  set credential_envelopes = p_envelopes,
      material_revision = attempt.material_revision + 1,
      updated_at = pg_catalog.statement_timestamp()
  where attempt.id = p_attempt_id
  returning attempt.* into v_attempt;

  return query
  select
    v_attempt.id,
    v_attempt.material_revision,
    v_envelope_ids,
    v_attempt.created_at,
    v_attempt.updated_at;
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
begin
  if p_attempt_id is null
    or p_attempt_id = p_connection_id
    or p_readiness <> 'requires_validation'
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
    where connection.id = v_attempt.target_connection_id
    for update;
    if not found
      or v_attempt.target_connection_id <> p_connection_id
      or v_target.oauth_generation_id <> p_attempt_id
      or v_target.tenant_id <> p_tenant_id
      or v_target.workspace_id <> p_workspace_id
      or v_target.auth_user_id <> p_auth_user_id
      or v_target.provider <> p_provider
      or v_target.environment <> p_environment
      or v_target.provider_account_id <> p_provider_account_id
      or v_target.account_display_name <> p_account_display_name
      or v_target.authorization_method <> p_authorization_method
      or v_target.granted_permissions <> p_granted_permissions
      or v_target.last_validated_at <> p_last_validated_at
      or v_target.provider_revocation_required
      or v_target.credential_envelope_ids = '{}'::pg_catalog.uuid[]
      or v_target.revision < v_attempt.target_revision
      or (
        v_attempt.acknowledged_at is null
        and v_target.readiness <> 'requires_validation'
      )
      or (
        v_attempt.acknowledged_at is not null
        and v_target.readiness <> 'ready'
      )
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
    or v_attempt.material_revision < 1
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
    'requires_validation',
    p_revision,
    p_last_validated_at,
    p_envelopes
  );

  update public.mercury_provider_connections as connection
  set oauth_generation_id = p_attempt_id
  where connection.id = p_connection_id
  returning connection.* into v_target;

  update public.mercury_provider_oauth_attempts as attempt
  set status = 'finalized',
      provider_account_id = p_provider_account_id,
      account_display_name = p_account_display_name,
      authorization_method = p_authorization_method,
      credential_envelopes = '[]'::pg_catalog.jsonb,
      target_connection_id = p_connection_id,
      target_revision = v_target.revision,
      acknowledged_at = null,
      provider_revocation_required = false,
      updated_at = pg_catalog.statement_timestamp()
  where attempt.id = p_attempt_id;

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
end;
$$;

create or replace function public.acknowledge_mercury_provider_oauth_attempt(
  p_attempt_id pg_catalog.uuid,
  p_tenant_id pg_catalog.uuid,
  p_workspace_id pg_catalog.uuid,
  p_auth_user_id pg_catalog.uuid,
  p_provider pg_catalog.text,
  p_environment pg_catalog.text
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
  v_now pg_catalog.timestamptz := pg_catalog.statement_timestamp();
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

  if not found or v_attempt.status <> 'finalized' then
    raise no_data_found
      using message = 'provider_connection_not_found';
  end if;
  select connection.*
  into v_target
  from public.mercury_provider_connections as connection
  where connection.id = v_attempt.target_connection_id
  for update;

  if not found
    or v_target.oauth_generation_id <> p_attempt_id
    or v_target.tenant_id <> p_tenant_id
    or v_target.workspace_id <> p_workspace_id
    or v_target.auth_user_id <> p_auth_user_id
    or v_target.provider <> p_provider
    or v_target.environment <> p_environment
    or v_target.provider_account_id <> v_attempt.provider_account_id
    or v_target.authorization_method <> v_attempt.authorization_method
    or v_target.granted_permissions <> v_attempt.granted_permissions
    or v_target.provider_revocation_required
    or v_target.credential_envelope_ids = '{}'::pg_catalog.uuid[]
    or v_target.revision < v_attempt.target_revision
  then
    raise unique_violation
      using message = 'provider_connection_conflict';
  end if;

  if v_attempt.acknowledged_at is null then
    if v_target.readiness <> 'requires_validation' then
      raise unique_violation
        using message = 'provider_connection_conflict';
    end if;
    update public.mercury_provider_connections as connection
    set readiness = 'ready',
        revision = connection.revision + 1,
        updated_at = v_now
    where connection.id = v_target.id
    returning connection.* into v_target;

    update public.mercury_provider_oauth_attempts as attempt
    set target_revision = v_target.revision,
        acknowledged_at = v_now,
        updated_at = v_now
    where attempt.id = p_attempt_id
    returning attempt.* into v_attempt;
  elsif v_target.readiness <> 'ready' then
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

  v_recovery_envelopes := v_attempt.credential_envelopes;
  if v_attempt.target_connection_id is not null then
    select connection.*
    into v_target
    from public.mercury_provider_connections as connection
    where connection.id = v_attempt.target_connection_id
    for update;

    if found and v_target.oauth_generation_id = p_attempt_id then
      if v_target.tenant_id <> p_tenant_id
        or v_target.workspace_id <> p_workspace_id
        or v_target.auth_user_id <> p_auth_user_id
        or v_target.provider <> p_provider
        or v_target.environment <> p_environment
      then
        raise unique_violation
          using message = 'provider_connection_conflict';
      end if;

      v_attempt.provider_account_id := v_target.provider_account_id;
      v_attempt.account_display_name := v_target.account_display_name;
      v_attempt.authorization_method := v_target.authorization_method;
      v_attempt.target_revision := v_target.revision;
      if v_target.readiness <> 'disconnected' then
        if v_target.provider_revocation_required
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
          v_target.id,
          true
        );
        v_attempt.target_revision := v_disconnected.revision;
      else
        v_recovery_envelopes := '[]'::pg_catalog.jsonb;
      end if;
    end if;
  end if;

  update public.mercury_provider_oauth_attempts as attempt
  set status = 'failed',
      provider_account_id = v_attempt.provider_account_id,
      account_display_name = v_attempt.account_display_name,
      authorization_method = v_attempt.authorization_method,
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

  if v_attempt.target_connection_id is not null
    and exists (
      select 1
      from public.mercury_provider_connections as connection
      where connection.id = v_attempt.target_connection_id
        and connection.oauth_generation_id = p_attempt_id
    )
  then
    perform 1
    from public.complete_mercury_provider_revocation(
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id,
      v_attempt.target_connection_id
    );
    update public.mercury_provider_connections as connection
    set oauth_generation_id = null
    where connection.id = v_attempt.target_connection_id
      and connection.oauth_generation_id = p_attempt_id;
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
    or p_tenant_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_workspace_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
    or p_auth_user_id
      = '00000000-0000-0000-0000-000000000000'::pg_catalog.uuid
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
  where envelope.connection_id = p_connection_id
    and envelope.tenant_id = p_tenant_id
    and envelope.workspace_id = p_workspace_id
    and envelope.auth_user_id = p_auth_user_id
  order by envelope.credential_type, envelope.id;
end;
$$;

revoke all on function
public.replace_mercury_provider_oauth_attempt_envelopes(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.int8,
  pg_catalog.jsonb
) from public, anon, authenticated;
revoke all on function public.acknowledge_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) from public, anon, authenticated;

grant execute on function
public.replace_mercury_provider_oauth_attempt_envelopes(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text,
  pg_catalog.int8,
  pg_catalog.jsonb
) to service_role;
grant execute on function public.acknowledge_mercury_provider_oauth_attempt(
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.uuid,
  pg_catalog.text,
  pg_catalog.text
) to service_role;

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
) from service_role;
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
) from service_role;
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
) from service_role;
