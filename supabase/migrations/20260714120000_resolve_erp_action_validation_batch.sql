begin;

create or replace function public.resolve_erp_action_validation_batch(
  p_requests jsonb,
  p_now timestamptz
)
returns table (
  request_index integer,
  connector_id text,
  action_id text,
  version_id text,
  environment text,
  records jsonb
)
language plpgsql
stable
set search_path = pg_catalog, pg_temp
as $function$
declare
  request_item jsonb;
begin
  if p_now is null
    or not isfinite(p_now)
  then
    raise exception using
      errcode = '22023',
      message = 'validation_batch_invalid';
  end if;

  if p_requests is null
    or jsonb_typeof(p_requests) is distinct from 'array'
  then
    raise exception using
      errcode = '22023',
      message = 'validation_batch_invalid';
  end if;

  if not (jsonb_array_length(p_requests) between 1 and 100) then
    raise exception using
      errcode = '22023',
      message = 'validation_batch_invalid';
  end if;

  for request_item in
    select requested.item
    from jsonb_array_elements(p_requests) as requested(item)
  loop
    if jsonb_typeof(request_item) <> 'object' then
      raise exception using
        errcode = '22023',
        message = 'validation_batch_invalid';
    end if;

    if (
        select count(*)
        from jsonb_object_keys(request_item)
      ) <> 4
      or not request_item ?& array[
        'connector_id',
        'action_id',
        'version_id',
        'environment'
      ]
      or jsonb_typeof(request_item->'connector_id') <> 'string'
      or jsonb_typeof(request_item->'action_id') <> 'string'
      or jsonb_typeof(request_item->'version_id') <> 'string'
      or jsonb_typeof(request_item->'environment') <> 'string'
      or request_item->>'connector_id' !~ '^[A-Za-z0-9._:-]{1,200}$'
      or request_item->>'action_id' !~ '^act_[0-9a-f]{24}$'
      or request_item->>'version_id' !~ '^av_[0-9a-f]{64}$'
      or request_item->>'environment' not in (
        'sandbox',
        'test',
        'uat',
        'production'
      )
    then
      raise exception using
        errcode = '22023',
        message = 'validation_batch_invalid';
    end if;
  end loop;

  if exists (
    select 1
    from jsonb_array_elements(p_requests) as requested(item)
    group by
      requested.item->>'connector_id',
      requested.item->>'action_id',
      requested.item->>'version_id',
      requested.item->>'environment'
    having count(*) > 1
  ) then
    raise exception using
      errcode = '22023',
      message = 'validation_batch_invalid';
  end if;

  return query
  with parsed as (
    select
      (requested.ordinality - 1)::integer as request_index,
      requested.item->>'connector_id' as connector_id,
      requested.item->>'action_id' as action_id,
      requested.item->>'version_id' as version_id,
      requested.item->>'environment' as environment
    from jsonb_array_elements(p_requests) with ordinality
      as requested(item, ordinality)
  )
  select
    parsed.request_index,
    parsed.connector_id,
    parsed.action_id,
    parsed.version_id,
    parsed.environment,
    coalesce(evidence.records, '[]'::jsonb) as records
  from parsed
  left join lateral (
    select coalesce(
      jsonb_agg(
        to_jsonb(candidate)
        order by candidate.evaluated_at desc, candidate.run_id desc
      ),
      '[]'::jsonb
    ) as records
    from (
      select
        knowledge.opaque_evidence_id,
        knowledge.run_id,
        knowledge.action_id,
        knowledge.version_id,
        knowledge.connector_id,
        knowledge.environment,
        knowledge.validation_status,
        knowledge.evidence_level,
        knowledge.execution_eligibility,
        knowledge.approved_public,
        knowledge.summary_th,
        knowledge.summary_en,
        knowledge.prerequisites,
        knowledge.limitations,
        knowledge.recommended_next_step,
        knowledge.response_shape,
        knowledge.status_class,
        knowledge.latency_ms,
        knowledge.semantic_contract,
        knowledge.evidence_sha256,
        knowledge.reviewed_by,
        knowledge.runner_version,
        knowledge.run_state,
        knowledge.evaluated_at,
        knowledge.expires_at
      from public.erp_action_validation_knowledge as knowledge
      where knowledge.connector_id = parsed.connector_id
        and knowledge.action_id = parsed.action_id
        and knowledge.version_id = parsed.version_id
        and knowledge.environment = parsed.environment
        and knowledge.approved_public
        and knowledge.evaluated_at <= p_now
        and (knowledge.expires_at is null or knowledge.expires_at > p_now)
    ) as candidate
  ) as evidence on true
  order by parsed.request_index;
end;
$function$;

revoke all on function public.resolve_erp_action_validation_batch(jsonb, timestamptz)
  from public, anon, authenticated;
grant execute on function public.resolve_erp_action_validation_batch(jsonb, timestamptz)
  to service_role;

commit;
