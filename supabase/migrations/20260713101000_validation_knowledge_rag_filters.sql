begin;

drop function if exists public.match_knowledge_chunks(
  text,
  vector(1536),
  integer,
  text,
  text,
  text,
  text,
  text,
  date
);

create function public.match_knowledge_chunks(
  query_text text,
  query_embedding vector(1536) default null,
  match_count integer default 8,
  search_mode text default 'hybrid',
  filter_jurisdiction text default null,
  filter_connector text default null,
  filter_doc_type text default null,
  filter_review_status text default null,
  filter_effective_date date default null,
  filter_action_id text default null,
  filter_version_id text default null,
  filter_environment text default null,
  filter_capability text default null,
  filter_accounting_use text default null
)
returns table (
  chunk_id uuid,
  document_id uuid,
  document_uri text,
  chunk_uri text,
  chunk_text text,
  score double precision,
  source_title text,
  source_uri text,
  source_url text,
  source_path text,
  citation jsonb,
  metadata jsonb
)
language sql
stable
set search_path = pg_catalog, public, pg_temp
as $function$
  with q as (
    select
      websearch_to_tsquery('simple', coalesce(query_text, '')) as tsq,
      coalesce(
        array_agg(term) filter (where length(term) > 1),
        array[]::text[]
      ) as terms
    from regexp_split_to_table(
      lower(coalesce(query_text, '')),
      '[^[:alnum:]_/.-]+'
    ) as term
  ),
  ranked as (
    select
      c.id as chunk_id,
      d.id as document_id,
      d.document_uri,
      c.chunk_uri,
      c.chunk_text,
      s.title as source_title,
      s.source_uri,
      s.source_url,
      s.source_path,
      c.citation,
      c.metadata,
      case
        when search_mode in ('hybrid', 'keyword')
          then greatest(
            ts_rank_cd(c.search_tsv, q.tsq),
            case
              when cardinality(q.terms) > 0
                then (
                  select count(*)::double precision / cardinality(q.terms)
                  from unnest(q.terms) as term
                  where lower(c.chunk_text) like '%' || term || '%'
                )
              else 0
            end
          )
        else 0
      end as keyword_score,
      case
        when query_embedding is not null
          and c.embedding is not null
          and search_mode in ('hybrid', 'vector')
          then 1 - (c.embedding <=> query_embedding)
        else 0
      end as vector_score
    from public.knowledge_chunks c
    join public.knowledge_documents d on d.id = c.document_id
    join public.knowledge_sources s on s.id = d.source_id
    cross join q
    where (filter_jurisdiction is null or s.jurisdiction = filter_jurisdiction)
      and (filter_connector is null or s.connector = filter_connector)
      and (filter_doc_type is null or s.doc_type = filter_doc_type)
      and (filter_review_status is null or s.review_status = filter_review_status)
      and (
        filter_effective_date is null
        or d.effective_date is null
        or d.effective_date <= filter_effective_date
      )
      and (filter_action_id is null or c.metadata ->> 'action_id' = filter_action_id)
      and (filter_version_id is null or c.metadata ->> 'version_id' = filter_version_id)
      and (filter_environment is null or c.metadata ->> 'environment' = filter_environment)
      and (filter_capability is null or c.metadata ->> 'capability' = filter_capability)
      and (
        filter_accounting_use is null
        or c.metadata -> 'accounting_use' ? filter_accounting_use
      )
  )
  select
    ranked.chunk_id,
    ranked.document_id,
    ranked.document_uri,
    ranked.chunk_uri,
    ranked.chunk_text,
    case
      when search_mode = 'keyword' then ranked.keyword_score
      when search_mode = 'vector' then ranked.vector_score
      else (ranked.keyword_score * 0.45) + (ranked.vector_score * 0.55)
    end as score,
    ranked.source_title,
    ranked.source_uri,
    ranked.source_url,
    ranked.source_path,
    ranked.citation,
    ranked.metadata
  from ranked
  order by score desc
  limit greatest(1, least(match_count, 50));
$function$;

revoke all on function public.match_knowledge_chunks(
  text,
  vector(1536),
  integer,
  text,
  text,
  text,
  text,
  text,
  date,
  text,
  text,
  text,
  text,
  text
) from public, anon, authenticated;

grant execute on function public.match_knowledge_chunks(
  text,
  vector(1536),
  integer,
  text,
  text,
  text,
  text,
  text,
  date,
  text,
  text,
  text,
  text,
  text
) to service_role;

commit;
