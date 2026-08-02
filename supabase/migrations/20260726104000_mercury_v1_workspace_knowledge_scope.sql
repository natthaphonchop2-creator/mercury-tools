begin;

alter table public.knowledge_sources
  add column if not exists tenant_id uuid
    references public.mercury_tenants(id) on delete restrict,
  add column if not exists workspace_id uuid
    references public.mercury_workspaces(id) on delete restrict,
  add column if not exists visibility_scope text,
  add column if not exists publication_status text,
  add column if not exists provider text,
  add column if not exists capability_version text;

-- Existing Wiki rows are global source records. Backfill their ownership before
-- any authenticated policy can expose them; document and chunk bodies are not rewritten.
update public.knowledge_sources
set visibility_scope = 'global'
where visibility_scope is null;

update public.knowledge_sources
set publication_status = case
    when review_status = 'reviewed' then 'published'
    when review_status = 'rejected' then 'rejected'
    else 'draft'
  end
where publication_status is null;

update public.knowledge_sources
set provider = connector
where provider is null
  and connector is not null;

alter table public.knowledge_sources
  alter column visibility_scope set default 'global',
  alter column visibility_scope set not null,
  alter column publication_status set default 'draft',
  alter column publication_status set not null,
  drop constraint if exists knowledge_sources_visibility_scope_check,
  add constraint knowledge_sources_visibility_scope_check
    check (visibility_scope in ('global', 'workspace')),
  drop constraint if exists knowledge_sources_publication_status_check,
  add constraint knowledge_sources_publication_status_check
    check (publication_status in ('draft', 'published', 'rejected', 'superseded')),
  drop constraint if exists knowledge_sources_scope_ownership_check,
  add constraint knowledge_sources_scope_ownership_check
    check (
      (
        visibility_scope = 'global'
        and tenant_id is null
        and workspace_id is null
      )
      or (
        visibility_scope = 'workspace'
        and tenant_id is not null
        and workspace_id is not null
      )
    ),
  drop constraint if exists knowledge_sources_provider_check,
  add constraint knowledge_sources_provider_check
    check (
      provider is null
      or provider ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
    ),
  drop constraint if exists knowledge_sources_capability_version_check,
  add constraint knowledge_sources_capability_version_check
    check (
      capability_version is null
      or capability_version ~ '^[0-9a-f]{64}$'
    );

create index if not exists knowledge_sources_v1_visibility_idx
  on public.knowledge_sources (
    visibility_scope,
    tenant_id,
    workspace_id,
    publication_status,
    review_status,
    id
  );

create index if not exists knowledge_sources_v1_filters_idx
  on public.knowledge_sources (
    jurisdiction,
    provider,
    doc_type,
    review_status,
    capability_version,
    id
  );

create index if not exists knowledge_chunks_search_tsv_idx
  on public.knowledge_chunks using gin (search_tsv);

create or replace function public.mercury_v1_claim_uuid(p_claim text)
returns uuid
language plpgsql
stable
set search_path = pg_catalog
as $function$
declare
  raw_value text;
  claim_values jsonb;
begin
  raw_value := current_setting('request.jwt.claim.' || p_claim, true);
  if raw_value is null or raw_value = '' then
    begin
      claim_values := nullif(
        current_setting('request.jwt.claims', true),
        ''
      )::jsonb;
      raw_value := claim_values ->> p_claim;
    exception
      when others then
        return null;
    end;
  end if;
  return nullif(raw_value, '')::uuid;
exception
  when others then
    return null;
end;
$function$;

create or replace function public.mercury_v1_workspace_member(
  p_tenant_id uuid,
  p_workspace_id uuid,
  p_auth_user_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select
    p_tenant_id is not null
    and p_workspace_id is not null
    and p_auth_user_id is not null
    and exists (
      select 1
      from public.mercury_workspaces as workspace
      join public.mercury_workspace_members as member
        on member.workspace_id = workspace.id
       and member.tenant_id = workspace.tenant_id
      where workspace.id = p_workspace_id
        and workspace.tenant_id = p_tenant_id
        and workspace.status = 'active'
        and member.auth_user_id = p_auth_user_id
        and member.status = 'active'
    );
$function$;

create or replace function public.mercury_v1_knowledge_is_visible(
  p_visibility_scope text,
  p_source_tenant_id uuid,
  p_source_workspace_id uuid,
  p_publication_status text,
  p_review_status text,
  p_tenant_id uuid,
  p_workspace_id uuid,
  p_auth_user_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select
    public.mercury_v1_workspace_member(
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id
    )
    and p_publication_status = 'published'
    and p_review_status = 'reviewed'
    and (
      (
        p_visibility_scope = 'global'
        and p_source_tenant_id is null
        and p_source_workspace_id is null
      )
      or (
        p_visibility_scope = 'workspace'
        and p_source_tenant_id = p_tenant_id
        and p_source_workspace_id = p_workspace_id
      )
    );
$function$;

create or replace function public.mercury_v1_knowledge_source_is_visible(
  p_source_id uuid,
  p_tenant_id uuid,
  p_workspace_id uuid,
  p_auth_user_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select coalesce(bool_or(public.mercury_v1_knowledge_is_visible(
    source.visibility_scope,
    source.tenant_id,
    source.workspace_id,
    source.publication_status,
    source.review_status,
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  )), false)
  from public.knowledge_sources as source
  where source.id = p_source_id;
$function$;

create or replace function public.mercury_v1_knowledge_document_is_visible(
  p_document_id uuid,
  p_tenant_id uuid,
  p_workspace_id uuid,
  p_auth_user_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select coalesce(bool_or(public.mercury_v1_knowledge_is_visible(
    source.visibility_scope,
    source.tenant_id,
    source.workspace_id,
    source.publication_status,
    source.review_status,
    p_tenant_id,
    p_workspace_id,
    p_auth_user_id
  )), false)
  from public.knowledge_documents as document
  join public.knowledge_sources as source
    on source.id = document.source_id
  where document.id = p_document_id;
$function$;

create or replace function public.mercury_v1_authenticated_knowledge_is_visible(
  p_visibility_scope text,
  p_source_tenant_id uuid,
  p_source_workspace_id uuid,
  p_publication_status text,
  p_review_status text
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select public.mercury_v1_knowledge_is_visible(
    p_visibility_scope,
    p_source_tenant_id,
    p_source_workspace_id,
    p_publication_status,
    p_review_status,
    public.mercury_v1_claim_uuid('tenant_id'),
    public.mercury_v1_claim_uuid('workspace_id'),
    auth.uid()
  );
$function$;

create or replace function public.mercury_v1_authenticated_source_is_visible(
  p_source_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select public.mercury_v1_knowledge_source_is_visible(
    p_source_id,
    public.mercury_v1_claim_uuid('tenant_id'),
    public.mercury_v1_claim_uuid('workspace_id'),
    auth.uid()
  );
$function$;

create or replace function public.mercury_v1_authenticated_document_is_visible(
  p_document_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select public.mercury_v1_knowledge_document_is_visible(
    p_document_id,
    public.mercury_v1_claim_uuid('tenant_id'),
    public.mercury_v1_claim_uuid('workspace_id'),
    auth.uid()
  );
$function$;

revoke all on function public.mercury_v1_claim_uuid(text)
  from public, anon, authenticated;
revoke all on function public.mercury_v1_workspace_member(uuid, uuid, uuid)
  from public, anon, authenticated;
revoke all on function public.mercury_v1_knowledge_is_visible(
  text, uuid, uuid, text, text, uuid, uuid, uuid
) from public, anon, authenticated;
revoke all on function public.mercury_v1_knowledge_source_is_visible(
  uuid, uuid, uuid, uuid
) from public, anon, authenticated;
revoke all on function public.mercury_v1_knowledge_document_is_visible(
  uuid, uuid, uuid, uuid
) from public, anon, authenticated;
revoke all on function public.mercury_v1_authenticated_knowledge_is_visible(
  text, uuid, uuid, text, text
) from public, anon, authenticated;
revoke all on function public.mercury_v1_authenticated_source_is_visible(uuid)
  from public, anon, authenticated;
revoke all on function public.mercury_v1_authenticated_document_is_visible(uuid)
  from public, anon, authenticated;

grant execute on function public.mercury_v1_claim_uuid(text)
  to service_role;
grant execute on function public.mercury_v1_workspace_member(uuid, uuid, uuid)
  to service_role;
grant execute on function public.mercury_v1_knowledge_is_visible(
  text, uuid, uuid, text, text, uuid, uuid, uuid
) to service_role;
grant execute on function public.mercury_v1_knowledge_source_is_visible(
  uuid, uuid, uuid, uuid
) to service_role;
grant execute on function public.mercury_v1_knowledge_document_is_visible(
  uuid, uuid, uuid, uuid
) to service_role;
grant execute on function public.mercury_v1_authenticated_knowledge_is_visible(
  text, uuid, uuid, text, text
) to authenticated;
grant execute on function public.mercury_v1_authenticated_source_is_visible(uuid)
  to authenticated;
grant execute on function public.mercury_v1_authenticated_document_is_visible(uuid)
  to authenticated;

alter table public.knowledge_sources enable row level security;
alter table public.knowledge_sources force row level security;
alter table public.knowledge_documents enable row level security;
alter table public.knowledge_documents force row level security;
alter table public.knowledge_chunks enable row level security;
alter table public.knowledge_chunks force row level security;

drop policy if exists knowledge_sources_v1_select on public.knowledge_sources;
create policy knowledge_sources_v1_select
  on public.knowledge_sources
  for select
  to authenticated
  using (
    public.mercury_v1_authenticated_knowledge_is_visible(
      visibility_scope,
      tenant_id,
      workspace_id,
      publication_status,
      review_status
    )
  );

drop policy if exists knowledge_documents_v1_select on public.knowledge_documents;
create policy knowledge_documents_v1_select
  on public.knowledge_documents
  for select
  to authenticated
  using (
    public.mercury_v1_authenticated_source_is_visible(source_id)
  );

drop policy if exists knowledge_chunks_v1_select on public.knowledge_chunks;
create policy knowledge_chunks_v1_select
  on public.knowledge_chunks
  for select
  to authenticated
  using (
    public.mercury_v1_authenticated_document_is_visible(document_id)
  );

revoke all on table public.knowledge_sources,
  public.knowledge_documents,
  public.knowledge_chunks
  from anon, authenticated;
grant select on table public.knowledge_sources,
  public.knowledge_documents,
  public.knowledge_chunks
  to authenticated;

drop function if exists public.search_mercury_v1_knowledge(
  uuid,
  uuid,
  uuid,
  text,
  integer,
  text,
  text,
  text,
  text,
  text,
  date,
  uuid,
  text
);

create function public.search_mercury_v1_knowledge(
  p_tenant_id uuid,
  p_workspace_id uuid,
  p_auth_user_id uuid,
  query_text text,
  match_count integer default 8,
  search_mode text default 'hybrid',
  filter_jurisdiction text default null,
  filter_provider text default null,
  filter_doc_type text default null,
  filter_review_status text default null,
  filter_effective_on date default null,
  filter_source_id uuid default null,
  filter_capability_version text default null
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
set search_path = pg_catalog
as $function$
  with query as (
    select websearch_to_tsquery('simple', coalesce(query_text, '')) as terms
  )
  select
    chunk.id,
    document.id,
    document.document_uri,
    chunk.chunk_uri,
    chunk.chunk_text,
    ts_rank_cd(chunk.search_tsv, query.terms)::double precision as score,
    source.title,
    source.source_uri,
    source.source_url,
    null::text as source_path,
    chunk.citation,
    jsonb_strip_nulls(jsonb_build_object(
      'source_id', source.id,
      'jurisdiction', source.jurisdiction,
      'provider', source.provider,
      'doc_type', source.doc_type,
      'review_status', source.review_status,
      'effective_on', document.effective_date,
      'capability_version', source.capability_version
    )) as metadata
  from public.knowledge_chunks as chunk
  join public.knowledge_documents as document
    on document.id = chunk.document_id
  join public.knowledge_sources as source
    on source.id = document.source_id
  cross join query
  where search_mode in ('keyword', 'hybrid')
    and nullif(btrim(query_text), '') is not null
    and char_length(query_text) <= 2000
    and chunk.search_tsv @@ query.terms
    and public.mercury_v1_knowledge_is_visible(
      source.visibility_scope,
      source.tenant_id,
      source.workspace_id,
      source.publication_status,
      source.review_status,
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id
    )
    and (
      filter_jurisdiction is null
      or source.jurisdiction = filter_jurisdiction
    )
    and (filter_provider is null or source.provider = filter_provider)
    and (filter_doc_type is null or source.doc_type = filter_doc_type)
    and (
      filter_review_status is null
      or source.review_status = filter_review_status
    )
    and (
      filter_effective_on is null
      or document.effective_date is null
      or document.effective_date <= filter_effective_on
    )
    and (filter_source_id is null or source.id = filter_source_id)
    and (
      filter_capability_version is null
      or source.capability_version = filter_capability_version
    )
  order by score desc, source.source_uri, chunk.chunk_index
  limit greatest(1, least(coalesce(match_count, 8), 20));
$function$;

revoke all on function public.search_mercury_v1_knowledge(
  uuid, uuid, uuid, text, integer, text, text, text, text, text, date, uuid, text
) from public, anon, authenticated;
grant execute on function public.search_mercury_v1_knowledge(
  uuid, uuid, uuid, text, integer, text, text, text, text, text, date, uuid, text
) to service_role;

create table if not exists public.mercury_published_skills (
  id uuid primary key default gen_random_uuid(),
  visibility_scope text not null,
  tenant_id uuid references public.mercury_tenants(id) on delete restrict,
  workspace_id uuid references public.mercury_workspaces(id) on delete restrict,
  skill_id text not null,
  skill_version text not null,
  publication_status text not null,
  projection jsonb not null,
  projection_sha256 text not null,
  git_source_path text,
  published_at timestamptz not null default statement_timestamp(),
  superseded_at timestamptz,
  created_at timestamptz not null default statement_timestamp(),
  constraint mercury_published_skills_scope_check
    check (visibility_scope in ('global', 'workspace')),
  constraint mercury_published_skills_ownership_check
    check (
      (
        visibility_scope = 'global'
        and tenant_id is null
        and workspace_id is null
        and git_source_path is not null
      )
      or (
        visibility_scope = 'workspace'
        and tenant_id is not null
        and workspace_id is not null
      )
    ),
  constraint mercury_published_skills_identity_check
    check (
      skill_id ~ '^[a-z][a-z0-9-]{0,199}$'
      and skill_version ~ '^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$'
      and projection ->> 'skill_id' = skill_id
      and projection ->> 'skill_version' = skill_version
    ),
  constraint mercury_published_skills_status_check
    check (publication_status in ('published', 'superseded')),
  constraint mercury_published_skills_projection_check
    check (
      jsonb_typeof(projection) = 'object'
      and jsonb_typeof(projection -> 'input_schema') = 'object'
      and jsonb_typeof(projection -> 'output_schema') = 'object'
      and (
        visibility_scope <> 'global'
        or projection ->> 'git_source_path' = git_source_path
      )
      and projection_sha256 ~ '^[0-9a-f]{64}$'
      and projection_sha256 = encode(
        digest(public.mercury_canonical_jsonb(projection), 'sha256'),
        'hex'
      )
    ),
  constraint mercury_published_skills_terminal_time_check
    check (
      (
        publication_status = 'published'
        and superseded_at is null
      )
      or (
        publication_status = 'superseded'
        and superseded_at is not null
      )
    )
);

create unique index if not exists mercury_published_skills_exact_identity_idx
  on public.mercury_published_skills (
    visibility_scope,
    coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid),
    coalesce(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid),
    skill_id,
    skill_version
  );

create index if not exists mercury_published_skills_visibility_idx
  on public.mercury_published_skills (
    visibility_scope,
    tenant_id,
    workspace_id,
    publication_status,
    skill_id,
    skill_version
  );

create or replace function public.mercury_v1_published_skill_is_visible(
  p_visibility_scope text,
  p_skill_tenant_id uuid,
  p_skill_workspace_id uuid,
  p_publication_status text,
  p_tenant_id uuid,
  p_workspace_id uuid,
  p_auth_user_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select
    public.mercury_v1_workspace_member(
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id
    )
    and p_publication_status = 'published'
    and (
      (
        p_visibility_scope = 'global'
        and p_skill_tenant_id is null
        and p_skill_workspace_id is null
      )
      or (
        p_visibility_scope = 'workspace'
        and p_skill_tenant_id = p_tenant_id
        and p_skill_workspace_id = p_workspace_id
      )
    );
$function$;

create or replace function public.mercury_v1_authenticated_skill_is_visible(
  p_visibility_scope text,
  p_skill_tenant_id uuid,
  p_skill_workspace_id uuid,
  p_publication_status text
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog
as $function$
  select public.mercury_v1_published_skill_is_visible(
    p_visibility_scope,
    p_skill_tenant_id,
    p_skill_workspace_id,
    p_publication_status,
    public.mercury_v1_claim_uuid('tenant_id'),
    public.mercury_v1_claim_uuid('workspace_id'),
    auth.uid()
  );
$function$;

create or replace function public.mercury_v1_guard_published_skill()
returns trigger
language plpgsql
set search_path = pg_catalog
as $function$
begin
  if (
    new.id,
    new.visibility_scope,
    new.tenant_id,
    new.workspace_id,
    new.skill_id,
    new.skill_version,
    new.projection,
    new.projection_sha256,
    new.git_source_path,
    new.published_at,
    new.created_at
  ) is distinct from (
    old.id,
    old.visibility_scope,
    old.tenant_id,
    old.workspace_id,
    old.skill_id,
    old.skill_version,
    old.projection,
    old.projection_sha256,
    old.git_source_path,
    old.published_at,
    old.created_at
  ) then
    raise exception 'mercury_published_skill_projection_immutable';
  end if;
  if old.publication_status <> 'published'
    or new.publication_status <> 'superseded'
    or new.superseded_at is null then
    raise exception 'mercury_published_skill_transition_invalid';
  end if;
  return new;
end;
$function$;

drop trigger if exists mercury_published_skills_immutable
  on public.mercury_published_skills;
create trigger mercury_published_skills_immutable
before update on public.mercury_published_skills
for each row execute function public.mercury_v1_guard_published_skill();

alter table public.mercury_published_skills enable row level security;
alter table public.mercury_published_skills force row level security;

drop policy if exists mercury_published_skills_v1_select
  on public.mercury_published_skills;
create policy mercury_published_skills_v1_select
  on public.mercury_published_skills
  for select
  to authenticated
  using (
    public.mercury_v1_authenticated_skill_is_visible(
      visibility_scope,
      tenant_id,
      workspace_id,
      publication_status
    )
  );

revoke all on table public.mercury_published_skills
  from public, anon, authenticated, service_role;
grant select on table public.mercury_published_skills
  to authenticated, service_role;

revoke all on function public.mercury_v1_published_skill_is_visible(
  text, uuid, uuid, text, uuid, uuid, uuid
) from public, anon, authenticated;
revoke all on function public.mercury_v1_authenticated_skill_is_visible(
  text, uuid, uuid, text
) from public, anon, authenticated;
revoke all on function public.mercury_v1_guard_published_skill()
  from public, anon, authenticated;
grant execute on function public.mercury_v1_published_skill_is_visible(
  text, uuid, uuid, text, uuid, uuid, uuid
) to service_role;
grant execute on function public.mercury_v1_authenticated_skill_is_visible(
  text, uuid, uuid, text
) to authenticated;

create or replace function public.resolve_mercury_v1_published_skill(
  p_tenant_id uuid,
  p_workspace_id uuid,
  p_auth_user_id uuid,
  p_skill_id text,
  p_skill_version text
)
returns table (
  skill_id text,
  skill_version text,
  projection jsonb,
  projection_sha256 text,
  git_source_path text,
  publication_status text
)
language sql
stable
set search_path = pg_catalog
as $function$
  select
    skill.skill_id,
    skill.skill_version,
    skill.projection,
    skill.projection_sha256,
    skill.git_source_path,
    skill.publication_status
  from public.mercury_published_skills as skill
  where skill.skill_id = p_skill_id
    and skill.skill_version = p_skill_version
    and public.mercury_v1_published_skill_is_visible(
      skill.visibility_scope,
      skill.tenant_id,
      skill.workspace_id,
      skill.publication_status,
      p_tenant_id,
      p_workspace_id,
      p_auth_user_id
    )
  order by skill.visibility_scope, skill.id;
$function$;

revoke all on function public.resolve_mercury_v1_published_skill(
  uuid, uuid, uuid, text, text
) from public, anon, authenticated;
grant execute on function public.resolve_mercury_v1_published_skill(
  uuid, uuid, uuid, text, text
) to service_role;

commit;
