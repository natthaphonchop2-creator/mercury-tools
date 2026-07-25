create table if not exists public.mercury_tenants (
  id uuid primary key default gen_random_uuid(),
  tenant_type text not null default 'personal',
  display_name text not null,
  personal_owner_auth_user_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint mercury_tenants_type_check
    check (tenant_type in ('personal', 'organization')),
  constraint mercury_tenants_personal_owner_check
    check (
      (tenant_type = 'personal' and personal_owner_auth_user_id is not null)
      or tenant_type = 'organization'
    )
);

create unique index if not exists mercury_tenants_one_personal_per_auth_user_idx
  on public.mercury_tenants (personal_owner_auth_user_id)
  where tenant_type = 'personal';

alter table public.mercury_workspaces
  add column if not exists tenant_id uuid
    references public.mercury_tenants(id) on delete restrict,
  add column if not exists owner_auth_user_id uuid,
  add column if not exists is_automatic_default boolean not null default false;

alter table public.mercury_workspace_members
  add column if not exists auth_user_id uuid,
  add column if not exists tenant_id uuid
    references public.mercury_tenants(id) on delete restrict;

alter table public.mercury_workspace_members
  alter column email drop not null;

create unique index if not exists
  mercury_workspaces_one_automatic_default_per_auth_user_idx
  on public.mercury_workspaces (owner_auth_user_id)
  where is_automatic_default;

create unique index if not exists mercury_workspace_members_auth_user_idx
  on public.mercury_workspace_members (workspace_id, auth_user_id)
  where auth_user_id is not null;

create index if not exists mercury_workspaces_tenant_idx
  on public.mercury_workspaces (tenant_id, id);

create index if not exists mercury_workspace_members_tenant_auth_user_idx
  on public.mercury_workspace_members (tenant_id, auth_user_id, workspace_id);

alter table public.mercury_tenants enable row level security;
alter table public.mercury_workspaces enable row level security;
alter table public.mercury_workspace_members enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_catalog.pg_policy
    where polname = 'mercury_tenants_select_member'
      and polrelid = 'public.mercury_tenants'::pg_catalog.regclass
  ) then
    execute $policy$
      create policy mercury_tenants_select_member
        on public.mercury_tenants
        for select
        to authenticated
        using (
          personal_owner_auth_user_id = (select auth.uid())
          or exists (
            select 1
            from public.mercury_workspace_members as member
            where member.tenant_id = mercury_tenants.id
              and member.auth_user_id = (select auth.uid())
              and member.status = 'active'
          )
        )
    $policy$;
  end if;
end;
$$;

do $$
begin
  if not exists (
    select 1
    from pg_catalog.pg_policy
    where polname = 'mercury_workspaces_select_member'
      and polrelid = 'public.mercury_workspaces'::pg_catalog.regclass
  ) then
    execute $policy$
      create policy mercury_workspaces_select_member
        on public.mercury_workspaces
        for select
        to authenticated
        using (
          exists (
            select 1
            from public.mercury_workspace_members as member
            where member.workspace_id = mercury_workspaces.id
              and member.tenant_id = mercury_workspaces.tenant_id
              and member.auth_user_id = (select auth.uid())
              and member.status = 'active'
          )
        )
    $policy$;
  end if;
end;
$$;

do $$
begin
  if not exists (
    select 1
    from pg_catalog.pg_policy
    where polname = 'mercury_workspace_members_select_self'
      and polrelid = 'public.mercury_workspace_members'::pg_catalog.regclass
  ) then
    execute $policy$
      create policy mercury_workspace_members_select_self
        on public.mercury_workspace_members
        for select
        to authenticated
        using (
          auth_user_id = (select auth.uid())
          and status = 'active'
        )
    $policy$;
  end if;
end;
$$;

revoke all on table public.mercury_tenants from public, anon, authenticated;
grant all on table public.mercury_tenants to service_role;

revoke all on table public.mercury_workspaces from anon, authenticated;
revoke all on table public.mercury_workspace_members from anon, authenticated;
grant select on table public.mercury_tenants,
  public.mercury_workspaces,
  public.mercury_workspace_members
  to authenticated;

create or replace function public.bootstrap_mercury_context()
returns table (
  status pg_catalog.text,
  active_workspace_id pg_catalog.uuid,
  memberships pg_catalog.jsonb,
  next_allowed_actions pg_catalog.text[]
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_auth_user_id pg_catalog.uuid;
  v_tenant_id pg_catalog.uuid;
  v_workspace_id pg_catalog.uuid;
  v_memberships pg_catalog.jsonb;
begin
  v_auth_user_id := auth.uid();
  if v_auth_user_id is null then
    raise insufficient_privilege
      using message = 'mercury_auth_required';
  end if;

  insert into public.mercury_tenants (
    tenant_type,
    display_name,
    personal_owner_auth_user_id
  )
  values (
    'personal',
    'Personal',
    v_auth_user_id
  )
  on conflict (personal_owner_auth_user_id)
    where tenant_type = 'personal'
  do update set
    personal_owner_auth_user_id = excluded.personal_owner_auth_user_id
  returning id into v_tenant_id;

  insert into public.mercury_workspaces (
    workspace_key,
    name,
    plan,
    status,
    metadata,
    tenant_id,
    owner_auth_user_id,
    is_automatic_default
  )
  values (
    'mercury-v1-personal-' || v_auth_user_id::pg_catalog.text,
    'Mercury Workspace',
    'v1-personal',
    'active',
    '{}'::pg_catalog.jsonb,
    v_tenant_id,
    v_auth_user_id,
    true
  )
  on conflict (owner_auth_user_id)
    where is_automatic_default
  do update set
    workspace_key = excluded.workspace_key,
    name = excluded.name,
    plan = excluded.plan,
    status = excluded.status,
    tenant_id = excluded.tenant_id,
    owner_auth_user_id = excluded.owner_auth_user_id,
    is_automatic_default = excluded.is_automatic_default,
    updated_at = pg_catalog.statement_timestamp()
  returning id into v_workspace_id;

  insert into public.mercury_workspace_members (
    workspace_id,
    email,
    role,
    host_app,
    status,
    last_seen_at,
    auth_user_id,
    tenant_id
  )
  values (
    v_workspace_id,
    null,
    'owner',
    'mercury-v1',
    'active',
    pg_catalog.statement_timestamp(),
    v_auth_user_id,
    v_tenant_id
  )
  on conflict (workspace_id, auth_user_id)
    where auth_user_id is not null
  do update set
    tenant_id = excluded.tenant_id,
    role = 'owner',
    host_app = excluded.host_app,
    status = 'active',
    last_seen_at = excluded.last_seen_at;

  select coalesce(
    pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'tenant_id', tenant.id,
        'tenant_display_name', tenant.display_name,
        'workspace_id', workspace.id,
        'workspace_display_name', workspace.name,
        'role', member.role
      )
      order by workspace.is_automatic_default desc, workspace.created_at, workspace.id
    ),
    '[]'::pg_catalog.jsonb
  )
  into v_memberships
  from public.mercury_workspace_members as member
  join public.mercury_workspaces as workspace
    on workspace.id = member.workspace_id
    and workspace.tenant_id = member.tenant_id
  join public.mercury_tenants as tenant
    on tenant.id = member.tenant_id
  where member.auth_user_id = v_auth_user_id
    and member.status = 'active'
    and workspace.status = 'active';

  return query
  select
    'ok'::pg_catalog.text,
    v_workspace_id,
    v_memberships,
    array[
      'list_accounting_providers',
      'start_provider_connection'
    ]::pg_catalog.text[];
end;
$$;

revoke all on function public.bootstrap_mercury_context()
  from public, anon, authenticated;
grant execute on function public.bootstrap_mercury_context()
  to authenticated;
