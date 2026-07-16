-- Keep validation helpers compatible with PostgreSQL 17 JSONPath evaluation.
-- PostgreSQL can evaluate .keyvalue() against scalar descendants unless the
-- path filters object nodes first, which raises SQLSTATE 2203C for safe JSON.

create or replace function public.validation_label_kind(value text)
returns text
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  with normalized as (
    select public.validation_label_tokens(value) as label_tokens
  ),
  classified as (
    select
      label_tokens,
      label_tokens && array[
        'auth',
        'authentication',
        'authorization',
        'cookie',
        'credential',
        'credentials',
        'file',
        'header',
        'headers',
        'local',
        'oauth',
        'password',
        'passwords',
        'path',
        'payload',
        'raw',
        'request',
        'response',
        'secret',
        'secrets',
        'source',
        'token',
        'tokens',
        'uri'
      ] as has_forbidden_token,
      exists (
        select 1
        from unnest(array[
          'access key',
          'access keys',
          'access token',
          'access tokens',
          'api key',
          'api keys',
          'api secret',
          'api secrets',
          'auth header',
          'auth headers',
          'auth token',
          'auth tokens',
          'authentication header',
          'authentication token',
          'authorization header',
          'authorization token',
          'client id',
          'client secret',
          'client secrets',
          'cookie header',
          'cookie token',
          'file name',
          'file path',
          'id token',
          'local file',
          'local path',
          'oauth token',
          'provider response',
          'raw payload',
          'raw response',
          'refresh token',
          'request body',
          'request payload',
          'response body',
          'response payload',
          'session cookie',
          'session token',
          'source file',
          'source path'
        ]) as forbidden_label(label)
        cross join lateral (
          select public.validation_label_tokens(forbidden_label.label)
        ) as forbidden_group(tokens)
        where forbidden_group.tokens <@ label_tokens
      ) as has_forbidden_group,
      (
        (
          'id' = any(label_tokens)
          and label_tokens && array[
            'provider',
            'source',
            'customer',
            'contact',
            'document',
            'record',
            'invoice',
            'payment'
          ]
        )
        or (
          label_tokens && array['provider', 'source']
          and label_tokens && array['record', 'document']
        )
      ) as is_provider_reference
    from normalized
  ),
  source_provider_reference as (
    select
      classified.*,
      (
        'source' = any(label_tokens)
        and is_provider_reference
        and not label_tokens && array[
          'auth',
          'authentication',
          'authorization',
          'cookie',
          'credential',
          'credentials',
          'file',
          'header',
          'headers',
          'local',
          'oauth',
          'password',
          'passwords',
          'path',
          'payload',
          'raw',
          'request',
          'response',
          'secret',
          'secrets',
          'token',
          'tokens',
          'uri'
        ]
        and not has_forbidden_group
      ) as is_safe
    from classified
  )
  select case
    when (
      has_forbidden_token or has_forbidden_group
    ) and not source_provider_reference.is_safe then 'forbidden'
    when is_provider_reference then 'provider_reference'
    else null
  end
  from source_provider_reference;
$function$;

revoke all on function public.validation_label_kind(text)
  from public, anon, authenticated;
grant execute on function public.validation_label_kind(text)
  to service_role;

create or replace function public.validation_text_has_safe_label_assignment(value text)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select exists (
    with delimiter_positions as (
      select assignment_delimiters.delimiter_index
      from generate_series(
        1,
        least(char_length(coalesce(value, '')), 512)
      ) as assignment_delimiters(delimiter_index)
      where substr(
        coalesce(value, ''),
        assignment_delimiters.delimiter_index,
        1
      ) in (':', '=')
    ),
    labelled_token_assignments as (
      select
        btrim(
          left(
            left(coalesce(value, ''), 512),
            delimiter_positions.delimiter_index - 1
          )
        ) as label,
        btrim(
          substr(
            left(coalesce(value, ''), 512),
            delimiter_positions.delimiter_index + 1
          )
        ) as candidate
      from delimiter_positions
    ),
    classified_labelled_token_assignments as (
      select
        labelled_token_assignments.*,
        (
          labelled_token_assignments.label !~ '[[:space:]]'
          and lower(labelled_token_assignments.label) ~ (
            '^(client[0-9]+[:._=-]?id|api[0-9]+[:._=-]?key)$'
          )
        ) as is_numeric_qualified_label
      from labelled_token_assignments
    ),
    contamination_checked_assignments as (
      select
        classified_labelled_token_assignments.*,
        (
          (
            lower(classified_labelled_token_assignments.label) ~ '[0-9]'
            and not classified_labelled_token_assignments.is_numeric_qualified_label
          )
          or lower(classified_labelled_token_assignments.candidate) ~ '[0-9]'
          or
          (
            lower(classified_labelled_token_assignments.label) ~ (
              '(^|[^a-z0-9])([a-z]+[0-9]+|[0-9]+[a-z]+)'
              '[a-z0-9]*($|[^a-z0-9])'
            )
            and not classified_labelled_token_assignments.is_numeric_qualified_label
          )
          or lower(classified_labelled_token_assignments.candidate) ~ (
            '(^|[^a-z0-9])([a-z]+[0-9]+|[0-9]+[a-z]+)'
            '[a-z0-9]*($|[^a-z0-9])'
          )
        ) as has_mixed_identifier_contamination
      from classified_labelled_token_assignments
    )
    select 1
    from contamination_checked_assignments as labelled_token_assignments
    where labelled_token_assignments.label <> ''
      and labelled_token_assignments.candidate <> ''
      and not labelled_token_assignments.has_mixed_identifier_contamination
      and public.validation_label_kind(
        labelled_token_assignments.label
      ) is not null
      and not public.validation_label_assignment_has_forbidden_value(
        labelled_token_assignments.label,
        labelled_token_assignments.candidate
      )
  );
$function$;

revoke all on function public.validation_text_has_safe_label_assignment(text)
  from public, anon, authenticated;
grant execute on function public.validation_text_has_safe_label_assignment(text)
  to service_role;

create or replace function public.validation_text_has_label_assignment_contamination(
  value text
)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select
    not public.validation_text_has_safe_label_assignment(value)
    and exists (
      with delimiter_positions as (
        select assignment_delimiters.delimiter_index
        from generate_series(
          1,
          least(char_length(coalesce(value, '')), 512)
        ) as assignment_delimiters(delimiter_index)
        where substr(
          coalesce(value, ''),
          assignment_delimiters.delimiter_index,
          1
        ) in (':', '=')
      ),
      labelled_token_assignments as (
        select
          btrim(
            left(
              left(coalesce(value, ''), 512),
              delimiter_positions.delimiter_index - 1
            )
          ) as label,
          btrim(
            substr(
              left(coalesce(value, ''), 512),
              delimiter_positions.delimiter_index + 1
            )
          ) as candidate
        from delimiter_positions
      ),
      classified_labelled_token_assignments as (
        select
          labelled_token_assignments.*,
          (
            labelled_token_assignments.label !~ '[[:space:]]'
            and lower(labelled_token_assignments.label) ~ (
              '^(client[0-9]+[:._=-]?id|api[0-9]+[:._=-]?key)$'
            )
          ) as is_numeric_qualified_label
        from labelled_token_assignments
      )
      select 1
      from classified_labelled_token_assignments as assignments
      where assignments.label <> ''
        and assignments.candidate <> ''
        and public.validation_label_kind(assignments.label) is not null
        and (
          (
            lower(assignments.label) ~ '[0-9]'
            and not assignments.is_numeric_qualified_label
          )
          or lower(assignments.candidate) ~ '[0-9]'
          or (
            lower(assignments.label) ~ (
              '(^|[^a-z0-9])([a-z]+[0-9]+|[0-9]+[a-z]+)'
              '[a-z0-9]*($|[^a-z0-9])'
            )
            and not assignments.is_numeric_qualified_label
          )
          or lower(assignments.candidate) ~ (
            '(^|[^a-z0-9])([a-z]+[0-9]+|[0-9]+[a-z]+)'
            '[a-z0-9]*($|[^a-z0-9])'
          )
        )
    );
$function$;

revoke all on function public.validation_text_has_label_assignment_contamination(text)
  from public, anon, authenticated;
grant execute on function public.validation_text_has_label_assignment_contamination(text)
  to service_role;

create or replace function public.validation_text_has_forbidden_value(value text)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select
    value is null
    or char_length(value) > 512
    or value ~ '[[:cntrl:]]'
    or strpos(value, '@') > 0
    or strpos(lower(value), '://') > 0
    or strpos(value, '../') > 0
    or strpos(value, './') > 0
    or strpos(value, '~/') > 0
    or strpos(value, chr(92)) > 0
    or strpos(value, chr(8725)) > 0
    or strpos(value, chr(65295)) > 0
    or regexp_replace(
      regexp_replace(
        lower(value),
        '(debit/credit|input/output)',
        '',
        'g'
      ),
      '[0-9]{1,4}/[0-9]{1,2}(/[0-9]{1,4})?',
      '',
      'g'
    ) ~ '/'
    or exists (
      select 1
      from unnest(array[
        'bearer ',
        'basic ',
        'digest ',
        'gho_',
        'ghp_',
        'github_pat_',
        'pk_live_',
        'rk_live_',
        'sk-',
        'sk_',
        'xoxb-',
        'xoxp-',
        'ya29.'
      ]) as forbidden(fragment)
      where strpos(lower(value), forbidden.fragment) > 0
    )
    or exists (
      with delimiter_positions as (
        select assignment_delimiters.delimiter_index
        from generate_series(
          1,
          least(char_length(coalesce(value, '')), 512)
        ) as assignment_delimiters(delimiter_index)
        where substr(
          coalesce(value, ''),
          assignment_delimiters.delimiter_index,
          1
        ) in (':', '=')
      ),
      labelled_token_assignments as (
        select
          btrim(
            left(
              left(coalesce(value, ''), 512),
              delimiter_positions.delimiter_index - 1
            )
          ) as label,
          btrim(
            substr(
              left(coalesce(value, ''), 512),
              delimiter_positions.delimiter_index + 1
            )
          ) as candidate
        from delimiter_positions
      )
      select 1
      from labelled_token_assignments
      where labelled_token_assignments.label <> ''
        and labelled_token_assignments.candidate <> ''
        and public.validation_label_assignment_has_forbidden_value(
          labelled_token_assignments.label,
          labelled_token_assignments.candidate
        )
    )
    or exists (
      with assignment_words as (
        select array_agg(words.word order by words.ordinality) as parts
        from regexp_split_to_table(
          btrim(left(coalesce(value, ''), 512)),
          '[[:space:]]+'
        ) with ordinality as words(word, ordinality)
      ),
      label_candidates as (
        select
          split_positions.split_index,
          array_to_string(
            assignment_words.parts[1:split_positions.split_index],
            ' '
          ) as label,
          array_to_string(
            assignment_words.parts[
              split_positions.split_index + 1:array_length(assignment_words.parts, 1)
            ],
            ' '
          ) as candidate,
          public.validation_label_kind(
            array_to_string(
              assignment_words.parts[1:split_positions.split_index],
              ' '
            )
          ) as label_kind
        from assignment_words
        cross join lateral generate_series(
          1,
          greatest(array_length(assignment_words.parts, 1) - 1, 0)
        ) as split_positions(split_index)
      ),
      preferred_candidate as (
        select
          split_index,
          label,
          candidate,
          label_kind
        from label_candidates
        where label_kind is not null
          and candidate <> ''
        order by split_index
        limit 1
      )
      select 1
      from preferred_candidate
      where public.validation_label_assignment_has_forbidden_value(
        preferred_candidate.label,
        preferred_candidate.candidate
      )
    )
    or value ~ '[A-Za-z0-9_-]{8,}[.][A-Za-z0-9_-]{8,}[.][A-Za-z0-9_-]{4,}'
    or value ~ '([[:digit:]][^[:alnum:]]*){9}'
    or btrim(value) ~ '^[[:digit:]]+$'
    or public.validation_text_has_label_assignment_contamination(value)
    or lower(value) ~ (
      '(^|[^a-z0-9])[a-z][a-z0-9]*[-_][a-z0-9_-]*'
      '[[:digit:]][a-z0-9_-]*($|[^a-z0-9])'
    )
    or (
      lower(value) ~ (
        '(^|[^a-z0-9])([a-z]+[0-9]+|[0-9]+[a-z]+)'
        '[a-z0-9]*($|[^a-z0-9])'
      )
      and btrim(lower(value)) !~ '^[1-5]xx$'
      and not public.validation_text_has_safe_label_assignment(value)
    )
    or left(btrim(value), 1) in ('{', '[')
    or value ~ '"[^"]+"[[:space:]]*:';
$function$;

revoke all on function public.validation_text_has_forbidden_value(text)
  from public, anon, authenticated;
grant execute on function public.validation_text_has_forbidden_value(text)
  to service_role;

create or replace function public.jsonb_has_forbidden_validation_key(value jsonb)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select exists (
    select 1
    from jsonb_path_query(
      coalesce(value, 'null'::jsonb),
      'lax $.** ? (@.type() == "object").keyvalue()'
    ) as keys(item)
    where public.validation_label_kind(keys.item->>'key') = 'forbidden'
  );
$function$;

revoke all on function public.jsonb_has_forbidden_validation_key(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_has_forbidden_validation_key(jsonb)
  to service_role;

create or replace function public.jsonb_has_forbidden_validation_value(value jsonb)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select
    exists (
      select 1
      from jsonb_path_query(
        coalesce(value, 'null'::jsonb),
        'lax $.**'
      ) as nodes(item)
      where jsonb_typeof(nodes.item) in ('number', 'boolean', 'null')
        or (
          jsonb_typeof(nodes.item) = 'string'
          and (
            (
              nodes.item #>> '{}' !~ '^act_[0-9a-f]{24}$'
              and nodes.item #>> '{}' !~ '^av_[0-9a-f]{64}$'
              and nodes.item #>> '{}' !~ '^ev_[a-z0-9_]{8,128}$'
              and nodes.item #>> '{}' !~ '^run_[a-z0-9_]{8,128}$'
              and public.validation_text_has_forbidden_value(nodes.item #>> '{}')
            )
          )
        )
    )
    or exists (
      select 1
      from jsonb_path_query(
        coalesce(value, 'null'::jsonb),
        'lax $.** ? (@.type() == "object").keyvalue()'
      ) as labelled_entry(item)
      where public.validation_label_assignment_has_forbidden_value(
        labelled_entry.item->>'key',
        case
          when jsonb_typeof(labelled_entry.item->'value') = 'string'
            then labelled_entry.item->>'value'
          else null
        end
      )
    );
$function$;

revoke all on function public.jsonb_has_forbidden_validation_value(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_has_forbidden_validation_value(jsonb)
  to service_role;

create or replace function public.jsonb_is_safe_validation_response_shape(value jsonb)
returns boolean
language sql
immutable
parallel safe
set search_path = pg_catalog, pg_temp
as $function$
  select
    coalesce(jsonb_typeof(value) = 'object', false)
    and not public.jsonb_has_forbidden_validation_key(value)
    and not public.jsonb_has_forbidden_validation_value(value)
    and not exists (
      select 1
      from jsonb_path_query(value, 'lax $.**') as nodes(item)
      where jsonb_typeof(nodes.item) not in ('object', 'string')
        or (
          jsonb_typeof(nodes.item) = 'string'
          and nodes.item #>> '{}' not in (
            'boolean', 'integer', 'null', 'number', 'string', 'truncated', 'unknown', 'array'
          )
        )
    )
    and not exists (
      select 1
      from jsonb_path_query(
        value,
        'lax $.** ? (@.type() == "object").keyvalue()'
      ) as entries(item)
      where char_length(entries.item->>'key') > 64
        or entries.item->>'key' !~ '^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)*$'
        or entries.item->>'key' ~ '[[:digit:]]{6,}'
    );
$function$;

revoke all on function public.jsonb_is_safe_validation_response_shape(jsonb)
  from public, anon, authenticated;
grant execute on function public.jsonb_is_safe_validation_response_shape(jsonb)
  to service_role;

create or replace function public.reject_validation_evidence_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $function$
begin
  raise exception using
    errcode = 'P0001',
    message = 'erp_validation_evidence_is_append_only';
end;
$function$;

revoke all on function public.reject_validation_evidence_mutation()
  from public, anon, authenticated;
grant execute on function public.reject_validation_evidence_mutation()
  to service_role;
